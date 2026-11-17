"""19.3 碎裂炸现真进成片的静态契约测试。

验证：模板确实实现碎裂层 DOM/CSS + 确定性 LCG + 每片 fromTo 错峰飞入；
project_generator 把 shatter/backdrop 参数映射进 ClipCtx 与 timeline_json；
默认 enable=false 时向旧行为兼容。真实出片证据见 fixtures/e2e_shatter_render.py。
"""
from __future__ import annotations

from pathlib import Path

from src.builder.project_generator import build_project_bundle, render_composition_html
from src.contracts import Clip, CinematicFx, Shatter, Timeline

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src" / "builder" / "template" / "composition.html.j2"


def _text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _timeline(fx: CinematicFx | None) -> Timeline:
    clip = Clip(index=0, start=0.0, end=2.0, duration=2.0,
                image="assets/a.jpg", text="碎裂实证", cinematic_fx=fx)
    return Timeline(task_id="shatter-test", total_duration=2.0, clips=[clip])


def _bundle(fx: CinematicFx | None):
    return build_project_bundle(
        _timeline(fx), composition_id="shatter-test", width=1080, height=1920,
        font_family="sans-serif", image_paths={0: "assets/a.jpg"},
    )


def test_template_has_shatter_layer_and_deterministic_lcg() -> None:
    t = _text()
    assert ".shatter-layer" in t and ".shard" in t
    assert "class=\"shatter-layer\"" in t
    assert "function buildShatter(clip, root, moveStart, Tin, stageW, stageH)" in t
    assert "function shatterRand(seed)" in t
    assert "Math.imul(s, 1664525) + 1013904223" in t  # 确定性 LCG
    assert "shards.forEach((s, i)" in t
    assert "x: s.dx, y: s.dy, rotation: s.rot" in t     # 显式起点
    assert "x: 0, y: 0, rotation: 0, opacity: 1, scale: 1" in t  # 显式终点


def test_shatter_determinism_no_unseeded_random() -> None:
    t = _text()
    for bad in ("Math.random(", "Date.now(", ".repeat(", "yoyo:",
                "requestAnimationFrame", "fetch(", "MASTER.time("):
        assert bad not in t, f"禁用符号出现在模板: {bad}"


def test_project_generator_maps_shatter_fields() -> None:
    fx = CinematicFx(shatter=Shatter(enable=True, cols=4, rows=3, stagger=0.02))
    ctx = _bundle(fx).context
    c = ctx.clips[0].cinematic
    assert c.shatter_enable is True
    assert c.shatter_cols == 4 and c.shatter_rows == 3
    assert abs(c.shatter_stagger - 0.02) < 1e-9
    assert '"shatter_enable":true' in ctx.timeline_json


def test_shatter_default_off_backward_compatible() -> None:
    ctx = _bundle(None).context
    assert ctx.clips[0].cinematic.shatter_enable is False
    html = render_composition_html(ctx, TEMPLATE)
    assert 'class="shatter-layer"' not in html  # 默认不生成碎裂层 DOM


def test_shatter_enabled_renders_layer_dom() -> None:
    fx = CinematicFx(shatter=Shatter(enable=True, cols=6, rows=6))
    html = render_composition_html(_bundle(fx).context, TEMPLATE)
    assert 'class="shatter-layer"' in html
    assert 'data-cols="6"' in html and 'data-rows="6"' in html


def test_shatter_contract_rejects_over_budget() -> None:
    from pydantic import ValidationError
    try:
        Shatter(enable=True, cols=6, rows=7)  # 42 > 36
    except ValidationError:
        pass
    else:
        raise AssertionError("Shatter 未拒绝超过 36 片的预算")
