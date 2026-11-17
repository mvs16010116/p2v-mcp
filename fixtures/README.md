# fixtures — 可复现测试素材与验收基准

本目录为「节拍卡点图文视频 MCP」提供真实、可再生成的测试素材与第一批验收基准。
禁止用占位文件冒充素材；所有素材均由下列命令/来源真实产出。

## 素材清单

| 文件 | 用途 | 生成方式 |
|---|---|---|
| `audio/bgm_120bpm.wav` | 8 秒、120 BPM、鼓点清晰，验证 librosa 节拍检测与卡点对齐 | ffmpeg `aevalsrc` 程序化合成 |
| `images/group_10people.jpg` | 约 10 人群像，验证「锁定其中一人推近」的视觉主体聚焦 | image_gen 生成（提示词见下），PNG 转 JPG |
| `images/person_ref.jpg` | 单主体人像，验证 reference 主体锁定 | image_gen 生成（提示词见下），PNG 转 JPG |
| `images/grain.png` | 19.5 胶片颗粒：预渲染静态灰度噪声纹理，平铺在 L4 overlay 层做轻微呼吸（禁逐帧 `feTurbulence`） | Pillow + 固定 seed `20261117` 程序化生成，见下方「5) 颗粒纹理」|
| `texts/captions.json` | 10 句中文短句，用于字幕/文案绑定测试 | 手工编写，JSON 结构由脚本校验 |
| `verify_beat.py` | 节拍检测验收基准：断言 BPM ∈ [115, 125] | 本项目编写 |
| `generate_all.sh` / `generate_all.ps1` | 一键再生成所有可程序化素材并跑验收 | 本项目编写 |
| `images/_gen_group/*.png`、`images/_gen_person/*.png` | AI 生成的原始源图（保留用于溯源与重转 JPG） | image_gen 工具输出 |

## 已验收的基准结果（2026-11-17，本机环境）

- 环境：ffmpeg N-125978；Python 3.13.12；librosa 1.0.1。
- `python fixtures/verify_beat.py` → `检测 BPM：120.19`，`PASS`（时长 8.00 s / 44100 Hz / 检出 15 个节拍）。
- WAV 文件大小 705,678 字节；两张 JPG 均已生成且非空（群像约 429 KB，人像约 284 KB）。
- 人工核图：群像图可数出**恰好 10 人**，中央为醒目的红色夹克人物（便于锁定推近）；单主体图为单人正面半身像。两图右下角带生成工具的 `AI生成 / WORK BUDDY` 小水印，不影响主体锁定，但下游做像素级回归比对时需留意。

## Wave 6：真实权重实证（R14「10 人中锁定 1 人推近」）

- 模型：`PekingU/rtdetr_r50vd`（HuggingFace，**Apache-2.0**，transformers 格式），本地缓存 `fixtures/models/rtdetr_r50vd/`。**该目录是大体积模型缓存，不应提交到 git**；用下方命令复现下载即可。
- 环境：transformers 5.10.2（用 `RTDetrImageProcessorPil` 回退，无需 torchvision）、torch 2.12.0、Pillow。
- 实测结果（阈值 0.25）：真实检出 **11 个 person 框，其中 10 个置信度 ≥ 0.845**；第 11 个是置信度 0.277 的重复碎框。中央红夹克人物为 **index=1**（bbox `[0.4096, 0.1462, 0.6053, 0.9984]`，confidence 0.958），已裁出 `fixtures/reports/selected_subject_crop.jpg` 人工核对无误。
- 锁定验收：用真实 bbox 经 `lock_subject(index=1, fill=0.62, fit=contain, stage=1080×1920)` → 主体中心推近后落在 `(540, 960)`，**居中误差 0.0%**，四角全部在舞台内，scale ≤ 4。
- 诚实说明：全身框高约 0.85 画高，`contain + fill=0.62` 要求主体完整落在 62% 画高内，因此 `scale=0.727`（是**拉远**，公式行为正确）。为实证真正的「推近」，从同一真实检测框取上半身子框（高度 45%）再跑一次 → `scale=1.1878`，仍居中误差 0、contain 不切头。
- 完整报告：`fixtures/reports/detection_report.json`（含全部 bbox、focus 参数、验收布尔值与复现命令）；候选落盘 `fixtures/reports/rtdetr/subjects.json`。
- 复现命令：

```bash
python -m pip install "transformers>=4.45" torch Pillow huggingface_hub
python -c "from huggingface_hub import snapshot_download; snapshot_download('PekingU/rtdetr_r50vd', local_dir='fixtures/models/rtdetr_r50vd')"
# PowerShell:  $env:P2V_RTDETR_CHECKPOINT = 'd:/Danny/p2v/fixtures/models/rtdetr_r50vd'
# Bash:        export P2V_RTDETR_CHECKPOINT=fixtures/models/rtdetr_r50vd
python fixtures/verify_detection.py
```

依赖/权重缺失时脚本不会伪造结果：报告写 `status="skipped"` 并附 `reproduction` 步骤；`tests/test_vision_real_weights.py` 在报告缺失时明确 skip。未下载人脸 embedding 权重（许可红线），未碰深度权重（默认关闭）。

## 重新生成方法

### 1) BGM（完全程序化，可复现）

依赖：`ffmpeg`（任意含 `aevalsrc` 滤镜的版本；本机为 N-125978）。

一行命令（Bash / PowerShell 通用，注意表达式内无逗号，避免跨平台转义问题）：

```bash
ffmpeg -hide_banner -loglevel error -f lavfi -i 'aevalsrc=0.35*sin(2*PI*55*(t-0.5*floor(2*t)))*exp(-25*(t-0.5*floor(2*t)))+0.12*sin(2*PI*440*(t-0.5*floor(2*t)))*exp(-60*(t-0.5*floor(2*t)))+0.05*sin(2*PI*220*t):s=44100:d=8' -c:a pcm_s16le -ar 44100 -y fixtures/audio/bgm_120bpm.wav
```

合成原理：`floor(2*t)` 把时间切成 0.5 s 一段（120 BPM = 2 拍/秒）。每段起始处叠加一个 55 Hz 低频指数衰减包络（底鼓）和一个 440 Hz 更快衰减的瞬态（click），并叠加 220 Hz 低音量持续音保证全程有信号。librosa 的 onset 检测据此稳定锁定 120 BPM。

Windows PowerShell 若单引号传递有问题，可把 `-i` 后的表达式赋给变量再传入（见 `generate_all.ps1`）。

### 2) 图片（AI 生成，非程序化；不可由脚本重新"合成"）

使用 `image_gen` 工具生成，保存目录分别为 `fixtures/images/_gen_group/` 与 `fixtures/images/_gen_person/`。原始提示词（逐字）：

- 群像（`group_10people.jpg`）：
  > A clear realistic photograph of exactly ten people standing side by side in a single row outdoors during daytime, full upper bodies visible, evenly spaced, all facing the camera, casual clothing. One person near the center wears a bright red jacket and stands out clearly from the others who wear muted blue, grey and black clothing. Neutral friendly expressions, soft natural lighting, sharp focus, high resolution, documentary photo style.
  > size=1536x1024, quality=high, output_dir=`fixtures/images/_gen_group`

- 单主体（`person_ref.jpg`）：
  > A clean realistic portrait photograph of a single adult person, front-facing, head and shoulders visible, neutral plain light grey studio background, soft even lighting, sharp focus on the face, natural expression, high resolution, professional headshot style, no other people in frame.
  > size=1024x1536, quality=high, output_dir=`fixtures/images/_gen_person`

转成目标文件名（本机已执行）：

```bash
ffmpeg -hide_banner -loglevel error -y -i fixtures/images/_gen_group/<生成的png文件名>.png -q:v 2 fixtures/images/group_10people.jpg
ffmpeg -hide_banner -loglevel error -y -i fixtures/images/_gen_person/<生成的png文件名>.png -q:v 2 fixtures/images/person_ref.jpg
```

`generate_all.sh` / `generate_all.ps1` 会在目标 JPG 缺失时，自动从上述 `_gen_*` 目录取首张 PNG 完成转换（不重复调用 AI）。

**如果 image_gen 不可用 / 生成失败**：不要放占位图伪装成功。替代方案是从明确许可的公开图库获取真实照片，例如 Pexels 或 Unsplash（免费许可，使用前确认具体条款），下载后按下列命令取名：

```bash
curl -L -o fixtures/images/group_10people.jpg '<公开图库直接图片URL>'
curl -L -o fixtures/images/person_ref.jpg '<公开图库直接图片URL>'
```

注意：必须挑选确实约 10 人、且其中一人可明显区分的群像，否则无法验证「锁定其中一人推近」；随机图片服务（如 picsum）只能用于管线冒烟测试，不能用于主体锁定验证。若采用替代素材，请把来源 URL 和许可补记到本文件。

### 3) 文案

`texts/captions.json` 为纯文本资产；`generate_all.*` 会校验它是合法 JSON 且 `captions` 恰好 10 句。

### 4) 颗粒纹理 `images/grain.png`（完全程序化，可复现）

依赖：`Pillow`。生成 256×256 灰度噪声（mid-gray 128 为 overlay 中性值，幅度 ±56），
固定 `Random(20261117)` seed，禁未 seed 随机；只生成一次作静态纹理，
渲染时由模板平铺 + opacity 呼吸（0.10→0.14），**不逐帧跑 `feTurbulence`**（性能红线，方案 19.5）。

```bash
python -c "import random; from PIL import Image; r=random.Random(20261117); n=256; im=Image.new('L',(n,n)); im.putdata([max(0,min(255,128+r.randint(-56,56))) for _ in range(n*n)]); im.save('fixtures/images/grain.png')"
```

验收基准：256×256 / mode L / 灰度均值 ≈128.12 / 值域 [72,184] / PNG SHA256 前 16 位 `3f5ce64385e0e711`。

### 5) 一键再生成 + 验收

```bash
# Linux / macOS
bash fixtures/generate_all.sh

# Windows PowerShell
powershell -File fixtures/generate_all.ps1
```

脚本流程：建目录 → 合成 BGM → 校验文案 →（必要时）从 AI 源图转 JPG → 运行 `verify_beat.py`。
验收退出码：`0` = PASS，`1` = BPM 越界（FAIL），`2` = 音频缺失/读取失败，`3` = 缺 librosa（SKIP，**不得视为通过**）。

单独跑节拍验收：

```bash
python -m pip install librosa   # 首次需要
python fixtures/verify_beat.py
```

## 许可声明

- `audio/bgm_120bpm.wav`、`images/grain.png`、`texts/captions.json`、`verify_beat.py`、`generate_all.sh`、`generate_all.ps1` 及本 README：由本项目原创程序化生成，按 [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) 贡献到公共领域，可自由复制、修改、用于测试。
- `images/*.jpg` 及其 `_gen_*` 源图：由 AI 图像生成工具产出，未引入第三方版权照片；其可商用性取决于所用生成服务的服务条款，使用者请自行确认。若日后改用公开图库照片，须在上方补记来源与许可。
- 工具依赖：`ffmpeg` 为 GPL/LGPL 许可的独立工具，仅用于生成本素材，其许可不传染生成产物；`librosa`（ISC/BSD 类许可）仅作开发期验收依赖。
