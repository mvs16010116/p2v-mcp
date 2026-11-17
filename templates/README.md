# Composition 模板库

8 个可复用的「节拍卡点图文视频」参数预设。每个 `0X_*.json` 都是一个完整的
`build_timeline` + `generate_composition` 参数集：BGM 节奏、mask、spring、
text_anim、kenburns、impact_profile、style_preset、cinematic_fx / focus 全部齐备。

所有参数**逐字对齐** `src/contracts.py` 的 Pydantic v2 字段，离线校验命令：

```powershell
python templates/validate_templates.py
# 期望输出: RESULT: TEMPLATE VALIDATION PASSED (8/8)
```

该脚本只 import `src.contracts` 的 model（`MaskConfig` / `ImageSpring` / `TextAnim` /
`KenBurns` / `Focus` / `CinematicFx` / `BeatImpact` / `Timeline`），不渲染任何帧、
不依赖 HyperFrames CLI / ffmpeg / 浏览器。`generate_composition` 参数用
`main.py::GenerateCompositionInput` 的等价镜像 model 校验（避免 import main 拉起 MCP 服务）。

## 模板清单

| 文件 | 名称 | 效果 | 适用场景 | 成熟度 |
|---|---|---|---|---|
| `01_beat_basic.json` | 基础节拍卡点 | circle 蒙版 + standard spring + 轻微冲击 | 社媒快讯、多图轮播、入门 | landed |
| `02_cinematic_teal_orange.json` | 电影感青橙 | irregular 裁切 + flash-in + teal-orange 调色 | 电影预告、产品发布 | landed |
| `03_subject_pushin.json` | 主体推近锁定 | 群像 index=1 红夹克 + upper_body 推近，scale≈1.188 | 人物特写、主角突出 | landed |
| `04_shatter_flash.json` | 碎裂闪入 | 6×6 shatter + blur 兜底 + flash-in | 高能卡点、潮牌/游戏风 | landed |
| `05_film_noir.json` | 黑白胶片颗粒 | noir 调色 + 预渲染 grain + 暗角 + fade | 复古/纪实/情绪短片 | landed |
| `06_wipe_story.json` | 横向擦除叙事 | horizontal-wipe + Ken Burns 1.0→1.12 | 时间线/步骤叙事 | landed |
| `07_crossfade_dream.json` | 交叉溶解梦境 | crossfade + 扫光 + 颗粒 + film | 情绪短片、图片故事 | landed |
| `08_vertical_rush.json` | 竖向快切冲击 | vertical-wipe + bouncy spring + 强冲击 | 高能混剪、快节奏种草 | landed |
| `09_shatter_burst.json` | 碎裂爆发冲击 | 6×6 shatter + shake + flash 三重冲击 | 高能卡点、游戏开场 | landed |
| `10_per_char_typography.json` | 逐字排版冲击 | per-char 逐字 + chromatic + noir | 金句卡点、文字主导 | landed |
| `11_multi_subject_tour.json` | 多主体漫游视差 | focus tour index1→4 + 分层视差 | 群像叙事、主角接力 | landed |

**成熟度口径（诚实标注，勿对外夸大）**：

- `landed`：模板用到的特效均已在 `fixtures/e2e_*.py` 真进成片并有抽帧/像素证据。
  `04` 的 shatter 已真进成片（`fixtures/e2e_shatter_render.py`：入场 diff 48.27 → 稳态 8.83，碎片飞入后淡出交回 img），
  由确定性 LCG + 每片 fromTo 保证可复现。
- 当前 11 个模板全部 `landed`，无 `contract_only` 残留。`10` 的 chromatic 为代码级（像素证据未达标），已在 note 注明。
- 所有模板的画质上限仍是 CSS / filter 级，非 AE 插件级、非生成式（见 `docs/业界对标评估.md`）。

## 模板字段结构

```text
schema_version      模板 schema 版本（当前 1）
template_id         与文件名一致的稳定 id
name / summary / effect_summary / use_cases / maturity
bgm_profile         audio_path / expected_bpm_range / beat_group /
                    beat_interval_hint_sec / rhythm_note
build_timeline      images / texts / loop_assets / effects[]（每个 effect 可被 contracts 校验）
timeline_defaults   impact_profile / style_preset / fps
generate_composition backend / width / height / fps / audio_path / font_family / output_dir
render_video        concurrency / timeout_sec
expected_visual     预期成片观感（人工核对用）
```

## 如何加载：调用 main.py 工具的具体 JSON 示例

标准链路见 `docs/节拍卡点图文视频MCP方案.md` 第 12 章。以 `02_cinematic_teal_orange.json` 为例
（`grouped_beats` 来自上一步 `extract_audio_beats` 的出参，不要手写死值）。

**① `validate_assets`**

```json
{"audio_path": "fixtures/audio/bgm_120bpm.wav", "image_paths": ["fixtures/images/group_10people.jpg"], "min_duration": 1.0}
```

**② `extract_audio_beats`**（用模板 `bgm_profile.beat_group=4`）

```json
{"audio_path": "fixtures/audio/bgm_120bpm.wav", "start_time": 0.0, "end_time": null, "beat_group": 4}
```

**③ `build_timeline`**（`effects` 直接取模板 `build_timeline.effects`）

```json
{
  "grouped_beats": [0.5, 2.5, 4.5, 6.5],
  "images": ["fixtures/images/group_10people.jpg"],
  "texts": ["电影感 · 青橙调色", "不规则裁切揭示", "闪入过曝"],
  "effects": [
    {
      "mask": {"type": "circle", "duration": 0.32, "ease": "spring"},
      "image_spring": {"stiffness": 240, "damping": 17, "mass": 1.0},
      "text_anim": {"type": "spring-up-fade", "stiffness": 220, "damping": 20, "delay": 0.08, "y_offset": 60.0, "stroke": false, "shadow": true},
      "kenburns": {"enable": false, "start_scale": 1.0, "end_scale": 1.15, "start_x": 0.0, "start_y": 0.0, "end_x": 0.0, "end_y": 0.0, "ease": "none", "pivot_x": "50%", "pivot_y": "50%"},
      "focus": null,
      "beat_impacts": [],
      "cinematic_fx": {
        "mask": {"mode": "irregular", "duration": 0.38, "seed_from": "clip.index"},
        "shatter": {"cols": 6, "rows": 6, "stagger": 0.018},
        "reveal": {"type": "flash-in", "blur_px": 14.0, "brightness": 2.4, "overshoot": 1.12, "duration": 0.22, "ghosts": 3},
        "backdrop": {"type": "blur", "blur_px": 24.0, "brightness": 0.5},
        "grade": {"preset": "teal-orange", "grain_png": null, "vignette": 0.0},
        "light": {"sweep": false, "flare_at_beat": [], "flare_asset": null}
      }
    }
  ],
  "loop_assets": true
}
```

**④ `generate_composition`**（取模板 `generate_composition`，`timeline` 用 ③ 的出参）

```json
{
  "timeline": "<③ 的出参 timeline 对象>",
  "backend": "hyperframes",
  "width": 1080,
  "height": 1920,
  "fps": 30,
  "audio_path": "fixtures/audio/bgm_120bpm.wav",
  "font_family": "Noto Sans SC",
  "output_dir": null
}
```

**⑤ `render_video`**（`project_dir` / `composition_id` 来自 ④ 的出参）

```json
{"project_dir": "<④ 出参 project_dir>", "composition_id": "<④ 出参 composition_id>", "audio_path": "fixtures/audio/bgm_120bpm.wav", "output_mp4": "workspace/<task_id>/out.mp4", "concurrency": 4, "timeout_sec": 600}
```

### 关于 `impact_profile` / `style_preset` 的传递范围（重要）

模板里的 `timeline_defaults.impact_profile` / `style_preset` 属于 **Timeline 契约字段**，
能被 `Timeline.model_validate` 校验（离线校验脚本已覆盖）。但当前 MCP 工具
`build_timeline(grouped_beats, images, texts, effects, loop_assets)` 的入参签名**不含这两个字段**
（见 `main.py`），`PipelineService.build_timeline_step` 也未透传。因此：

- 走 MCP 工具链：这两个值由底层 `src.timeline.builder.build_timeline` 用默认值
  `normal` / `standard` 生成；模板的其他字段全部照常生效。
- 需要模板声明的完整档位时，用 Python 侧核心函数直接调用：

```python
from src.timeline.builder import build_timeline, EffectSpec
import json

tpl = json.load(open("templates/02_cinematic_teal_orange.json", encoding="utf-8"))
effects = [EffectSpec.model_validate(e) for e in tpl["build_timeline"]["effects"]]
d = tpl["timeline_defaults"]
timeline = build_timeline(
    grouped_beats, tpl["build_timeline"]["images"], tpl["build_timeline"]["texts"],
    effects, tpl["build_timeline"]["loop_assets"], d["fps"],
    impact_profile=d["impact_profile"], style_preset=d["style_preset"],
)
# 之后把 timeline.model_dump(mode="json") 传给 generate_composition 即可。
```

这是现有契约/接口的真实边界，不是模板缺字段；不要为了让 MCP 签名"看起来支持"而伪造入参。

### 关于颗粒纹理 `grain_png`

`05` / `07` 的 `grade.grain_png="assets/grain.png"` 是独立 overlay 素材。
`generate_composition` 只拷镜头图片，不会自动拷 grain。请按
`fixtures/e2e_fx2_render.py` 的做法，把 `fixtures/images/grain.png` 复制进
`workspace/<task_id>/composition/assets/grain.png` 后再渲染，否则颗粒不会生效。
模板不伪装"填了字段就一定有像素证据"。

## 与 benchmark / 案例的对应

每个 `landed` 模板都能映射到 `docs/BENCHMARK.md` 登记的 baseline 案例：
`01`→`e2e_real_render.py`，`02`→`e2e_fx_render.py`，`03`→`e2e_focus_render.py`，
`05/07`→`e2e_fx2_render.py`。真实端到端成片与抽帧见 `docs/CASES.md`。
