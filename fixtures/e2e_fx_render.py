"""端到端实证：三个电影级特效真正渲染进 MP4（非 mock、非 DOM 钩子）。

用真实 BGM + 群像图，注入 cinematic_fx（irregular + flash-in + teal-orange），
走 generate_composition → HyperFrames CLI → ffmpeg → 抽帧，确认：
1. HTML 携带三特效参数；2. 成片非空；3. 入场帧与稳态帧像素分布不同（裁切生效）；
4. 稳态帧有色彩（调色生效）。本机 Windows 无 Docker，用 docker=False。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backend.hyperframes_backend import HyperFramesBackend  # noqa: E402
from src.contracts import (  # noqa: E402
    BeatAnalysis,
    CinematicFx,
    CinematicMask,
    Grade,
    Reveal,
    Timeline,
)
from src.service.pipeline import PipelineRequest, PipelineService  # noqa: E402
from src.timeline.builder import EffectSpec  # noqa: E402
from src.utils.subprocess_helper import run_command  # noqa: E402

AUDIO = str(ROOT / "fixtures" / "audio" / "bgm_120bpm.wav")
IMAGE = str(ROOT / "fixtures" / "images" / "group_10people.jpg")


def _extract_frame(video: str, at_sec: float, dest: Path, task_id: str) -> None:
    args = [
        "ffmpeg", "-y", "-ss", f"{at_sec:.3f}", "-i", video,
        "-frames:v", "1", str(dest),
    ]
    result = run_command(
        args, cwd=str(ROOT), timeout=60, log_dir=str(ROOT / "logs"),
        task_id=f"{task_id}_fxframe_{dest.stem}",
    )
    if not result.ok or not dest.is_file():
        raise RuntimeError(f"抽帧失败: {dest} / {result.stderr[-500:]}")


def main() -> int:
    backend = HyperFramesBackend(
        workspace_root=str(ROOT / "workspace"),
        logs_dir=str(ROOT / "logs"),
        vendor_dir=str(ROOT / "vendor"),
        use_docker=False,
    )
    service = PipelineService(
        backend=backend,
        workspace_root=str(ROOT / "workspace"),
        logs_dir=str(ROOT / "logs"),
    )
    req = PipelineRequest(
        audio_path=AUDIO,
        image_paths=[IMAGE],
        texts=["电影级特效实证 · 不规则裁切 + 闪入 + 调色"],
        beat_group=4,  # 少切镜，给足入场与稳态时间
        fps=30,
        width=1080,
        height=1920,
        do_render=True,
    )

    print("[1/6] validate_assets", flush=True)
    validation = service.validate_assets_step(req.audio_path, req.image_paths, req.min_duration)
    if not validation.ok:
        print("validate failed", validation.error); return 1

    print("[2/6] extract_audio_beats", flush=True)
    beats_resp = service.extract_beats_step(req.audio_path, beat_group=req.beat_group)
    if not beats_resp.ok:
        print("beats failed", beats_resp.error); return 1
    beats = BeatAnalysis.model_validate(beats_resp.data)

    task_id = service.ensure_task(req.task_id)
    rel_images = service.prepare_images(req.image_paths, task_id)

    fx = CinematicFx(
        mask=CinematicMask(mode="irregular", duration=0.38),
        reveal=Reveal(type="flash-in", blur_px=14, brightness=2.4, overshoot=1.12, duration=0.22),
        grade=Grade(preset="teal-orange"),
    )
    print("[3/6] build_timeline (cinematic_fx injected)", flush=True)
    tl_resp = service.build_timeline_step(
        beats.grouped_beats, rel_images, req.texts, [EffectSpec(cinematic_fx=fx)], True,
        fps=req.fps, audio_duration=beats.audio_duration, task_id=task_id,
        raw_beats=beats.raw_beats,
    )
    if not tl_resp.ok:
        print("timeline failed", tl_resp.error); return 1
    timeline = Timeline.model_validate(tl_resp.data)

    comp = service.generate_composition_step(
        timeline, "hyperframes", req.width, req.height, req.fps,
        req.audio_path, req.font_family,
    )
    if not comp.ok:
        print("composition failed", comp.error); return 1
    project_dir = Path(comp.data["project_dir"])

    html = (project_dir / "index.html").read_text(encoding="utf-8")
    flags = {
        "mask_mode": '"mask_mode":"irregular"' in html,
        "reveal_type": '"reveal_type":"flash-in"' in html,
        "grade_preset": '"grade_preset":"teal-orange"' in html,
        "irregular_fn": "function irregularPolygon" in html,
        "grade_filter": "teal-orange': 'contrast" in html,
    }
    print(f"[4/6] HTML effect flags: {flags}", flush=True)
    if not all(flags.values()):
        print("HTML missing effect flags"); return 1

    output = str(project_dir.parent / "fx_out.mp4")
    print("[5/6] real render (HyperFrames CLI + ffmpeg)", flush=True)
    started = time.time()
    render = service.render_step(project_dir, task_id, req.audio_path, output)
    print(f"render ok={render.ok} elapsed={time.time() - started:.1f}s", flush=True)
    if not render.ok:
        print("render failed", render.error.code.value, render.error.message, render.error.detail)
        return 1

    print("[6/6] extract frames & verify pixels", flush=True)
    frames_dir = project_dir / "fx_frames"
    frames_dir.mkdir(exist_ok=True)
    frame0_png = frames_dir / "frame0.png"
    enter_png, steady_png = frames_dir / "enter.png", frames_dir / "steady.png"
    _extract_frame(output, 0.0, frame0_png, task_id)    # polygon 收缩为点 → 裁切证据
    _extract_frame(output, 0.05, enter_png, task_id)    # blur+brightness 过曝 → 闪入证据
    _extract_frame(output, 1.20, steady_png, task_id)   # 稳态 → 调色证据

    from PIL import Image  # 延迟导入：仅 e2e 验证使用

    def stats(path: Path) -> dict:
        im = Image.open(path).convert("RGB")
        small = im.resize((108, 192))
        pixels = list(small.getdata())
        n = len(pixels)
        mean_rgb = tuple(sum(p[i] for p in pixels) / n for i in range(3))
        return {
            "mean_rgb": tuple(round(v, 1) for v in mean_rgb),
            "mean_luma": round(sum(mean_rgb) / 3, 1),
            "grayish": max(mean_rgb) - min(mean_rgb) < 3,
        }

    frame0, enter, steady = stats(frame0_png), stats(enter_png), stats(steady_png)
    print(f"frame0 (polygon closed): {frame0}", flush=True)
    print(f"enter (flash-in): {enter}", flush=True)
    print(f"steady: {steady}", flush=True)

    checks = {
        "file_nonempty": Path(output).is_file() and Path(output).stat().st_size > 100_000,
        "frames_nonempty": all(p.stat().st_size > 10_000 for p in (frame0_png, enter_png, steady_png)),
        "irregular_crop_visible": frame0["mean_luma"] < steady["mean_luma"] * 0.6,
        "flash_in_visible": sum(enter["mean_rgb"]) > sum(steady["mean_rgb"]) * 1.05,
        "grade_has_color": not steady["grayish"],
    }
    print(f"checks: {checks}", flush=True)
    if not all(checks.values()):
        print("pixel checks failed"); return 1

    print(f"output: {output}")
    print("RESULT: FX RENDER PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
