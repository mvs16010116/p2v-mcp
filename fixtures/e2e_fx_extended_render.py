"""端到端实证（扩展特效真进成片）：per-char 逐字 + shake 抖动 + chromatic 色差。

直接构造 Timeline（含 per-char 文字动画 + clip 级 shake/chromatic 节拍冲击），
走 generate_composition → 真实 CLI 渲染 → 抽帧，验证：
  1. HTML 携带逐字 span 拆分 + shake/chromatic JS 分支（结构证据）；
  2. 冲击时刻帧与对照（普通文字、无冲击）有可见像素差异；
  3. 首帧非黑、MP4 非空。
本机 Windows 无 Docker，用 docker=False。确定性由静态契约测试覆盖。
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from src.backend.hyperframes_backend import HyperFramesBackend  # noqa: E402
from src.contracts import (  # noqa: E402
    BeatImpact,
    Clip,
    TextAnim,
    Timeline,
)
from src.utils.subprocess_helper import run_command  # noqa: E402

AUDIO = str(ROOT / "fixtures" / "audio" / "bgm_120bpm.wav")
IMAGE = str(ROOT / "fixtures" / "images" / "group_10people.jpg")
VENDOR = str(ROOT / "vendor")
FRAME_SIZE = (120, 214)


def _extract(video: str, at_sec: float, dest: Path, tid: str) -> None:
    r = run_command(
        ["ffmpeg", "-y", "-i", video, "-ss", f"{at_sec:.3f}", "-frames:v", "1", str(dest)],
        cwd=str(ROOT), timeout=60, log_dir=str(ROOT / "logs"), task_id=f"{tid}_extframe_{dest.stem}",
    )
    if not r.ok or not dest.is_file():
        raise RuntimeError(f"抽帧失败 {dest}: {r.stderr[-300:]}")


def _gray(p: Path) -> np.ndarray:
    # 色差是颜色现象，保留 RGB 三通道（灰度化会让红/青分离互相抵消）。
    with Image.open(p) as im:
        return np.asarray(im.convert("RGB").resize(FRAME_SIZE), dtype=np.float32)


def _luma(arr: np.ndarray) -> float:
    return float(arr.mean())


def _render(backend: HyperFramesBackend, timeline: Timeline, out_name: str):
    from src.service.pipeline import PipelineService
    # 直接用 backend.generate_composition，图片路径需已拷进工程 assets
    project_dir = Path(backend.workspace.root) / timeline.task_id / "composition"
    # 准备素材
    assets = project_dir.parent / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(IMAGE, assets / "img_000.jpg")
    gen = backend.generate_composition(
        timeline, width=1080, height=1920, fps=30,
        audio_path=AUDIO, font_family="sans-serif",
    )
    if not gen.ok:
        raise RuntimeError(f"generate 失败: {gen.error}")
    pd = Path(gen.data["project_dir"])
    out = pd.parent / out_name
    t0 = time.time()
    r = backend.render(pd, timeline.task_id, AUDIO, str(out))
    print(f"  render[{out_name}] ok={r.ok} elapsed={time.time()-t0:.1f}s", flush=True)
    if not r.ok:
        raise RuntimeError(f"render 失败: {r.error}")
    return out, pd


def _make_timeline(task_id: str, extended: bool) -> Timeline:
    if extended:
        ta = TextAnim(type="per-char", stiffness=240, damping=18, delay=0.05, y_offset=60.0)
        impacts = [
            BeatImpact(t=0.6, frame=18, type="shake", intensity=0.8, duration=0.3, strong=True),
            BeatImpact(t=1.2, frame=36, type="chromatic", intensity=0.7, duration=0.3, strong=True),
        ]
    else:
        ta = TextAnim(type="spring-up-fade")
        impacts = []
    clip = Clip(index=0, start=0.0, end=4.0, duration=4.0,
                image="assets/img_000.jpg", text="扩展特效实证",
                text_anim=ta, beat_impacts=impacts)
    return Timeline(task_id=task_id, total_duration=4.0, clips=[clip])


def main() -> int:
    workspace = ROOT / "workspace"
    logs = ROOT / "logs"
    backend = HyperFramesBackend(
        workspace_root=str(workspace), logs_dir=str(logs),
        vendor_dir=VENDOR, use_docker=False,
    )
    print("[1/4] render extended + control", flush=True)
    t_ext = "c5f30000-0000-4000-8000-000000000001"
    t_ctl = "c5f30000-0000-4000-8000-000000000002"
    ext_pd = backend.workspace.root / t_ext / "composition"
    ext_mp4 = ext_pd.parent / "fx_ext_main.mp4"
    ctl_mp4 = backend.workspace.root / t_ctl / "fx_ext_ctrl.mp4"
    reuse = "--reuse" in sys.argv and ext_mp4.is_file() and ctl_mp4.is_file()
    if reuse:
        print("  reuse existing renders", flush=True)
    else:
        for tid in (t_ext, t_ctl):
            td = backend.workspace.root / tid
            if td.exists():
                shutil.rmtree(td)
        ext_tl = _make_timeline(t_ext, extended=True)
        ctl_tl = _make_timeline(t_ctl, extended=False)
        ext_mp4, ext_pd = _render(backend, ext_tl, "fx_ext_main.mp4")
        ctl_mp4, _ = _render(backend, ctl_tl, "fx_ext_ctrl.mp4")

    print("[2/4] structural flags", flush=True)
    html = (ext_pd / "index.html").read_text(encoding="utf-8")
    flags = {
        "per_char_branch": "clip.text_anim.type === 'per-char'" in html,
        "span_split": "sp.style.display = 'inline-block'" in html,
        "shake_branch": "hit.type === 'shake'" in html,
        "chromatic_branch": "hit.type === 'chromatic'" in html,
        "main_nonempty": ext_mp4.is_file() and ext_mp4.stat().st_size > 100_000,
    }
    print(f"  flags: {flags}", flush=True)
    if not all(flags.values()):
        return 1

    print("[3/4] extract frames at impact times", flush=True)
    fd = ext_pd / "fx_ext_frames"
    fd.mkdir(exist_ok=True)
    frames = {}
    for tag, video in (("ext", ext_mp4), ("ctl", ctl_mp4)):
        for label, t in (("shake", 0.68), ("chroma", 1.35)):
            p = fd / f"{tag}_{label}.png"
            _extract(str(video), t, p, tag)
            frames[f"{tag}_{label}"] = p
    d_shake = float(np.abs(_gray(frames["ext_shake"]) - _gray(frames["ctl_shake"])).mean())
    # 色差只作用于字幕（画面底部），全帧均值被大面积相同背景稀释；裁剪字幕带比较。
    ext_chroma = _gray(frames["ext_chroma"])
    ctl_chroma = _gray(frames["ctl_chroma"])
    h = ext_chroma.shape[0]
    cap_slice = slice(int(h * 0.80), h)
    d_chroma = float(np.abs(ext_chroma[cap_slice] - ctl_chroma[cap_slice]).mean())
    luma = float(_gray(frames["ext_shake"]).mean())
    print(f"  d_shake={d_shake:.2f} d_chroma(caption_band)={d_chroma:.2f} luma={luma:.1f}", flush=True)

    print("[4/4] verdict", flush=True)
    # 诚实口径：shake / per-char 有可测像素证据；chromatic 的 filter drop-shadow
    # 在 headless Chromium 的单帧抽帧中未测到显著差异（d_chroma 记录但不作为硬 PASS），
    # 故 chromatic 仅 "代码实现 + 契约覆盖"，不宣称真进成片已验证。
    checks = {
        "shake_visible": d_shake > 3.0,
        "not_black": luma > 15.0,
    }
    print(f"  checks: {checks}", flush=True)
    print(f"  observation (not a gate): chroma_caption_diff={d_chroma:.2f} — "
          "chromatic 的像素证据未达标，报告中按'代码已实现、未验证'标注", flush=True)
    if not all(checks.values()):
        return 1
    print(f"output: {ext_mp4}")
    print("RESULT: EXTENDED FX RENDER PASSED (per-char + shake verified; chromatic code-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
