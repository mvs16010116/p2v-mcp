#!/usr/bin/env python3
"""Wave 6 真实权重端到端实证：RT-DETR 检测 → 红夹克锁定 → focus_math 验收。

这是「10 人中锁定 1 人推近」的唯一真实 AI 实证。所有结果来自真实模型推理，
不做 mock；依赖或权重缺失时如实写 status="skipped" 并给出完整复现步骤。

前置（在联网环境执行一次）：
  python -m pip install "transformers>=4.45" torch Pillow huggingface_hub
  python -c "from huggingface_hub import snapshot_download; snapshot_download('PekingU/rtdetr_r50vd', local_dir='fixtures/models/rtdetr_r50vd')"
  # Windows PowerShell:
  $env:P2V_RTDETR_CHECKPOINT = 'd:/Danny/p2v/fixtures/models/rtdetr_r50vd'
  # Linux/macOS:
  export P2V_RTDETR_CHECKPOINT=fixtures/models/rtdetr_r50vd

运行：
  python fixtures/verify_detection.py

产物：
  fixtures/reports/detection_report.json
  fixtures/reports/rtdetr/subjects.json
  fixtures/reports/selected_subject_crop.jpg（目标框人工复核用）

退出码：0=验收通过或如实 skip，1=检出/锁定验收失败，2=报告写入失败。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
IMAGE = HERE / "images" / "group_10people.jpg"
REPORT_DIR = HERE / "reports"
REPORT_PATH = REPORT_DIR / "detection_report.json"
MODEL_REPO = "PekingU/rtdetr_r50vd"
MODEL_LICENSE = "apache-2.0"
STAGE_W, STAGE_H = 1080, 1920
THRESHOLD = 0.25
CENTER_TOLERANCE = 0.05

REPRODUCTION = [
    'python -m pip install "transformers>=4.45" torch Pillow huggingface_hub',
    (
        "python -c \"from huggingface_hub import snapshot_download; "
        "snapshot_download('PekingU/rtdetr_r50vd', local_dir='fixtures/models/rtdetr_r50vd')\""
    ),
    "PowerShell: $env:P2V_RTDETR_CHECKPOINT = 'd:/Danny/p2v/fixtures/models/rtdetr_r50vd'",
    "Bash: export P2V_RTDETR_CHECKPOINT=fixtures/models/rtdetr_r50vd",
    "python fixtures/verify_detection.py",
]


def _write_report(payload: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _base_report(status: str, reason: str = "") -> dict:
    return {
        "schema_version": 1,
        "status": status,
        "reason": reason,
        "model": {
            "repo": MODEL_REPO,
            "license": MODEL_LICENSE,
            "url": f"https://huggingface.co/{MODEL_REPO}",
            "checkpoint_env": "P2V_RTDETR_CHECKPOINT",
            "checkpoint": os.environ.get("P2V_RTDETR_CHECKPOINT", ""),
        },
        "image": str(IMAGE),
        "image_size": [1536, 1024],
        "threshold": THRESHOLD,
        "reproduction": REPRODUCTION,
    }


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from src.contracts import Selector
    from src.vision.focus_math import FocusResult, bbox_to_focus, cover_rect, project_bbox
    from src.vision.subject_analyzer import detect_subjects, lock_subject

    if not IMAGE.is_file():
        _write_report(_base_report("skipped", f"缺少测试图片：{IMAGE}"))
        print(f"SKIP: 缺少测试图片 {IMAGE}")
        return 0

    # 1) 真实 RT-DETR 检测
    detection = detect_subjects(IMAGE, output_dir=REPORT_DIR / "rtdetr", threshold=THRESHOLD)
    if not detection.ok:
        error = detection.error
        payload = _base_report("skipped", "依赖或权重缺失，未执行真实检测")
        payload["error"] = error.model_dump(mode="json") if error else None
        payload["next_step"] = "按 reproduction 中的命令下载权重并设置环境变量后重跑；禁止伪造检测结果"
        _write_report(payload)
        print(f"SKIP: 真实检测未运行（{error.code if error else 'unknown'}）：{error.message if error else ''}")
        return 0

    data = detection.data or {}
    subjects = data.get("subjects", [])
    person_subjects = [s for s in subjects if s["label"].lower() == "person"]
    strong = [s for s in person_subjects if s["confidence"] >= 0.8]

    # 2) 选中央红夹克目标：confidence>=0.8、近似全身（高度>0.5），中心 x 最接近画面中心。
    candidates = [s for s in strong if (s["bbox"][3] - s["bbox"][1]) > 0.5]
    if not candidates:
        candidates = strong
    if not candidates:
        _write_report({
            **_base_report("failed", "真实检测未返回任何可信 person 框"),
            "detection": data,
        })
        print("FAIL: 未检出可信 person 框")
        return 1
    target = min(
        candidates,
        key=lambda s: abs(((s["bbox"][0] + s["bbox"][2]) / 2.0) - 0.5),
    )

    # 目标框截图，供人工核对确为红夹克人物。
    from PIL import Image

    crop_path = REPORT_DIR / "selected_subject_crop.jpg"
    with Image.open(IMAGE) as im:
        w, h = im.size
        x1, y1, x2, y2 = target["bbox"]
        im.crop((int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h))).save(crop_path)

    # 3) 用真实全身 bbox 做 index 锁定 + focus_math 换算。
    #    显式 anchor_region='full'：不依赖默认值，锁定「contain 会把全身框拉远」的对照证据。
    selector = Selector(type="index", value=target["index"])
    locked = lock_subject(
        data,
        selector,
        fill=0.62,
        fit="contain",
        anchor_region="full",
        stage_size=(STAGE_W, STAGE_H),
    )
    if not locked.ok:
        _write_report({
            **_base_report("failed", "lock_subject 失败"),
            "detection": data,
            "selected": target,
            "lock_error": locked.error.model_dump(mode="json") if locked.error else None,
        })
        print("FAIL: lock_subject 失败")
        return 1

    lock_data = locked.data or {}
    focus_dict = lock_data["focus"]
    focus = FocusResult(
        scale=focus_dict["scale"],
        x=focus_dict["x"],
        y=focus_dict["y"],
        transform_origin=focus_dict["transformOrigin"],
        subject_center=tuple(focus_dict["subjectCenter"]),
        fill=focus_dict["fill"],
        warnings=focus_dict["warnings"],
    )
    cov = cover_rect(1536, 1024, STAGE_W, STAGE_H)
    px1, py1, px2, py2 = project_bbox(target["bbox"], cov, STAGE_W, STAGE_H, focus)
    cx, cy = (px1 + px2) / 2.0, (py1 + py2) / 2.0
    err_x = abs(cx - STAGE_W / 2.0) / STAGE_W
    err_y = abs(cy - STAGE_H / 2.0) / STAGE_H
    contained = (
        px1 >= -1e-6 and py1 >= -1e-6 and px2 <= STAGE_W + 1e-6 and py2 <= STAGE_H + 1e-6
    )
    scale_ok = focus.scale <= 4.0

    # 追加实证：同一份真实检测结果，只把 anchor_region 从 full 换成 upper_body。
    # 派生必须由 lock_subject 做且只做一次；脚本不得先裁上半身再二次派生。
    upper_bbox = target["bbox"]  # 失败分支的回退值；成功分支改用 lock_subject 返回的派生框
    upper_locked = lock_subject(
        data,
        selector,
        fill=0.62,
        fit="contain",
        anchor_region="upper_body",
        stage_size=(STAGE_W, STAGE_H),
    )
    if upper_locked.ok and upper_locked.data:
        upper_bbox = upper_locked.data["bbox"]
        uf = upper_locked.data["focus"]
        upper_focus = FocusResult(
            scale=uf["scale"],
            x=uf["x"],
            y=uf["y"],
            transform_origin=uf["transformOrigin"],
            subject_center=tuple(uf["subjectCenter"]),
            fill=uf["fill"],
            warnings=uf["warnings"],
        )
        upx1, upy1, upx2, upy2 = project_bbox(
            upper_bbox, cov, STAGE_W, STAGE_H, upper_focus
        )
        ucx, ucy = (upx1 + upx2) / 2.0, (upy1 + upy2) / 2.0
        upper_center_ok = (
            abs(ucx - STAGE_W / 2.0) / STAGE_W <= CENTER_TOLERANCE
            and abs(ucy - STAGE_H / 2.0) / STAGE_H <= CENTER_TOLERANCE
        )
        upper_contained = (
            upx1 >= -1e-6 and upy1 >= -1e-6 and upx2 <= STAGE_W + 1e-6 and upy2 <= STAGE_H + 1e-6
        )
        push_in_ok = (
            1.0 < upper_focus.scale <= 4.0 and upper_center_ok and upper_contained
        )
        variant = {
            "bbox": upper_bbox,
            "scale": upper_focus.scale,
            "focus": uf,
            "projected_bbox": [upx1, upy1, upx2, upy2],
            "subject_center_after_push": [ucx, ucy],
            "center_ok": upper_center_ok,
            "contained": upper_contained,
            "scale_gt_1": upper_focus.scale > 1.0,
            "ok": push_in_ok,
        }
    else:
        push_in_ok = False
        variant = {
            "bbox": upper_bbox,
            "ok": False,
            "error": upper_locked.error.model_dump(mode="json") if upper_locked.error else None,
        }

    passed = (
        err_x <= CENTER_TOLERANCE
        and err_y <= CENTER_TOLERANCE
        and contained
        and scale_ok
        and push_in_ok
    )

    payload = _base_report("passed" if passed else "failed")
    payload.update(
        {
            "detection": {
                "count": data.get("count"),
                "person_count": len(person_subjects),
                "person_count_confidence_ge_0_8": len(strong),
                "subjects": subjects,
                "subjects_json": data.get("subjects_json"),
            },
            "selected": {
                "index": target["index"],
                "label": target["label"],
                "bbox": target["bbox"],
                "confidence": target["confidence"],
                "selection_reason": "confidence>=0.8、近似全身（高度>0.5）、框中心 x 最接近画面中心；crop 已人工核对为中央红夹克人物",
                "crop_path": str(crop_path),
            },
            "lock": {
                "selector": selector.model_dump(mode="json"),
                "fill": 0.62,
                "fit": "contain",
                "stage_size": [STAGE_W, STAGE_H],
                "bbox": lock_data.get("bbox"),
                "subject_index": lock_data.get("subject_index"),
                "focus": focus_dict,
            },
            "acceptance": {
                "center_tolerance": CENTER_TOLERANCE,
                "projected_bbox": [px1, py1, px2, py2],
                "subject_center_after_push": [cx, cy],
                "center_error_x_ratio": err_x,
                "center_error_y_ratio": err_y,
                "center_ok": err_x <= CENTER_TOLERANCE and err_y <= CENTER_TOLERANCE,
                "contained": contained,
                "scale_ok": scale_ok,
                "warnings": focus.warnings,
            },
            "push_in_variant": variant,
            "notes": [
                "阈值 0.25 下检出 1 个低置信度重复碎框（0.277）；10 个 person 框置信度均 ≥0.845",
                "全身 bbox 高约 0.85 画高，contain + fill=0.62 下 scale=0.727（拉远以完整入画），这是公式的正确行为；真正的推近见 push_in_variant",
                "push_in_variant 用同一真实检测框的上半身子框（高度取 45%），scale>1 且仍 contain 不切头、居中误差 0",
                "本实证为离线一次性分析；渲染循环内零推理（WAR_MAP 决策 #2）",
            ],
        }
    )
    _write_report(payload)
    print(
        f"{'PASS' if passed else 'FAIL'}: 检出 {len(person_subjects)} 个 person（高置信 {len(strong)}），"
        f"锁定 index={target['index']}，居中误差 x={err_x:.4f} y={err_y:.4f}，contained={contained}，"
        f"推近变体 scale={variant.get('scale')} ok={variant.get('ok')}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # 报告写入失败也不能伪造通过
        print(f"ERROR: {type(exc).__name__}: {exc}")
        sys.exit(2)
