"""方案 20.4 / 20.7 验收：纯几何换算 + 视觉层降级契约。

纯函数测试不依赖任何 ML 库；真实模型测试在依赖/权重缺失时明确 skip（不 mock）。
fixture 图片尺寸来自真实素材 fixtures/images/group_10people.jpg（ffprobe: 1536x1024）。
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from src.contracts import ErrorCode, Selector
from src.vision.depth_estimate import estimate_depth
from src.vision.focus_math import (
    bbox_to_focus,
    cover_rect,
    derive_anchor_bbox,
    project_bbox,
)
from src.vision.models import Subject, SubjectAnalysis
from src.vision.subject_analyzer import detect_subjects, lock_subject, segment_subject

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / "fixtures" / "images" / "group_10people.jpg"
IMAGE_W, IMAGE_H = 1536, 1024  # ffprobe -select_streams v:0 -show_entries stream=width,height
STAGE_W, STAGE_H = 1080, 1920
COV = cover_rect(IMAGE_W, IMAGE_H, STAGE_W, STAGE_H)

# 中央红夹克、左边缘、右边缘、极小目标（触发 scale 上限）
BBOXES = [
    (0.43, 0.20, 0.57, 0.70),
    (0.02, 0.15, 0.15, 0.85),
    (0.85, 0.25, 0.98, 0.60),
    (0.49, 0.49, 0.51, 0.51),
]


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


DETECT_READY = (
    all(_has(m) for m in ("torch", "transformers", "PIL"))
    and bool(os.environ.get("P2V_RTDETR_CHECKPOINT"))
)
SEGMENT_READY = (
    all(_has(m) for m in ("torch", "numpy", "cv2", "PIL", "segment_anything"))
    and bool(os.environ.get("P2V_SAM_CHECKPOINT"))
    and bool(os.environ.get("P2V_SAM_MODEL_TYPE"))
)


# ---------------------------------------------------------------------------
# 纯几何：coverRect / bboxToFocus
# ---------------------------------------------------------------------------
def test_fixture_dimensions_match_constant() -> None:
    assert IMAGE.is_file(), f"缺少群像测试素材：{IMAGE}"
    if not _has("PIL"):
        pytest.skip("Pillow 未安装，无法读取图片实际尺寸（不使用 mock 伪造尺寸）")
    from PIL import Image

    with Image.open(IMAGE) as im:
        assert im.size == (IMAGE_W, IMAGE_H)


def test_cover_rect_matches_spec() -> None:
    # s = max(1080/1536, 1920/1024) = 1.875
    assert COV.w == pytest.approx(2880.0)
    assert COV.h == pytest.approx(1920.0)
    assert COV.x == pytest.approx(-900.0)
    assert COV.y == pytest.approx(0.0)


@pytest.mark.parametrize("bbox", BBOXES)
def test_contain_keeps_subject_inside_stage(bbox: tuple[float, float, float, float]) -> None:
    focus = bbox_to_focus(bbox, COV, STAGE_W, STAGE_H, fit="contain")
    x1, y1, x2, y2 = project_bbox(bbox, COV, STAGE_W, STAGE_H, focus)
    assert x1 >= -1e-6, f"主体越出左边界：{bbox} -> {x1}"
    assert y1 >= -1e-6, f"contain 切到头部（上边界）：{bbox} -> {y1}"
    assert x2 <= STAGE_W + 1e-6, f"主体越出右边界：{bbox} -> {x2}"
    assert y2 <= STAGE_H + 1e-6, f"主体越出下边界：{bbox} -> {y2}"


@pytest.mark.parametrize("bbox", BBOXES[:3])
def test_pushed_subject_center_error_within_5pct(
    bbox: tuple[float, float, float, float],
) -> None:
    focus = bbox_to_focus(bbox, COV, STAGE_W, STAGE_H)
    x1, y1, x2, y2 = project_bbox(bbox, COV, STAGE_W, STAGE_H, focus)
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    assert abs(cx - STAGE_W / 2.0) <= 0.05 * STAGE_W
    assert abs(cy - STAGE_H / 2.0) <= 0.05 * STAGE_H


def test_scale_cap_and_warning() -> None:
    focus = bbox_to_focus(BBOXES[3], COV, STAGE_W, STAGE_H, fit="contain")
    assert focus.scale <= 4.0
    assert focus.scale == pytest.approx(4.0)
    assert any("4.0" in w for w in focus.warnings)


def test_cover_overflow_reduces_fill_and_warns() -> None:
    narrow = (0.49, 0.20, 0.51, 0.70)
    focus = bbox_to_focus(narrow, COV, STAGE_W, STAGE_H, fill=0.62, fit="cover")
    assert focus.fill < 0.62
    assert focus.scale <= 4.0
    assert any("fill" in w for w in focus.warnings)
    x1, y1, x2, y2 = project_bbox(narrow, COV, STAGE_W, STAGE_H, focus)
    assert -1e-6 <= x1 < x2 <= STAGE_W + 1e-6
    assert -1e-6 <= y1 < y2 <= STAGE_H + 1e-6


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bbox": (0.9, 0.1, 0.2, 0.8)},  # x1 > x2
        {"bbox": (-0.1, 0.1, 0.2, 0.8)},  # 越出 [0,1]
        {"bbox": (0.1, 0.1, 0.2, 0.8), "fill": 1.5},
        {"bbox": (0.1, 0.1, 0.2, 0.8), "fit": "bogus"},
        {"bbox": (0.1, 0.1, 0.2, 0.8), "cov": (0, 0, 1, 1)},
    ],
)
def test_invalid_inputs_raise_value_error(kwargs: dict) -> None:
    payload = {
        "bbox": kwargs.pop("bbox"),
        "cov": kwargs.pop("cov", COV),
        "stage_w": STAGE_W,
        "stage_h": STAGE_H,
        **kwargs,
    }
    with pytest.raises((ValueError, TypeError)):
        bbox_to_focus(**payload)


# ---------------------------------------------------------------------------
# lock_subject: index selector 为纯计算，不需要 ML
# ---------------------------------------------------------------------------
def _analysis() -> SubjectAnalysis:
    return SubjectAnalysis(
        image=str(IMAGE),
        image_size=(IMAGE_W, IMAGE_H),
        subjects=[
            Subject(index=0, label="person", bbox=list(BBOXES[0]), confidence=0.97),
            Subject(index=1, label="person", bbox=list(BBOXES[1]), confidence=0.95),
        ],
        count=2,
    )


def test_lock_by_index_is_deterministic_and_no_ml() -> None:
    selector = Selector(type="index", value=1)
    first = lock_subject(_analysis(), selector, fill=0.62, fit="contain")
    second = lock_subject(_analysis(), selector, fill=0.62, fit="contain")
    assert first.ok and second.ok
    assert first.data == second.data
    assert first.data["subject_index"] == 1
    assert first.data["anchor_region"] == "upper_body"
    assert first.data["full_bbox"] == list(BBOXES[1])
    derived = derive_anchor_bbox(list(BBOXES[1]), "upper_body", (IMAGE_W, IMAGE_H))
    assert derived.data is not None
    assert first.data["bbox"] == derived.data["bbox"]
    assert first.data["focus"]["transformOrigin"] == "50% 50%"
    assert first.data["focus"]["scale"] <= 4.0


def test_lock_by_index_out_of_range_raises() -> None:
    with pytest.raises(ValueError, match="越界"):
        lock_subject(_analysis(), Selector(type="index", value=99))


# ---------------------------------------------------------------------------
# anchor_region：用 Wave 6 真实检测框验证「上半身派生 → 真推近」
# ---------------------------------------------------------------------------
# Wave 6 报告里 index=1 的红夹克人物全身框（真实 RT-DETR 检出）
REAL_RED_JACKET_BBOX = (
    0.4095589319864909,
    0.14617928862571716,
    0.6052964925765991,
    0.9984405040740967,
)


@pytest.mark.parametrize(
    "anchor_region, expect_same_as_full",
    [("full", True), ("upper_body", False)],
)
def test_derive_anchor_bbox_full_and_upper_body(
    anchor_region: str, expect_same_as_full: bool
) -> None:
    resp = derive_anchor_bbox(
        list(REAL_RED_JACKET_BBOX), anchor_region, (IMAGE_W, IMAGE_H)  # type: ignore[arg-type]
    )
    assert resp.ok and resp.data is not None
    derived = resp.data["bbox"]
    if expect_same_as_full:
        assert derived == list(REAL_RED_JACKET_BBOX)
    else:
        x1, y1, x2, y2 = REAL_RED_JACKET_BBOX
        assert derived[:3] == [x1, y1, x2]
        assert derived[3] == pytest.approx(y1 + 0.45 * (y2 - y1))
        assert derived[3] < y2  # 从顶部裁掉下半身


def test_upper_body_derived_bbox_gives_real_push_in() -> None:
    derived = derive_anchor_bbox(
        list(REAL_RED_JACKET_BBOX), "upper_body", (IMAGE_W, IMAGE_H)
    )
    assert derived.ok and derived.data is not None
    bbox = derived.data["bbox"]
    focus = bbox_to_focus(bbox, COV, STAGE_W, STAGE_H, fill=0.62, fit="contain")
    assert focus.scale > 1.0, "上半身派生后必须得到 scale>1 的真推近"
    assert focus.scale <= 4.0
    x1, y1, x2, y2 = project_bbox(bbox, COV, STAGE_W, STAGE_H, focus)
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    assert abs(cx - STAGE_W / 2.0) <= 0.05 * STAGE_W
    assert abs(cy - STAGE_H / 2.0) <= 0.05 * STAGE_H
    assert x1 >= -1e-6 and y1 >= -1e-6
    assert x2 <= STAGE_W + 1e-6 and y2 <= STAGE_H + 1e-6


def test_lock_subject_default_anchor_uses_upper_body_on_real_bbox() -> None:
    analysis = SubjectAnalysis(
        image=str(IMAGE),
        image_size=(IMAGE_W, IMAGE_H),
        subjects=[
            Subject(
                index=0,
                label="person",
                bbox=list(REAL_RED_JACKET_BBOX),
                confidence=0.958,
            )
        ],
        count=1,
    )
    resp = lock_subject(analysis, Selector(type="index", value=0))
    assert resp.ok and resp.data is not None
    assert resp.data["anchor_region"] == "upper_body"
    assert resp.data["full_bbox"] == list(REAL_RED_JACKET_BBOX)
    assert resp.data["bbox"] != list(REAL_RED_JACKET_BBOX)
    assert resp.data["focus"]["scale"] > 1.0


def test_anchor_region_face_degrades_with_binary_not_found() -> None:
    resp = derive_anchor_bbox(list(REAL_RED_JACKET_BBOX), "face", (IMAGE_W, IMAGE_H))  # type: ignore[arg-type]
    assert resp.ok is False
    assert resp.error is not None
    assert resp.error.code == ErrorCode.BINARY_NOT_FOUND
    assert "许可" in str(resp.error.detail) or "人脸" in resp.error.message


@pytest.mark.parametrize("bad", ["torso", "", None])
def test_anchor_region_invalid_raises(bad: object) -> None:
    with pytest.raises(ValueError):
        derive_anchor_bbox(
            list(REAL_RED_JACKET_BBOX), bad, (IMAGE_W, IMAGE_H)  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        lock_subject(_analysis(), Selector(type="index", value=0), anchor_region=bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 降级契约：缺依赖返回 BINARY_NOT_FOUND，不 mock
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    DETECT_READY,
    reason="本机已配置 RT-DETR 权重；未配置环境才能验证 detect_subjects 的降级返回",
)
def test_detect_subjects_degrades_without_model() -> None:
    resp = detect_subjects(IMAGE)
    assert resp.ok is False
    assert resp.error is not None
    assert resp.error.code == ErrorCode.BINARY_NOT_FOUND


@pytest.mark.skipif(
    not DETECT_READY,
    reason="需安装 torch/transformers/Pillow 且设置 P2V_RTDETR_CHECKPOINT 才能跑真实 RT-DETR 检测",
)
def test_detect_subjects_writes_subjects_json(tmp_path: Path) -> None:
    resp = detect_subjects(IMAGE, output_dir=tmp_path)
    assert resp.ok is True
    assert (tmp_path / "subjects.json").is_file()


@pytest.mark.skipif(
    SEGMENT_READY,
    reason="本机已配置 SAM 权重与依赖；未配置环境才能验证 segment_subject 的降级返回",
)
def test_segment_subject_degrades_without_sam() -> None:
    resp = segment_subject(IMAGE, BBOXES[0])
    assert resp.ok is False
    assert resp.error is not None
    assert resp.error.code == ErrorCode.BINARY_NOT_FOUND


@pytest.mark.skipif(
    not SEGMENT_READY,
    reason="需安装 segment-anything/cv2/torch 且设置 P2V_SAM_CHECKPOINT、P2V_SAM_MODEL_TYPE 才能跑真实分割",
)
def test_segment_subject_writes_transparent_png(tmp_path: Path) -> None:
    resp = segment_subject(IMAGE, BBOXES[0], output_path=tmp_path / "subject.png")
    assert resp.ok is True
    assert (tmp_path / "subject.png").is_file()


def test_depth_disabled_by_default() -> None:
    resp = estimate_depth(IMAGE)
    assert resp.ok is True
    assert resp.data is not None
    assert resp.data["enabled"] is False
    assert resp.data["depth_path"] is None


def test_depth_degrades_without_cached_weights() -> None:
    # 用一个绝不存在的本地模型名，确保 local_files_only 不会联网。
    resp = estimate_depth(IMAGE, enable=True, model_name="__no_such_local_depth_model__")
    assert resp.ok is False
    assert resp.error is not None
    assert resp.error.code == ErrorCode.BINARY_NOT_FOUND
