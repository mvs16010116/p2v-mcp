"""渲染确定性回归（CODING_STANDARDS 第 5 节 / 方案第 21 章）。

两类测试：
1. 静态契约测试（不依赖 CLI）：模板必须用 totalTime、帧量化、ready gate、clip-path 四同；
2. 真实确定性测试：同一 composition 同一 t 渲染两次，抽取同一帧比较 SHA256。

**绝不 mock 掉确定性断言**：环境不满足时整条测试 skip，并在 reason 中写明缺口与
Docker 复现命令。skip 是环境限制声明，不是 workaround。
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.backend.hyperframes_backend import HyperFramesBackend
from src.builder.project_generator import build_project_bundle, render_composition_html
from src.contracts import Clip, Timeline

ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT / "vendor"
TEMPLATE_PATH = ROOT / "src" / "builder" / "template" / "composition.html.j2"
DOCKER_REPRO = (
    "npx --yes hyperframes@0.9.59 render --docker --output output.mp4 ; "
    "pytest tests/test_render_determinism.py -k same_time -v"
)


def _environment_check() -> tuple[bool, str]:
    """探测真实渲染所需环境；只检查，不安装、不下载。"""
    reasons: list[str] = []
    if shutil.which("ffmpeg") is None:
        reasons.append("ffmpeg 不在 PATH")
    if shutil.which("ffprobe") is None:
        reasons.append("ffprobe 不在 PATH")
    for name in ("gsap.min.js", "CustomEase.min.js"):
        if not (VENDOR_DIR / name).is_file():
            reasons.append(f"vendor/{name} 缺失（见 vendor/README.md 获取命令）")

    cli_ok = shutil.which("hyperframes") is not None
    if not cli_ok and shutil.which("npx") is not None:
        try:
            probe = subprocess.run(
                ["npx", "--no-install", "hyperframes", "--version"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            cli_ok = probe.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            cli_ok = False
    if not cli_ok:
        reasons.append("HyperFrames CLI 未安装（npx --no-install hyperframes --version 失败）")

    if sys.platform != "linux":
        reasons.append(f"当前平台 {sys.platform} 非 Linux，无法走 beginFrame/Docker 严格确定性路径")
    return (not reasons), "; ".join(reasons)


_READY, _SKIP_REASON = _environment_check()
_SKIP = pytest.mark.skipif(
    not _READY,
    reason=(
        f"环境缺口: {_SKIP_REASON}。Docker 复现命令: {DOCKER_REPRO}。"
        "skip 仅表示本机无法实跑，不降低断言强度。"
    ),
)


def _tiny_timeline(task_id: str, image_rel: str) -> Timeline:
    """构造最小可渲染 Timeline（全部时间为秒）。"""
    return Timeline(
        task_id=task_id,
        fps=30,
        total_duration=1.0,
        impact_profile="subtle",
        clips=[
            Clip(
                index=0,
                start=0.0,
                end=1.0,
                duration=1.0,
                image=image_rel,
                text="determinism",
            )
        ],
    )


# ---------------------------------------------------------------------------
# 1. 不依赖外部工具的静态契约测试
# ---------------------------------------------------------------------------


def test_template_enforces_determinism_contract() -> None:
    timeline = _tiny_timeline("00000000-0000-4000-8000-000000000001", "assets/img_000.jpg")
    bundle = build_project_bundle(
        timeline,
        composition_id=timeline.task_id,
        width=1080,
        height=1920,
        font_family="Noto Sans SC",
        image_paths={0: "assets/img_000.jpg"},
    )
    html = render_composition_html(bundle.context, TEMPLATE_PATH)

    assert "MASTER.totalTime(quantize(t), false)" in html
    assert "MASTER.time(" not in html
    assert "repeat: true" not in html
    assert "yoyo: true" not in html
    assert "Math.round(t * FPS) / FPS" in html
    assert "window.__ready = true" in html
    assert "document.fonts.ready" in html
    assert "window.__hf" in html

    # 决策 #7：四同（这里重点验证同单位——所有 inset 坐标均为百分比）
    assert "inset(0 100% 0 0)" not in html
    assert "inset(0% 100% 0% 0%)" in html
    assert html.count("fromTo(img,") >= 2  # 入场 + 退场都显式两端

    # JSON 内联必须转义 '<'，防止 </script> 提前闭合
    assert "\\u003c" not in html or "</script><script>" not in html


def test_project_bundle_uses_models_not_flat_state() -> None:
    timeline = _tiny_timeline("00000000-0000-4000-8000-000000000002", "assets/img_000.jpg")
    bundle = build_project_bundle(
        timeline,
        composition_id=timeline.task_id,
        width=1080,
        height=1920,
        font_family="sans-serif",
        image_paths={0: "assets/img_000.jpg"},
    )
    assert bundle.manifest.entries[0].relative_path == "assets/img_000.jpg"
    assert bundle.context.clips[0].mask.type == "circle"
    assert '"impact_profile":"subtle"' in bundle.context.timeline_json
    assert "<" not in bundle.context.timeline_json


# ---------------------------------------------------------------------------
# 2. 真实确定性回归（环境不满足则 skip）
# ---------------------------------------------------------------------------


def _ffmpeg(*args: str, timeout: int = 30) -> None:
    subprocess.run(
        ["ffmpeg", "-y", *args],
        check=True,
        capture_output=True,
        timeout=timeout,
        shell=False,
    )


def _frame_hash(video: Path, frame_index: int, workspace: Path) -> str:
    raw = workspace / f"frame_{frame_index:06d}.gray"
    _ffmpeg(
        "-i", str(video),
        "-vf", f"select=eq(n\\,{frame_index})",
        "-vsync", "0", "-frames:v", "1",
        "-f", "rawvideo", "-pix_fmt", "gray", str(raw),
    )
    return hashlib.sha256(raw.read_bytes()).hexdigest()


@_SKIP
def test_same_time_two_seeks_have_identical_frame_hash(tmp_path: Path) -> None:
    """同一 composition 同一 t 渲染两次，抽同一帧比较 SHA256（零 mock）。"""
    task_id = "00000000-0000-4000-8000-000000000003"
    workspace_root = tmp_path / "workspace"
    logs_dir = tmp_path / "logs"
    workspace_root.mkdir()
    logs_dir.mkdir()

    # 用 ffmpeg 生成最小素材（同步磁盘IO即可）。
    img = tmp_path / "source.jpg"
    _ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=1", "-frames:v", "1", str(img))
    audio = tmp_path / "bgm.mp3"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(audio))

    from src.utils.workspace import WorkspaceManager

    ws = WorkspaceManager(workspace_root)
    tid = ws.create_task()
    shutil.copy2(img, ws.task_dir(tid) / "assets" / "img_000.jpg")
    timeline = _tiny_timeline(tid, "assets/img_000.jpg")

    backend = HyperFramesBackend(
        workspace_root=workspace_root,
        logs_dir=logs_dir,
        vendor_dir=VENDOR_DIR,
        cli_command=("hyperframes",),  # 固定版本本地安装，禁止渲染时取包
        use_docker=True,               # 21.1 C4：像素级回归必须走 Docker
    )
    generated = backend.generate_composition(
        timeline,
        width=1080,
        height=1920,
        fps=30,
        audio_path=str(audio),
        font_family="sans-serif",
    )
    assert generated.ok, generated.error
    project_dir = generated.data["project_dir"]

    outputs: list[Path] = []
    for run in (1, 2):
        out = tmp_path / f"out_{run}.mp4"
        response = backend.render(
            project_dir,
            timeline.task_id,
            str(audio),
            str(out),
            timeout_sec=600,
        )
        assert response.ok, response.error
        outputs.append(out)

    probe_frame = 15  # t = 0.5s @ 30fps
    hashes = [_frame_hash(path, probe_frame, tmp_path) for path in outputs]
    assert hashes[0] == hashes[1], (
        f"同一 t 两次渲染帧 hash 不一致: {hashes[0]} != {hashes[1]}；"
        f"按 21.3 排查顺序检查字体/隐式时间源/媒体预解码/Docker/motion blur"
    )
