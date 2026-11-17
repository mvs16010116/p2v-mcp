"""修复证据瑕疵：从已存在的 Wave 10 focus_out.mp4 抽 before / 2.0s 对比帧。

产物（均带明确文字标注，避免"focus_after.png 近乎纯黑"的误读）：
  fixtures/reports/focus_compare_before.png  —— t=0.00s，全景/入场前
  fixtures/reports/focus_compare_after.png   —— t=2.00s，红夹克被推近居中放大（focus_render scale=1.188）

AI 不参与：纯 ffmpeg 抽帧 + Pillow 画标注，确定性输出（同一视频同一 t 同像素）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.subprocess_helper import run_command  # noqa: E402

VIDEO = ROOT / "workspace" / "fcac7fc3-9c88-4a42-b071-4c415d540ded" / "focus_out.mp4"
TMP_DIR = ROOT / "workspace" / "_focus_compare_frames"
REPORT_DIR = ROOT / "fixtures" / "reports"
BEFORE = REPORT_DIR / "focus_compare_before.png"
AFTER = REPORT_DIR / "focus_compare_after.png"


def _extract(video: Path, at_sec: float, dest: Path) -> None:
    args = [
        "ffmpeg", "-y", "-ss", f"{at_sec:.3f}", "-i", str(video),
        "-frames:v", "1", str(dest),
    ]
    result = run_command(
        args, cwd=str(ROOT), timeout=60, log_dir=str(ROOT / "logs"),
        task_id=f"focus_compare_{dest.stem}",
    )
    if not result.ok or not dest.is_file():
        raise RuntimeError(f"抽帧失败: {dest} / {result.stderr[-500:]}")


def _label(src: Path, dest: Path, line1: str, line2: str) -> None:
    from PIL import Image, ImageDraw

    with Image.open(src) as im:
        frame = im.convert("RGB")
    # 顶部加一条高对比度标注带；只写 ASCII，避免依赖中文字体。
    band_h = 96
    labeled = Image.new("RGB", (frame.width, frame.height + band_h), (12, 12, 16))
    labeled.paste(frame, (0, band_h))
    draw = ImageDraw.Draw(labeled)
    draw.rectangle([0, 0, frame.width, band_h], fill=(18, 20, 28))
    draw.text((24, 12), line1, fill=(255, 235, 120))
    draw.text((24, 46), line2, fill=(220, 230, 245))
    labeled.save(dest)


def main() -> int:
    if not VIDEO.is_file():
        print(f"源视频不存在：{VIDEO}")
        return 1
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    raw_before = TMP_DIR / "raw_before.png"
    raw_after = TMP_DIR / "raw_after.png"
    _extract(VIDEO, 0.0, raw_before)
    _extract(VIDEO, 2.0, raw_after)

    _label(
        raw_before, BEFORE,
        "BEFORE  t=0.000s  |  source: Wave 10 focus_out.mp4",
        "Full scene at clip entry; subject focus push has not applied yet.",
    )
    _label(
        raw_after, AFTER,
        "AFTER  t=2.000s  |  focus_render.scale=1.188 (red jacket)",
        "Subject centered and pushed in; head intact (upper_body anchor, contain).",
    )

    for p in (BEFORE, AFTER):
        assert p.is_file() and p.stat().st_size > 10_000, f"对比帧缺失/过小: {p}"
        print(f"wrote {p} ({p.stat().st_size} bytes)")
    print("RESULT: FOCUS COMPARE FRAMES READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
