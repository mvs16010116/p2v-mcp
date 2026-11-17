"""节拍人工微调（方案 5.4 / 9.2）。

三种模式互斥：offset / set_times / merge_split。返回更新后的 Timeline 与
结构化变更摘要（哪些 clip 的 start/end 被改写）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from src.contracts import StrictModel, Timeline


class TimelineAdjustError(RuntimeError):
    """微调违反不变量时抛出（如 split 后单镜不足 2×T_in）。"""


class ClipChange(StrictModel):
    """单条变更记录。"""

    index: int = Field(ge=0, description="变更镜头序号")
    field: str = Field(min_length=1, description="字段名，如 start / end / structure")
    old: float = Field(description="变更前数值")
    new: float = Field(description="变更后数值")


class AdjustmentSummary(StrictModel):
    """adjust_timeline 的变更摘要。"""

    mode: Literal["offset", "set_times", "merge_split"] = Field(description="微调模式")
    changes: list[ClipChange] = Field(default_factory=list, description="具体变更列表")
    warnings: list[str] = Field(default_factory=list, description="降级/边界处理告警")


def adjust_timeline(
    timeline: Timeline,
    *,
    mode: Literal["offset", "set_times", "merge_split"],
    offset: float | None = None,
    times: list[float] | None = None,
    action: Literal["merge", "split"] | None = None,
    index: int | None = None,
) -> tuple[Timeline, AdjustmentSummary]:
    """按三种互斥模式微调时间线，返回 (新 timeline, 变更摘要)。"""
    if mode == "offset":
        if offset is None or times is not None or action is not None or index is not None:
            raise ValueError("mode=offset 时只允许提供 offset")
    elif mode == "set_times":
        if times is None or offset is not None or action is not None or index is not None:
            raise ValueError("mode=set_times 时只允许提供 times")
    elif mode == "merge_split":
        if action is None or index is None or offset is not None or times is not None:
            raise ValueError("mode=merge_split 时只允许提供 action 与 index")
        if action not in ("merge", "split"):
            raise ValueError("action 只能是 merge 或 split")
    else:
        raise ValueError("mode 只能是 offset / set_times / merge_split")

    clips = [clip.model_copy(deep=True) for clip in timeline.clips]
    changes: list[ClipChange] = []
    warnings: list[str] = []

    if mode == "offset":
        shift = float(offset)
        for clip in clips:
            old_start, old_end = clip.start, clip.end
            new_start = max(0.0, old_start + shift)
            new_end = max(new_start + 1e-3, old_end + shift)
            if new_start != old_start:
                changes.append(ClipChange(index=clip.index, field="start", old=old_start, new=new_start))
            if new_end != old_end:
                changes.append(ClipChange(index=clip.index, field="end", old=old_end, new=new_end))
            clip.start, clip.end, clip.duration = new_start, new_end, new_end - new_start
        if shift < 0:
            warnings.append("offset 为负：部分镜头 start 已钳制到 0")
        total_duration = max(timeline.total_duration + shift, max(c.end for c in clips))

    elif mode == "set_times":
        new_times = [float(value) for value in times]
        if len(new_times) != len(clips) + 1:
            raise ValueError("times 长度必须为镜头数 + 1")
        if any(t < 0 for t in new_times):
            raise ValueError("times 不得为负")
        if any(later <= earlier for earlier, later in zip(new_times, new_times[1:])):
            raise ValueError("times 必须严格递增")
        for i, clip in enumerate(clips):
            old_start, old_end = clip.start, clip.end
            clip.start, clip.end = new_times[i], new_times[i + 1]
            clip.duration = clip.end - clip.start
            changes.append(ClipChange(index=i, field="start", old=old_start, new=clip.start))
            changes.append(ClipChange(index=i, field="end", old=old_end, new=clip.end))
        total_duration = new_times[-1]

    else:
        if index < 0 or index >= len(clips):
            raise ValueError("index 越界")
        if action == "merge":
            if index + 1 >= len(clips):
                raise ValueError("末镜没有下一镜可合并")
            current, following = clips[index], clips[index + 1]
            merged = current.model_copy(
                update={
                    "end": following.end,
                    "duration": following.end - current.start,
                    "beat_impacts": current.beat_impacts + following.beat_impacts,
                }
            )
            clips = clips[:index] + [merged] + clips[index + 2 :]
            changes.append(ClipChange(index=index, field="structure", old=2, new=1))
        else:
            current = clips[index]
            mid = (current.start + current.end) / 2
            min_required = 2 * current.mask.duration
            if mid - current.start < min_required:
                raise TimelineAdjustError("拆分后单镜时长不足 2×T_in，拒绝 split")
            first = current.model_copy(update={"end": mid, "duration": mid - current.start})
            second = current.model_copy(update={"start": mid, "duration": current.end - mid})
            clips = clips[:index] + [first, second] + clips[index + 1 :]
            changes.append(ClipChange(index=index, field="structure", old=1, new=2))
        total_duration = timeline.total_duration

    for new_index, clip in enumerate(clips):
        clip.index = new_index

    updated = timeline.model_copy(update={"clips": clips, "total_duration": total_duration}, deep=True)
    return updated, AdjustmentSummary(mode=mode, changes=changes, warnings=warnings)
