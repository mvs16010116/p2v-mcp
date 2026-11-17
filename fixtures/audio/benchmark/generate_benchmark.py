#!/usr/bin/env python3
"""生成节拍基准音频（ffmpeg aevalsrc 程序化合成，完全可复现）。

5 段：90 / 120 / 140 BPM 清晰鼓点，1 段弱鼓点（amplitude 低、长衰减），
1 段变速（前段 90 BPM → 后段 140 BPM，拼接）。

每段 12 秒，44.1 kHz 单声道 PCM。命令经 subprocess list 调用，shell=False（安全）。
用法：python fixtures/audio/benchmark/generate_benchmark.py
"""
from __future__ import annotations

import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SR = 44100
DUR = 12.0


def _kick_expr(bpm: float, amp: float, decay: float, click: float, dur: float = DUR) -> str:
    """一段周期鼓点：底鼓(低频指数衰减) + click(高频快衰减) + 低音量持续床。"""
    interval = 60.0 / bpm
    phase = f"(t-{interval:.6f}*floor({bpm / 60.0:.6f}*t))"
    return (
        f"aevalsrc={amp:.4f}*sin(2*PI*55*{phase})*exp(-{decay:.2f}*{phase})"
        f"+{click:.4f}*sin(2*PI*880*{phase})*exp(-{decay + 35:.2f}*{phase})"
        f"+0.03*sin(2*PI*220*t):s={SR}:d={dur:.1f}"
    )


def _run(args: list[str], out: Path) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", *args, "-y", str(out)],
                   check=True, shell=False, timeout=60)


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)

    # 1-3) 清晰鼓点 90 / 120 / 140 BPM
    for bpm in (90, 120, 140):
        _run(["-f", "lavfi", "-i", _kick_expr(bpm, 0.35, 25, 0.12)],
             HERE / f"beat_{bpm}bpm.wav")

    # 4) 弱鼓点：底鼓幅度大幅降低、衰减更慢（onset 不尖锐），持续床占比高
    weak = (
        "aevalsrc=0.04*sin(2*PI*55*(t-0.5*floor(2*t)))*exp(-8*(t-0.5*floor(2*t)))"
        "+0.35*sin(2*PI*220*t):s=44100:d=12"
    )
    _run(["-f", "lavfi", "-i", weak], HERE / "weak_beat.wav")

    # 5) 变速：前 6s 90 BPM，后 6s 140 BPM，两段拼接
    seg_a = HERE / "_seg90.wav"
    seg_b = HERE / "_seg140.wav"
    _run(["-f", "lavfi", "-i", _kick_expr(90, 0.35, 25, 0.12, 6.0)], seg_a)
    _run(["-f", "lavfi", "-i", _kick_expr(140, 0.35, 25, 0.12, 6.0)], seg_b)
    _run(["-i", str(seg_a), "-i", str(seg_b),
          "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1"],
         HERE / "tempo_shift.wav")
    seg_a.unlink(missing_ok=True)
    seg_b.unlink(missing_ok=True)

    for wav in sorted(HERE.glob("*.wav")):
        print(f"OK {wav.name} ({wav.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
