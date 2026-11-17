"""视觉层新增 Pydantic model 的合法 + 非法校验（CODING_STANDARDS 第 5 节）。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.contracts import Focus
from src.vision.focus_math import CoverRect, FocusResult
from src.vision.models import DepthResult, FocusLock, Subject, SubjectAnalysis

VALID_SUBJECT = {"index": 0, "label": "person", "bbox": [0.1, 0.2, 0.3, 0.8], "confidence": 0.97}
VALID_ANALYSIS = {
    "image": "assets/group.jpg",
    "image_size": [1536, 1024],
    "subjects": [VALID_SUBJECT],
    "count": 1,
}

# (model, 合法 payload, 非法 payload)
CASES = [
    (
        Subject,
        VALID_SUBJECT,
        {**VALID_SUBJECT, "bbox": [0.3, 0.2, 0.1, 0.8]},
    ),
    (
        SubjectAnalysis,
        VALID_ANALYSIS,
        {**VALID_ANALYSIS, "count": 2},
    ),
    (
        CoverRect,
        {"w": 2880.0, "h": 1920.0, "x": -900.0, "y": 0.0},
        {"w": 0.0, "h": 1920.0, "x": 0.0, "y": 0.0},
    ),
    (
        FocusResult,
        {
            "scale": 1.5,
            "x": 10.0,
            "y": -5.0,
            "subject_center": (540.0, 960.0),
            "fill": 0.62,
        },
        {
            "scale": 1.5,
            "x": 10.0,
            "y": -5.0,
            "subject_center": (540.0, 960.0),
            "fill": 1.5,
        },
    ),
    (
        DepthResult,
        {"image": "assets/group.jpg", "enabled": False, "model": "depth-anything-v2"},
        {"image": "assets/group.jpg", "enabled": "not-a-bool", "model": "depth-anything-v2"},
    ),
    (
        FocusLock,
        {
            "bbox": [0.1, 0.2, 0.3, 0.8],
            "subject_index": 0,
            "label": "person",
            "confidence": 0.97,
            "fill": 0.62,
            "fit": "contain",
            "scale": 2.1,
            "x": 10.0,
            "y": -5.0,
            "subject_center": [540.0, 960.0],
        },
        {
            "bbox": [0.1, 0.2, 0.3, 0.8],
            "subject_index": 0,
            "label": "person",
            "confidence": 1.97,
            "fill": 0.62,
            "fit": "contain",
            "scale": 2.1,
            "x": 10.0,
            "y": -5.0,
            "subject_center": [540.0, 960.0],
        },
    ),
]


@pytest.mark.parametrize("model,payload,_", CASES)
def test_valid_payload(model, payload, _) -> None:
    instance = model.model_validate(payload)
    assert isinstance(instance, model)


@pytest.mark.parametrize("model,_,bad_payload", CASES)
def test_invalid_payload(model, _, bad_payload) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(bad_payload)


def test_focus_anchor_region_default_is_upper_body() -> None:
    focus = Focus(bbox=[0.1, 0.2, 0.3, 0.8])
    assert focus.anchor_region == "upper_body"


@pytest.mark.parametrize("valid", ["full", "upper_body", "face"])
def test_focus_anchor_region_valid_values(valid: str) -> None:
    assert Focus(anchor_region=valid).anchor_region == valid  # type: ignore[arg-type]


def test_focus_anchor_region_invalid_value_raises() -> None:
    with pytest.raises(ValidationError):
        Focus(anchor_region="torso")  # type: ignore[arg-type]
