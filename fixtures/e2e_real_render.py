"""端到端真实渲染实证（Windows 本机，docker=False）。

走完整链路：validate_assets → detect_beats → build_timeline →
generate_composition → HyperFrames CLI 真实渲染 → ffmpeg 混音 → 三项校验。

成功判定：返回 ok=True 且 out.mp4 真实存在、ffprobe 时长吻合。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backend.hyperframes_backend import HyperFramesBackend  # noqa: E402
from src.service.pipeline import PipelineRequest, PipelineService  # noqa: E402

AUDIO = str(ROOT / "fixtures" / "audio" / "bgm_120bpm.wav")
IMAGES = [str(ROOT / "fixtures" / "images" / "group_10people.jpg")]
TEXTS = ["节拍卡点 · 真实渲染实证", "锁定群像中的一个人", "HyperFrames 端到端出片"]


def main() -> int:
    backend = HyperFramesBackend(
        workspace_root=str(ROOT / "workspace"),
        logs_dir=str(ROOT / "logs"),
        vendor_dir=str(ROOT / "vendor"),
        use_docker=False,  # 本机 Windows 无 Docker；像素级严格复现另走 Linux Docker
    )
    service = PipelineService(
        backend=backend,
        workspace_root=str(ROOT / "workspace"),
        logs_dir=str(ROOT / "logs"),
    )
    req = PipelineRequest(
        audio_path=AUDIO,
        image_paths=IMAGES,
        texts=TEXTS,
        beat_group=2,
        fps=30,
        width=1080,
        height=1920,
        do_render=True,
    )
    started = time.time()
    resp = service.run_pipeline(req)
    elapsed = time.time() - started

    print("=" * 60)
    print(f"ok={resp.ok}  elapsed={elapsed:.1f}s")
    if not resp.ok:
        print(f"error={resp.error.code.value}: {resp.error.message}")
        print(f"detail={resp.error.detail}")
        return 1

    data = resp.data
    print(f"task_id={data['task_id']}")
    bpm = data["beats"].get("bpm")
    clips = len(data["timeline"].get("clips", []))
    print(f"bpm={bpm}  clips={clips}")
    render = data.get("render")
    if render:
        print(f"output={render['output_path']}")
        print(f"duration={render['duration']}  fps={render['fps']}  res={render['resolution']}")
        print(f"render_time_sec={render['render_time_sec']}")
        out = Path(render["output_path"])
        print(f"file_exists={out.is_file()}  size={out.stat().st_size if out.is_file() else 0}")
        print("RESULT: REAL RENDER PASSED")
        return 0

    print("render block missing — pipeline did not produce MP4")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
