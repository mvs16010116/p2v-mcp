"""GPU 自适应渲染的 CLI 命令构造单元测试（无副作用，纯 _build_render_command）。

验证 gpu_mode 三档（auto/on/off）与 resolution 白名单正确拼接为 CLI v0.9.59
实证支持的 flag，非法值与 docker+on 冲突被明确拒绝，且不臆造 flag。
"""
from __future__ import annotations

from pathlib import Path

from src.backend.hyperframes_backend import HyperFramesBackend
from src.contracts import ToolResponse


def _backend(use_docker: bool = False) -> HyperFramesBackend:
    return HyperFramesBackend(
        workspace_root="workspace", logs_dir="logs",
        cli_command=("hyperframes",), use_docker=use_docker,
    )


def _cmd(gpu_mode: str, resolution: str | None, use_docker: bool = False):
    return _backend(use_docker)._build_render_command(
        silent=Path("out.mp4"), use_docker=use_docker,
        gpu_mode=gpu_mode, resolution=resolution,
    )


def test_auto_mode_adds_no_gpu_flag() -> None:
    """auto（默认）：不传 GPU flag，交给 CLI 自动探测。"""
    args = _cmd("auto", None)
    assert isinstance(args, list)
    assert "--browser-gpu" not in args and "--no-browser-gpu" not in args
    assert args[:2] == ["hyperframes", "render"]
    assert args[-2:] == ["--output", "out.mp4"]


def test_on_mode_adds_browser_gpu() -> None:
    args = _cmd("on", None)
    assert isinstance(args, list)
    assert "--browser-gpu" in args and "--no-browser-gpu" not in args


def test_off_mode_adds_no_browser_gpu() -> None:
    args = _cmd("off", None)
    assert isinstance(args, list)
    assert "--no-browser-gpu" in args and "--browser-gpu" not in args


def test_resolution_whitelist_passthrough() -> None:
    args = _cmd("auto", "portrait-4k")
    assert isinstance(args, list)
    assert args[-4:-2] == ["--resolution", "portrait-4k"]
    assert "--output" in args


def test_default_no_resolution_flag() -> None:
    """默认不指定分辨率：保持 composition 的 1080×1920（业界交付规格，不强制 4K）。"""
    args = _cmd("auto", None)
    assert isinstance(args, list)
    assert "--resolution" not in args


def test_docker_flag_present_when_docker() -> None:
    args = _cmd("off", None, use_docker=True)
    assert isinstance(args, list)
    assert "--docker" in args and "--no-browser-gpu" in args


def test_invalid_gpu_mode_rejected() -> None:
    r = _cmd("turbo", None)
    assert isinstance(r, ToolResponse)
    assert r.ok is False and "gpu_mode" in r.error.message


def test_invalid_resolution_rejected() -> None:
    r = _cmd("auto", "8000p-ultra")
    assert isinstance(r, ToolResponse)
    assert r.ok is False and "resolution" in r.error.message


def test_docker_plus_gpu_on_rejected() -> None:
    r = _cmd("on", None, use_docker=True)
    assert isinstance(r, ToolResponse)
    assert r.ok is False and "docker" in r.error.message
