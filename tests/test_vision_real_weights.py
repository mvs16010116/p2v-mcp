"""Wave 6 真实权重实证的验收门槛（读取 verify_detection.py 产出的报告）。

报告不存在时明确 skip 并写明复现命令；报告存在时按方案 20.7 断言，禁止 mock。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "fixtures" / "reports" / "detection_report.json"
REPRO = "python fixtures/verify_detection.py（先按 fixtures/README.md 配置 P2V_RTDETR_CHECKPOINT）"


@pytest.mark.skipif(not REPORT.is_file(), reason=f"尚未运行真实权重实证脚本：{REPRO}")
def test_real_detection_report_passes() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    if report["status"] == "skipped":
        pytest.skip(
            f"真实检测未运行（如实记录）：{report.get('reason', '')}；复现步骤见 report['reproduction']"
        )

    assert report["status"] == "passed", report.get("reason", "")
    assert report["model"]["license"] == "apache-2.0"
    # 方案 20.7：群像中应检出接近 10 个可信 person 框
    assert report["detection"]["person_count_confidence_ge_0_8"] >= 9
    assert report["selected"]["label"] == "person"
    assert Path(report["selected"]["crop_path"]).is_file(), "目标框人工复核截图缺失"

    acceptance = report["acceptance"]
    assert acceptance["center_ok"], "推近后主体中心偏差 > 5%"
    assert acceptance["contained"], "contain 模式下主体越界/切头"
    assert acceptance["scale_ok"], "scale 超过 4.0 上限"

    variant = report["push_in_variant"]
    assert variant["ok"] and variant["scale_gt_1"], "真实框派生上半身未实现 scale>1 的推近"


@pytest.mark.skipif(not REPORT.is_file(), reason=f"尚未运行真实权重实证脚本：{REPRO}")
def test_report_carries_reproduction_steps() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    steps = report.get("reproduction", [])
    assert any("snapshot_download" in s for s in steps)
    assert any("P2V_RTDETR_CHECKPOINT" in s for s in steps)
    assert any("verify_detection.py" in s for s in steps)
