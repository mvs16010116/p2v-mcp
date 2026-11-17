"""扩展特效静态契约测试：per-char 逐字动画 + shake 抖动 + chromatic 色差。

验证模板确实实现这三种此前仅为 DOM 钩子的效果，且遵守确定性铁律
（fromTo 显式两端、MASTER 单时钟、无未 seed 随机/rAF/Date.now）。
真实出片证据见 fixtures/e2e_fx_extended_render.py。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src" / "builder" / "template" / "composition.html.j2"


def _text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_per_char_implementation() -> None:
    t = _text()
    assert "clip.text_anim.type === 'per-char'" in t
    assert "Array.from(txt)" in t                       # 逐字拆分
    assert "sp.style.display = 'inline-block'" in t
    assert "tStart + i * perChar" in t                  # 固定错峰
    assert "spans.forEach((sp, i)" in t


def test_shake_implementation_uses_stage_xy() -> None:
    t = _text()
    assert "hit.type === 'shake'" in t
    assert "const amp = 6 + 14 * A" in t
    assert "const phase = (hit.frame % 2) ? 1 : -1" in t  # 相位由 frame 派生，确定性
    assert "{ x: 0, y: 0, duration: seg" in t            # 段末归零，显式两端


def test_chromatic_implementation_uses_proxy_text_shadow() -> None:
    t = _text()
    assert "hit.type === 'chromatic'" in t
    assert "rgba(255,0,0,.95)" in t and "rgba(0,255,255,.95)" in t  # 红/青分离
    assert "const cpProxy = { p: 1 }" in t and "cp.style.textShadow" in t  # proxy 数值 tween
    assert "onUpdate:" in t  # 每帧写回，规避 filter 字符串插值不稳


def test_extended_fx_determinism_no_unseeded_random() -> None:
    t = _text()
    for bad in ("Math.random(", "Date.now(", ".repeat(", "yoyo:",
                "requestAnimationFrame", "fetch(", "MASTER.time("):
        assert bad not in t, f"禁用符号出现在模板: {bad}"


def test_all_beat_impact_types_implemented() -> None:
    t = _text()
    for kind in ("flash", "punch", "shake", "chromatic"):
        assert f"hit.type === '{kind}'" in t
