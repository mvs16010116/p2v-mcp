"""端到端实证（阶段 C WebGL 增强层）：开启 WebGL 后真实出片 + 确定性双渲染 SHA256。

用 monkeypatch 临时开启 config 的 fx.webgl（不改仓库 config.yaml，默认仍关闭），
走完整链路 generate_composition → HyperFrames CLI 真实渲染 → ffmpeg 抽帧，验证：
  1. HTML 携带 WebGL canvas + renderShaderFrame + 开启配置；
  2. 两次独立渲染同 t 抽帧 SHA256 完全一致（零 mock，决策 #6 确定性铁律）；
  3. 首帧非黑（WebGL 探测失败降级 no-op 也不能破坏出片，硬红线）；
  4. MP4 非空。

诚实边界：HyperFrames CLI 不接受 --use-gl/--use-angle flag，headless Chromium
是否真拿到 WebGL 上下文不可控。若 getContext 失败，canvas 隐藏 → 两次渲染仍一致
（本测试通过，但只证明"降级安全"）。要证明 shader 真跑需本地带 swiftshader 的
Playwright/Chrome 探测，不能作为 CLI e2e 硬断言。本机 Windows 用 docker=False。
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.builder.project_generator as pg  # noqa: E402
from src.backend.hyperframes_backend import HyperFramesBackend  # noqa: E402
from src.contracts import Clip, Timeline  # noqa: E402

AUDIO = str(ROOT / "fixtures" / "audio" / "bgm_120bpm.wav")
IMAGE = str(ROOT / "fixtures" / "images" / "group_10people.jpg")
VENDOR = str(ROOT / "vendor")
TASK_ID = "e2e-webgl-0001"


def _ffmpeg(*args: str, timeout: int = 60) -> None:
    subprocess.run(["ffmpeg", "-y", *args], check=True, capture_output=True,
                   timeout=timeout, shell=False)


def _frame_hash(video: Path, frame_index: int, work: Path) -> tuple[str, bytes]:
    # -ss 放在 -i 之后逐帧精确定位（e2e_fx2 验证过的写法），避免 select filter 的兼容问题。
    from PIL import Image
    png = work / f"webgl_frame_{frame_index:06d}.png"
    at_sec = frame_index / 30.0
    _ffmpeg("-i", str(video), "-ss", f"{at_sec:.3f}", "-frames:v", "1", str(png))
    with Image.open(png) as im:
        data = im.convert("L").tobytes()
    return hashlib.sha256(data).hexdigest(), data


def _new_service(workspace: Path, logs: Path) -> HyperFramesBackend:
    return HyperFramesBackend(
        workspace_root=str(workspace), logs_dir=str(logs),
        vendor_dir=VENDOR, use_docker=False,
    )


def _prepare(backend: HyperFramesBackend) -> tuple[Timeline, Path]:
    from src.utils.workspace import WorkspaceManager
    ws = WorkspaceManager(Path(backend.workspace.root))
    tid = ws.create_task()
    ws.task_dir(tid)
    for sub in ("assets", "composition"):
        (ws.task_dir(tid) / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy2(IMAGE, ws.task_dir(tid) / "assets" / "img_000.jpg")
    clip = Clip(index=0, start=0.0, end=4.0, duration=4.0,
                image="assets/img_000.jpg", text="WebGL 增强层实证")
    return Timeline(task_id=tid, total_duration=4.0, clips=[clip]), ws.task_dir(tid)


def main() -> int:
    workspace = ROOT / "workspace"
    logs = ROOT / "logs"
    work = ROOT / "fixtures" / "reports"
    work.mkdir(parents=True, exist_ok=True)

    # 临时开启 WebGL（进程内 monkeypatch，不改 config.yaml 默认值）。
    original = pg._fx_webgl_config
    pg._fx_webgl_config = lambda: {  # type: ignore[assignment]
        "enabled": True, "seed": 20261117, "particle_count": 180,
        "godray_intensity": 0.18, "sharpen_strength": 0.0,
    }
    try:
        backend = _new_service(workspace, logs)
        print("[1/5] generate composition (webgl enabled)", flush=True)
        timeline, task_dir = _prepare(backend)
        gen = backend.generate_composition(
            timeline, width=1080, height=1920, fps=30,
            audio_path=AUDIO, font_family="sans-serif",
        )
        if not gen.ok:
            print(f"generate failed: {gen.error}"); return 1
        project_dir = Path(gen.data["project_dir"])

        html = (project_dir / "index.html").read_text(encoding="utf-8")
        structural = {
            "webgl_on": '"webgl":{"enabled":true' in html,
            "canvas_dom": 'id="webgl-layer"' in html,
            "shader_fn": "window.renderShaderFrame = function () {};" in html,
            "install_call": "installWebGL();" in html,
            "degrade": "canvas.style.display = 'none'" in html,
        }
        print(f"  structural: {structural}", flush=True)
        if not all(structural.values()):
            print("structural flags failed"); return 1

        print("[2/5] render run 1", flush=True)
        out1 = task_dir / "webgl_1.mp4"
        t0 = time.time()
        r1 = backend.render(project_dir, timeline.task_id, AUDIO, str(out1))
        print(f"  run1 ok={r1.ok} elapsed={time.time()-t0:.1f}s", flush=True)
        if not r1.ok:
            print(f"render1 failed: {r1.error}"); return 1

        print("[3/5] render run 2", flush=True)
        out2 = task_dir / "webgl_2.mp4"
        t0 = time.time()
        r2 = backend.render(project_dir, timeline.task_id, AUDIO, str(out2))
        print(f"  run2 ok={r2.ok} elapsed={time.time()-t0:.1f}s", flush=True)
        if not r2.ok:
            print(f"render2 failed: {r2.error}"); return 1

        print("[4/5] determinism + first-frame-nonblack", flush=True)
        probe = 15  # t=0.5s @ 30fps
        h1, raw1 = _frame_hash(out1, probe, work)
        h2, _ = _frame_hash(out2, probe, work)
        det_ok = h1 == h2
        first_nonblack = max(raw1) > 8 if raw1 else False
        nonempty = out1.is_file() and out1.stat().st_size > 100_000
        print(f"  hash1={h1[:16]} hash2={h2[:16]} det={det_ok} "
              f"first_nonblack={first_nonblack} (max={max(raw1) if raw1 else 0}) "
              f"nonempty={nonempty}", flush=True)

        print("[5/5] verdict", flush=True)
        checks = {
            "deterministic_sha256": det_ok,
            "first_frame_nonblack": first_nonblack,
            "mp4_nonempty": nonempty,
        }
        if not all(checks.values()):
            print(f"checks failed: {checks}"); return 1
        print("NOTE: headless WebGL context availability is not controllable via CLI; "
              "PASS here proves deterministic degradation + safe render, not shader activation.")
        print(f"output1: {out1}")
        print(f"output2: {out2}")
        print("RESULT: WEBGL LAYER RENDER PASSED (deterministic + nonblocking)")
        return 0
    finally:
        pg._fx_webgl_config = original  # type: ignore[assignment]


if __name__ == "__main__":
    raise SystemExit(main())
