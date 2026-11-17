"""时间线编排（方案 5.3 / 18.2 / 19.7）。

不变量（CODING_STANDARDS 第 2 节，由代码断言）：
- 镜头数 = len(grouped_beats) - 1；首镜 start 强制归零（18.3）。
- 每镜 T ≥ 2×T_in（T_in = mask.duration），违反则与下一镜合并并写 warnings。
- 单镜最短 0.2s，合并策略同上。
时间统一为秒，帧换算只在渲染层。
"""
from __future__ import annotations

from typing import Literal, TypeVar
from uuid import uuid4

from src.contracts import (
    BeatImpact,
    CinematicFx,
    Clip,
    ErrorCode,
    Focus,
    ImageSpring,
    KenBurns,
    MaskConfig,
    StrictModel,
    TextAnim,
    Timeline,
)

T = TypeVar("T")

_IMPACT_DURATION = {"flash": 0.12, "punch": 0.16, "shake": 0.10, "chromatic": 0.08}
_IMPACT_GAIN = {"subtle": 0.5, "normal": 1.0, "aggressive": 1.6, "off": 0.0}
_MIN_CLIP_DURATION = 0.2


class TimelineBuildError(RuntimeError):
    """时间线构建失败，携带统一错误码。"""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class EffectSpec(StrictModel):
    """单镜特效入参（方案 5.3 effects 元素），替代裸 dict 跨函数边界。"""

    mask: MaskConfig | None = None
    image_spring: ImageSpring | None = None
    text_anim: TextAnim | None = None
    kenburns: KenBurns | None = None
    focus: Focus | None = None
    beat_impacts: list[BeatImpact] | None = None
    cinematic_fx: CinematicFx | None = None


def _pick(items: list[T] | None, index: int, loop: bool) -> T | None:
    if not items:
        return None
    return items[index % len(items)] if loop else items[min(index, len(items) - 1)]


def _build_beat_impacts(
    raw_beats: list[float],
    grouped_beats: list[float],
    fps: int,
    profile: Literal["off", "subtle", "normal", "aggressive"],
) -> list[BeatImpact]:
    """按方案 18.2 生成节拍冲击：强拍 flash/punch，弱拍 shake 强度减半。"""
    if profile == "off" or not raw_beats:
        return []
    gain = _IMPACT_GAIN[profile]
    strong_set = {round(b, 3) for b in grouped_beats}
    impacts: list[BeatImpact] = []
    for i, raw in enumerate(raw_beats):
        t = float(raw)
        strong = round(t, 3) in strong_set
        hit_type = ("flash" if i % 2 == 0 else "punch") if strong else "shake"
        base = 0.55 if strong else 0.275
        impacts.append(
            BeatImpact(
                t=t,
                frame=round(t * fps),
                type=hit_type,
                intensity=min(base * gain, 0.9),
                duration=_IMPACT_DURATION[hit_type],
                strong=strong,
            )
        )
    return impacts


def build_timeline(
    grouped_beats: list[float],
    images: list[str],
    texts: list[str],
    effects: list[EffectSpec] | None,
    loop_assets: bool,
    fps: int,
    *,
    audio_duration: float | None = None,
    task_id: str | None = None,
    raw_beats: list[float] | None = None,
    impact_profile: Literal["off", "subtle", "normal", "aggressive"] = "normal",
    style_preset: Literal["minimal", "standard", "cinematic", "aggressive"] = "standard",
) -> Timeline:
    """把节拍切点编排为 Timeline（方案 5.3 + 18.2 + 19.7）。

    audio_duration 缺省时用最后两个切点的间隔外推，保证末镜时长为正；
    task_id 缺省时按 CODING_STANDARDS 4 生成 uuid4。
    """
    beats = [float(b) for b in grouped_beats]
    if len(beats) < 2:
        raise TimelineBuildError(ErrorCode.EMPTY_TIMELINE, "节拍切点少于 2 个，无法编排")
    if any(b < 0 for b in beats):
        raise ValueError("grouped_beats 不得为负")
    if any(later <= earlier for earlier, later in zip(beats, beats[1:])):
        raise ValueError("grouped_beats 必须严格递增")
    if fps < 1:
        raise ValueError("fps 必须 ≥ 1")
    if not images:
        raise ValueError("images 至少要有 1 张")

    if audio_duration is None:
        audio_duration = beats[-1] + (beats[-1] - beats[-2])
    total_duration = float(audio_duration)
    if total_duration <= beats[-2]:
        raise ValueError("audio_duration 必须大于倒数第二个切点，否则末镜时长非正")

    # 首镜 start 归零（18.3），最后一个切点由精确 audio_duration 覆盖（5.3）
    bounds = [0.0, *beats[1:-1], total_duration]
    spans = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]

    mask_duration = 0.32
    for effect in effects or []:
        if effect.mask is not None:
            mask_duration = effect.mask.duration
            break
    min_required = max(_MIN_CLIP_DURATION, 2 * mask_duration)

    warnings: list[str] = []
    merged: list[tuple[float, float]] = []
    i = 0
    while i < len(spans):
        start, end = spans[i]
        if end - start < min_required and i + 1 < len(spans):
            merged.append((start, spans[i + 1][1]))
            warnings.append(
                f"镜头 {i} 时长 {end - start:.3f}s < 2×T_in({min_required:.3f}s)，已与下一镜合并"
            )
            i += 2
        else:
            if end - start < min_required:
                warnings.append(
                    f"镜头 {i} 时长 {end - start:.3f}s < 2×T_in({min_required:.3f}s)，末镜无法合并"
                )
            merged.append((start, end))
            i += 1

    if not loop_assets:
        if len(images) < len(merged):
            warnings.append("images 不足且 loop_assets=false，已复用最后一项")
        if texts and len(texts) < len(merged):
            warnings.append("texts 不足且 loop_assets=false，已复用最后一项")

    effect_list = effects or []
    image_list = [str(item) for item in images]
    text_list = [str(item) for item in texts]
    global_impacts = _build_beat_impacts(raw_beats or [], beats, fps, impact_profile)

    clips: list[Clip] = []
    for index, (start, end) in enumerate(merged):
        effect = _pick(effect_list, index, loop_assets) or EffectSpec()
        image = _pick(image_list, index, loop_assets)
        text = _pick(text_list, index, loop_assets)
        clip_impacts = (
            effect.beat_impacts
            if effect.beat_impacts is not None
            else [hit for hit in global_impacts if start <= hit.t < end]
        )
        clips.append(
            Clip(
                index=index,
                start=start,
                end=end,
                duration=end - start,
                image=image or "",
                text=text or "",
                mask=effect.mask or MaskConfig(),
                image_spring=effect.image_spring or ImageSpring(),
                text_anim=effect.text_anim or TextAnim(),
                kenburns=effect.kenburns or KenBurns(),
                focus=effect.focus,
                cinematic_fx=effect.cinematic_fx,
                beat_impacts=clip_impacts,
            )
        )

    return Timeline(
        task_id=task_id or str(uuid4()),
        fps=fps,
        total_duration=total_duration,
        clips=clips,
        impact_profile=impact_profile,
        beat_impacts=global_impacts,
        style_preset=style_preset,
        warnings=warnings,
    )
