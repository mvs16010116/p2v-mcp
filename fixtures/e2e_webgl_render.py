"""端到端实证（GPU 自适应 WebGL）：默认 auto 模式真实出片 + 确定性双渲染 + GPU 观测。

用仓库真实 config.yaml（fx.webgl.mode=auto），走完整链路
generate_composition → HyperFrames CLI（默认 browserGpuMode=auto）→ ffmpeg 抽帧，验证：
  1. HTML 携带 mode=auto、WebGL canvas、__WEBGL_INFO__ 可观测钩子、renderShaderFrame；
  2. 两次独立渲染同 t 抽帧 SHA256 完全一致（同档确定性，决策 #6 铁律）；
  3. 首帧非黑、MP4 非空（WebGL 探测失败降级 no-op 也不破坏出片）；
  4. 从渲染日志读 CLI 的 GPU 探测结论（hardware/software），作为"有显卡充分利用"的实证。

诚实边界：auto 模式下若 getContext 失败（无显卡环境），canvas 隐藏 no-op，
两次渲染仍一致（证明降级安全）。是否有硬件 GPU 以 CLI 日志实测为准，不臆测。
本机 Windows 用 docker=False；CI（Linux Docker，无 GPU）会走软件路径，各自档内确定性。
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backend.hyperframes_backend import HyperFramesBackend  # noqa: E402
from src.contracts import Clip, Timeline  # noqa: E402

AUDIO = str(ROOT / "fixtures" / "audio" / "bgm_120bpm.wav")
IMAGE = str(ROOT / "fixtures" / "images" / "group_10people.jpg")
VENDOR = str(ROOT / "vendor")


def _ffmpeg(*args: str, timeout: int = 60) -> None:
    subprocess.run(["ffmpeg", "-y", *args], check=True, capture_output=True,
                   timeout=timeout, shell=False)


def _frame_hash(video: Path, frame_index: int, work: Path) -> tuple[str, bytes]:
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


def _observed_gpu(logs: Path, task_id: str) -> str | None:
    """读本次渲染的 CLI 日志里 browserGpuMode probe 结论（可观测证据）。"""
    log = logs / f"cmd_{task_id}_hyperframes.log"
    if not log.is_file():
        return None
    content = log.read_text(encoding="utf-8", errors="ignore")
    hit = re.search(r"browserGpuMode probe[^\n]*", content)
    return hit.group(0).strip()[:300] if hit else None


def main() -> int:
    workspace = ROOT / "workspace"
    logs = ROOT / "logs"
    work = ROOT / "fixtures" / "reports"
    work.mkdir(parents=True, exist_ok=True)

    # 不 monkeypatch：使用仓库真实 config.yaml 的 fx.webgl.mode=auto（默认 GPU 自适应）。
    backend = _new_service(workspace, logs)
    print("[1/5] generate composition (config mode=auto)", flush=True)
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
        "webgl_auto": '"webgl":{"mode":"auto","enabled":true' in html,
        "mode_gate": "WEBGL_MODE === 'off'" in html,
        "observability": "window.__WEBGL_INFO__" in html,
        "canvas_dom": 'id="webgl-layer"' in html,
        "shader_hook": "window.renderShaderFrame(quantize(t))" in html,
        "renderer_probe": "WEBGL_debug_renderer_info" in html,
        "install_call": "installWebGL();" in html,
    }
    print(f"  structural: {structural}", flush=True)
    if not all(structural.values()):
        print("structural flags failed"); return 1

    print("[2/5] render run 1 (gpu_mode=auto)", flush=True)
    out1 = task_dir / "webgl_1.mp4"
    t0 = time.time()
    r1 = backend.render(project_dir, timeline.task_id, AUDIO, str(out1), gpu_mode="auto")
    print(f"  run1 ok={r1.ok} elapsed={time.time()-t0:.1f}s", flush=True)
    if not r1.ok:
        print(f"render1 failed: {r1.error}"); return 1

    print("[3/5] render run 2 (gpu_mode=auto) + GPU observation", flush=True)
    out2 = task_dir / "webgl_2.mp4"
    t0 = time.time()
    r2 = backend.render(project_dir, timeline.task_id, AUDIO, str(out2), gpu_mode="auto")
    print(f"  run2 ok={r2.ok} elapsed={time.time()-t0:.1f}s", flush=True)
    if not r2.ok:
        print(f"render2 failed: {r2.error}"); return 1
    gpu = _observed_gpu(logs, timeline.task_id)
    print(f"  observed_gpu: {gpu}", flush=True)

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
    print("NOTE: gpu 探测以日志 observed_gpu 为准（hardware=有显卡充分利用 / software=无显卡降级）；"
          "本 e2e 证明 auto 模式同档确定性 + 不阻断出片。跨档不比较像素。")
    print(f"output1: {out1}")
    print(f"output2: {out2}")
    print("RESULT: GPU-ADAPTIVE WEBGL RENDER PASSED (auto mode, deterministic + observable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
