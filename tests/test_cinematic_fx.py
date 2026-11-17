"""19 章电影级特效子集的静态契约测试。

验证：模板确实实现 irregular 裁切 / flash-in / grade，以及第二梯队的
crossfade 交叉溶解 / 扫光 sweep / 预渲染颗粒 grain；遵守决策 #7 四同约束
与决策 #6 确定性硬契约；project_generator 把参数写进 ClipCtx 与 timeline_json；
未配置 cinematic_fx 时保持旧行为。
"""
from __future__ import annotations

import json
from pathlib import Path

from src.builder.project_generator import build_project_bundle, render_composition_html
from src.contracts import Clip, CinematicFx, CinematicMask, Grade, Light, Reveal, Timeline

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src" / "builder" / "template" / "composition.html.j2"


def _template_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _timeline(fx: CinematicFx | None) -> Timeline:
    clip = Clip(
        index=0, start=0.0, end=2.0, duration=2.0,
        image="assets/a.jpg", text="特效实证", cinematic_fx=fx,
    )
    return Timeline(task_id="fx-test", total_duration=2.0, clips=[clip])


def _bundle(fx: CinematicFx | None):
    return build_project_bundle(
        _timeline(fx), composition_id="fx-test", width=1080, height=1920,
        font_family="sans-serif", image_paths={0: "assets/a.jpg"},
    )


def _timeline_two_clips(fx: CinematicFx) -> Timeline:
    """crossfade 需多镜；相邻两镜 start/end 首尾相接。"""
    c0 = Clip(index=0, start=0.0, end=2.0, duration=2.0,
              image="assets/a.jpg", text="A", cinematic_fx=fx)
    c1 = Clip(index=1, start=2.0, end=4.0, duration=2.0,
              image="assets/b.jpg", text="B", cinematic_fx=fx)
    return Timeline(task_id="fx-xfade", total_duration=4.0, clips=[c0, c1])


def _bundle_two_clips(fx: CinematicFx):
    return build_project_bundle(
        _timeline_two_clips(fx), composition_id="fx-xfade", width=1080, height=1920,
        font_family="sans-serif", image_paths={0: "assets/a.jpg", 1: "assets/b.jpg"},
    )


def test_template_has_irregular_polygon_with_four_same_constraints() -> None:
    """irregularPolygon 固定 12 顶点 / 全百分比 / 显式两端（决策 #7）。"""
    text = _template_text()
    assert "const IRREGULAR_POINTS = 12;" in text
    assert "function irregularPolygon(p, seed)" in text
    assert "for (let i = 0; i < IRREGULAR_POINTS; i++)" in text
    assert "'%'" in text and "pts.push(x + ' ' + y);" in text  # 同单位全百分比
    assert "irregularPolygon(0, seed)" in text  # 显式起点
    assert "irregularPolygon(1, seed)" in text  # 显式终点
    assert "const pEnter = { p: 0 };" in text and "const pExit = { p: 1 };" in text


def test_template_irregular_uses_proxy_onupdate_writeback() -> None:
    """clipPath 由 proxy tween 的 onUpdate 写回，避免与 transform 抢属性。"""
    text = _template_text()
    assert "onUpdate: () => { img.style.clipPath = irregularPolygon(pEnter.p, seed); }" in text
    assert "onUpdate: () => { img.style.clipPath = irregularPolygon(pExit.p, seed); }" in text


def test_template_has_flash_in_filter_animation() -> None:
    """flash-in 用 blur + brightness 的 filter 动画，power4.out，与 transform 解耦。"""
    text = _template_text()
    assert "fx.reveal_type === 'flash-in'" in text
    assert "brightness(" in text and "blur(" in text
    assert "blur(0px) brightness(1)" in text
    assert "ease: 'power4.out'" in text
    assert "const enterScale = flashIn" in text  # scale 增量并入入场


def test_template_has_all_grade_presets_and_stage_filter() -> None:
    """grade 四档预设齐全，且通过 stage filter 生效（不覆盖 img filter）。"""
    text = _template_text()
    assert "const GRADE_FILTERS" in text
    for preset in ("none", "film", "teal-orange", "noir"):
        assert f"'{preset}'" in text or f"{preset}:" in text
    assert "MASTER.set(stage, { filter: GRADE_FILTERS" in text


def test_template_determinism_contract_intact() -> None:
    """仍走 totalTime(t,false) + 帧量化；禁 Date.now / 未 seed 随机。"""
    text = _template_text()
    assert "MASTER.totalTime(quantize(t), false)" in text
    # 只禁止实际调用形式；注释里说明「禁用 Date.now」是允许的
    assert "Date.now(" not in text
    assert "Math.random(" not in text
    assert ".repeat(" not in text
    assert "yoyo:" not in text


def test_project_generator_writes_cinematic_fields() -> None:
    """cinematic_fx 参数进入 ClipCtx 与 timeline_json。"""
    fx = CinematicFx(
        mask=CinematicMask(mode="irregular", duration=0.38),
        reveal=Reveal(type="flash-in"),
        grade=Grade(preset="teal-orange"),
    )
    bundle = _bundle(fx)
    ctx = bundle.context.clips[0]
    assert ctx.cinematic.mask_mode == "irregular"
    assert ctx.cinematic.mask_seed == 0
    assert ctx.cinematic.reveal_type == "flash-in"
    assert ctx.cinematic.grade_preset == "teal-orange"

    timeline_json = json.loads(bundle.context.timeline_json)
    clip_json = timeline_json["clips"][0]
    assert clip_json["cinematic"]["mask_mode"] == "irregular"
    assert clip_json["cinematic"]["grade_preset"] == "teal-orange"
    assert clip_json["cinematic"]["reveal_type"] == "flash-in"


def test_project_generator_backward_compatible_without_cinematic_fx() -> None:
    """未配置 cinematic_fx 时全 none，模板保持旧行为，不破坏 focus 出片。"""
    bundle = _bundle(None)
    cinematic = bundle.context.clips[0].cinematic
    assert cinematic.mask_mode == "none"
    assert cinematic.reveal_type == "none"
    assert cinematic.grade_preset == "none"


def test_rendered_html_contains_effect_flags() -> None:
    """Jinja2 渲染出的 HTML 携带三特效参数与 JS 实现。"""
    fx = CinematicFx(
        mask=CinematicMask(mode="irregular", duration=0.38),
        reveal=Reveal(type="flash-in"),
        grade=Grade(preset="teal-orange"),
    )
    html = render_composition_html(_bundle(fx).context, TEMPLATE)
    assert '"mask_mode":"irregular"' in html
    assert '"reveal_type":"flash-in"' in html
    assert '"grade_preset":"teal-orange"' in html
    assert "function irregularPolygon" in html
    assert "const GRADE_FILTERS" in html


# ---------------------------------------------------------------------------
# 阶段 B 第二梯队特效：crossfade / 扫光 / 预渲染颗粒
# ---------------------------------------------------------------------------


def test_template_crossfade_overlap_window() -> None:
    """18.3：T_cross 落在 0.08–0.15s，旧镜退场尾部与新镜入场头部在 MASTER 上重叠。"""
    text = _template_text()
    assert "const XFADE = TIMELINE.crossfade" in text
    # T_cross clamp 到 [0.08, 0.15]，并受 clip 时长约束
    assert "Math.max(0.08, Math.min(Number(XFADE.duration) || 0.12, 0.15, T * 0.25))" in text
    # 新镜提前 Tc 入场（moveStart = start - Tc），旧镜延后到 end 才隐藏
    assert "const moveStart = (xfOn && hasPrev) ? clip.start - Tc : clip.start;" in text
    assert "MASTER.set(root, { display: 'block' }, moveStart)" in text
    assert ".set(root, { display: 'none'  }, clip.end);" in text
    # opacity + zIndex 控制，不靠 display 硬切
    assert "root.style.zIndex = String(clip.index + 1);" in text
    assert "{ opacity: 1, duration: Tc, ease: 'none', immediateRender: false }, moveStart);" in text
    assert "{ opacity: 0, duration: Tc, ease: 'none', immediateRender: false }, clip.end - Tc);" in text
    # 单条 MASTER、帧量化、无 repeat/yoyo（决策 #3/#6）
    assert "MASTER.totalTime(quantize(t), false)" in text
    assert "yoyo:" not in text and ".repeat(" not in text


def test_template_sweep_overlay_layer() -> None:
    """19.5：扫光为独立 overlay DOM，105deg 渐变条 + overlay 混合 + xPercent -120→120。"""
    text = _template_text()
    assert "id=\"sweep-layer\"" in text and "id=\"sweep-bar\"" in text
    assert "linear-gradient(105deg, transparent 40%, rgba(255,255,255,.45) 50%, transparent 60%)" in text
    assert "mix-blend-mode:overlay" in text
    assert "{ xPercent: -120 }" in text and "{ xPercent: 120" in text
    assert "MASTER.fromTo(bar, { opacity: 0 }" in text
    # 不抢 img 的 transform/filter：扫光目标是独立 bar，不是 img
    assert "MASTER.fromTo(bar" in text


def test_template_grain_overlay_and_prerendered_png() -> None:
    """19.5：颗粒为预渲染静态 PNG，平铺 opacity 0.10–0.14 呼吸，禁逐帧 feTurbulence。"""
    text = _template_text()
    png = ROOT / "fixtures" / "images" / "grain.png"
    assert png.is_file(), f"缺预渲染颗粒纹理: {png}"
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    from PIL import Image
    with Image.open(png) as im:
        assert im.format == "PNG" and im.mode == "L" and im.size == (256, 256)
    assert "id=\"grain-layer\"" in text
    assert "background-image:url('{{ grain_image }}')" in text
    assert "background-repeat:repeat" in text
    assert "{ opacity: 0.10 }" in text and "{ opacity: 0.14" in text
    assert "feTurbulence" not in text  # 性能红线：禁逐帧噪声


def test_project_generator_writes_crossfade_sweep_grain() -> None:
    """cinematic_fx 的 light/grain 与 crossfade 全局开关写进 timeline_json 与 context。"""
    fx = CinematicFx(light=Light(sweep=True), grade=Grade(grain_png="assets/grain.png"))
    bundle = _bundle_two_clips(fx)
    ctx = bundle.context
    assert ctx.crossfade_enable is True
    assert 0.08 <= ctx.crossfade_duration <= 0.15
    assert ctx.sweep_enable is True
    assert ctx.grain_image == "assets/grain.png"
    assert all(c.cinematic.crossfade_enable for c in ctx.clips)

    tj = json.loads(ctx.timeline_json)
    assert tj["crossfade"] == {"enable": True, "duration": 0.12}
    assert tj["fx_overlay"]["sweep"] is True
    assert tj["fx_overlay"]["grain_image"] == "assets/grain.png"


def test_crossfade_defaults_off_without_fx_or_single_clip() -> None:
    """缺 cinematic_fx 或单镜时 crossfade/sweep/grain 全部关闭，向后兼容。"""
    single = _bundle(None)
    assert single.context.crossfade_enable is False
    assert single.context.sweep_enable is False
    assert single.context.grain_image is None
    # 多镜但两镜都无 cinematic_fx：crossfade 仍关闭（缺配置保持现状）
    c0 = Clip(index=0, start=0.0, end=2.0, duration=2.0, image="assets/a.jpg")
    c1 = Clip(index=1, start=2.0, end=4.0, duration=2.0, image="assets/b.jpg")
    no_fx_tl = Timeline(task_id="fx-off", total_duration=4.0, clips=[c0, c1])
    no_fx = build_project_bundle(
        no_fx_tl, composition_id="fx-off", width=1080, height=1920,
        font_family="sans-serif", image_paths={0: "assets/a.jpg", 1: "assets/b.jpg"},
    )
    assert no_fx.context.crossfade_enable is False


def test_rendered_html_contains_new_overlay_layers() -> None:
    """Jinja2 渲染的 HTML 带 crossfade 参数与 sweep/grain overlay DOM。"""
    fx = CinematicFx(light=Light(sweep=True), grade=Grade(grain_png="assets/grain.png"))
    html = render_composition_html(_bundle_two_clips(fx).context, TEMPLATE)
    assert '"crossfade":{"enable":true,"duration":0.12}' in html
    assert '"sweep":true' in html
    assert "background-image:url('assets/grain.png')" in html
    assert "id=\"sweep-bar\"" in html
    # 未配置 grain 时不渲染颗粒层（缺配置向后兼容）
    html_off = render_composition_html(_bundle_two_clips(CinematicFx()).context, TEMPLATE)
    assert "id=\"grain-layer\"" not in html_off
