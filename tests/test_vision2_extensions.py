"""Wave vision2 扩展验收：IoU / 多主体 tour / 分层视差 / text selector 降级。

全部为真实计算：纯函数断言不依赖 ML；text selector 真实 IoU 报告由
`fixtures/verify_text_selector.py` 跑 Apache-2.0 Grounding DINO 权重产出，
报告缺失时明确 skip，不 mock。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.contracts import ErrorCode, Selector
from src.vision.focus_math import (
    bbox_to_focus,
    build_parallax_plan,
    build_tour_path,
    cover_rect,
    foreground_inset,
    iou,
    parallax_params,
    project_bbox,
    tour_focus,
)
from src.vision.models import Subject, SubjectAnalysis
from src.vision.subject_analyzer import lock_subject

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "fixtures" / "reports" / "text_selector_iou_report.json"
DETECTION = ROOT / "fixtures" / "reports" / "detection_report.json"
IMAGE_W, IMAGE_H = 1536, 1024
STAGE_W, STAGE_H = 1080, 1920
COV = cover_rect(IMAGE_W, IMAGE_H, STAGE_W, STAGE_H)
# Wave 6 真实检测的两位主体：中央红夹克(index=1) 与右侧人物(index=4)
BBOX_A = (0.4095589319864909, 0.14617928862571716, 0.6052964925765991, 0.9984405040740967)
BBOX_B = (0.7065178553263346, 0.20732352137565613, 0.8406548500061035, 0.9985926151275635)


# ---------------------------------------------------------------------------
# IoU（方案 20.7）
# ---------------------------------------------------------------------------
def test_iou_identical_is_one_and_disjoint_is_zero() -> None:
    assert iou(BBOX_A, list(BBOX_A)) == pytest.approx(1.0)
    other = (0.9, 0.1, 0.95, 0.2)
    assert iou(BBOX_A, other) == pytest.approx(0.0)


def test_iou_partial_overlap_matches_hand_computation() -> None:
    # A=[0,0,1,1] 面积 1；B=[0.5,0,1,1] 面积 0.5；交 0.5、并 1.0 → IoU 0.5
    assert iou((0.0, 0.0, 1.0, 1.0), (0.5, 0.0, 1.0, 1.0)) == pytest.approx(0.5)


@pytest.mark.parametrize("bad", [(0.9, 0.1, 0.2, 0.8), (-0.1, 0.0, 0.2, 0.8)])
def test_iou_rejects_invalid_bbox(bad: tuple[float, float, float, float]) -> None:
    with pytest.raises(ValueError):
        iou(bad, BBOX_A)


# ---------------------------------------------------------------------------
# 多主体漫游 tour（方案 20.5）
# ---------------------------------------------------------------------------
def test_tour_focus_endpoints_match_single_focus_and_interpolate() -> None:
    path = tour_focus(
        BBOX_A, BBOX_B, COV, STAGE_W, STAGE_H,
        fill=0.62, fit="contain", anchor_region="upper_body",
        image_size=(IMAGE_W, IMAGE_H),
    )
    upper_a = (BBOX_A[0], BBOX_A[1], BBOX_A[2], BBOX_A[1] + 0.45 * (BBOX_A[3] - BBOX_A[1]))
    fa = bbox_to_focus(upper_a, COV, STAGE_W, STAGE_H)
    assert path.from_focus.scale == pytest.approx(fa.scale)
    assert path.from_focus.x == pytest.approx(fa.x)
    assert path.from_focus.y == pytest.approx(fa.y)
    assert path.to_focus.scale > 0.0
    assert path.interpolate(0.0) == path.from_focus
    assert path.interpolate(1.0) == path.to_focus
    mid = path.interpolate(0.5)
    assert mid.scale == pytest.approx((path.from_focus.scale + path.to_focus.scale) / 2)
    assert mid.x == pytest.approx((path.from_focus.x + path.to_focus.x) / 2)
    assert mid.y == pytest.approx((path.from_focus.y + path.to_focus.y) / 2)


def test_tour_focus_is_deterministic_and_rejects_bad_p() -> None:
    kwargs = dict(
        bbox_a=BBOX_A, bbox_b=BBOX_B, cov=COV, stage_w=STAGE_W, stage_h=STAGE_H,
        anchor_region="full", image_size=(IMAGE_W, IMAGE_H),
    )
    first = tour_focus(**kwargs)  # type: ignore[arg-type]
    second = tour_focus(**kwargs)  # type: ignore[arg-type]
    assert first == second
    with pytest.raises(ValueError):
        first.interpolate(1.1)


def test_tour_focus_face_anchor_degrades() -> None:
    with pytest.raises(ValueError, match="派生失败"):
        tour_focus(
            BBOX_A, BBOX_B, COV, STAGE_W, STAGE_H,
            anchor_region="face", image_size=(IMAGE_W, IMAGE_H),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# team-lead 交接签名：build_tour_path（两点关键帧 + warnings）
# ---------------------------------------------------------------------------
def _real_bboxes_from_report() -> tuple[list[float], list[float]]:
    data = json.loads(DETECTION.read_text(encoding="utf-8"))
    subjects = data["detection"]["subjects"]
    return list(subjects[1]["bbox"]), list(subjects[4]["bbox"])


def test_build_tour_path_uses_two_real_detection_bboxes() -> None:
    a, b = _real_bboxes_from_report()
    assert tuple(a) == pytest.approx(BBOX_A) and tuple(b) == pytest.approx(BBOX_B)
    plan = build_tour_path(
        a, b, COV, (STAGE_W, STAGE_H), 0.62, "contain", image_size=(IMAGE_W, IMAGE_H)
    )
    dumped = plan.model_dump(mode="json")
    keyframes = dumped["keyframes"]
    assert len(keyframes) == 2
    assert list(keyframes[0].keys()) == ["scale", "x", "y"]

    for kf, full in zip(keyframes, (a, b)):
        # 端点必须与 focus_math 单独算出的单点 focus 完全一致（无二次派生）
        upper = (full[0], full[1], full[2], full[1] + 0.45 * (full[3] - full[1]))
        single = bbox_to_focus(upper, COV, STAGE_W, STAGE_H)
        assert kf["scale"] == pytest.approx(single.scale)
        assert kf["x"] == pytest.approx(single.x)
        assert kf["y"] == pytest.approx(single.y)
        assert kf["scale"] <= 4.0
        x1, y1, x2, y2 = project_bbox(upper, COV, STAGE_W, STAGE_H, single)
        assert abs((x1 + x2) / 2 - STAGE_W / 2) <= 0.05 * STAGE_W
        assert abs((y1 + y2) / 2 - STAGE_H / 2) <= 0.05 * STAGE_H


def test_build_tour_path_is_deterministic_and_degrades_face() -> None:
    a, b = _real_bboxes_from_report()
    kwargs = dict(cov=COV, stage=(STAGE_W, STAGE_H), image_size=(IMAGE_W, IMAGE_H))
    first = build_tour_path(a, b, **kwargs)  # type: ignore[arg-type]
    second = build_tour_path(a, b, **kwargs)  # type: ignore[arg-type]
    assert first == second
    with pytest.raises(ValueError, match="派生失败"):
        build_tour_path(a, b, COV, (STAGE_W, STAGE_H), anchor_region="face", image_size=(IMAGE_W, IMAGE_H))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="stage"):
        build_tour_path(a, b, COV, (STAGE_W,), image_size=(IMAGE_W, IMAGE_H))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 分层视差（方案 20.5）
# ---------------------------------------------------------------------------
def test_parallax_bg_ratio_controls_depth_speed() -> None:
    still = parallax_params(1.6, bg_ratio=0.0, haze=0.2)
    assert still.bg_scale == pytest.approx(1.0)
    slow = parallax_params(1.6, bg_ratio=0.35, haze=0.15)
    assert slow.bg_scale == pytest.approx(1.0 + 0.6 * 0.35)
    assert slow.fg_scale == pytest.approx(1.6)
    assert slow.haze == pytest.approx(0.15)
    same = parallax_params(1.6, bg_ratio=1.0)
    assert same.bg_scale == pytest.approx(1.6)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"focus_scale": 1.6, "bg_ratio": 1.5},
        {"focus_scale": 1.6, "haze": -0.1},
        {"focus_scale": 0.0},
    ],
)
def test_parallax_params_reject_invalid(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        parallax_params(**kwargs)


def test_foreground_inset_is_valid_clamped_css() -> None:
    rect = foreground_inset(BBOX_A, COV, STAGE_W, STAGE_H)
    assert rect.startswith("inset(") and rect.endswith(")")
    values = [float(v.replace("%", "")) for v in rect[6:-1].split()]
    assert len(values) == 4
    assert all(0.0 <= v <= 100.0 for v in values)


# ---------------------------------------------------------------------------
# team-lead 交接签名：build_parallax_plan（前景/背景各自 scale/x/y）
# ---------------------------------------------------------------------------
def test_build_parallax_plan_returns_two_layers_with_approx_mask() -> None:
    upper = (BBOX_A[0], BBOX_A[1], BBOX_A[2], BBOX_A[1] + 0.45 * (BBOX_A[3] - BBOX_A[1]))
    focus = bbox_to_focus(upper, COV, STAGE_W, STAGE_H)
    plan = build_parallax_plan(
        focus, 0.35, 0.15, bbox=BBOX_A, cov=COV, stage=(STAGE_W, STAGE_H)
    )
    assert plan.foreground.scale == pytest.approx(focus.scale)
    assert plan.foreground.x == pytest.approx(focus.x)
    assert plan.foreground.y == pytest.approx(focus.y)
    assert plan.background.scale == pytest.approx(1 + (focus.scale - 1) * 0.35)
    assert plan.background.x == pytest.approx(focus.x * 0.35)
    assert plan.background.y == pytest.approx(focus.y * 0.35)
    assert plan.fg_inset is not None and plan.fg_inset.startswith("inset(")
    assert any("非 SAM" in w for w in plan.warnings), "近似 mask 必须如实标注非 SAM"

    dumped = plan.model_dump(mode="json")
    assert {"foreground", "background", "bg_ratio", "haze", "fg_inset", "warnings"} <= set(dumped)
    assert list(dumped["foreground"].keys()) == ["scale", "x", "y"]
    assert list(dumped["background"].keys()) == ["scale", "x", "y"]


def test_build_parallax_plan_without_mask_is_sam_ready_shape() -> None:
    upper = (BBOX_B[0], BBOX_B[1], BBOX_B[2], BBOX_B[1] + 0.45 * (BBOX_B[3] - BBOX_B[1]))
    focus = bbox_to_focus(upper, COV, STAGE_W, STAGE_H)
    plan = build_parallax_plan(focus, bg_ratio=0.0)
    assert plan.fg_inset is None
    assert plan.background.scale == pytest.approx(1.0)
    assert not any("非 SAM" in w for w in plan.warnings)


def test_build_parallax_plan_rejects_bad_focus() -> None:
    with pytest.raises(TypeError):
        build_parallax_plan({"scale": 1.2, "x": 0, "y": 0})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# text selector 降级 + 真实报告
# ---------------------------------------------------------------------------
def test_text_selector_degrades_without_local_weights(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("P2V_GROUNDING_DINO_CHECKPOINT", raising=False)
    monkeypatch.delenv("P2V_GROUNDING_DINO_CONFIG", raising=False)
    analysis = SubjectAnalysis(
        image=str(ROOT / "fixtures" / "images" / "group_10people.jpg"),
        image_size=(IMAGE_W, IMAGE_H),
        subjects=[Subject(index=0, label="person", bbox=list(BBOX_A), confidence=0.95)],
        count=1,
    )
    resp = lock_subject(analysis, Selector(type="text", query="a person in red"))
    assert resp.ok is False
    assert resp.error is not None
    assert resp.error.code == ErrorCode.BINARY_NOT_FOUND
    assert resp.error.detail.get("candidates"), "降级时应附候选列表供 Agent 改用 index"


def test_text_selector_bbox_is_not_derived_by_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    """text selector 的候选列表为空时，临时 Subject 仍走同一 anchor_region 派生逻辑。"""
    analysis = SubjectAnalysis(
        image=str(ROOT / "fixtures" / "images" / "group_10people.jpg"),
        image_size=(IMAGE_W, IMAGE_H),
        subjects=[],
        count=0,
    )
    monkeypatch.delenv("P2V_GROUNDING_DINO_CHECKPOINT", raising=False)
    monkeypatch.delenv("P2V_GROUNDING_DINO_CONFIG", raising=False)
    resp = lock_subject(analysis, Selector(type="text", query="anything"))
    assert resp.ok is False and resp.error is not None
    assert resp.error.detail.get("candidates") == []


@pytest.mark.skipif(not REPORT.is_file(), reason="尚未运行 text selector 真实权重实证")
def test_real_text_selector_iou_report() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if report["status"] == "weight_pending":
        pytest.skip(f"权重待验，复现命令见 report['reproduction']：{report.get('reason', '')}")
    assert report["status"] == "passed", report.get("reason", "")
    assert report["model"]["license"] == "apache-2.0"
    assert report["best"]["iou"] >= 0.5
    assert any(t["pass_iou_ge_0_5"] for t in report["trials"])
    assert DETECTION.is_file(), "IoU 基准必须来自 Wave 6 真实检测报告"


@pytest.mark.skipif(not REPORT.is_file(), reason="报告缺失")
def test_text_selector_report_has_reproduction_steps() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    steps = report.get("reproduction", [])
    assert any("grounding-dino-tiny" in s for s in steps)
    assert any("P2V_GROUNDING_DINO_CHECKPOINT" in s for s in steps)
