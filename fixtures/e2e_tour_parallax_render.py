"""端到端实证（阶段 B 主体感知广度）：tour 两点漫游 + 分层视差真进成片（非 mock）。

用 RT-DETR 真实检出的两个人物 bbox（detection_report.json）：
  - 起点 A：中央红夹克人物 index=1；
  - 终点 B：右侧人物 index=4。
主渲染 focus 配 tour_to(B) + parallax；对照渲染只有单点 focus(A)。
走完整链路 generate_composition → 真实渲染 → 抽帧，验证：
  1. HTML 携带 tour_keyframes 两点 + parallax 参数 + bg-parallax DOM；
  2. 主/对照均产出非空 MP4；
  3. tour 焦点位移：主渲染在中段与对照开始分叉、晚段差异进一步拉大，
     证明画面焦点真的从 A 漫游到了 B（而非静止单点聚焦）；
  4. tour 晚段主帧不是纯黑（背景层慢推 + 雾化未把画面带崩）。
本机 Windows 无 Docker，用 docker=False。
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from src.backend.hyperframes_backend import HyperFramesBackend  # noqa: E402
from src.contracts import (  # noqa: E402
    BeatAnalysis,
    Focus,
    Parallax,
    Selector,
    Timeline,
    TourTo,
)
from src.service.pipeline import PipelineRequest, PipelineService  # noqa: E402
from src.timeline.builder import EffectSpec  # noqa: E402
from src.utils.subprocess_helper import run_command  # noqa: E402

AUDIO = str(ROOT / "fixtures" / "audio" / "bgm_120bpm.wav")
IMAGE = str(ROOT / "fixtures" / "images" / "group_10people.jpg")
# 真实检测结果（fixtures/reports/detection_report.json subjects[1] / subjects[4]）
BBOX_A = [0.4095589319864909, 0.14617928862571716, 0.6052964925765991, 0.9984405040740967]
BBOX_B = [0.7065178553263346, 0.20732352137565613, 0.8406548500061035, 0.9985926151275635]
FRAME_SIZE = (120, 214)


def _extract_frame(video: str, at_sec: float, dest: Path, task_id: str) -> None:
    args = [
        "ffmpeg", "-y", "-i", video,
        "-ss", f"{at_sec:.3f}", "-frames:v", "1", str(dest),
    ]
    result = run_command(
        args, cwd=str(ROOT), timeout=60, log_dir=str(ROOT / "logs"),
        task_id=f"{task_id}_tourframe_{dest.stem}",
    )
    if not result.ok or not dest.is_file():
        raise RuntimeError(f"抽帧失败: {dest} / {result.stderr[-500:]}")


def _load_gray(path: Path) -> np.ndarray:
    from PIL import Image  # 延迟导入：仅 e2e 验证使用

    with Image.open(path) as im:
        small = im.convert("L").resize(FRAME_SIZE)
    return np.asarray(small, dtype=np.float32)


def _new_service(workspace_root: Path, logs_dir: Path) -> PipelineService:
    backend = HyperFramesBackend(
        workspace_root=str(workspace_root),
        logs_dir=str(logs_dir),
        vendor_dir=str(ROOT / "vendor"),
        use_docker=False,
    )
    return PipelineService(
        backend=backend,
        workspace_root=str(workspace_root),
        logs_dir=str(logs_dir),
    )


def _reset_task_dir(service: PipelineService, task_id: str) -> None:
    task_dir = service.workspace.root / task_id
    if task_dir.exists():
        import shutil
        shutil.rmtree(task_dir)
    for sub in ("assets", "composition"):
        (task_dir / sub).mkdir(parents=True, exist_ok=True)


def _prepare_beats(service: PipelineService, req: PipelineRequest):
    validation = service.validate_assets_step(req.audio_path, req.image_paths, req.min_duration)
    if not validation.ok:
        raise RuntimeError(f"validate_assets 失败: {validation.error}")
    beats_resp = service.extract_beats_step(req.audio_path, beat_group=req.beat_group)
    if not beats_resp.ok:
        raise RuntimeError(f"extract_beats 失败: {beats_resp.error}")
    return BeatAnalysis.model_validate(beats_resp.data)


def _render_one(
    service: PipelineService,
    req: PipelineRequest,
    beats,
    *,
    task_id: str,
    focus: Focus,
    out_name: str,
) -> tuple[Path, Timeline, Path]:
    task = service.ensure_task(task_id)
    rel_images = service.prepare_images(req.image_paths, task)
    tl_resp = service.build_timeline_step(
        beats.grouped_beats,
        rel_images,
        req.texts,
        [EffectSpec(focus=focus)],
        req.loop_assets,
        fps=req.fps,
        audio_duration=beats.audio_duration,
        task_id=task,
    )
    if not tl_resp.ok:
        raise RuntimeError(f"build_timeline 失败: {tl_resp.error}")
    timeline = Timeline.model_validate(tl_resp.data)

    comp = service.generate_composition_step(
        timeline, "hyperframes", req.width, req.height, req.fps,
        req.audio_path, req.font_family,
    )
    if not comp.ok:
        raise RuntimeError(f"generate_composition 失败: {comp.error}")
    project_dir = Path(comp.data["project_dir"])

    output = str(project_dir.parent / out_name)
    started = time.time()
    render = service.render_step(project_dir, task, req.audio_path, output)
    print(f"  render[{task}] ok={render.ok} elapsed={time.time() - started:.1f}s", flush=True)
    if not render.ok:
        raise RuntimeError(f"render 失败: {render.error.code.value} {render.error.message}")
    return Path(output), timeline, project_dir


def main() -> int:
    workspace = ROOT / "workspace"
    logs = ROOT / "logs"

    req = PipelineRequest(
        audio_path=AUDIO,
        image_paths=[IMAGE],
        texts=["主体漫游 · tour + 视差实证"],
        beat_group=4,        # 与 focus e2e 一致，保证合并节拍 >= 2
        fps=30,
        width=1080,
        height=1920,
        loop_assets=True,
        do_render=True,
    )

    focus_main = Focus(
        mode="subject",
        selector=Selector(type="index", value=1),
        subject_index=1,
        bbox=BBOX_A,
        fill=0.62,
        fit="contain",
        anchor_region="upper_body",
        hold_ratio=0.05,
        parallax=Parallax(enable=True, bg_ratio=0.35, haze=0.25),
        tour_to=TourTo(subject_index=4, bbox=BBOX_B),
    )
    focus_ctrl = Focus(
        mode="subject",
        selector=Selector(type="index", value=1),
        subject_index=1,
        bbox=BBOX_A,
        fill=0.62,
        fit="contain",
        anchor_region="upper_body",
        hold_ratio=0.05,
    )

    task_main = str(uuid.UUID("a3f30000-0000-4000-8000-000000000001"))
    task_ctrl = str(uuid.UUID("a3f30000-0000-4000-8000-000000000002"))
    mp4_main_path = workspace / task_main / "tour_main.mp4"
    mp4_ctrl_path = workspace / task_ctrl / "tour_ctrl.mp4"
    proj_main_dir = workspace / task_main / "composition"
    reuse = "--reuse" in sys.argv and mp4_main_path.is_file() and mp4_ctrl_path.is_file()

    main_service = _new_service(workspace, logs)
    ctrl_service = _new_service(workspace, logs)

    print("[1/6] validate + beats", flush=True)
    beats = _prepare_beats(main_service, req)

    if reuse:
        print("[2/6][3/6] reuse existing renders (--reuse)", flush=True)
        mp4_main, mp4_ctrl = mp4_main_path, mp4_ctrl_path
        timeline = Timeline.model_validate_json(
            (proj_main_dir / "timeline.json").read_text(encoding="utf-8")
        )
    else:
        _reset_task_dir(main_service, task_main)
        _reset_task_dir(ctrl_service, task_ctrl)
        print("[2/6] main render (tour A->B + parallax)", flush=True)
        mp4_main, timeline, _ = _render_one(
            main_service, req, beats, task_id=task_main,
            focus=focus_main, out_name="tour_main.mp4",
        )
        print("[3/6] control render (single focus A, no tour/parallax)", flush=True)
        mp4_ctrl, _, _ = _render_one(
            ctrl_service, req, beats, task_id=task_ctrl,
            focus=focus_ctrl, out_name="tour_ctrl.mp4",
        )

    clips = timeline.clips
    total = float(timeline.total_duration)
    # 必须在同一镜头内比较：每个镜头都各自做 A→B 漫游，跨镜头取样会失去单调性。
    # 取镜头 0 的 tour 早段（接近 A）与晚段（接近 B，且在退场之前）。
    c0_start = float(clips[0].start)
    c0_dur = float(clips[0].duration)
    t_mid = c0_start + c0_dur * 0.22   # tour 早段，接近 A
    t_late = c0_start + c0_dur * 0.74  # tour 晚段，接近 B
    print(f"  total={total:.3f}s clip0=[{c0_start:.3f},{c0_start + c0_dur:.3f}] "
          f"t_mid={t_mid:.3f}s t_late={t_late:.3f}s clips={len(clips)}", flush=True)
    if not (c0_start < t_mid < t_late < c0_start + c0_dur):
        print("抽帧时间不合理"); return 1

    print("[4/6] HTML structural flags", flush=True)
    html = (proj_main_dir / "index.html").read_text(encoding="utf-8")
    structural = {
        "tour_keyframes": '"tour_keyframes":[{"scale":' in html,
        "parallax_params": '"parallax":{"foreground":' in html,
        "bg_parallax_dom": 'class="bg-parallax"' in html,
        "tour_js_branch": "tour_keyframes.length === 2" in html,
        "parallax_js_branch": "clip.focus_render.parallax" in html,
        "mp4_main_nonempty": mp4_main.is_file() and mp4_main.stat().st_size > 100_000,
        "mp4_ctrl_nonempty": mp4_ctrl.is_file() and mp4_ctrl.stat().st_size > 100_000,
    }
    print(f"  structural: {structural}", flush=True)
    if not all(structural.values()):
        print("structural flags failed"); return 1

    print("[5/6] extract frames", flush=True)
    frames_dir = proj_main_dir / "tour_frames"
    frames_dir.mkdir(exist_ok=True)
    frames: dict[str, Path] = {}
    for tag, video in (("main", mp4_main), ("ctrl", mp4_ctrl)):
        for label, t in (("mid", t_mid), ("late", t_late)):
            p = frames_dir / f"{tag}_{label}.png"
            _extract_frame(str(video), t, p, f"tour-{tag}")
            frames[f"{tag}_{label}"] = p

    Mmid = _load_gray(frames["main_mid"])
    Mlate = _load_gray(frames["main_late"])
    Cmid = _load_gray(frames["ctrl_mid"])
    Clate = _load_gray(frames["ctrl_late"])

    print("[6/6] pixel analysis", flush=True)
    diff_mid = float(np.abs(Mmid - Cmid).mean())
    diff_late = float(np.abs(Mlate - Clate).mean())
    luma_late = float(Mlate.mean())
    tour_checks = {
        # tour 焦点位移：晚段主-对照差异必须显著，且大于中段（漫游随时间推进）。
        "tour_diverged": diff_late > 8.0,
        "tour_progresses": diff_late > diff_mid + 1.5,
        # 背景层慢推 + 雾化后画面仍未崩坏。
        "not_black": luma_late > 18.0,
    }
    print(f"  tour: diff_mid={diff_mid:.2f} diff_late={diff_late:.2f} "
          f"luma_late={luma_late:.1f} -> {tour_checks}", flush=True)

    print("verdict", flush=True)
    if not all(tour_checks.values()):
        print("pixel checks failed"); return 1

    print(f"output(main): {mp4_main}")
    print(f"output(ctrl): {mp4_ctrl}")
    print("RESULT: TOUR + PARALLAX RENDER PASSED (both in MP4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
