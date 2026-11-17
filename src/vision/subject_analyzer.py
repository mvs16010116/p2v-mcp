"""离线主体分析（方案 20.1–20.3）：检测 / 锁定 / 分割。

铁律：AI 只在离线分析阶段运行；本模块所有重依赖（torch / transformers /
groundingdino / insightface / cv2 / segment_anything / PIL / numpy）都在函数内部
惰性导入，ImportError 一律映射为 ErrorCode.BINARY_NOT_FOUND，主流程不崩，
Agent 可退化为 R6 手工锚点。模型权重只从本地路径读取（local_files_only），
绝不隐式联网下载。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from src.contracts import Selector, ToolResponse

from ._common import (
    VisionDependencyError,
    binary_not_found,
    ensure_dir,
    file_not_found,
    import_or_raise,
    read_image_size,
    require_checkpoint,
    require_env,
)
from .focus_math import FocusResult, bbox_to_focus, cover_rect, derive_anchor_bbox
from .models import ArtifactPaths, FocusLock, Subject, SubjectAnalysis

DEFAULT_DETECT_CLASSES = ("person",)
DEFAULT_STAGE_SIZE = (1080, 1920)  # 方案默认竖屏 9:16


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _label_name(id2label: Any, label_id: int) -> str:
    if isinstance(id2label, dict):
        return str(id2label.get(label_id, label_id))
    try:
        return str(id2label[label_id])
    except (TypeError, IndexError, KeyError):
        return str(label_id)


def _normalize_pixel_bbox(
    box: tuple[float, float, float, float] | list[float], width: int, height: int
) -> list[float]:
    x1, y1, x2, y2 = (float(v) for v in box)
    return [
        min(1.0, max(0.0, x1 / width)),
        min(1.0, max(0.0, y1 / height)),
        min(1.0, max(0.0, x2 / width)),
        min(1.0, max(0.0, y2 / height)),
    ]


def _coerce_analysis(analysis: SubjectAnalysis | dict[str, Any] | str | Path) -> SubjectAnalysis:
    if isinstance(analysis, SubjectAnalysis):
        return analysis
    if isinstance(analysis, (str, Path)):
        path = Path(analysis).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"subjects.json 不存在：{path}")
        data = json.loads(path.read_text(encoding="utf-8"))
    elif isinstance(analysis, dict):
        data = dict(analysis)
    else:
        raise TypeError("analysis 须为 SubjectAnalysis / dict / subjects.json 路径")
    # 兼容 detect_subjects 返回的 data（多带了 subjects_json 键）。
    allowed = set(SubjectAnalysis.model_fields)
    return SubjectAnalysis.model_validate({k: v for k, v in data.items() if k in allowed})


def _coerce_selector(selector: Selector | dict[str, Any]) -> Selector:
    if isinstance(selector, Selector):
        return selector
    if isinstance(selector, dict):
        return Selector.model_validate(selector)
    raise TypeError("selector 须为 Selector 或等价 dict")


def _candidates(analysis: SubjectAnalysis) -> list[dict[str, Any]]:
    return [s.model_dump(mode="json") for s in analysis.subjects]


def _focus_response(
    analysis: SubjectAnalysis,
    selector: Selector,
    subject: Subject,
    stage_size: tuple[int, int],
    fill: float,
    fit: Literal["contain", "cover"],
    *,
    bbox: list[float] | None = None,
    anchor_region: str = "full",
) -> ToolResponse:
    anchor_bbox = subject.bbox if bbox is None else bbox
    cov = cover_rect(analysis.image_size[0], analysis.image_size[1], *stage_size)
    result: FocusResult = bbox_to_focus(anchor_bbox, cov, *stage_size, fill=fill, fit=fit)
    lock = FocusLock(
        bbox=anchor_bbox,
        subject_index=subject.index,
        label=subject.label,
        confidence=subject.confidence,
        fill=result.fill,
        fit=fit,
        scale=result.scale,
        x=result.x,
        y=result.y,
        subject_center=result.subject_center,
        warnings=result.warnings,
        extra={"selector": selector.model_dump(mode="json")},
    )
    data = lock.model_dump(mode="json")
    # 保留原始全身框与取框区域，便于复核 anchor_region 的派生行为。
    data["full_bbox"] = subject.bbox
    data["anchor_region"] = anchor_region
    # 渲染层是 JS/GSAP：额外提供方案 20.4 的 verbatim 键名。
    data["focus"] = {
        "scale": result.scale,
        "x": result.x,
        "y": result.y,
        "transformOrigin": result.transform_origin,
        "subjectCenter": list(result.subject_center),
        "fill": result.fill,
        "fit": fit,
        "warnings": result.warnings,
    }
    return ToolResponse(ok=True, data=data, error=None)


# ---------------------------------------------------------------------------
# 1) detect_subjects
# ---------------------------------------------------------------------------
def detect_subjects(
    image_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    detect: list[str] | None = None,
    threshold: float = 0.3,
    checkpoint: str | Path | None = None,
) -> ToolResponse:
    """用 RT-DETR 检测候选主体，落盘 subjects.json，返回候选列表。

    缺少 torch / transformers、未配置本地权重或版本过低时返回 BINARY_NOT_FOUND。
    checkpoint 参数优先于环境变量 P2V_RTDETR_CHECKPOINT。
    """
    try:
        image = Path(image_path).expanduser()
        if not image.is_file():
            raise FileNotFoundError(f"图片不存在：{image}")
        width, height = read_image_size(image)

        if checkpoint is not None:
            ckpt = Path(checkpoint).expanduser()
            if not ckpt.exists():
                raise VisionDependencyError(
                    f"RT-DETR 权重路径不存在：{ckpt}", {"checkpoint": str(ckpt)}
                )
        else:
            ckpt = require_checkpoint(
                "P2V_RTDETR_CHECKPOINT",
                "从 HuggingFace 下载 PekingU/rtdetr_r50vd 到本地后设置该环境变量",
            )

        torch = import_or_raise("torch", "pip install torch（CPU 版即可）")
        transformers = import_or_raise("transformers", "pip install transformers>=4.45")
        image_module = import_or_raise("PIL.Image", "pip install Pillow")
        model_cls = getattr(transformers, "RTDetrForObjectDetection", None)
        # transformers 5.x 的 AutoImageProcessor 依赖 torchvision；
        # RTDetrImageProcessorPil 是官方提供的无 torchvision 回退实现。
        processor_cls = getattr(transformers, "RTDetrImageProcessorPil", None)
        if processor_cls is None:
            processor_cls = getattr(transformers, "AutoImageProcessor", None)
        if model_cls is None or processor_cls is None:
            raise VisionDependencyError(
                "当前 transformers 版本缺少 RTDetrForObjectDetection / AutoImageProcessor",
                {"hint": "pip install -U transformers>=4.45"},
            )

        processor = processor_cls.from_pretrained(str(ckpt), local_files_only=True)
        model = model_cls.from_pretrained(str(ckpt), local_files_only=True)
        model.eval()

        with image_module.open(image) as im:
            pil = im.convert("RGB")
        inputs = processor(images=pil, return_tensors="pt")
        with torch.no_grad():
            outputs = model(**inputs)
        detected = processor.post_process_object_detection(
            outputs, target_sizes=[(height, width)], threshold=threshold
        )[0]

        wanted = {c.lower() for c in (detect or DEFAULT_DETECT_CLASSES)}
        subjects: list[Subject] = []
        for box, score, label in zip(detected["boxes"], detected["scores"], detected["labels"]):
            name = _label_name(model.config.id2label, int(label))
            if wanted and name.lower() not in wanted:
                continue
            bbox = _normalize_pixel_bbox([float(v) for v in box.tolist()], width, height)
            subjects.append(
                Subject(
                    index=len(subjects),
                    label=name,
                    bbox=bbox,
                    confidence=max(0.0, min(1.0, float(score))),
                )
            )

        target_dir = ensure_dir(output_dir) if output_dir else ensure_dir(image.parent / "analysis")
        subjects_json = target_dir / "subjects.json"
        analysis = SubjectAnalysis(
            image=str(image),
            image_size=(width, height),
            subjects=subjects,
            count=len(subjects),
            artifacts=ArtifactPaths(subjects_json=str(subjects_json)),
        )
        subjects_json.write_text(
            json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        data = analysis.model_dump(mode="json")
        data["subjects_json"] = str(subjects_json)
        return ToolResponse(ok=True, data=data, error=None)

    except VisionDependencyError as exc:
        return binary_not_found(exc.message, exc.detail)
    except FileNotFoundError as exc:
        return file_not_found(str(exc))
    except Exception as exc:  # 权重损坏 / 运行时不兼容：明示原因，不伪装成功
        return binary_not_found(
            "RT-DETR 离线检测失败（权重或运行时不兼容）", {"reason": f"{type(exc).__name__}: {exc}"}
        )


# ---------------------------------------------------------------------------
# 2) lock_subject
# ---------------------------------------------------------------------------
def _text_box_grounding_dino_hf(image: Path, query: str) -> tuple[list[float], str, float]:
    """Grounding DINO 文字锁定（transformers 原生实现 + Apache 2.0 权重）。

    与 RT-DETR 同构：处理器/模型仅从本地目录加载（local_files_only=True），
    不依赖 groundingdino-py 的 CUDA 编译扩展；重依赖惰性导入，缺失时统一降级。
    """
    ckpt = require_checkpoint(
        "P2V_GROUNDING_DINO_CHECKPOINT",
        "hf download IDEA-Research/grounding-dino-tiny --local-dir "
        "./fixtures/models/grounding-dino-tiny 后把该目录设为本变量（Apache 2.0）",
    )
    torch = import_or_raise("torch", "pip install torch（CPU 版即可）")
    transformers = import_or_raise("transformers", "pip install transformers>=4.45")
    image_module = import_or_raise("PIL.Image", "pip install Pillow")
    processor_cls = getattr(transformers, "GroundingDinoProcessor", None)
    model_cls = getattr(transformers, "GroundingDinoForObjectDetection", None)
    if processor_cls is None or model_cls is None:
        raise VisionDependencyError(
            "当前 transformers 版本缺少 GroundingDinoProcessor / GroundingDinoForObjectDetection",
            {"hint": "pip install -U transformers>=4.45"},
        )

    processor = processor_cls.from_pretrained(str(ckpt), local_files_only=True)
    model = model_cls.from_pretrained(str(ckpt), local_files_only=True)
    model.eval()
    width, height = read_image_size(image)
    with image_module.open(image) as im:
        pil = im.convert("RGB")
    inputs = processor(images=pil, text=query.strip(), return_tensors="pt")
    with torch.no_grad():
        outputs = model(**inputs)
    results = processor.post_process_grounded_object_detection(
        outputs=outputs,
        input_ids=inputs["input_ids"],
        target_sizes=torch.tensor([[height, width]]),
        threshold=0.1,      # grounding-dino-tiny 对属性词置信度偏低（实测 ~0.13）
        text_threshold=0.1,
    )[0]
    boxes, scores = results["boxes"], results["scores"]
    if len(boxes) == 0:
        raise ValueError(f"text selector 未检出与「{query}」匹配的主体，请改用 index selector")
    best = max(range(len(scores)), key=lambda i: float(scores[i]))
    bbox = _normalize_pixel_bbox([float(v) for v in boxes[best].tolist()], width, height)
    return bbox, query, max(0.0, min(1.0, float(scores[best])))


def _text_box_legacy(image: Path, query: str) -> tuple[list[float], str, float]:
    """旧 groundingdino-py 路径（.pth + .py 配置），保留给已装扩展的环境。"""
    ckpt = require_checkpoint("P2V_GROUNDING_DINO_CHECKPOINT", "下载 groundingdino 权重到本地后设置")
    config = require_checkpoint("P2V_GROUNDING_DINO_CONFIG", "下载 GroundingDINO_SwinT_OGC.py 配置到本地后设置")
    inference = import_or_raise("groundingdino.util.inference", "pip install groundingdino-py")
    width, height = read_image_size(image)
    model = inference.load_model(str(config), str(ckpt), device="cpu")
    _, transformed = inference.load_image(str(image))
    boxes, logits, _ = inference.predict(
        model, transformed, query, box_threshold=0.3, text_threshold=0.25
    )
    if len(boxes) == 0:
        raise ValueError(f"text selector 未检出与「{query}」匹配的主体，请改用 index selector")
    best = 0
    bbox = _normalize_pixel_bbox([float(v) for v in boxes[best].tolist()], width, height)
    return bbox, query, max(0.0, min(1.0, float(logits[best])))


def _text_box(image: Path, query: str) -> tuple[list[float], str, float]:
    """按本地配置选择 Grounding DINO 实现，返回归一化 bbox / 标签 / 置信度。"""
    ckpt_raw = os.environ.get("P2V_GROUNDING_DINO_CHECKPOINT", "").strip()
    cfg_raw = os.environ.get("P2V_GROUNDING_DINO_CONFIG", "").strip()
    if cfg_raw and ckpt_raw and Path(ckpt_raw).expanduser().is_file():
        return _text_box_legacy(image, query)   # 老式 .pth + .py 配置
    if ckpt_raw:
        return _text_box_grounding_dino_hf(image, query)  # transformers 本地目录（默认）
    if cfg_raw:
        return _text_box_legacy(image, query)
    raise VisionDependencyError(
        "text selector 需要本地 Grounding DINO 权重（Apache 2.0），未配置时已降级",
        {
            "env_var": "P2V_GROUNDING_DINO_CHECKPOINT",
            "hint": (
                "hf download IDEA-Research/grounding-dino-tiny --local-dir "
                "./fixtures/models/grounding-dino-tiny；然后把该目录设为 "
                "P2V_GROUNDING_DINO_CHECKPOINT（render 阶段零推理）"
            ),
            "license": "apache-2.0",
        },
    )


def _reference_box(
    image: Path, analysis: SubjectAnalysis, reference: str
) -> tuple[list[float], str, float]:
    """参考图 embedding 比对（实验性，默认关闭；许可见 src/vision/README.md）。"""
    ref_path = Path(reference).expanduser()
    if not ref_path.is_file():
        raise FileNotFoundError(f"参考图不存在：{ref_path}")
    model_root = require_env(
        "P2V_INSIGHTFACE_MODEL_ROOT",
        "下载 insightface 模型包（如 buffalo_l）到本地目录后设置",
    )
    face_module = import_or_raise("insightface", "pip install insightface")
    cv2 = import_or_raise("cv2", "pip install opencv-python-headless")
    np = import_or_raise("numpy", "pip install numpy")
    image_module = import_or_raise("PIL.Image", "pip install Pillow")

    app = face_module.FaceAnalysis(name="buffalo_l", root=model_root, providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1)
    ref_faces = app.get(cv2.imread(str(ref_path)))
    if not ref_faces:
        raise ValueError("参考图中未检出人脸，请改用 index selector")
    ref_embedding = np.asarray(ref_faces[0].embedding, dtype="float32")
    ref_norm = np.linalg.norm(ref_embedding) + 1e-9

    best_subject: Subject | None = None
    best_score = -1.0
    with image_module.open(image) as im:
        rgb = im.convert("RGB")
    width, height = rgb.size
    for subject in analysis.subjects:
        x1, y1, x2, y2 = subject.bbox
        crop = rgb.crop((int(x1 * width), int(y1 * height), int(x2 * width), int(y2 * height)))
        bgr = cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2BGR)
        faces = app.get(bgr)
        if not faces:
            continue
        emb = np.asarray(faces[0].embedding, dtype="float32")
        score = float(np.dot(ref_embedding, emb) / ((ref_norm + np.linalg.norm(emb) + 1e-9)))
        if score > best_score:
            best_score = score
            best_subject = subject
    if best_subject is None:
        raise ValueError("群像中未检出可与参考图比对的人脸，请改用 index selector")
    return best_subject.bbox, f"reference:{ref_path.name}", max(0.0, min(1.0, best_score))


def lock_subject(
    analysis: SubjectAnalysis | dict[str, Any] | str | Path,
    selector: Selector | dict[str, Any],
    fill: float = 0.62,
    fit: Literal["contain", "cover"] = "contain",
    *,
    anchor_region: Literal["full", "upper_body", "face"] = "upper_body",
    stage_size: tuple[int, int] = DEFAULT_STAGE_SIZE,
) -> ToolResponse:
    """按 text / reference / index 锁定主体，返回 bbox + focus 数据块。

    默认先按 anchor_region='upper_body' 从全身框派生头+肩区域再算 focus，避免
    contain 把镜头拉远（方案 20.4 实测洞察）。index selector 为纯计算，无 ML
    依赖；text / reference 缺依赖时返回 BINARY_NOT_FOUND，并在
    error.detail.candidates 附带候选列表供 Agent 改用 index。
    """
    ana = _coerce_analysis(analysis)
    sel = _coerce_selector(selector)
    candidates = _candidates(ana)
    if not (0.1 <= fill <= 1.0):
        raise ValueError("fill 必须在 [0.1, 1.0]")
    if fit not in ("contain", "cover"):
        raise ValueError("fit 只能是 'contain' 或 'cover'")
    if anchor_region not in ("full", "upper_body", "face"):
        raise ValueError("anchor_region 只能是 'full' / 'upper_body' / 'face'")

    try:
        if sel.type == "index":
            value = sel.value
            assert value is not None  # Selector 模型已保证
            if value >= len(ana.subjects):
                raise ValueError(
                    f"index={value} 越界（共 {len(ana.subjects)} 个候选）：{candidates}"
                )
            subject = ana.subjects[value]
        else:
            image = Path(ana.image).expanduser()
            if sel.type == "text":
                assert sel.query is not None
                bbox, label, confidence = _text_box(image, sel.query)
            else:
                assert sel.image is not None
                bbox, label, confidence = _reference_box(image, ana, sel.image)

            # text/reference 得到的框不在候选列表里，构造一个临时 Subject 参与换算。
            subject = Subject(
                index=len(ana.subjects), label=label, bbox=bbox, confidence=confidence
            )

        # 方案 20.6：在 focus_math 换算前按 anchor_region 派生取框区域。
        derived = derive_anchor_bbox(subject.bbox, anchor_region, ana.image_size)
        if not derived.ok:
            if derived.error is not None:
                derived.error.detail["candidates"] = candidates
            return derived
        anchor_bbox = (derived.data or {})["bbox"]
        return _focus_response(
            ana,
            sel,
            subject,
            stage_size,
            fill,
            fit,
            bbox=anchor_bbox,
            anchor_region=anchor_region,
        )

    except VisionDependencyError as exc:
        detail = dict(exc.detail)
        detail["candidates"] = candidates
        return binary_not_found(exc.message, detail)
    except FileNotFoundError as exc:
        detail = {"candidates": candidates}
        return file_not_found(f"{exc}；候选列表见 detail.candidates，可改用 index selector", detail)


# ---------------------------------------------------------------------------
# 3) segment_subject
# ---------------------------------------------------------------------------
def segment_subject(
    image: str | Path,
    bbox: tuple[float, float, float, float] | list[float],
    *,
    output_path: str | Path | None = None,
    checkpoint: str | Path | None = None,
    model_type: str | None = None,
) -> ToolResponse:
    """用 SAM 将主体抠成透明 PNG（方案 20.5 分层视差），返回产物路径。

    bbox 为归一化 [0,1]；缺 SAM 权重 / 依赖时返回 BINARY_NOT_FOUND。
    """
    import hashlib

    try:
        image_path = Path(image).expanduser()
        if not image_path.is_file():
            raise FileNotFoundError(f"图片不存在：{image_path}")
        if len(bbox) != 4:
            raise ValueError("bbox 必须为 [x1,y1,x2,y2]（归一化 [0,1]）")
        x1, y1, x2, y2 = (float(v) for v in bbox)
        if not (0.0 <= x1 < x2 <= 1.0 and 0.0 <= y1 < y2 <= 1.0):
            raise ValueError("bbox 须满足 0 ≤ x1 < x2 ≤ 1、0 ≤ y1 < y2 ≤ 1")

        if checkpoint is not None:
            ckpt = Path(checkpoint).expanduser()
            if not ckpt.is_file():
                raise VisionDependencyError(
                    f"SAM 权重文件不存在：{ckpt}", {"checkpoint": str(ckpt)}
                )
        else:
            ckpt = require_checkpoint("P2V_SAM_CHECKPOINT", "下载 SAM viT-B 权重到本地后设置")
        chosen_type = model_type or require_env("P2V_SAM_MODEL_TYPE", "例如 vit_b / vit_l / vit_h")

        torch = import_or_raise("torch", "pip install torch（CPU 版即可）")
        np = import_or_raise("numpy", "pip install numpy")
        _cv2 = import_or_raise("cv2", "pip install opencv-python-headless")
        image_module = import_or_raise("PIL.Image", "pip install Pillow")
        sam_module = import_or_raise("segment_anything", "pip install segment-anything")
        registry = getattr(sam_module, "sam_model_registry", None)
        predictor_cls = getattr(sam_module, "SamPredictor", None)
        if registry is None or predictor_cls is None or chosen_type not in registry:
            raise VisionDependencyError(
                f"SAM 不支持 model_type={chosen_type!r}",
                {"model_type": chosen_type, "hint": "设置 P2V_SAM_MODEL_TYPE 为 vit_b/vit_l/vit_h"},
            )

        with image_module.open(image_path) as im:
            rgb = im.convert("RGB")
        w, h = rgb.size
        sam = registry[chosen_type](checkpoint=str(ckpt))
        sam.to(device="cpu")
        predictor = predictor_cls(sam)
        predictor.set_image(np.asarray(rgb))
        box_prompt = np.asarray([x1 * w, y1 * h, x2 * w, y2 * h])
        masks, scores, _ = predictor.predict(box=box_prompt, multimask_output=False)
        best = int(np.argmax(scores))
        mask = masks[best].astype(bool)

        rgba = np.dstack([np.asarray(rgb), (mask * 255).astype("uint8")])
        if output_path is not None:
            target = Path(output_path).expanduser()
        else:
            key = hashlib.sha1(f"{bbox}|{image_path}".encode("utf-8")).hexdigest()[:8]
            target = image_path.parent / "masks" / f"subject_{key}.png"
        ensure_dir(target.parent)
        image_module.fromarray(rgba, mode="RGBA").save(target)
        return ToolResponse(
            ok=True,
            data={
                "mask_path": str(target),
                "bbox": list(bbox),
                "image_size": [w, h],
                "confidence": float(scores[best]),
                "warnings": ["SAM 透明 PNG 为近似视差图层，非真光学景深（方案 20.8）"],
            },
            error=None,
        )

    except VisionDependencyError as exc:
        return binary_not_found(exc.message, exc.detail)
    except FileNotFoundError as exc:
        return file_not_found(str(exc))
    except Exception as exc:
        return binary_not_found(
            "SAM 离线分割失败（权重或运行时不兼容）", {"reason": f"{type(exc).__name__}: {exc}"}
        )


__all__ = ["detect_subjects", "lock_subject", "segment_subject"]
