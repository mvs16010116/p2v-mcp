# 真实端到端案例库

3 个从「输入素材 → 成片」的真实案例。**不编造成片**：每个案例都引用已存在的
`fixtures/e2e_*.py` 脚本与 workspace 中已产出的 MP4 / 抽帧路径；标注「可复现」者表示
运行对应脚本即可重新生成同路径产物（固定 UUID 的场景连工程目录都一致）。

运行前置见 `docs/BENCHMARK.md` 第 5 节（HyperFrames CLI / ffmpeg / 中文字体）。
本机 Windows 使用 `docker=False`，像素级严格复现需 Linux Docker（见 B8）。

---

## 案例 1 — 8 秒 BGM + 群像图的节拍卡点成片

- **脚本**：`fixtures/e2e_real_render.py`（成功标记 `RESULT: REAL RENDER PASSED`）
- **输入素材**：
  - BGM：`fixtures/audio/bgm_120bpm.wav`（8s / 120BPM 程序化合成）
  - 图片：`fixtures/images/group_10people.jpg`（约 10 人，中央红夹克）
  - 文案：`["节拍卡点 · 真实渲染实证", "锁定群像中的一个人", "HyperFrames 端到端出片"]`
- **关键参数**：`beat_group=2`、`fps=30`、`1080×1920`、`loop_assets=true`、`docker=False`；
  `PipelineRequest(do_render=True)` 走完整 7 步链（方案第 12 章）。
- **输出**：
  - 成片：`workspace/<task_id>/out.mp4`（1080×1920 / 30fps / 8.0s，约 2.7–2.85MB；
    已存在实例：`workspace/186feb65-4c3d-40c6-941d-6909d28c2d69/out.mp4`）
  - 抽帧证据：`fixtures/reports/e2e_frame.png`（首帧目视：群像图 + 红夹克 + 中文字幕清晰）
- **过程实证**：`extract_audio_beats` 实测 BPM 117.45，`build_timeline` 生成 7 镜，
  `render_step` 返回 `duration≈8.0`、`render_time_sec≈29.7`、MP4 文件非空。
- **复现**：`python fixtures/e2e_real_render.py`。`<task_id>` 每次新建，以 stdout 的
  `task_id=` / `output=` 行为准。
- **对应模板**：`templates/01_beat_basic.json`。

---

## 案例 2 — 群像中锁定红夹克并推近进成片

- **脚本**：`fixtures/e2e_focus_render.py`（成功标记 `RESULT: FOCUS RENDER PASSED`）
- **输入素材**：
  - BGM：`fixtures/audio/bgm_120bpm.wav`
  - 图片：`fixtures/images/group_10people.jpg`
  - 真实检测框：红夹克 `bbox=[0.4096, 0.1462, 0.6053, 0.9984]`（来自
    `fixtures/reports/detection_report.json` 的 RT-DETR 实证，index=1，conf 0.958）
  - 文案：`["锁定红夹克人物 · 主体推近实证"]`
- **关键参数**：`beat_group=4`（少切镜，给足推近时间）、`fps=30`、`1080×1920`；
  `Focus(mode="subject", selector=Selector(type="index", value=1), subject_index=1,
  bbox=RED_JACKET_BBOX, fill=0.62, fit="contain", anchor_region="upper_body",
  hold_ratio=0.25)`。脚本手动复刻 pipeline 前半段并注入真实 bbox，避免依赖检测权重再跑。
- **输出**：
  - 成片：`workspace/<task_id>/focus_out.mp4`（如
    `workspace/fcac7fc3-9c88-4a42-b071-4c415d540ded/focus_out.mp4`，约 2.5–2.67MB）
  - 抽帧证据：`fixtures/reports/focus_before.png`（入场前全景）与
    `fixtures/reports/scan_2.png`（2.0s 红夹克被推近居中放大、头部完整）；
    `fixtures/reports/fix_2.3.png`/`fix_2.4.png`/`fix_2.5.png`（切点由纯黑→模糊背景兜底）
- **过程实证**：HTML 中 `"focus_render":{"enable":true,"scale":1.188...}`；
  渲染 `ok=True`（约 28.3s）；upper_body 派生后 scale≈1.188，居中误差 0、contain 不切头。
- **诚实标注**：`fixtures/reports/focus_after.png` 抽帧近乎纯黑，**不是**推近证据，
  清晰证据是 `scan_2.png`。
- **复现**：`python fixtures/e2e_focus_render.py`。
- **对应模板**：`templates/03_subject_pushin.json`。

---

## 案例 3 — 电影级特效成片（不规则裁切 / 闪入 / 调色 + 溶解 / 扫光 / 颗粒）

- **脚本**：`fixtures/e2e_fx_render.py`（`RESULT: FX RENDER PASSED`）与
  `fixtures/e2e_fx2_render.py`（`RESULT: FX2 RENDER PASSED`）。
  两者是同一成片链路的两批特效，合为一个"电影感图文卡点"案例。
- **输入素材**：
  - BGM：`fixtures/audio/bgm_120bpm.wav`
  - 图片：群像 `fixtures/images/group_10people.jpg`；FX2 另用单主体
    `fixtures/images/person_ref.jpg`（两张不同图验证 crossfade）
  - 颗粒：`fixtures/images/grain.png`（FX2/模板 05/07 由脚本拷入工程 `assets/`）
  - 文案：如 `["电影级特效实证 · 不规则裁切 + 闪入 + 调色"]`、FX2 用
    `["群像 · 交叉溶解 A", "单人 · 交叉溶解 B"]`
- **关键参数**：
  - FX：`beat_group=4`；`CinematicFx(mask=irregular/0.38,
    reveal=flash-in(14px/2.4x/1.12/0.22s), grade=preset="teal-orange")`。
  - FX2：两张图；主渲染 `mask=iris/0.12, reveal=fade, grade=film+grain_png,
    light.sweep=true`；对照渲染仅关 sweep/grain；固定 UUID
    `a1f20000-0000-4000-8000-000000000001`（主）与 `...0002`（对照）。
- **输出**：
  - FX 成片：`workspace/<task_id>/fx_out.mp4`（如
    `workspace/0102f303-3b91-444d-9dfa-5ba733c3b176/fx_out.mp4`，约 1.58–1.77MB）；
    抽帧 `fixtures/reports/fx_view_0.15.png`、`fixtures/reports/fx_view_1.5.png`；
    脚本自身输出 `workspace/<task_id>/composition/fx_frames/{frame0,enter,steady}.png`。
  - FX2 成片：`workspace/a1f20000-0000-4000-8000-000000000001/fx2_main.mp4`（主）
    与 `workspace/a1f20000-0000-4000-8000-000000000002/fx2_ctrl.mp4`（对照）；
    抽帧 `workspace/a1f20000-0000-4000-8000-000000000001/composition/fx2_frames/{a,x,sweep,b}.png`
    及对照帧 `ctrl_sweep.png`/`ctrl_b.png`。
- **过程实证**：
  - FX：HTML flags 全真；frame0 亮度 15.3 vs 稳态 49.3（裁切收缩）、入场帧 59.3
    vs 稳态 49.3（闪入过曝）、稳态非灰度（青橙调色生效）。
  - FX2：切点中点帧 alpha≈0.715、方向相关 0.949（crossfade 混合而非硬切/纯黑）；
    扫光差分亮带占比 7.9%、p99=28；颗粒原生 crop 零均值噪声 std=3.31。
- **诚实标注**：三项落地均为 CSS / filter / SVG 级，**不是** AE 插件级或生成式；
  画质上限口径维持 2 分（见 `docs/业界对标评估.md`）。`shatter` 碎裂炸现等重特效未进成片。
- **复现**：`python fixtures/e2e_fx_render.py`；
  `python fixtures/e2e_fx2_render.py`（免重渲染复分析加 `--reuse`）。
- **对应模板**：`templates/02_cinematic_teal_orange.json`、
  `templates/05_film_noir.json`、`templates/07_crossfade_dream.json`。

---

## 案例 4 — 碎裂炸现成片（6×6 碎片飞入）

- **脚本**：`fixtures/e2e_shatter_render.py`（`RESULT: SHATTER RENDER PASSED`）。
- **输入**：BGM `bgm_120bpm.wav`、群像 `group_10people.jpg`；首镜配
  `CinematicFx(shatter=enable, 6×6, stagger=0.018)`，对照渲染关闭 shatter。
- **成片**：`workspace/b4f30000-0000-4000-8000-000000000001/shatter_main.mp4`（主），
  `...00000002/shatter_ctrl.mp4`（对照）。
- **实证**：HTML 含碎裂层 DOM + 确定性 LCG；入场帧主/对照 diff=48.27（碎片飞散），
  稳态 diff=8.83（碎片淡出交回 img），亮度 75 非黑——碎片真的飞入又归位。

## 案例 5 — 多主体漫游 + 分层视差成片

- **脚本**：`fixtures/e2e_tour_parallax_render.py`（`RESULT: TOUR + PARALLAX RENDER PASSED`）。
- **输入**：真实 RT-DETR bbox（index1 红夹克 → index4 右侧人）；主渲染 tour+视差，
  对照单点聚焦。
- **成片**：`workspace/a3f30000-...0001/tour_main.mp4`（主），`...0002/tour_ctrl.mp4`（对照）。
- **实证**：镜头内主/对照 diff_mid=19.08 → diff_late=32.20 单调拉大，证明焦点从 A 漫游到 B；
  非黑、结构断言全绿。

## 案例 6 — 扩展特效成片（逐字动画 + 画面抖动）

- **脚本**：`fixtures/e2e_fx_extended_render.py`（`RESULT: EXTENDED FX RENDER PASSED`）。
- **输入**：构造 Timeline（per-char 文字 + BeatImpact shake/chromatic），对照普通文字无冲击。
- **成片**：`workspace/c5f30000-...0001/fx_ext_main.mp4`（主），`...0002/fx_ext_ctrl.mp4`（对照）。
- **实证**：shake 冲击帧主/对照 diff=16.9（画面抖动生效）；per-char 拆 span 结构断言通过。
  **诚实标注**：chromatic 色差代码已实现，但单帧像素证据未达标（字幕带 diff<1），按"未验证"处理。

---

## 案例与模板 / benchmark 索引

| 案例 | 脚本 | 模板 | baseline 编号 |
|---|---|---|---|
| 1 节拍卡点出片 | `e2e_real_render.py` | `01_beat_basic` | B4 |
| 2 主体推近 | `e2e_focus_render.py` | `03_subject_pushin` | B2 + B5 |
| 3 电影级特效 | `e2e_fx_render.py` / `e2e_fx2_render.py` | `02/05/07` | B6 + B7 |
| 4 碎裂炸现 | `e2e_shatter_render.py` | `04/09` | B9 |
| 5 多主体漫游视差 | `e2e_tour_parallax_render.py` | `11` | B10 |
| 6 扩展特效 | `e2e_fx_extended_render.py` | `10` | B11 |

> 案例 1/2/3 的成片均在 workspace 中真实存在；`<task_id>` 为运行生成值，
> 文档给出的实例路径是本机已产出目录，若被 workspace 清理策略移除，重跑脚本即可再生成。
