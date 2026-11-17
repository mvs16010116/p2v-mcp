# 节拍卡点图文视频 · 标准评测集规格

> **重要声明：这是「项目自评基准（self-benchmark）」，不是第三方评测。**
> 评测集、脚本、阈值与打分口径均由本项目自行设计并执行；阈值在测量前按方案文档固定，
> 未针对本机实测结果反推放宽；`SKIP` / `limitation` / 未验证项一律如实标注，**绝不以 mock
> 冒充 PASS**。本文件供回归与复现使用，不构成独立第三方背书。
> 真正独立的外部对标见 `docs/业界对标评估.md`（由未参与实现的 benchmark agent 产出，
> 其结论与本项目自评分开记录）。

评估日期：2026-11-17　|　环境：Windows 11 / Python 3.13.12 / librosa 0.11.0 / ffmpeg N-125978 /
HyperFrames CLI 0.9.59（经 `npx` 调用，`docker=False`）。

---

## 1. 评测目标与能力维度

| 能力维度 | 评测对象 | 期望能力（目标态） | 本基准当前状态 |
|---|---|---|---|
| 节拍检测精度 | `src/analyzer/beat_detector.py` | 恒定/弱鼓点 BPM 相对误差 ≤5%，节拍对理想网格 precision/recall ≥0.85 | 已测，4 pass + 1 limitation |
| 编排不变量 | `src/timeline/builder.py` | 镜头数=`len(beats)-1`、首镜起 0、`T≥2×T_in`、`duration=end-start` | 有单元测试断言；无独立基准脚本 |
| 主体锁定 | `src/vision/` | index/text/reference 三路锁定，居中误差 ≤0.05、`contain` 不越界、text IoU≥0.5 | index 已实证；text 已测（查询词敏感）；reference 未测 |
| 特效真进成片 | `src/builder/template/composition.html.j2` | HTML 携带参数 + 成片非空 + 抽帧像素断言成立 | irregular/flash-in/grade/crossfade/sweep/grain 已实证；shatter/光晕/粒子未做 |
| 渲染工程严谨度 | `src/backend/hyperframes_backend.py` | 同 t 两次渲染 SHA256 一致、时长误差 ≤1 帧、首帧非纯黑 | 时长/首帧已验；同 t SHA256 本环境 **SKIP**（无 Linux Docker） |
| Agent 可调用性 | `main.py` | 10 个 MCP 工具注册、契约强校验、统一 `{ok,data,error}` + 9 错误码 | 有 pipeline 测试覆盖 |

> 「画质上限」不作为本基准的通过项：已落地特效均为 CSS / filter / SVG 级，
> 非 AE 插件级、非生成式，口径见 `docs/业界对标评估.md`。

---

## 2. 评测集规格

### 2.1 音频类型

| 文件 | 类型 | 真值 | 用途 |
|---|---|---|---|
| `fixtures/audio/benchmark/beat_90bpm.wav` | 恒定 BPM | 90 | 低速鼓点检测精度 |
| `fixtures/audio/benchmark/beat_120bpm.wav` | 恒定 BPM | 120 | 标准鼓点检测精度 |
| `fixtures/audio/benchmark/beat_140bpm.wav` | 恒定 BPM | 140 | 高速鼓点检测精度/漏拍 |
| `fixtures/audio/benchmark/weak_beat.wav` | 弱鼓点 | 120 | 低信噪比 onset 鲁棒性 |
| `fixtures/audio/benchmark/tempo_shift.wav` | 变速 | 0–6s:90 / 6–12s:140 | 已知局限：单一 BPM 无法代表分段真值，记为 `limitation` |
| `fixtures/audio/bgm_120bpm.wav` | 8s/120BPM 程序化 BGM | 120（实测 120.19 / 端到端 117.45） | 所有 e2e 渲染案例的真实 BGM |

合成音频由 `fixtures/audio/benchmark/generate_benchmark.py` 程序化生成（说明见该目录 `README.md`），
BGM 由 `fixtures/generate_all.ps1` / `generate_all.sh` 中的 ffmpeg 命令生成。均为原创可再生产物。

### 2.2 图片类型

| 文件 | 类型 | 期望能力 |
|---|---|---|
| `fixtures/images/group_10people.jpg` | 约 10 人群像，中央红夹克可明显区分 | 群像主体检测 + `index=1` 锁定推近 + text selector 属性查询 |
| `fixtures/images/person_ref.jpg` | 单人正面半身像 | 单主体画面与群像做 crossfade 对照（两张不同图） |
| `fixtures/images/grain.png` | 256×256 静态灰度噪声（seed 20261117） | 预渲染颗粒纹理平铺；值域 [72,184]、SHA256 前 16 位 `3f5ce64385e0e711` |

群像/单主体图由 image_gen 生成并转 JPG，右下角带生成工具小水印，不影响主体锁定；
像素级回归比对时需留意（见 `fixtures/README.md`）。

### 2.3 文本

`fixtures/texts/captions.json`：10 句中文化短句，用于字幕绑定与文字动画测试。

---

## 3. 打分口径（阈值测量前固定）

| 指标 | 判定规则 | 来源 |
|---|---|---|
| BPM 误差 | `abs(detected-true)/true ≤ 0.05` | 方案第 9 章通用容差 |
| 节拍网格对齐 | precision / recall ≥ 0.85，容差 ±15% 拍间隔 | 同上 |
| 主体居中 | `center_error_x/y ≤ 0.05`（归一化） | 方案 20.4 |
| 主体 `contain` | 投影四角均在舞台内、不切头 | 方案 20.4 |
| text 锁定 | `IoU ≥ 0.5` | 方案 20.8 |
| 成片时长 | 与音频时长误差 ≤ 1 帧（30fps 下 ≈0.033s） | 方案 18.3 |
| 成片画面 | 首帧非纯黑、MP4 >100KB、分辨率 1080×1920、30fps | 方案 5.7 / 19.9 |
| 特效生效 | HTML flags 全为真 **且** 抽帧像素断言成立（不能只看参数） | `docs/业界对标评估.md` 证据口径 |
| 确定性 | 同一 composition 同一 t 两次渲染逐帧 SHA256 一致 | 方案 21.3（本环境 SKIP） |
| 退出码 | 0=PASS、1=FAIL、2=ERROR/素材缺失、3=SKIP（缺依赖） | 各脚本一致约定 |

**SKIP 不是 PASS**：缺依赖/环境不满足时脚本显式退出 3 并写明原因，禁止视为通过。

---

## 4. Baseline 案例登记

> 每个案例写清「验证了什么能力 / 证据文件在哪 / 如何重跑」。
> `workspace/<task_id>/` 中的 `task_id` 除 FX2 固定 UUID 外均按运行时 stdout 打印值为准；
> `fixtures/reports/` 下为人工归档的稳定证据图/报告。

### B1 — 节拍标准测试集（5 段音频）

- **验证能力**：恒定 BPM 90/120/140、弱鼓点、变速段的检测精度与网格对齐；变速段如实记为 `limitation`。
- **证据**：`fixtures/reports/beat_benchmark.json`（summary: total=5 / pass=4 / fail=0 / limitation=1 / overall=pass；含逐例 BPM 误差、precision/recall 与结论）。
- **重跑**：`python fixtures/verify_beat_benchmark.py`（约数秒；退出码 0/1/2/3 见第 3 节）。

### B2 — 主体检测与锁定（真实权重）

- **验证能力**：RT-DETR 真实检出 10 人（10 框 conf≥0.845）、`index=1` 红夹克锁定、居中误差 0、`contain` 不越界；对照全身框 `scale=0.727`（拉远）与上半身子框 `scale=1.1878`（真推近）。
- **证据**：`fixtures/reports/detection_report.json`、`fixtures/reports/rtdetr/subjects.json`、`fixtures/reports/selected_subject_crop.jpg`。
- **重跑**：先下载权重并设环境变量（命令见 `fixtures/reports/detection_report.json` 的 `reproduction` 字段），再 `python fixtures/verify_detection.py`。权重 `fixtures/models/rtdetr_r50vd/` 不进 git。

### B3 — text selector IoU

- **验证能力**：Grounding DINO 离线出框，`query="a person in red"` 对红夹克 IoU=0.987、`query="red"` IoU=0.985 均 ≥0.5；`query="red jacket"` IoU=0（**查询词敏感，非通用锁定**，如实记录）。
- **证据**：`fixtures/reports/text_selector_iou_report.json`（status=passed，含 3 组 trial、ground_truth 与 best）。
- **重跑**：下载 `IDEA-Research/grounding-dino-tiny` 并设 `P2V_GROUNDING_DINO_CHECKPOINT`（命令见报告 `reproduction`），再 `python fixtures/verify_text_selector.py`。

### B4 — 端到端真实出片（全链基线）

- **脚本**：`fixtures/e2e_real_render.py`
- **验证能力**：`validate_assets → detect_beats → build_timeline(7 镜) → generate_composition → HyperFrames CLI → ffmpeg 混音 → 三项校验` 全链真实跑通；1080×1920 / 30fps / 8s MP4 非空，BPM 117.45。
- **证据**：成片 `workspace/<task_id>/out.mp4`（约 2.7–2.85MB，如 `workspace/186feb65-4c3d-40c6-941d-6909d28c2d69/out.mp4`）；抽帧 `fixtures/reports/e2e_frame.png`。
- **重跑**：`python fixtures/e2e_real_render.py`（本机 Windows `docker=False`，约 30s；成功打印 `RESULT: REAL RENDER PASSED`）。

### B5 — 主体推近进成片

- **脚本**：`fixtures/e2e_focus_render.py`
- **验证能力**：真实红夹克 bbox `[0.4096,0.1462,0.6053,0.9984]` 走完整链；HTML 中 `focus_render.enable=true, scale≈1.188`；成片推近居中放大、头部完整；转场切点黑闪由 `bg-layer` 兜底消除。
- **证据**：成片 `workspace/<task_id>/focus_out.mp4`（如 `workspace/fcac7fc3-9c88-4a42-b071-4c415d540ded/focus_out.mp4`）；抽帧 `fixtures/reports/focus_before.png`（入场前全景）、`fixtures/reports/scan_2.png`（2.0s 推近居中放大，清晰证据）；`fix_2.3.png`/`fix_2.4.png`/`fix_2.5.png`（切点模糊兜底）。
- **注意**：命名 `focus_after.png` 的抽帧近乎纯黑，**不作为推近证据**。
- **重跑**：`python fixtures/e2e_focus_render.py`（打印 `RESULT: FOCUS RENDER PASSED`）。

### B6 — 电影级特效子集进成片

- **脚本**：`fixtures/e2e_fx_render.py`
- **验证能力**：`irregular` 12 顶点不规则裁切 + `flash-in` 闪入过曝 + `grade=teal-orange` 四档调色真进成片；HTML 携带 flags；抽帧像素断言（frame0 亮度 15.3 vs 稳态 49.3、入场帧 59.3、稳态非灰度）。
- **证据**：成片 `workspace/<task_id>/fx_out.mp4`（如 `workspace/0102f303-3b91-444d-9dfa-5ba733c3b176/fx_out.mp4`）；抽帧 `fixtures/reports/fx_view_0.15.png`、`fixtures/reports/fx_view_1.5.png`（目视确认斜切轮廓与青橙调色）；脚本自身输出在 `workspace/<task_id>/composition/fx_frames/{frame0,enter,steady}.png`。
- **重跑**：`python fixtures/e2e_fx_render.py`（打印 `RESULT: FX RENDER PASSED`）。

### B7 — 第二梯队特效：crossfade + 扫光 + 颗粒

- **脚本**：`fixtures/e2e_fx2_render.py`
- **验证能力**：两张不同图（群像/单人）切点 crossfade 线性混合（alpha≈0.715、方向相关 0.949、mad_ab=76.3、非纯黑）；扫光主/对照差分亮带占比 7.9%、p99=28；颗粒原生分辨率零均值噪声 std=3.31。主/对照双渲染分离像素证据，非 mock。
- **证据**：成片 `workspace/a1f20000-0000-4000-8000-000000000001/fx2_main.mp4`（主）与 `workspace/a1f20000-0000-4000-8000-000000000002/fx2_ctrl.mp4`（对照）；抽帧 `workspace/a1f20000-0000-4000-8000-000000000001/composition/fx2_frames/{a,x,sweep,b,ctrl_sweep,ctrl_b}.png`。
- **重跑**：`python fixtures/e2e_fx2_render.py`（约 29.5s + 26.4s，打印 `RESULT: FX2 RENDER PASSED`）；免重渲染复分析用 `python fixtures/e2e_fx2_render.py --reuse`（需两份 MP4 与工程已存在）。

### B8 — 像素级确定性回归（未通过，诚实登记）

- **脚本**：`fixtures/verify_determinism.py`
- **验证能力（目标）**：同一 composition 同一 t 两次 Docker 渲染逐帧 SHA256 一致。
- **证据**：`fixtures/reports/determinism_report.json` 与 `.md`，status=**skipped**，原因：win32 非 Linux、docker 不在 PATH；无帧 hash、未采集 runtime bundle sha256。**SKIP 不是 PASS，本环境未完成该项验收。**
- **重跑**：在 Linux + Docker 环境执行报告 `reproduction` 字段命令，或 push 触发 `.github/workflows/render-determinism.yml`（ubuntu-24.04 runner）。

### B9 — 碎裂炸现真进成片

- **脚本**：`fixtures/e2e_shatter_render.py`
- **验证能力**：6×6 碎片由确定性 LCG 生成偏移、错峰飞入就位后淡出交回 img；主/对照双渲染分离像素证据。入场帧主/对照 diff=48.27（碎片飞散）、稳态 diff=8.83（碎片已淡出）、亮度 75 非黑。
- **证据**：成片 `workspace/b4f30000-...0001/shatter_main.mp4` 与 `...0002/shatter_ctrl.mp4`；抽帧在 `...0001/composition/shatter_frames/`。
- **重跑**：`python fixtures/e2e_shatter_render.py`（或 `--reuse` 免重渲染复分析）。

### B10 — 多主体漫游 + 分层视差真进成片

- **脚本**：`fixtures/e2e_tour_parallax_render.py`
- **验证能力**：真实 RT-DETR bbox（index1→index4），tour 两点漫游 + 视差背景层；镜头内主/对照 diff_mid=19.08→diff_late=32.20 单调推进、非黑。
- **证据**：成片 `workspace/a3f30000-...0001/tour_main.mp4` 与 `...0002/tour_ctrl.mp4`。
- **重跑**：`python fixtures/e2e_tour_parallax_render.py`（或 `--reuse`）。

### B11 — 扩展特效（逐字动画 + 画面抖动）

- **脚本**：`fixtures/e2e_fx_extended_render.py`
- **验证能力**：per-char 逐字拆 span + BeatImpact shake 抖动；shake 冲击帧主/对照 diff=16.9；per-char 结构断言通过。**chromatic 色差代码已实现但单帧像素证据未达标，按未验证登记。**
- **证据**：成片 `workspace/c5f30000-...0001/fx_ext_main.mp4` 与 `...0002/fx_ext_ctrl.mp4`。
- **重跑**：`python fixtures/e2e_fx_extended_render.py`（或 `--reuse`）。

---

## 5. 如何复现（环境前置）

```powershell
# 1. 依赖（版本锁定见 requirements.txt / pyproject.toml）
python -m pip install -r requirements.txt
# 2. 素材（已存在则跳过；Windows 用 ps1）
powershell -File fixtures/generate_all.ps1
# 3. 逐项跑 baseline（各自命令见第 4 节）
python fixtures/verify_beat_benchmark.py
python fixtures/e2e_real_render.py
python fixtures/e2e_focus_render.py
python fixtures/e2e_fx_render.py
python fixtures/e2e_fx2_render.py
python templates/validate_templates.py   # 模板契约校验（8/8 PASS）
```

需要 HyperFrames CLI（经 `npx hyperframes@0.9.59`）、ffmpeg 6.1+、中文字体。
视觉类 B2/B3 还需下载对应模型权重（报告内有命令，权重不进 git）。

---

## 6. 已知边界与诚实声明

1. 本基准为项目自评，样本量小（音频 6 段、图片 3 张），不足以代表公开数据集的统计显著性。
2. 渲染类案例均在本机 Windows、`docker=False` 跑通；**同 t 两次 SHA256 一致（B8）仍未验证**，不宣称通过。
3. `shatter` 碎裂炸现、光晕、真粒子/体积光、多主体漫游等仍是 DOM 钩子或未做，未纳入通过项。
4. text selector 对查询词敏感（B3），reference 锁定与多主体 IoU 未测。
5. 模板库（见 `templates/`）的参数通过契约校验，但 `04_shatter_flash.json` 标注为 `contract_only`，
   不承诺碎裂像素证据。
6. 完整、独立的外部对标评分与差距清单见 `docs/业界对标评估.md`；本文件不与之互相替代。
