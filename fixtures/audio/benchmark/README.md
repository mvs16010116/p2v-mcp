# 节拍标准测试集（beat benchmark）

5 段程序化合成音频，用于量化当前 `detect_beats` 的真实准确率。全部由 ffmpeg 合成，
无外部版权素材，可完全复现。生成器：`generate_benchmark.py`（subprocess list、shell=False）。

## 素材清单

| 文件 | 时长 | 设计 BPM | 用途 |
|---|---|---|---|
| `beat_90bpm.wav` | 12s | 90 | 清晰鼓点，慢速 |
| `beat_120bpm.wav` | 12s | 120 | 清晰鼓点，中速 |
| `beat_140bpm.wav` | 12s | 140 | 清晰鼓点，快速 |
| `weak_beat.wav` | 12s | 120 | 弱鼓点（底鼓幅度 0.04、衰减慢、220Hz 持续床占比高） |
| `tempo_shift.wav` | 12s | 90→140 | 变速：前 6s 90 BPM + 后 6s 140 BPM 拼接 |

## 生成命令（ffmpeg，可逐段复现）

一键：`python fixtures/audio/benchmark/generate_benchmark.py`（生成上述 5 个 wav）。

单段清晰鼓点公式为 `aevalsrc` 周期包络：`amp*sin(2π·55·φ)·exp(-decay·φ) + click*sin(2π·880·φ)·exp(-(decay+35)·φ) + 0.03*sin(2π·220·t)`，
其中 `φ = t - (60/BPM)*floor((BPM/60)*t)`。以 120 BPM 为例（Bash/PowerShell 通用）：

```bash
ffmpeg -hide_banner -loglevel error -f lavfi -i 'aevalsrc=0.3500*sin(2*PI*55*(t-0.500000*floor(2.000000*t)))*exp(-25.00*(t-0.500000*floor(2.000000*t)))+0.1200*sin(2*PI*880*(t-0.500000*floor(2.000000*t)))*exp(-60.00*(t-0.500000*floor(2.000000*t)))+0.03*sin(2*PI*220*t):s=44100:d=12.0' -c:a pcm_s16le -y beat_120bpm.wav
```

弱鼓点（幅度大幅降低、衰减慢、持续床高）：

```bash
ffmpeg -hide_banner -loglevel error -f lavfi -i 'aevalsrc=0.04*sin(2*PI*55*(t-0.5*floor(2*t)))*exp(-8*(t-0.5*floor(2*t)))+0.35*sin(2*PI*220*t):s=44100:d=12' -c:a pcm_s16le -y weak_beat.wav
```

变速（两段拼接）：

```bash
ffmpeg -f lavfi -i '<上公式 BPM=90 d=6>' -f lavfi -i '<上公式 BPM=140 d=6>' -filter_complex '[0:a][1:a]concat=n=2:v=0:a=1' -y tempo_shift.wav
```

完整、无转义歧义的命令以 `generate_benchmark.py` 为准（它把公式参数化后传给 ffmpeg）。

## 验收

```bash
python fixtures/verify_beat_benchmark.py
```

断言（阈值测量前固定，不按实测反推放宽）：清晰/弱鼓点段 BPM 相对误差 ≤ 5%、节拍对齐
precision/recall ≥ 0.85（对齐容差 ±15% 拍间隔）；变速段无单一真值，记为 limitation 如实报告。
报告落盘 `fixtures/reports/beat_benchmark.json`。退出码 0=PASS / 1=FAIL / 2=ERROR / 3=SKIP。

## 许可

全部为本项目用 ffmpeg 程序化原创合成，无第三方版权输入，按 CC0 1.0 贡献；ffmpeg 为 GPL/LGPL
独立工具，仅用于生成素材，不传染产物。
