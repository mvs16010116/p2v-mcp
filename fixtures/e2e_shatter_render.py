"""端到端实证（19.3 碎裂炸现真进成片）：shatter 开启 vs 关闭双渲染对照。

主渲染给首镜配 CinematicFx(shatter enable=True, 6×6)；对照同素材同时间线但不启用
shatter。走完整链路 generate_composition → 真实 CLI 渲染 → 精确抽帧，验证：
  1. HTML 携带碎裂层 DOM + buildShatter + 每片 fromTo（结构证据）；
  2. 两条 MP4 均非空、首帧非黑；
  3. 入场早期主渲染 vs 对照有显著像素差异（碎片飞散造成），证明碎裂真进成片；
  4. 稳态（碎片就位淡出后）主/对照差异收窄（碎片层已交回 img），证明不破坏稳态。
本机 Windows 无 Docker，用 docker=False。确定性由现有静态契约测试覆盖。
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from src.backend.hyperframes_backend import HyperFramesBackend  # noqa: E402
from src.contracts import (  # noqa: E402
    BeatAnalysis,
    CinematicFx,
    Clip,
    Shatter,
    Timeline,
)
from src.service.pipeline import PipelineRequest, PipelineService  # noqa: E402
from src.timeline.builder import EffectSpec  # noqa: E402
from src.utils.subprocess_helper import run_command  # noqa: E402

AUDIO = str(ROOT / "fixtures" / "audio" / "bgm_120bpm.wav")
IMAGE = str(ROOT / "fixtures" / "images" / "group_10people.jpg")
FRAME_SIZE = (120, 214)


def _extract_frame(video: str, at_sec: float, dest: Path, task_id: str) -> None:
    args = ["ffmpeg", "-y", "-i", video, "-ss", f"{at_sec:.3f}", "-frames:v", "1", str(dest)]
    result = run_command(args, cwd=str(ROOT), timeout=60,
                         log_dir=str(ROOT / "logs"), task_id=f"{task_id}_shatterframe_{dest.stem}")
    if not result.ok or not dest.is_file():
        raise RuntimeError(f"抽帧失败: {dest} / {result.stderr[-400:]}")


def _load_gray(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("L").resize(FRAME_SIZE), dtype=np.float32)


def _new_service(workspace: Path, logs: Path) -> PipelineService:
    backend = HyperFramesBackend(
        workspace_root=str(workspace), logs_dir=str(logs),
        vendor_dir=str(ROOT / "vendor"), use_docker=False,
    )
    return PipelineService(backend=backend, workspace_root=str(workspace), logs_dir=str(logs))


def _reset_task_dir(service: PipelineService, task_id: str) -> None:
    import shutil
    td = service.workspace.root / task_id
    if td.exists():
        shutil.rmtree(td)
    for sub in ("assets", "composition"):
        (td / sub).mkdir(parents=True, exist_ok=True)


def _render_one(service: PipelineService, req: PipelineRequest, beats, *,
                task_id: str, fx: CinematicFx | None, out_name: str):
    service.ensure_task(task_id)
    rel_images = service.prepare_images(req.image_paths, task_id)
    effects = [EffectSpec(cinematic_fx=fx)] if fx else None
    tl_resp = service.build_timeline_step(
        beats.grouped_beats, rel_images, req.texts, effects, req.loop_assets,
        fps=req.fps, audio_duration=beats.audio_duration, task_id=task_id,
    )
    if not tl_resp.ok:
        raise RuntimeError(f"build_timeline 失败: {tl_resp.error}")
    from src.contracts import Timeline
    timeline = Timeline.model_validate(tl_resp.data)

    comp = service.generate_composition_step(
        timeline, "hyperframes", req.width, req.height, req.fps,
        req.audio_path, req.font_family,
    )
    if not comp.ok:
        raise RuntimeError(f"generate_composition 失败: {comp.error}")
    project_dir = Path(comp.data["project_dir"])
    output = str(project_dir.parent / out_name)
    t0 = time.time()
    render = service.render_step(project_dir, task_id, req.audio_path, output)
    print(f"  render[{task_id}] ok={render.ok} elapsed={time.time()-t0:.1f}s", flush=True)
    if not render.ok:
        raise RuntimeError(f"render 失败: {render.error}")
    return Path(output), timeline, project_dir


def main() -> int:
    workspace = ROOT / "workspace"
    logs = ROOT / "logs"
    req = PipelineRequest(
        audio_path=AUDIO, image_paths=[IMAGE], texts=["碎裂炸现实证"],
        beat_group=4, fps=30, width=1080, height=1920,
        loop_assets=True, do_render=True,
    )
    fx_on = CinematicFx(shatter=Shatter(enable=True, cols=6, rows=6, stagger=0.018))

    t_main = str(uuid.UUID("b4f30000-0000-4000-8000-000000000001"))
    t_ctrl = str(uuid.UUID("b4f30000-0000-4000-8000-000000000002"))
    main_mp4 = workspace / t_main / "shatter_main.mp4"
    ctrl_mp4 = workspace / t_ctrl / "shatter_ctrl.mp4"
    proj_main = workspace / t_main / "composition"

    s_main = _new_service(workspace, logs)
    s_ctrl = _new_service(workspace, logs)
    print("[1/5] validate + beats", flush=True)
    v = s_main.validate_assets_step(req.audio_path, req.image_paths, req.min_duration)
    if not v.ok:
        print(f"validate 失败: {v.error}"); return 1
    br = s_main.extract_beats_step(req.audio_path, beat_group=req.beat_group)
    if not br.ok:
        print(f"beats 失败: {br.error}"); return 1
    beats = BeatAnalysis.model_validate(br.data)

    reuse = "--reuse" in sys.argv and main_mp4.is_file() and ctrl_mp4.is_file()
    if reuse:
        print("[2/5][3/5] reuse renders", flush=True)
        from src.contracts import Timeline
        timeline = Timeline.model_validate_json((proj_main / "timeline.json").read_text(encoding="utf-8"))
    else:
        _reset_task_dir(s_main, t_main)
        _reset_task_dir(s_ctrl, t_ctrl)
        print("[2/5] main render (shatter on)", flush=True)
        main_mp4, timeline, _ = _render_one(s_main, req, beats, task_id=t_main, fx=fx_on, out_name="shatter_main.mp4")
        print("[3/5] control render (shatter off)", flush=True)
        ctrl_mp4, _, _ = _render_one(s_ctrl, req, beats, task_id=t_ctrl, fx=None, out_name="shatter_ctrl.mp4")

    clips = timeline.clips
    c0_dur = float(clips[0].duration)
    t_early = c0_dur * 0.06   # 入场早期：碎片飞散中
    t_settle = c0_dur * 0.85  # 稳态：碎片已淡出
    print(f"  clip0_dur={c0_dur:.3f}s t_early={t_early:.3f}s t_settle={t_settle:.3f}s", flush=True)

    print("[4/5] HTML structural flags", flush=True)
    html = (proj_main / "index.html").read_text(encoding="utf-8")
    flags = {
        "shatter_dom": 'class="shatter-layer"' in html,
        "build_shatter": "function buildShatter(clip, root, moveStart, Tin, stageW, stageH)" in html,
        "deterministic_lcg": "function shatterRand(seed)" in html,
        "no_unseeded_random": "Math.random(" not in html,
        "shard_fromto": "shards.forEach((s, i)" in html,
        "main_nonempty": main_mp4.is_file() and main_mp4.stat().st_size > 100_000,
        "ctrl_nonempty": ctrl_mp4.is_file() and ctrl_mp4.stat().st_size > 100_000,
    }
    print(f"  flags: {flags}", flush=True)
    if not all(flags.values()):
        print("structural flags failed"); return 1

    print("[5/5] extract + pixel analysis", flush=True)
    frames_dir = proj_main / "shatter_frames"
    frames_dir.mkdir(exist_ok=True)
    frames: dict[str, Path] = {}
    for tag, video in (("main", main_mp4), ("ctrl", ctrl_mp4)):
        for label, t in (("early", t_early), ("settle", t_settle)):
            p = frames_dir / f"{tag}_{label}.png"
            _extract_frame(str(video), t, p, f"shatter-{tag}")
            frames[f"{tag}_{label}"] = p
    Me, Ms = _load_gray(frames["main_early"]), _load_gray(frames["main_settle"])
    Ce, Cs = _load_gray(frames["ctrl_early"]), _load_gray(frames["ctrl_settle"])
    diff_early = float(np.abs(Me - Ce).mean())
    diff_settle = float(np.abs(Ms - Cs).mean())
    luma_early = float(Me.mean())
    print(f"  diff_early={diff_early:.2f} diff_settle={diff_settle:.2f} luma_early={luma_early:.1f}", flush=True)
    checks = {
        # 入场早期碎片飞散 → 主/对照有可见差异。
        "shatter_visible_at_enter": diff_early > 3.0,
        # 稳态碎片已淡出 → 差异应显著小于入场期（碎片层交回 img）。
        "settles_back_to_img": diff_settle < diff_early,
        "not_black": luma_early > 15.0,
    }
    print(f"  checks: {checks}", flush=True)
    if not all(checks.values()):
        print("pixel checks failed"); return 1
    print(f"output(main): {main_mp4}")
    print(f"output(ctrl): {ctrl_mp4}")
    print("RESULT: SHATTER RENDER PASSED (shards fly in and settle)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
