"""编排层测试：build_timeline 不变量与 adjust_timeline 三模式。

全部使用假 beats 数组，不依赖 librosa / ffprobe / 渲染后端。
"""
from __future__ import annotations

import pytest

from src.contracts import CinematicFx, ErrorCode, Focus
from src.timeline.adjuster import AdjustmentSummary, TimelineAdjustError, adjust_timeline
from src.timeline.builder import EffectSpec, TimelineBuildError, build_timeline

BEATS = [0.21, 1.05, 1.89, 2.73]
AUDIO_DURATION = 4.12


def _build(**kwargs):
    return build_timeline(
        BEATS,
        ["a.jpg", "b.jpg", "c.jpg"],
        ["第一句", "第二句", "第三句"],
        None,
        True,
        30,
        audio_duration=AUDIO_DURATION,
        task_id="test-task",
        **kwargs,
    )


def test_build_clip_count_and_first_start_zero() -> None:
    """镜头数=len(beats)-1，首镜 start 强制归零（18.3），末镜 end 取 audio_duration。"""
    timeline = _build()
    assert len(timeline.clips) == len(BEATS) - 1
    assert timeline.clips[0].start == 0.0
    assert timeline.clips[-1].end == AUDIO_DURATION
    assert timeline.total_duration == AUDIO_DURATION
    assert timeline.task_id == "test-task"
    assert timeline.fps == 30


def test_build_duration_equals_end_minus_start() -> None:
    """每镜 duration = end - start 且不小于最短时长。"""
    for clip in _build().clips:
        assert abs(clip.duration - (clip.end - clip.start)) < 1e-6
        assert clip.duration >= 2 * clip.mask.duration


def test_build_merges_short_clips_with_warning() -> None:
    """T < 2×T_in 的镜头必须与下一镜合并并写 warnings。"""
    timeline = build_timeline(
        [0.0, 0.1, 1.0, 2.0],
        ["a.jpg", "b.jpg", "c.jpg"],
        [],
        None,
        True,
        30,
        audio_duration=3.0,
    )
    assert len(timeline.clips) == 2
    assert any("合并" in warning for warning in timeline.warnings)


def test_build_loop_assets() -> None:
    """素材不足时按 loop_assets=true 循环取模。"""
    timeline = build_timeline(
        BEATS, ["a.jpg"], [], None, True, 30, audio_duration=AUDIO_DURATION
    )
    assert [clip.image for clip in timeline.clips] == ["a.jpg"] * 3
    assert [clip.text for clip in timeline.clips] == [""] * 3


def test_build_no_loop_clamps_and_warns() -> None:
    """loop_assets=false 时复用最后一项并写 warning。"""
    timeline = build_timeline(
        BEATS, ["a.jpg", "b.jpg"], [], None, False, 30, audio_duration=AUDIO_DURATION
    )
    assert [clip.image for clip in timeline.clips] == ["a.jpg", "b.jpg", "b.jpg"]
    assert any("复用" in warning for warning in timeline.warnings)


def test_build_beat_impacts_frame_aligned() -> None:
    """冲击层严格帧对齐，且按时间窗分配到 Clip。"""
    raw = [0.21, 0.63, 1.05, 1.47, 1.89, 2.31, 2.73]
    timeline = _build(raw_beats=raw, impact_profile="normal")
    assert len(timeline.beat_impacts) == len(raw)
    for hit in timeline.beat_impacts:
        assert hit.frame == round(hit.t * timeline.fps)
        assert 0 <= hit.intensity <= 0.9
    assert any(clip.beat_impacts for clip in timeline.clips)


def test_build_impact_profile_off() -> None:
    """impact_profile=off 时冲击层为空。"""
    timeline = _build(raw_beats=[0.21, 0.63], impact_profile="off")
    assert timeline.beat_impacts == []


def test_build_focus_and_cinematic_fx_passthrough() -> None:
    """focus / cinematic_fx 按 EffectSpec 透传到 Clip。"""
    effect = EffectSpec(
        focus=Focus(bbox=[0.31, 0.18, 0.44, 0.61]),
        cinematic_fx=CinematicFx(),
    )
    timeline = build_timeline(
        BEATS, ["a.jpg"], [], [effect], True, 30, audio_duration=AUDIO_DURATION
    )
    assert timeline.clips[0].focus is not None
    assert timeline.clips[0].focus.bbox == [0.31, 0.18, 0.44, 0.61]
    assert timeline.clips[0].cinematic_fx is not None


def test_build_empty_beats() -> None:
    """节拍少于 2 报 EMPTY_TIMELINE。"""
    with pytest.raises(TimelineBuildError) as exc:
        build_timeline([0.5], ["a.jpg"], [], None, True, 30)
    assert exc.value.code == ErrorCode.EMPTY_TIMELINE


def test_adjust_offset() -> None:
    """offset 平移所有镜头并写入变更摘要。"""
    timeline, summary = adjust_timeline(_build(), mode="offset", offset=0.1)
    assert isinstance(summary, AdjustmentSummary)
    assert summary.changes
    assert timeline.clips[0].start == pytest.approx(0.1)
    assert timeline.total_duration == pytest.approx(AUDIO_DURATION + 0.1)


def test_adjust_set_times() -> None:
    """set_times 用新切点重建所有镜头边界。"""
    timeline, summary = adjust_timeline(
        _build(), mode="set_times", times=[0.0, 1.2, 2.5, AUDIO_DURATION]
    )
    assert [clip.start for clip in timeline.clips] == [0.0, 1.2, 2.5]
    assert [clip.end for clip in timeline.clips] == [1.2, 2.5, AUDIO_DURATION]
    assert summary.mode == "set_times"


def test_adjust_merge() -> None:
    """merge 合并相邻镜头，镜头数减 1。"""
    timeline, summary = adjust_timeline(_build(), mode="merge_split", action="merge", index=0)
    assert len(timeline.clips) == 2
    assert timeline.clips[0].start == 0.0
    assert timeline.clips[0].end == BEATS[2]
    assert summary.changes[0].field == "structure"


def test_adjust_split() -> None:
    """split 拆分镜头，镜头数加 1（选时长足够的末镜）。"""
    timeline, summary = adjust_timeline(_build(), mode="merge_split", action="split", index=2)
    assert len(timeline.clips) == 4
    assert summary.changes[0].field == "structure"
    assert all(i == clip.index for i, clip in enumerate(timeline.clips))


def test_adjust_split_too_short_rejected() -> None:
    """拆分后不足 2×T_in 必须拒绝，保持强制不变量。"""
    with pytest.raises(TimelineAdjustError):
        adjust_timeline(_build(), mode="merge_split", action="split", index=0)


def test_adjust_modes_exclusive() -> None:
    """三种模式互斥，混传参数必须报错。"""
    timeline = _build()
    with pytest.raises(ValueError):
        adjust_timeline(timeline, mode="offset", offset=0.1, times=[0.0, 1.0, 2.0, 3.0])
    with pytest.raises(ValueError):
        adjust_timeline(timeline, mode="set_times", times=[0.0, 1.0, 2.0, 3.0], action="merge", index=0)
