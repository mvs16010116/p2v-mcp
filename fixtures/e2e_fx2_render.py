"""端到端实证（阶段 B 第二梯队特效）：crossfade + 扫光 + 颗粒真进成片（非 mock）。

用真实 BGM + 群像图 + 单主体图（两张不同图验证 crossfade），注入
cinematic_fx（light.sweep + grade.grain_png）走 generate_composition →
HyperFrames CLI → ffmpeg → 抽帧，像素级确认：
1. HTML 携带 crossfade / sweep / grain 参数与 overlay DOM；
2. 主渲染出非空 MP4；
3. 切点中点帧是「两张图的混合」而非硬切/纯黑（crossfade）；
4. 扫光时段主渲染比对照渲染在多处像素显著提亮，且呈局部条带（sweep）；
5. 稳态帧主渲染比无颗粒对照渲染有零均值高频噪声（预渲染 grain 平铺）。

对照渲染（crossfade/调色/图片完全相同，仅关 sweep + grain）用于把扫光与颗粒
从 Kodak 调色/Ken Burns 中分离出来——这是像素级证据的必要基线，不是 mock。
本机 Windows 无 Docker，用 docker=False。
"""
from __future__ import annotations

import shutil
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
    CinematicFx,
    CinematicMask,
    Grade,
    Light,
    Reveal,
    Timeline,
)
from src.service.pipeline import PipelineRequest, PipelineService  # noqa: E402
from src.timeline.builder import EffectSpec  # noqa: E402
from src.utils.subprocess_helper import run_command  # noqa: E402

AUDIO = str(ROOT / "fixtures" / "audio" / "bgm_120bpm.wav")
IMG_A = str(ROOT / "fixtures" / "images" / "group_10people.jpg")
IMG_B = str(ROOT / "fixtures" / "images" / "person_ref.jpg")
GRAIN = ROOT / "fixtures" / "images" / "grain.png"

T_CROSS = 0.12  # 与 project_generator 的 crossfade_duration 一致
FRAME_SIZE = (120, 214)


def _extract_frame(video: str, at_sec: float, dest: Path, task_id: str) -> None:
    # -ss 放在 -i 之后：逐帧解码精确定位，切点/扫光抽帧不能有关键帧误差。
    args = [
        "ffmpeg", "-y", "-i", video,
        "-ss", f"{at_sec:.3f}", "-frames:v", "1", str(dest),
    ]
    result = run_command(
        args, cwd=str(ROOT), timeout=60, log_dir=str(ROOT / "logs"),
        task_id=f"{task_id}_fx2frame_{dest.stem}",
    )
    if not result.ok or not dest.is_file():
        raise RuntimeError(f"抽帧失败: {dest} / {result.stderr[-500:]}")


def _load_gray(path: Path) -> np.ndarray:
    """下采样用于低频分析（crossfade/sweep）。"""
    from PIL import Image  # 延迟导入：仅 e2e 验证使用

    with Image.open(path) as im:
        small = im.convert("L").resize(FRAME_SIZE)
    return np.asarray(small, dtype=np.float32)


def _load_gray_native_crop(path: Path) -> np.ndarray:
    """原生分辨率中央裁剪用于高频分析（grain）——下采样会把逐像素噪声平均掉。"""
    from PIL import Image

    with Image.open(path) as im:
        g = im.convert("L")
        w, h = g.size
        crop = g.crop((w // 4, h // 4, (3 * w) // 4, (3 * h) // 4))
    return np.asarray(crop, dtype=np.float32)


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
    """清理并重建固定 task 目录，保证跨次 e2e 复现同一工程路径。"""
    task_dir = service.workspace.root / task_id
    if task_dir.exists():
        shutil.rmtree(task_dir)
    for sub in ("assets", "composition"):
        (task_dir / sub).mkdir(parents=True, exist_ok=True)


def _prepare_beats(service: PipelineService, req: PipelineRequest):
    from src.contracts import BeatAnalysis as _BA

    validation = service.validate_assets_step(req.audio_path, req.image_paths, req.min_duration)
    if not validation.ok:
        raise RuntimeError(f"validate_assets 失败: {validation.error}")
    beats_resp = service.extract_beats_step(req.audio_path, beat_group=req.beat_group)
    if not beats_resp.ok:
        raise RuntimeError(f"extract_beats 失败: {beats_resp.error}")
    return _BA.model_validate(beats_resp.data)


def _render_one(
    service: PipelineService,
    req: PipelineRequest,
    beats,
    *,
    task_id: str,
    fx: CinematicFx,
    out_name: str,
    copy_grain: bool,
) -> tuple[Path, Timeline, Path]:
    """跑单条渲染链；返回 (mp4, timeline, project_dir)。"""
    task = service.ensure_task(task_id)
    rel_images = service.prepare_images(req.image_paths, task)
    # 不传 raw_beats：避免 flash/punch 在切点污染 crossfade/sweep 的像素证据；
    # beat_impacts 为空时扫光退化为在各镜头起点触发（仍是强拍边界）。
    tl_resp = service.build_timeline_step(
        beats.grouped_beats,
        rel_images,
        req.texts,
        [EffectSpec(cinematic_fx=fx)],
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

    if copy_grain:
        if not GRAIN.is_file():
            raise RuntimeError(f"缺预渲染颗粒纹理: {GRAIN}")
        # 渲染层只拷镜头图片；grain 作为独立 overlay 素材由本 e2e 放进工程 assets/。
        dest = project_dir / "assets" / GRAIN.name
        shutil.copy2(GRAIN, dest)

    output = str(project_dir.parent / out_name)
    started = time.time()
    render = service.render_step(project_dir, task, req.audio_path, output)
    print(f"  render[{task}] ok={render.ok} elapsed={time.time() - started:.1f}s", flush=True)
    if not render.ok:
        err = render.error
        raise RuntimeError(f"render 失败: {err.code.value} {err.message} {err.detail}")
    return Path(output), timeline, project_dir


def main() -> int:
    workspace = ROOT / "workspace"
    logs = ROOT / "logs"

    req = PipelineRequest(
        audio_path=AUDIO,
        image_paths=[IMG_A, IMG_B],
        texts=["群像 · 交叉溶解 A", "单人 · 交叉溶解 B"],
        beat_group=4,
        fps=30,
        width=1080,
        height=1920,
        loop_assets=True,
        do_render=True,
    )

    # 两条链各自独立 service，避免 _asset_map 跨任务复用导致素材路径错指。
    main_service = _new_service(workspace, logs)
    ctrl_service = _new_service(workspace, logs)

    print("[1/7] validate + beats", flush=True)
    beats = _prepare_beats(main_service, req)
    print(f"  BPM grouped beats: {beats.grouped_beats}", flush=True)

    fx_main = CinematicFx(
        mask=CinematicMask(mode="iris", duration=0.12),  # 非 irregular → 标准蒙版，不干扰溶解
        reveal=Reveal(type="fade"),                       # 关闪入，避免过曝污染像素
        grade=Grade(preset="teal-orange", grain_png="assets/grain.png"),
        light=Light(sweep=True),
    )
    fx_ctrl = CinematicFx(
        mask=CinematicMask(mode="iris", duration=0.12),
        reveal=Reveal(type="fade"),
        grade=Grade(preset="teal-orange", grain_png=None),  # 无颗粒
        light=Light(sweep=False),                            # 无扫光
    )

    # 固定 UUID：跨次渲染可复现同一工程目录，便于像素对照；非随机。
    task_main = str(uuid.UUID("a1f20000-0000-4000-8000-000000000001"))
    task_ctrl = str(uuid.UUID("a1f20000-0000-4000-8000-000000000002"))
    proj_main_dir = workspace / task_main / "composition"
    mp4_main_path = workspace / task_main / "fx2_main.mp4"
    mp4_ctrl_path = workspace / task_ctrl / "fx2_ctrl.mp4"
    reuse = "--reuse" in sys.argv and mp4_main_path.is_file() and mp4_ctrl_path.is_file()

    if reuse:
        print("[2/7][3/7] reuse existing renders (--reuse)", flush=True)
        mp4_main, mp4_ctrl, proj_main = mp4_main_path, mp4_ctrl_path, proj_main_dir
        timeline = Timeline.model_validate_json(
            (proj_main_dir / "timeline.json").read_text(encoding="utf-8")
        )
    else:
        _reset_task_dir(main_service, task_main)
        _reset_task_dir(ctrl_service, task_ctrl)
        print("[2/7] main render (crossfade + sweep + grain)", flush=True)
        mp4_main, timeline, proj_main = _render_one(
            main_service, req, beats, task_id=task_main, fx=fx_main,
            out_name="fx2_main.mp4", copy_grain=True,
        )
        print("[3/7] control render (same everything, no sweep/grain)", flush=True)
        mp4_ctrl, _, _ = _render_one(
            ctrl_service, req, beats, task_id=task_ctrl, fx=fx_ctrl,
            out_name="fx2_ctrl.mp4", copy_grain=False,
        )

    clips = timeline.clips
    if len(clips) < 2:
        print("镜头数 < 2，无法验证 crossfade"); return 1
    cut = float(clips[1].start)
    total = float(timeline.total_duration)
    t_a = max(0.35, cut - 0.55)
    t_x = cut - T_CROSS / 2          # crossfade 重叠窗口正中
    t_sweep = cut + 0.20            # 扫光进行中（起点=cut，时长 0.55s）
    t_b = cut + 0.95                # sweep 已结束的稳态
    if not (0 < t_x < cut < t_sweep < t_b < total):
        print(f"切点/抽帧时间不合理: cut={cut}, total={total}"); return 1
    print(f"  cut={cut:.3f}s  t_a={t_a:.3f}  t_x={t_x:.3f}  "
          f"t_sweep={t_sweep:.3f}  t_b={t_b:.3f}", flush=True)

    print("[4/7] HTML / asset structural flags", flush=True)
    html = (proj_main / "index.html").read_text(encoding="utf-8")
    structural = {
        "crossfade_on": '"crossfade":{"enable":true,"duration":0.12}' in html,
        "sweep_on": '"sweep":true' in html,
        "grain_dom": "id=\"grain-layer\"" in html and "url('assets/grain.png')" in html,
        "sweep_dom": "id=\"sweep-bar\"" in html,
        "grain_copied": (proj_main / "assets" / GRAIN.name).is_file(),
        "mp4_nonempty": mp4_main.is_file() and mp4_main.stat().st_size > 100_000,
    }
    print(f"  structural: {structural}", flush=True)
    if not all(structural.values()):
        print("structural flags failed"); return 1

    print("[5/7] extract frames", flush=True)
    frames_dir = proj_main / "fx2_frames"
    frames_dir.mkdir(exist_ok=True)
    paths = {}
    for name, t in (("a", t_a), ("x", t_x), ("sweep", t_sweep), ("b", t_b)):
        p = frames_dir / f"{name}.png"
        _extract_frame(str(mp4_main), t, p, "fx2-main")
        paths[f"main_{name}"] = p
    p_ctrl_sweep = frames_dir / "ctrl_sweep.png"
    p_ctrl_b = frames_dir / "ctrl_b.png"
    _extract_frame(str(mp4_ctrl), t_sweep, p_ctrl_sweep, "fx2-ctrl")
    _extract_frame(str(mp4_ctrl), t_b, p_ctrl_b, "fx2-ctrl")

    A = _load_gray(paths["main_a"])
    X = _load_gray(paths["main_x"])
    S = _load_gray(paths["main_sweep"])
    B = _load_gray(paths["main_b"])
    CS = _load_gray(p_ctrl_sweep)
    CB = _load_gray(p_ctrl_b)

    print("[6/7] pixel analysis", flush=True)
    # --- crossfade：X 应为 A 与 B 的线性混合（alpha 投影落在中间带，且方向与 B-A 一致）。
    d_ba = (B - A).ravel()
    x_minus_a = (X - A).ravel()
    alpha = float(np.dot(x_minus_a, d_ba) / max(np.dot(d_ba, d_ba), 1e-6))
    corr = float(np.corrcoef(x_minus_a, d_ba)[0, 1])
    mad_ab = float(np.abs(A - B).mean())
    crossfade_checks = {
        "images_distinct": mad_ab > 10.0,
        "blend_alpha_mid": 0.20 < alpha < 0.85,
        "blend_direction": corr > 0.35,
        "not_black": float(X.mean()) > 18.0,
    }
    print(f"  crossfade: alpha={alpha:.3f} corr={corr:.3f} mad_ab={mad_ab:.1f} "
          f"luma={X.mean():.1f} -> {crossfade_checks}", flush=True)

    # --- sweep：主-对照差分在主渲染一侧出现显著提亮，且是局部条带（非整屏均匀）。
    ds = S - CS
    bright_frac = float((ds > 10).mean())
    p99 = float(np.percentile(ds, 99))
    sweep_checks = {
        "has_bright_band": bright_frac > 0.005 and p99 > 15.0,
        "band_is_localized": bright_frac < 0.70,
    }
    print(f"  sweep: bright_frac={bright_frac:.4f} p99={p99:.1f} -> {sweep_checks}", flush=True)

    # --- grain：稳态主-对照差分为零均值高频噪声（预渲染纹理平铺的呼吸）。
    #     必须原生分辨率：下采样会把逐像素颗粒平均掉（见 _load_gray_native_crop）。
    Bn = _load_gray_native_crop(paths["main_b"])
    CBn = _load_gray_native_crop(p_ctrl_b)
    g = (Bn - CBn).ravel()
    grain_checks = {
        "noise_present": float(g.std()) > 2.0,
        "zero_mean": abs(float(g.mean())) < 4.0,
    }
    print(f"  grain(native crop): std={g.std():.2f} mean={g.mean():.2f} -> {grain_checks}", flush=True)

    print("[7/7] verdict", flush=True)
    checks = {**crossfade_checks, **sweep_checks, **grain_checks}
    print(f"  checks: {checks}", flush=True)
    if not all(checks.values()):
        print("pixel checks failed"); return 1

    print(f"output(main): {mp4_main}")
    print(f"output(ctrl): {mp4_ctrl}")
    print("RESULT: FX2 RENDER PASSED (crossfade + sweep + grain all in MP4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
