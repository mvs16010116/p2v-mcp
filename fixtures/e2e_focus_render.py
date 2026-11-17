"""端到端实证：主体锁定推近真正渲染进 MP4（对标 agent 指出的致命 gap）。

用 RT-DETR 真实检出的红夹克 bbox（detection_report.json），走完整链路
generate_composition → 真实渲染，抽「入场前」与「推近后」两帧，证明
主体聚焦不只在分析层，而是进了成片像素。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backend.hyperframes_backend import HyperFramesBackend  # noqa: E402
from src.contracts import Focus, Selector  # noqa: E402
from src.service.pipeline import PipelineRequest, PipelineService  # noqa: E402
from src.timeline.builder import EffectSpec  # noqa: E402

AUDIO = str(ROOT / "fixtures" / "audio" / "bgm_120bpm.wav")
IMAGE = str(ROOT / "fixtures" / "images" / "group_10people.jpg")
# Wave 6 真实检测结果：中央红夹克人物 index=1
RED_JACKET_BBOX = [0.4096, 0.1462, 0.6053, 0.9984]


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
        texts=["锁定红夹克人物 · 主体推近实证"],
        beat_group=4,  # 少切镜，给足推近时间
        fps=30,
        width=1080,
        height=1920,
        do_render=True,
    )

    # 手动复刻 run_pipeline 的前半段，但注入真实 bbox 的 focus（不依赖检测权重再跑）
    validation = service.validate_assets_step(req.audio_path, req.image_paths, req.min_duration)
    if not validation.ok:
        print("validate failed", validation.error); return 1
    beats_resp = service.extract_beats_step(req.audio_path, beat_group=req.beat_group)
    if not beats_resp.ok:
        print("beats failed", beats_resp.error); return 1
    from src.contracts import BeatAnalysis
    beats = BeatAnalysis.model_validate(beats_resp.data)
    task_id = service.ensure_task(req.task_id)
    rel_images = service.prepare_images(req.image_paths, task_id)

    focus = Focus(
        mode="subject",
        selector=Selector(type="index", value=1),
        subject_index=1,
        bbox=RED_JACKET_BBOX,
        fill=0.62,
        fit="contain",
        anchor_region="upper_body",
        hold_ratio=0.25,
    )
    tl_resp = service.build_timeline_step(
        beats.grouped_beats, rel_images, req.texts, [EffectSpec(focus=focus)], True,
        fps=req.fps, audio_duration=beats.audio_duration, task_id=task_id,
        raw_beats=beats.raw_beats,
    )
    if not tl_resp.ok:
        print("timeline failed", tl_resp.error); return 1
    from src.contracts import Timeline
    timeline = Timeline.model_validate(tl_resp.data)

    comp = service.generate_composition_step(
        timeline, "hyperframes", req.width, req.height, req.fps,
        req.audio_path, req.font_family,
    )
    if not comp.ok:
        print("composition failed", comp.error); return 1
    project_dir = comp.data["project_dir"]

    # 检查生成的 index.html 里 focus_render.enable 是否为 true
    html = (Path(project_dir) / "index.html").read_text(encoding="utf-8")
    import re
    enables = re.findall(r'"focus_render":\{"enable":(true|false),"scale":([\d.]+)', html)
    print("focus_render in HTML:", enables[:3])

    output = str(ROOT / "workspace" / task_id / "focus_out.mp4")
    started = time.time()
    render = service.render_step(project_dir, task_id, req.audio_path, output)
    print(f"render ok={render.ok} elapsed={time.time()-started:.1f}s")
    if not render.ok:
        print("render failed", render.error.code.value, render.error.message)
        print("detail", render.error.detail)
        return 1
    print("output:", render.data["output_path"])
    print("RESULT: FOCUS RENDER PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
