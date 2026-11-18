"""Timeline → 合成模板上下文 + 素材清单（渲染层，不改时间线语义）。

跨层传递全部使用 Pydantic v2 model（CODING_STANDARDS 第 1 节）；
只有交给 Jinja2 的那一刻才调用 model_dump()，字典不跨层传递。

时间单位：输入 Timeline 全部为秒；帧换算只在渲染层（本模块与模板）进行。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from pydantic import Field

from src.contracts import BeatImpact, Clip, StrictModel, Timeline
from src.vision.focus_math import (
    bbox_to_focus,
    build_parallax_plan,
    build_tour_path,
    cover_rect,
    derive_anchor_bbox,
)


class MaskCtx(StrictModel):
    type: str
    duration: float


class SpringCtx(StrictModel):
    stiffness: float
    damping: float
    mass: float


class TextAnimCtx(StrictModel):
    type: str
    stiffness: float
    damping: float
    delay: float
    y_offset: float


class KenBurnsCtx(StrictModel):
    enable: bool
    start_scale: float
    end_scale: float
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    ease: str
    pivot_x: str
    pivot_y: str


class FocusRenderCtx(StrictModel):
    """从主体框换算出的渲染参数（像素 x/y + scale），供模板稳态段直接使用。"""

    enable: bool
    scale: float
    x: float
    y: float
    hold_ratio: float
    # 20.5 多主体漫游：两点 [{scale,x,y},{scale,x,y}]；None 表示单点聚焦。
    tour_keyframes: list[dict[str, float]] | None = None
    # 20.5 分层视差：前景/背景各自参数 + 近似 mask + 告警；None 表示不启用。
    parallax: dict[str, object] | None = None


class CinematicRenderCtx(StrictModel):
    """19 章最小特效子集的模板参数；未配置时全部为 none，模板保持旧行为。"""

    mask_mode: str = "none"            # 仅 irregular 真正启用；其余仍走 6.1 蒙版
    mask_duration: float = 0.32
    mask_seed: int = 0                 # 固定整数 seed，来自 clip.index，禁未 seed 随机
    reveal_type: str = "none"          # 仅 flash-in 真正启用
    reveal_blur_px: float = 14.0
    reveal_brightness: float = 2.4
    reveal_overshoot: float = 1.12
    reveal_duration: float = 0.22
    grade_preset: str = "none"         # teal-orange / film / noir / none
    grade_vignette: float = 0.0
    # 19.3 碎裂炸现：启用时生成 cols×rows 碎片 div 错峰就位（用 mask_seed 保证确定性）。
    shatter_enable: bool = False
    shatter_cols: int = 6
    shatter_rows: int = 6
    shatter_stagger: float = 0.018
    backdrop_blur: float = 24.0        # 碎片间隙露出的模糊背景
    backdrop_brightness: float = 0.5
    # 18.3 crossfade：相邻 clip 在退场尾部与入场头部重叠溶解。
    # 默认关闭；仅在任一 clip 配置 cinematic_fx 且镜头数 > 1 时由 build_project_bundle 打开。
    # T_cross 硬约束 0.08–0.15s，默认取中值 0.12；模板再按 clip 时长二次 clamp。
    crossfade_enable: bool = False
    crossfade_duration: float = 0.12


class WebGLRenderCtx(StrictModel):
    """GPU 自适应 WebGL 增强层的模板参数（决策 #8 + 2026-11-18 GPU 自适应评估）。

    mode（权威）：
      - off：整层 no-op（最保守，旧 enabled=false 等价）；
      - on：强制尝试启用，失败才降级（旧 enabled=true 等价）；
      - auto（默认）：模板探测 WebGL context + 编译成功即启用，否则降级 no-op。
    enabled 为向后兼容字段：由 mode 派生（mode=off → false，其余 → true）。
    只启用真粒子 + 体积光（纯 t 闭式解、零 DOM 采样）；sharpen 保留但默认 0。
    """

    mode: Literal["auto", "on", "off"] = "auto"
    enabled: bool = True          # 由 mode 派生：off→False，auto/on→True
    seed: int = 20261117
    particle_count: int = 220
    godray_intensity: float = 0.18
    sharpen_strength: float = 0.0


@lru_cache(maxsize=1)
def _fx_webgl_config() -> dict[str, object]:
    """生成期读取 config.yaml 的 fx.webgl（非渲染期 I/O，不违反确定性禁 fetch）。

    同一进程内同 config → 同输出；任何异常退回 {} → WebGLRenderCtx 默认关闭，
    不阻断出片。config.yaml 不存在的部署环境也能安全降级。
    """
    cfg_path = Path(__file__).resolve().parents[2] / "config.yaml"
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        webgl = data.get("fx", {}).get("webgl", {}) or {}
        return webgl if isinstance(webgl, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


class ClipCtx(StrictModel):
    index: int
    start: float
    end: float
    duration: float
    image: str
    text: str
    mask: MaskCtx
    image_spring: SpringCtx
    text_anim: TextAnimCtx
    kenburns: KenBurnsCtx
    focus_render: FocusRenderCtx
    cinematic: CinematicRenderCtx
    cinematic_mode: str
    focus_mode: str
    text_anim_type: str
    pivot_x: str
    pivot_y: str


class CompositionContext(StrictModel):
    """喂给 composition.html.j2 的完整上下文。"""

    composition_id: str
    width: int
    height: int
    fps: int
    total_duration: float
    font_family: str
    impact_profile: str
    text_bottom: int
    text_size: int
    timeline_json: str
    clips: list[ClipCtx]
    bg_image: str | None = None
    beat_impacts: list[BeatImpact] = []
    # 18.3 crossfade（全局开关，模板据此决定退场/入场是否重叠溶解）。
    crossfade_enable: bool = False
    crossfade_duration: float = 0.12
    # 19.5 光效与颗粒（独立 overlay 层，不抢 img 的 transform/filter）。
    sweep_enable: bool = False
    sweep_duration: float = 0.55
    grain_image: str | None = None      # 相对路径，如 assets/grain.png；None 时不渲染颗粒层
    # 阶段 C WebGL 增强层（默认关闭）：模板参数也写入 timeline_json.webgl。
    webgl: WebGLRenderCtx = Field(default_factory=WebGLRenderCtx)


class AssetEntry(StrictModel):
    """一个镜头对应的素材清单条目。"""

    clip_index: int
    relative_path: str


class AssetManifest(StrictModel):
    entries: list[AssetEntry]


class ProjectBundle(StrictModel):
    """生成工程的完整产物描述：模板上下文 + 素材清单。"""

    context: CompositionContext
    manifest: AssetManifest


def _focus_render(clip: Clip, image_size: tuple[int, int] | None, stage: tuple[int, int]) -> FocusRenderCtx:
    """把主体框（含 anchor_region 派生）换算成 GSAP 渲染参数（方案 20.4/20.6）。

    无 focus / 无 bbox / 无图片尺寸时返回 enable=False，模板回退 Ken Burns。
    """
    if clip.focus is None or clip.focus.bbox is None or image_size is None:
        return FocusRenderCtx(enable=False, scale=1.0, x=0.0, y=0.0, hold_ratio=0.0)
    try:
        derived = derive_anchor_bbox(
            list(clip.focus.bbox), clip.focus.anchor_region, image_size
        )
        if not derived.ok:
            return FocusRenderCtx(enable=False, scale=1.0, x=0.0, y=0.0, hold_ratio=0.0)
        box = derived.data["bbox"]
        cov = cover_rect(image_size[0], image_size[1], stage[0], stage[1])
        result = bbox_to_focus(
            box, cov, stage[0], stage[1], clip.focus.fill, clip.focus.fit
        )

        # 20.5 多主体漫游：tour_to 存在且 bbox 合法时生成两点关键帧；
        # 失败（如 face 许可降级）则忽略 tour，退回单点聚焦，不阻断。
        tour_keyframes: list[dict[str, float]] | None = None
        if clip.focus.tour_to is not None:
            try:
                plan = build_tour_path(
                    box,
                    list(clip.focus.tour_to.bbox),
                    cov,
                    stage,
                    clip.focus.fill,
                    clip.focus.fit,
                    anchor_region=clip.focus.anchor_region,
                    image_size=image_size,
                )
                tour_keyframes = [k.model_dump() for k in plan.keyframes]
            except (ValueError, TypeError):
                tour_keyframes = None

        # 20.5 分层视差：SAM 缺失时 build_parallax_plan 用近似矩形 mask（如实标注）。
        parallax: dict[str, object] | None = None
        if clip.focus.parallax.enable:
            try:
                pplan = build_parallax_plan(
                    result,
                    clip.focus.parallax.bg_ratio,
                    clip.focus.parallax.haze,
                    bbox=box,
                    cov=cov,
                    stage=stage,
                )
                parallax = pplan.model_dump(mode="json")
            except (ValueError, TypeError):
                parallax = None

        return FocusRenderCtx(
            enable=True,
            scale=result.scale,
            x=result.x,
            y=result.y,
            hold_ratio=clip.focus.hold_ratio,
            tour_keyframes=tour_keyframes,
            parallax=parallax,
        )
    except (ValueError, TypeError):
        return FocusRenderCtx(enable=False, scale=1.0, x=0.0, y=0.0, hold_ratio=0.0)


def _cinematic_render(clip: Clip, *, crossfade_enable: bool = False) -> CinematicRenderCtx:
    """把 clip.cinematic_fx 转成模板参数；未配置时返回全 none（向后兼容）。"""
    fx = clip.cinematic_fx
    if fx is None:
        return CinematicRenderCtx(crossfade_enable=crossfade_enable)
    source = fx.mask.seed_from
    try:
        seed = clip.index if source == "clip.index" else int(str(source))
    except (ValueError, TypeError):
        seed = clip.index
    return CinematicRenderCtx(
        mask_mode=fx.mask.mode,
        mask_duration=fx.mask.duration,
        mask_seed=seed,
        reveal_type=fx.reveal.type,
        reveal_blur_px=fx.reveal.blur_px,
        reveal_brightness=fx.reveal.brightness,
        reveal_overshoot=fx.reveal.overshoot,
        reveal_duration=fx.reveal.duration,
        grade_preset=fx.grade.preset,
        grade_vignette=fx.grade.vignette,
        shatter_enable=fx.shatter.enable,
        shatter_cols=fx.shatter.cols,
        shatter_rows=fx.shatter.rows,
        shatter_stagger=fx.shatter.stagger,
        backdrop_blur=fx.backdrop.blur_px,
        backdrop_brightness=fx.backdrop.brightness,
        crossfade_enable=crossfade_enable,
    )


def _clip_context(
    clip: Clip,
    relative_image: str,
    *,
    image_size: tuple[int, int] | None = None,
    stage: tuple[int, int] = (1080, 1920),
    crossfade_enable: bool = False,
) -> ClipCtx:
    mode = clip.cinematic_fx.mask.mode if clip.cinematic_fx is not None else "none"
    focus_mode = "none"
    focus_render = _focus_render(clip, image_size, stage)
    if clip.focus is not None:
        focus_mode = clip.focus.mode
    pivot_x, pivot_y = clip.kenburns.pivot_x, clip.kenburns.pivot_y
    if focus_render.enable:
        # focus 推近以画面中心为锚点，bboxToFocus 的 x/y 也基于该前提。
        pivot_x, pivot_y = "50%", "50%"
    return ClipCtx(
        index=clip.index,
        start=clip.start,
        end=clip.end,
        duration=clip.duration,
        image=relative_image,
        text=clip.text,
        mask=MaskCtx(type=clip.mask.type, duration=clip.mask.duration),
        image_spring=SpringCtx(
            stiffness=clip.image_spring.stiffness,
            damping=clip.image_spring.damping,
            mass=clip.image_spring.mass,
        ),
        text_anim=TextAnimCtx(
            type=clip.text_anim.type,
            stiffness=clip.text_anim.stiffness,
            damping=clip.text_anim.damping,
            delay=clip.text_anim.delay,
            y_offset=clip.text_anim.y_offset,
        ),
        kenburns=KenBurnsCtx(
            enable=clip.kenburns.enable,
            start_scale=clip.kenburns.start_scale,
            end_scale=clip.kenburns.end_scale,
            start_x=clip.kenburns.start_x,
            start_y=clip.kenburns.start_y,
            end_x=clip.kenburns.end_x,
            end_y=clip.kenburns.end_y,
            ease=clip.kenburns.ease,
            pivot_x=pivot_x,
            pivot_y=pivot_y,
        ),
        focus_render=focus_render,
        cinematic=_cinematic_render(clip, crossfade_enable=crossfade_enable),
        cinematic_mode=mode,
        focus_mode=focus_mode,
        text_anim_type=clip.text_anim.type,
        pivot_x=pivot_x,
        pivot_y=pivot_y,
    )


def _merge_impacts(timeline: Timeline) -> list[BeatImpact]:
    """Clip 级冲击与 Timeline 级 fallback 合并；按帧排序、同帧同类型去重。

    Clip 级为空时回退 Timeline 全局默认（契约注释中的双级约定）。
    """
    merged: dict[tuple[int, str], BeatImpact] = {}
    for hit in timeline.beat_impacts:
        merged[(hit.frame, hit.type)] = hit
    for clip in timeline.clips:
        for hit in clip.beat_impacts:
            merged.setdefault((hit.frame, hit.type), hit)
    return [merged[key] for key in sorted(merged)]


def _json_for_script(value: object) -> str:
    """生成可安全内联到 <script> 的 JSON：禁止出现字面量 '</'，避免提前闭合标签。"""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c")


def build_project_bundle(
    timeline: Timeline,
    *,
    composition_id: str,
    width: int,
    height: int,
    font_family: str,
    image_paths: dict[int, str],
    image_sizes: dict[int, tuple[int, int]] | None = None,
) -> ProjectBundle:
    """把 Timeline 与素材相对路径组装为模板上下文 + 素材清单。"""
    missing = [c.index for c in timeline.clips if c.index not in image_paths]
    if missing:
        raise ValueError(f"缺少镜头素材相对路径: {missing}")

    sizes = image_sizes or {}
    stage = (width, height)
    # 18.3 crossfade 仅在「配置了 cinematic_fx 且多镜」时开启；无配置时保持旧行为。
    crossfade_enable = any(c.cinematic_fx is not None for c in timeline.clips) and len(timeline.clips) > 1
    # 19.5 sweep 由任一 clip.light.sweep 触发；grain 取第一个非空 grain_png 相对路径。
    sweep_enable = any(
        c.cinematic_fx is not None and c.cinematic_fx.light.sweep
        for c in timeline.clips
    )
    grain_image = next(
        (
            c.cinematic_fx.grade.grain_png
            for c in timeline.clips
            if c.cinematic_fx is not None and c.cinematic_fx.grade.grain_png
        ),
        None,
    )
    clips = [
        _clip_context(
            clip, image_paths[clip.index], image_size=sizes.get(clip.index), stage=stage,
            crossfade_enable=crossfade_enable,
        )
        for clip in timeline.clips
    ]
    impacts = _merge_impacts(timeline)
    wc = _fx_webgl_config()
    # mode 兼容归一化：新 config 用 mode；旧 config 只有 enabled → on/off；都没有 → auto。
    raw_mode = wc.get("mode")
    raw_enabled = wc.get("enabled")
    if raw_mode in ("auto", "on", "off"):
        webgl_mode: Literal["auto", "on", "off"] = raw_mode  # type: ignore[assignment]
    elif isinstance(raw_enabled, bool):
        webgl_mode = "on" if raw_enabled else "off"
    else:
        webgl_mode = "auto"   # 默认：GPU 自适应探测（有显卡充分利用，无显卡安全降级）
    webgl_ctx = WebGLRenderCtx(
        mode=webgl_mode,
        enabled=(webgl_mode != "off"),
        seed=int(wc.get("seed", 20261117)),
        particle_count=int(wc.get("particle_count", 220)),
        godray_intensity=float(wc.get("godray_intensity", 0.18)),
        sharpen_strength=float(wc.get("sharpen_strength", 0.0)),
    )
    timeline_json = _json_for_script(
        {
            "fps": timeline.fps,
            "total_duration": timeline.total_duration,
            "impact_profile": timeline.impact_profile,
            "clips": [c.model_dump(mode="json") for c in clips],
            "beat_impacts": [hit.model_dump(mode="json") for hit in impacts],
            "crossfade": {"enable": crossfade_enable, "duration": 0.12},
            "fx_overlay": {
                "sweep": sweep_enable,
                "sweep_duration": 0.55,
                "grain_image": grain_image or "",
            },
            "webgl": webgl_ctx.model_dump(mode="json"),
        }
    )
    context = CompositionContext(
        composition_id=composition_id,
        width=width,
        height=height,
        fps=timeline.fps,
        total_duration=timeline.total_duration,
        font_family=font_family,
        impact_profile=timeline.impact_profile,
        text_bottom=max(24, int(height * 0.16)),
        text_size=max(18, int(height * 0.045)),
        timeline_json=timeline_json,
        clips=clips,
        bg_image=clips[0].image if clips else None,
        beat_impacts=impacts,
        crossfade_enable=crossfade_enable,
        crossfade_duration=0.12,
        sweep_enable=sweep_enable,
        sweep_duration=0.55,
        grain_image=grain_image,
        webgl=webgl_ctx,
    )
    manifest = AssetManifest(
        entries=[
            AssetEntry(clip_index=c.index, relative_path=c.image)
            for c in clips
        ]
    )
    return ProjectBundle(context=context, manifest=manifest)


def render_composition_html(context: CompositionContext, template_path: str | Path) -> str:
    """用 Jinja2 渲染模板。autoescape 开启；timeline_json 已用 |safe 且做过转义。"""
    path = Path(template_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"模板不存在: {path}")
    env = Environment(
        loader=FileSystemLoader(str(path.parent)),
        undefined=StrictUndefined,
        autoescape=select_autoescape(enabled_extensions=("html", "j2"), default=True),
        keep_trailing_newline=True,
    )
    template = env.get_template(path.name)
    return template.render(**context.model_dump(mode="json"))
