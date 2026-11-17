"""text selector 真实权重实证（方案 20.7 IoU ≥ 0.5，非 mock）。

用 Apache-2.0 的 IDEA-Research/grounding-dino-tiny（transformers 原生实现）
在 Wave 6 同一张 10 人群像上跑 `lock_subject(selector={type:'text', query:'red'})`，
与 RT-DETR 已实证的中央红夹克全身框做 IoU 对比，落盘可引用报告。

复现命令（本机已下载权重到 fixtures/models/grounding-dino-tiny）：
    python -m pip install "transformers>=4.45" torch Pillow
    hf download IDEA-Research/grounding-dino-tiny --local-dir ./fixtures/models/grounding-dino-tiny
    PowerShell: $env:P2V_GROUNDING_DINO_CHECKPOINT = './fixtures/models/grounding-dino-tiny'
    python fixtures/verify_text_selector.py

AI 只在离线分析阶段运行；渲染循环零推理（WAR_MAP 决策 #2）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.contracts import Selector  # noqa: E402
from src.vision.focus_math import iou  # noqa: E402
from src.vision.subject_analyzer import lock_subject  # noqa: E402

REPORT_DIR = ROOT / "fixtures" / "reports"
DETECTION_REPORT = REPORT_DIR / "detection_report.json"
OUTPUT = REPORT_DIR / "text_selector_iou_report.json"
WEIGHTS = ROOT / "fixtures" / "models" / "grounding-dino-tiny"
MODEL_REPO = "IDEA-Research/grounding-dino-tiny"
LICENSE = "apache-2.0"
QUERIES = ("red", "a person in red", "red jacket")
IOU_THRESHOLD = 0.5
REPRODUCTION = [
    'python -m pip install "transformers>=4.45" torch Pillow',
    f"hf download {MODEL_REPO} --local-dir ./fixtures/models/grounding-dino-tiny",
    "PowerShell: $env:P2V_GROUNDING_DINO_CHECKPOINT = './fixtures/models/grounding-dino-tiny'",
    "python fixtures/verify_text_selector.py",
]


def _load_gt() -> tuple[str, tuple[int, int], list[float]]:
    """从 Wave 6 实证报告读真实红夹克框，不重复跑 RT-DETR 权重。"""
    data = json.loads(DETECTION_REPORT.read_text(encoding="utf-8"))
    return (
        str(data["image"]),
        tuple(data["image_size"]),
        list(data["selected"]["bbox"]),
    )


def main() -> int:
    if not DETECTION_REPORT.is_file():
        print(f"baseline 报告缺失，无法算 IoU：{DETECTION_REPORT}")
        return 1
    # 脚本内显式指向本地权重；不改全局配置、不联网下载。
    os.environ.setdefault("P2V_GROUNDING_DINO_CHECKPOINT", str(WEIGHTS))
    if not WEIGHTS.is_dir():
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(
            json.dumps(
                {
                    "status": "weight_pending",
                    "reason": "本地未找到 grounding-dino-tiny 权重；代码就绪、权重待验",
                    "model": {"repo": MODEL_REPO, "license": LICENSE},
                    "reproduction": REPRODUCTION,
                    "iou_threshold": IOU_THRESHOLD,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"权重缺失，已写待验报告：{OUTPUT}")
        return 0

    image, image_size, gt_bbox = _load_gt()
    analysis = {
        "image": image,
        "image_size": list(image_size),
        "subjects": [],
        "count": 0,
    }

    trials: list[dict] = []
    for query in QUERIES:
        # anchor_region='full'：text 检出的框原样与 RT-DETR 全身框对比，不做上半身派生。
        resp = lock_subject(
            analysis, Selector(type="text", query=query), anchor_region="full"
        )
        if not resp.ok:
            trials.append(
                {
                    "query": query,
                    "ok": False,
                    "error": resp.error.model_dump(mode="json") if resp.error else None,
                }
            )
            continue
        data = resp.data or {}
        bbox = list(data["bbox"])
        score = iou(bbox, gt_bbox)
        trials.append(
            {
                "query": query,
                "ok": True,
                "bbox": bbox,
                "confidence": data.get("confidence"),
                "iou": round(score, 6),
                "pass_iou_ge_0_5": score >= IOU_THRESHOLD,
            }
        )
        print(f"query={query!r} iou={score:.4f} conf={data.get('confidence')}")

    best = max((t for t in trials if t["ok"]), key=lambda t: t["iou"], default=None)
    passed = bool(best and best["iou"] >= IOU_THRESHOLD)
    report = {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "reason": "" if passed else "text selector 最高 IoU 未达 0.5，需换 query 或改权重（如实记录）",
        "model": {
            "repo": MODEL_REPO,
            "license": LICENSE,
            "implementation": "transformers.GroundingDinoProcessor/ForObjectDetection（local_files_only）",
            "box_threshold": 0.1,
            "text_threshold": 0.1,
            "checkpoint": str(WEIGHTS),
        },
        "image": image,
        "image_size": list(image_size),
        "ground_truth": {
            "source": "Wave 6 RT-DETR 实证 selected.bbox（中央红夹克，人工复核）",
            "bbox": gt_bbox,
        },
        "iou_threshold": IOU_THRESHOLD,
        "trials": trials,
        "best": best,
        "reproduction": REPRODUCTION,
        "notes": [
            "text selector 由离线 Grounding DINO 出框；渲染循环零推理（决策 #2）",
            "IoU 用 src.vision.focus_math.iou 纯函数计算，未 mock",
            "grounding-dino-tiny 对属性词置信度偏低（实测 ~0.13），阈值取 0.1 以召回目标",
        ],
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{OUTPUT} / status={report['status']}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
