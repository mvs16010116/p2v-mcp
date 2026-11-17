#!/usr/bin/env python3
"""节拍检测验收基准（第一道关卡）。

用 librosa 检测 fixtures/audio/bgm_120bpm.wav 的 BPM，断言落在 [115, 125] 区间。

退出码：
  0 = PASS（BPM 在验收区间内）
  1 = FAIL（BPM 超出验收区间）
  2 = ERROR（音频文件不存在或无法读取）
  3 = SKIP（缺少 librosa / numpy 依赖，未做验收，禁止当作通过）

用法：
  python fixtures/verify_beat.py
"""
from __future__ import annotations

import sys
from pathlib import Path

LOW_BPM = 115.0
HIGH_BPM = 125.0
AUDIO_PATH = Path(__file__).resolve().parent / "audio" / "bgm_120bpm.wav"


def main() -> int:
    if not AUDIO_PATH.is_file():
        print(f"ERROR: 音频不存在：{AUDIO_PATH}")
        print("       请先运行 generate_all.sh / generate_all.ps1 生成素材。")
        return 2

    try:
        import librosa
        import numpy as np
    except ImportError as exc:
        print(f"SKIP: 缺少依赖 {exc.name}，未做节拍验收（不可视为 PASS）。")
        print("      安装命令：python -m pip install librosa")
        return 3

    try:
        y, sr = librosa.load(str(AUDIO_PATH), sr=None, mono=True)
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
    except Exception as exc:  # 解码/分析失败按错误处理，不伪装成通过
        print(f"ERROR: librosa 分析失败：{exc}")
        return 2

    # librosa 1.x 的 tempo 是 ndarray，旧版是标量；统一取第一个值。
    bpm = float(np.asarray(tempo).reshape(-1)[0])

    print(f"文件：{AUDIO_PATH}")
    print(f"采样率：{sr} Hz，时长：{len(y) / sr:.2f} s，检测到的节拍数：{len(beats)}")
    print(f"检测 BPM：{bpm:.2f}，验收区间：[{LOW_BPM:.0f}, {HIGH_BPM:.0f}]")

    if not (LOW_BPM <= bpm <= HIGH_BPM):
        print(f"FAIL: BPM {bpm:.2f} 不在 [{LOW_BPM:.0f}, {HIGH_BPM:.0f}] 内。")
        return 1

    print("PASS: 节拍检测通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
