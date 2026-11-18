"""阶段 C WebGL 增强层的静态契约测试（决策 #8）。

验证：模板确实挂载独立 canvas overlay、默认 no-op、失败降级不阻断；
粒子用整数 seed 的 mulberry32 + p0+v0*t 闭式解（无帧间累积），
uniform 只由 t 推导；遵守决策 #3/#6 确定性铁律（禁 Date.now /
Math.random / repeat / yoyo / rAF / fetch / MASTER.time）。
project_generator 默认关闭，且 config 开启时注入 timeline_json.webgl。
全部为本机可真跑的静态/纯 Python 断言，零 CLI/Docker 依赖。
"""
from __future__ import annotations

from pathlib import Path

import src.builder.project_generator as pg
from src.builder.project_generator import WebGLRenderCtx, build_project_bundle, render_composition_html
from src.contracts import Clip, Timeline

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src" / "builder" / "template" / "composition.html.j2"


def _template_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _timeline() -> Timeline:
    clip = Clip(
        index=0, start=0.0, end=2.0, duration=2.0,
        image="assets/a.jpg", text="webgl 契约",
    )
    return Timeline(task_id="webgl-test", total_duration=2.0, clips=[clip])


def _bundle():
    return build_project_bundle(
        _timeline(), composition_id="webgl-test", width=1080, height=1920,
        font_family="sans-serif", image_paths={0: "assets/a.jpg"},
    )


def test_template_webgl_determinism_contract() -> None:
    """WebGL 层共享 MASTER 时钟，粒子闭式解；不引入任何隐式时间源/随机源。"""
    t = _template_text()
    assert "const WEBGL = TIMELINE.webgl" in t
    assert "window.renderShaderFrame(quantize(t));" in t
    assert "MASTER.totalTime(quantize(t), false)" in t
    assert "getContext('webgl'" in t
    assert "canvas.style.display = 'none'" in t
    assert "function mulberry32(seed)" in t
    assert "aP0V0.zw * uT" in t            # 位置 = p0 + v0*t 闭式解，无 pos +=
    for bad in (
        "Date.now(", "Math.random(", ".repeat(", "yoyo:",
        "requestAnimationFrame", "fetch(", "MASTER.time(",
    ):
        assert bad not in t, f"禁用符号出现在模板: {bad}"


def test_canvas_zindex_between_grain_and_flash() -> None:
    """canvas 独立 overlay，z 介于 grain(60) 与 flash(70)，不遮挡字幕。"""
    t = _template_text()
    assert 'id="webgl-layer"' in t
    assert "z-index:65" in t
    assert "z-index:60" in t and "z-index:70" in t  # grain / flash 未被动
    assert "mix-blend-mode:screen" in t
    assert "pointer-events:none; z-index:65" in t


def test_renderatframe_single_entry_no_double_shader_call() -> None:
    """renderAtFrame 仍只转发 renderAtTime，避免二次量化/双调用。"""
    t = _template_text()
    assert "window.renderAtTime(f / FPS);" in t
    assert "window.renderShaderFrame" in t


def test_shader_init_in_ready_gate() -> None:
    """shader 初始化纳入 markReady gate，但降级路径 resolve 不改变就绪语义。"""
    t = _template_text()
    assert "const shaderInit = imagesReady.then(() => shaderReady());" in t
    assert "Promise.all([fontsReady, imagesReady, shaderInit])" in t


def test_install_webgl_called_after_tweens() -> None:
    """installWebGL 在所有 tween 建好后（buildFxOverlays 末尾）调用。"""
    t = _template_text()
    assert "installWebGL();   /* tween 全部建好后再装 WebGL" in t
    assert "WEBGL_MODE === 'off'" in t
    assert "window.__WEBGL_INFO__" in t


def test_project_generator_webgl_default_auto_and_injection() -> None:
    """默认 config 中 mode=auto（GPU 自适应）；timeline_json.webgl 注入 mode+enabled。"""
    ctx = _bundle().context
    assert ctx.webgl.mode == "auto"
    assert ctx.webgl.enabled is True      # auto/on 派生 enabled=true，由模板探测决定真启用
    assert ctx.webgl.sharpen_strength == 0.0
    assert '"webgl":{"mode":"auto","enabled":true' in ctx.timeline_json


def test_project_generator_webgl_enabled_injection(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """monkeypatch config 加载器：开启后参数注入 timeline_json，无需改仓库文件。"""
    monkeypatch.setattr(
        pg, "_fx_webgl_config",
        lambda: {"enabled": True, "seed": 42, "particle_count": 128,
                 "godray_intensity": 0.3, "sharpen_strength": 0.0},
    )
    ctx = _bundle().context
    assert ctx.webgl.mode == "on"         # 旧 enabled:True 兼容映射为 mode=on
    assert ctx.webgl.enabled is True
    assert ctx.webgl.seed == 42
    assert ctx.webgl.particle_count == 128
    assert '"webgl":{"mode":"on","enabled":true' in ctx.timeline_json


def test_rendered_html_webgl_default_auto() -> None:
    """默认渲染出的 HTML 携带 no-op 占位与 mode=auto 配置（探测失败时保持 no-op）。"""
    html = render_composition_html(_bundle().context, TEMPLATE)
    assert "window.renderShaderFrame = function () {};" in html
    assert '"webgl":{"mode":"auto","enabled":true' in html
    assert 'id="webgl-layer"' in html
    assert "window.__WEBGL_INFO__" in html


def test_webgl_ctx_is_strict_model() -> None:
    """WebGLRenderCtx 继承 StrictModel：禁止未声明字段（跨层禁止裸 dict typo）。"""
    ctx = WebGLRenderCtx(enabled=True, seed=7, particle_count=10)
    assert ctx.enabled is True and ctx.seed == 7
    from pydantic import ValidationError
    try:
        WebGLRenderCtx(enabled=True, typo_field=1)  # type: ignore[call-arg]
    except ValidationError:
        pass
    else:
        raise AssertionError("WebGLRenderCtx 未拒绝未声明字段")
