# 节拍卡点图文视频 MCP 完整技术方案

> 项目代号：`beat-slideshow-mcp`
> 文档类型：技术方案 / 架构设计
> 版本：v1.0
> 定位：面向 AI Agent（Cursor / Claude Code / Claude Desktop）的**生成型** MCP Server，输入图片 + 文字 + BGM + 特效配置，输出节拍卡点的短视频

---

## 目录

1. 需求规约与能力边界
2. 渲染引擎选型决策（Remotion vs HyperFrames）
3. 系统架构
4. 工程目录结构
5. MCP 工具定义（7 个）
6. 数据契约（Schema）
7. 特效实现规范
8. HyperFrames 模板实现（HTML + GSAP）
9. 节拍检测与人工微调
10. 素材校验与静态资源规范
11. 子进程安全与渲染规范
12. Agent 标准调用链路
13. 部署与接入
14. 已知局限性
15. 演进路线
16. 附录：参数速查表
17. 落地前复核清单
18. 关键修正与增强实现（v1 落地必读）
    - 18.1 spring 缓动的正确实现
    - 18.2 节拍冲击特效层（beat impact）
    - 18.3 帧对齐、黑场与转场修正
19. 电影级图片特效层（v2 增强）
    - 19.1 渲染模型：四层合成
    - 19.2 动态裁切 / 19.3 碎裂炸现 / 19.4 闪入
    - 19.5 光效与调色 / 19.6 WebGL 增强层
    - 19.7 风格预设 / 19.8 数据契约 / 19.9 验收标准
20. 主体感知聚焦与分层运镜（v3 增强）
    - 20.1 视觉分析层 Sidecar / 20.2 模型选型
    - 20.3 三种锁定方式 / 20.4 bbox→聚焦换算 / 20.5 漫游与视差
    - 20.6 数据契约 / 20.7 验收 / 20.8 已知边界
21. 确定性渲染硬契约（渲染 agent 施工依据）
    - 21.1 七条硬契约 / 21.2 四大坑与解法
    - 21.3 验收门槛 / 21.4 落地路径（开发 / 验收环境分工）

---

## 1. 需求规约与能力边界

### 1.1 必须支持

| 编号 | 能力 | 说明 |
|---|---|---|
| R1 | 节拍分析 | 输入 BGM，输出 BPM 与 beat 时间戳；支持「N 拍一镜」合并 |
| R2 | 节拍编排 | 按 beat 时间轴生成镜头序列，图片/文字自动对齐 |
| R3 | 图片入场 | spring 弹簧阻尼缩放入场、位移滑入，带回弹 |
| R4 | 蒙版 | `clip-path` 圆形 / 椭圆 / 矩形圆角 / 线性擦除，与图片动画共享进度 |
| R5 | 文字特效 | spring 上浮淡入、spring 缩放弹出、淡入、逐字延迟、描边/阴影 |
| R6 | 运镜 | Ken Burns 类 2D 运镜：缓慢拉近、拉远、平移摇镜、自定义聚焦锚点 |
| R7 | 转场 | beat 切点蒙版收缩退场 + 下一镜展开入场，消除硬切黑闪 |
| R8 | 渲染 | 输出 MP4，合成 BGM，支持竖屏 1080×1920 / 横屏 1920×1080，自定义 FPS |
| R9 | 预览 | 支持 Remotion Studio 类实时预览，避免反复渲染调参 |
| R10 | 素材校验 | 图片格式、音频采样率/声道、时长合法性前置校验 |
| R11 | 时间微调 | librosa 节拍非 100% 精准，必须提供人工偏移/增删通道 |
| R12 | 节拍冲击层 | flash / punch / shake / chromatic 四种冲击，强度分档（见 18.2） |
| R13 | 电影级图片特效 | 动态裁切（不规则/路径/切片）、碎裂炸现、闪入（曝光过冲）、光效与调色（见第 19 章） |
| R14 | 主体感知聚焦 | 群像中检测多主体，按文字/索引/参考图锁定目标，自动推近并居中；支持多主体漫游与前景背景分层视差（见第 20 章） |

### 1.2 明确不做（防止需求蔓延）

- 视频逆向拉片、参考片动画参数拟合（属于另一个 MCP，不混入本工具）
- 真 3D 透视推拉、真实光学景深虚化、空间纵深（WebGL 只能近似，不做真光学）
- AI 图像生成
- 实时流 / 直播渲染
- 抠像 / 绿幕

> **边界更新（v3，见第 20 章）**：原排除项「人脸/物体自动跟踪对焦」已纳入能力范围。通过独立 AI 视觉分析层（检测 / 分割 / 深度 / 语义匹配）输出主体框与焦点轨迹，再交给图层合成层渲染，实现"10 人中锁定某 1 人推近"这类行业先进运镜。视觉模型作为**可选前置分析服务**，不侵入 MCP 主流程；未安装时退化为手工锚点（R6），不阻断出片。

> **边界更新（v2，见第 19 章）**：原排除项中的「复杂粒子、光效」与「复杂调色」已重新纳入能力范围，通过 SVG filter / Canvas 2D / 可选 WebGL 实现影视级图层合成，**不依赖 AE 插件、不引入闭源许可**。AE 插件级合成的"完全等价"仍不做，但裁切、碎裂、闪入、光效、调色等电影感效果必须能做到。

> 边界原则：v1 做「**节拍驱动的 2D 图文动画编排**」；v2 起扩展为「**节拍驱动的电影级 2D 图层合成**」，渲染上限由 HTML / SVG / Canvas / 可选 WebGL 共同决定，目标是不依赖 AE 插件也能产出裁切、碎裂、闪入、光效、调色等电影感效果。

---

## 2. 渲染引擎选型决策

### 2.1 核心分歧

| 维度 | Remotion | HyperFrames（HeyGen 出品，已实证） |
|---|---|---|
| 协议性质 | **自定义双轨许可，非 MIT/Apache、非 OSI 开源**（Remotion License） | Apache 2.0（标准 OSI 开源，LICENSE 逐字核实为 `Copyright 2026 HeyGen, Inc.`） |
| 授权成本 | 个人 / **≤3 people 的公司** / 非营利免费；超出后需 Company License：Automators `$0.01/render, $100/月最低`（Agent / prompt-to-video / 批量渲染落此档），Creators `$25/月/席`，Enterprise `$500/月` 起；禁止套壳转售。来源：https://www.remotion.dev/docs/license/pricing （最后更新 **2026-11-16**）；第三方镜像 `$1M 营收/融资` 门槛**未采信** | 无授权费、无人数/配额门槛，可二次封装、嵌入分发，可商用 |
| 创作范式 | React + TSX，需 bundler 构建（package.json / remotion.config.ts） | 原生 HTML + CSS + GSAP，**单 HTML 无构建步骤** |
| 动画体系 | 内置 `spring()`，帧驱动 | GSAP 时间线；弹簧需 `CustomEase` 自行构造（见 18.1），非原生但参数自由度更高 |
| Agent 友好度 | 成熟但 React 对 LLM 更重，易生成打包失败的组件 | 原生面向 Agent 自动化，单文件确定性渲染，自带 skills / catalog |
| 调试 | Remotion Studio 强 | 浏览器直接打开，帧 seek 快 |
| 生态成熟度 | 多年积累，案例与排错资料丰富 | 仓库真实且活跃（约 85k stars），**确定性渲染已生产验证**；案例仍少于 Remotion，首次落地需实测 |
| 授权费用 | 取决于团队规模与用途；本 MCP 形态命中 Automators 计费档 | 0 |

> **实证依据（recon 2026-11-17）**：官方仓库 https://github.com/heygen-com/hyperframes ；LICENSE https://raw.githubusercontent.com/heygen-com/hyperframes/main/LICENSE ；FAQ https://hyperframes.heygen.com/faq ；Remotion 官方许可页 https://www.remotion.dev/docs/license/pricing （最后更新 2026-11-16）。完整证据与裁决见 `docs/技术侦察报告.md`。

### 2.2 决策结论

**首选 HyperFrames（HTML + GSAP，官方 `hyperframes.heygen.com`）**，理由按优先级排序：

1. **合规安全（已实证）**：官方仓库真实存在，LICENSE 逐字为 Apache 2.0（`Copyright 2026 HeyGen, Inc.`），FAQ 明确 framework/CLI/skills 全部 Apache 2.0、本地渲染免费免账号、允许商用与再分发；本方案是「MCP 工具」，天然存在多人分发、客户部署、对外服务场景，Apache 2.0 可彻底规避授权红线。
2. **确定性渲染已被生产验证（已实证）**：官方确定性文档给出 `time = floor(frame)/fps`、GSAP 时间线只 pause+seek、`HeadlessExperimental.beginFrame` 原子截图；HeyGen 工程博客记录了四大坑与解法。施工约束见第 21 章。
3. **LLM 生成成功率更高**：单 HTML 文件 + `renderAtTime(t)` 确定性帧函数，比维护完整 React 工程更不容易崩；HyperFrames 原生提供 Agent skills。
4. **运镜实现更干净**：GSAP 时间线天然支持「入场弹簧」与「持续 Ken Burns」两条独立时间线并行，不会互相抢进度。
5. **能力覆盖完整**：`clip-path` 蒙版、`transform` 缩放平移、文字动画全部为 CSS 原生能力，无需框架特性。

**架构要求：渲染后端必须可插拔**。上层工具链（节拍分析 / 时间线编排 / 素材校验）与后端无关；合规备选为 **Revideo（MIT，官方自述 `fully open source and MIT licensed`，https://re.video/ ）**，Remotion 仅作为 React 技术栈偏好者的工程备选（需按上述商业条款付费，**不得作为合规备选**）。

> 注意两个易混淆点：
> - HyperFrames 开源 CLI 框架（Apache 2.0，官方域名 **`hyperframes.heygen.com`**）≠ 托管的云渲染 / Studio Pro 计费功能；本方案只调用**本地开源 CLI**，不使用需要账号与计量的托管能力。
> - GSAP 自 **3.13（2025-04-29）** 起在 Webflow 支持下 **100% 免费，包括全部原 Club/bonus 插件（含 CustomEase），且可商用**；官方 Standard License 明确 commercial use covered、AI 生成代码不是 Prohibited Use。唯一红线是不得构建与 Webflow 视觉动画构建能力竞争的无代码可视化动画工具（见 `docs/技术侦察报告.md` 假设 2）。

### 2.3 为什么不用 FFmpeg filter / MoviePy

| 方案 | 判断 | 依据 |
|---|---|---|
| FFmpeg filter | ❌ 不适用 | 弹簧阻尼需逐帧表达式，filter 表达式晦涩、AI 生成易错；`clip-path` 无原生等价滤镜 |
| MoviePy | ❌ 不适用 | 动画靠 Python 回调逐帧计算，性能差、高帧率抖动，工程可维护性弱 |
| Revideo | ✅ 合规备选（MIT） | 许可宽松、确定性内置、headless render API 可用；但生态与 Agent skills 小于 HyperFrames，且引入自有 DSL / React 依赖 |
| Remotion | ⚠️ 工程备选，非合规备选 | 自定义双轨许可；本 MCP 的 Agent 批量渲染形态命中 Automators 计费档（`$0.01/render, $100/月最低`），团队 >3 people 即不再免费 |

---

## 3. 系统架构

```text
┌──────────────────────────────────────────────┐
│  调用方：Cursor / Claude Code / Claude Desktop │
└───────────────────┬──────────────────────────┘
                    │ MCP stdio（JSON-RPC）
┌───────────────────▼──────────────────────────┐
│         beat-slideshow-mcp（调度层）           │
│                                              │
│  validate_assets      素材前置校验             │
│  extract_audio_beats  节拍 / BPM 提取          │
│  build_timeline       时间线编排              │
│  adjust_timeline      节拍人工微调            │
│  generate_composition 模板填充 → 工程产物      │
│  preview_project      预览服务               │
│  render_video         渲染出片                │
└──────┬──────────────┬──────────────┬─────────┘
       │              │              │
┌──────▼─────┐ ┌──────▼─────┐ ┌──────▼─────────┐
│  librosa   │ │  ffmpeg    │ │ HyperFrames CLI│
│  节拍检测   │ │ 校验/预处理│ │ headless+ffmpeg│
└────────────┘ └────────────┘ └────────────────┘
```

**分层职责（关键设计原则）**

| 层 | 职责 | 禁止事项 |
|---|---|---|
| MCP 协议层 | 工具声明、参数校验、结构化返回 | 不写业务算法 |
| 编排层 | beat → 时间线 → 特效参数绑定 | 不碰渲染 |
| 分析层 | 音频节拍、素材元信息 | 不碰动画 |
| 渲染层 | 模板填充 + CLI 渲染 | 不改时间线语义 |
| 沙箱层 | 任务目录隔离、素材拷贝、清理 | 不跨任务读写 |

---

## 4. 工程目录结构

```text
beat-slideshow-mcp/
├── main.py                      # MCP stdio 服务入口
├── pyproject.toml
├── requirements.txt
├── config.yaml                  # 后端选择、超时、默认分辨率
├── src/
│   ├── analyzer/
│   │   ├── beat_detector.py     # librosa 节拍提取 + beat_group 合并
│   │   └── asset_validator.py   # ffprobe 校验图片/音频
│   ├── timeline/
│   │   ├── builder.py           # 时间线编排
│   │   └── adjuster.py          # 偏移 / 增删 / 变速
│   ├── backend/
│   │   ├── base.py              # 渲染后端抽象接口
│   │   ├── hyperframes_backend.py
│   │   └── remotion_backend.py  # 可选备选
│   ├── builder/
│   │   ├── template/
│   │   │   ├── composition.html.j2   # HyperFrames 主模板
│   │   │   └── composition.tsx.j2    # Remotion 备选模板
│   │   └── project_generator.py
│   ├── service/
│   │   └── preview.py           # 静态服务 + 帧 seek 预览
│   └── utils/
│       ├── subprocess_helper.py # 子进程安全封装
│       └── workspace.py         # 沙箱目录管理
├── workspace/
│   └── {task_id}/
│       ├── assets/              # 拷贝进来的图片
│       ├── timeline.json
│       └── composition/         # 生成的工程
└── logs/
```

**沙箱规则**：每次调用生成 `task_id = uuid4()`，所有读写限制在该目录内；对外返回的绝对路径必须是该目录下的产物，避免素材污染与跨任务冲突。

---

## 5. MCP 工具定义

所有工具入参使用 Pydantic v2 强校验，返回统一结构：

```json
{ "ok": true,  "data": {}, "error": null }
{ "ok": false, "data": null, "error": { "code": "INVALID_AUDIO", "message": "", "detail": {} } }
```

### 5.1 `validate_assets`

素材前置校验，把坏文件拦截在渲染之前。

**入参**
```json
{
  "audio_path": "string",
  "image_paths": ["string"],
  "min_duration": 1.0
}
```

**出参**
```json
{
  "ok": true,
  "data": {
    "audio": { "duration": 4.12, "sample_rate": 44100, "channels": 2, "codec": "mp3" },
    "images": [ { "path": "a.jpg", "width": 1080, "height": 1440, "valid": true } ],
    "warnings": ["b.png 宽高比 1:1，将被 object-fit:cover 裁切"]
  }
}
```

**错误码**：`FILE_NOT_FOUND` / `INVALID_AUDIO` / `INVALID_IMAGE` / `AUDIO_TOO_SHORT`

---

### 5.2 `extract_audio_beats`

**入参**
```json
{
  "audio_path": "string",
  "start_time": 0.0,
  "end_time": null,
  "beat_group": 1
}
```

**出参**
```json
{
  "ok": true,
  "data": {
    "bpm": 124.0,
    "raw_beats": [0.21, 0.63, 1.05, 1.47, 1.89],
    "grouped_beats": [0.21, 1.05, 1.89],
    "audio_duration": 4.12,
    "confidence": "high"
  }
}
```

字段说明：
- `raw_beats`：librosa 原始鼓点时间（秒）
- `grouped_beats`：合并后的**镜头切换点**，`beat_group=2` 即两拍一镜
- `confidence`：基于 beat 间隔标准差估算（`high` 间隔稳定 / `low` 间隔抖动大，建议走人工微调）

---

### 5.3 `build_timeline`

**入参**
```json
{
  "grouped_beats": [0.21, 1.05, 1.89, 2.73],
  "images": ["a.jpg", "b.jpg", "c.jpg"],
  "texts": ["第一句", "第二句", "第三句"],
  "effects": [ { "mask": {}, "image_spring": {}, "text_anim": {}, "kenburns": {} } ],
  "loop_assets": true
}
```

**编排规则**
1. 镜头数 = `len(grouped_beats) - 1`；最后一镜结束时间取 `audio_duration`
2. `images` / `texts` / `effects` 长度不足时按 `loop_assets=true` 循环取模
3. 每镜最短时长保护：默认 0.2s，低于则与下一镜合并并写入 `warnings`
4. 输出时间统一单位「秒」，帧换算交给渲染层

**出参（timeline）**
```json
{
  "ok": true,
  "data": {
    "task_id": "…",
    "fps": 30,
    "total_duration": 4.12,
    "clips": [
      {
        "index": 0,
        "start": 0.21,
        "end": 1.05,
        "duration": 0.84,
        "image": "assets/a.jpg",
        "text": "第一句",
        "mask": { "type": "circle", "duration": 0.32 },
        "image_spring": { "stiffness": 240, "damping": 17, "mass": 1 },
        "text_anim": { "type": "spring-up-fade", "stiffness": 220, "damping": 15, "delay": 0.08 },
        "kenburns": { "enable": true, "start_scale": 1.0, "end_scale": 1.15, "start_x": 0, "start_y": 0, "end_x": -2, "end_y": -2, "ease": "none", "pivot_x": "50%", "pivot_y": "50%" }
      }
    ]
  }
}
```

---

### 5.4 `adjust_timeline`

节拍微调是行业刚需，librosa 在弱鼓点、氛围音乐上必然漂移。

**入参（三种模式，互斥）**
```json
{ "mode": "offset",     "offset": 0.12 }
{ "mode": "set_times",  "times": [0.2, 1.0, 1.85, 2.7] }
{ "mode": "merge_split","action": "merge", "index": 1 }
```

**出参**：更新后的 timeline + 变更摘要（哪些 clip 的 start/end 被改写）。

---

### 5.5 `generate_composition`

**入参**
```json
{
  "timeline": {},
  "backend": "hyperframes",
  "width": 1080,
  "height": 1920,
  "fps": 30,
  "audio_path": "bgm.mp3",
  "font_family": "Noto Sans SC",
  "output_dir": null
}
```

**出参**
```json
{
  "ok": true,
  "data": {
    "project_dir": "/…/workspace/{task_id}/composition",
    "entry": "index.html",
    "composition_id": "BeatSlide",
    "copy_report": { "copied": 3, "skipped": 0 }
  }
}
```

**必须做的事（否则一定渲染失败）**
1. 图片全部**拷贝**到工程 `assets/` 或 `static/` 目录，模板内只写相对路径
2. 字体缺失时降级到系统字体并在 `warnings` 声明
3. 生成 `composition.json`（timeline 快照），便于复现与 diff

---

### 5.6 `preview_project`

**入参**：`project_dir`、`port`（默认 0 自动分配）
**出参**：`{ "preview_url": "http://127.0.0.1:8765", "seek_api": "/seek?t=" }`

用途：调 spring 参数时不必渲染 MP4，直接浏览器看帧。

---

### 5.7 `render_video`

**入参**
```json
{
  "project_dir": "string",
  "composition_id": "BeatSlide",
  "audio_path": "bgm.mp3",
  "output_mp4": "out.mp4",
  "concurrency": 4,
  "timeout_sec": 600
}
```

**出参**
```json
{
  "ok": true,
  "data": {
    "output_path": "out.mp4",
    "duration": 4.12,
    "resolution": "1080x1920",
    "fps": 30,
    "render_time_sec": 42,
    "log_path": "logs/render_{task_id}.log"
  }
}
```

**错误码**：`RENDER_TIMEOUT` / `BROWSER_LAUNCH_FAILED` / `FFMPEG_MUX_FAILED` / `BINARY_NOT_FOUND`

---

## 6. 数据契约（Schema）

### 6.1 `mask`

| 字段 | 类型 | 取值 | 说明 |
|---|---|---|---|
| `type` | string | `circle` / `ellipse` / `inset-rounded` / `horizontal-wipe` / `vertical-wipe` | 蒙版类型 |
| `duration` | float | 0.2 – 0.5 | 展开/收缩时长（秒），同时决定入场与退场窗口 |
| `ease` | string | `spring` / `power2.out` | 默认 `spring`，与 `image_spring` 共享参数 |

对应 clip-path：
```css
circle            → circle(${p*100}% at center)
ellipse           → ellipse(${p*100}% ${p*100}% at center)
inset-rounded     → inset(${(1-p)*50}% round 40px)
horizontal-wipe   → inset(0 ${(1-p)*100}% 0 0)
vertical-wipe     → inset(${(1-p)*100}% 0 0 0)
```

### 6.2 `image_spring`

| 字段 | 类型 | 建议范围 | 说明 |
|---|---|---|---|
| `stiffness` | float | 120 – 320 | 刚度，越大回弹越快 |
| `damping` | float | 12 – 28 | 阻尼，越小回弹越明显 |
| `mass` | float | 1 | 质量，一般固定 1 |

预设：
| 预设 | stiffness | damping | 观感 |
|---|---|---|---|
| `soft` | 160 | 22 | 柔和，几乎不回弹 |
| `standard` | 240 | 17 | 轻微回弹（默认） |
| `bouncy` | 300 | 12 | 明显弹跳，适合节奏感强的 BGM |

### 6.3 `text_anim`

| 字段 | 类型 | 取值 |
|---|---|---|
| `type` | string | `spring-up-fade` / `spring-scale` / `fade-in` / `per-char` |
| `stiffness` / `damping` | float | 同 image_spring |
| `delay` | float | 0 – 0.3，文字晚于图片入场，观感更专业 |
| `y_offset` | float | 上浮起始位移（px），默认 60 |
| `stroke` / `shadow` | bool | 描边 / 投影 |

### 6.4 `kenburns`

| 字段 | 类型 | 说明 |
|---|---|---|
| `enable` | bool | 运镜开关 |
| `start_scale` / `end_scale` | float | `>1` 拉近，`<1` 拉远，`1.0` 不动 |
| `start_x` / `start_y` / `end_x` / `end_y` | float | 平移百分比（`xPercent` / `yPercent`） |
| `pivot_x` / `pivot_y` | string | 聚焦锚点，如 `50% 35%`（画面偏上主体） |
| `ease` | string | Ken Burns 推荐 `none`（匀速），`power1.inOut` 用于慢起慢停 |

### 6.5 统一错误码

| code | 触发条件 | 建议动作 |
|---|---|---|
| `FILE_NOT_FOUND` | 路径不存在 | 让 Agent 重新确认路径 |
| `INVALID_AUDIO` | ffprobe 无音频流 | 转码重试 |
| `AUDIO_TOO_SHORT` | 音频时长 < `min_duration`（见 5.1/10.1） | 更换更长 BGM |
| `INVALID_IMAGE` | 图片损坏/不支持格式 | 转码或跳过 |
| `EMPTY_TIMELINE` | beats 少于 2 | 检查 BGM 时长 |
| `BINARY_NOT_FOUND` | 未装 ffmpeg / node | 提示环境前置依赖 |
| `BROWSER_LAUNCH_FAILED` | headless 浏览器启动失败（见 5.7） | 检查 Chromium 依赖 |
| `RENDER_TIMEOUT` | 超时 | 降低分辨率/并发重试 |
| `FFMPEG_MUX_FAILED` | 音视频合成失败（见 5.7/8.3） | 检查编码参数与磁盘 |

---

## 7. 特效实现规范

### 7.1 三段时间线解耦（最关键的设计）

单个镜头生命周期切三段，**互不抢占进度**：

```text
镜头 start ────────────────┬─────────────── end
   │<── 入场窗口 T_in ──>│<── 稳态 ──>│<── 退场 T_out ──>│

入场窗口：spring 弹簧缩放 + 蒙版展开   （一次性短动画，T_in = mask.duration）
稳态：    Ken Burns 匀速拉近/拉远/摇镜 （持续动画，贯穿 T_in_end → T_out_start）
退场窗口：反向 spring + 蒙版收缩       （一次性短动画，T_out = mask.duration）
```

进度合成：
```text
progress = enterProgress × exitProgress
enterProgress: 0 → 1（spring）
exitProgress : 1 → 0（spring，仅在最后 T_out 内生效）
```
图片的 `scale` / `clip-path` / `opacity` **共享同一个 progress**，保证蒙版与缩放严丝合缝同步。

### 7.2 运镜与入场的关系

- Ken Burns 从 `T_in` 结束时刻启动，至 `T_out` 开始时刻结束，**避开入场弹簧的抖动区间**
- 若镜头过短（`duration < 2×T_in`），自动关闭 Ken Burns 并写入 `warnings`
- 起始 scale 必须等于入场结束值（`1.0`），否则会看到跳帧

### 7.3 文字时序

```text
文字入场起点 = 镜头 start + text_anim.delay
文字入场时长 = T_in（默认与图片同长或稍短）
退场：与图片同步收缩淡出，或固定保留至镜头结束（配置项 hold_to_end）
```

### 7.4 转场衔接

- 相邻镜头入场窗口**不重叠**（`T_in ≤ 镜头时长 - T_out`，由 builder 强制校验）
- 上一镜退场收缩与下一镜展开同时发生时，用黑底容器兜底，避免中间出现 DOM 空白 → 黑闪

---

## 8. HyperFrames 模板实现（HTML + GSAP）

### 8.1 设计要点

- **确定性渲染**：所有动画挂在 GSAP 时间线，暴露 `window.renderAtTime(t)` 供渲染器逐帧 seek，禁止使用 `requestAnimationFrame` 自驱动
- **全局暂停**：timeline 保持 `paused`，时间由外部 `renderAtTime(t)` 驱动，保证同一 t 必出同一帧；不要移除 ticker 私有 `_tick`（见 18.3）
- **统一用 `MASTER.totalTime(t, false)`，禁用 `repeat` / `yoyo`**：`totalTime` 覆盖整个时间线（含子时间线偏移），与 `time()` 在无重复时等价，但一旦引入 `repeat`/`yoyo` 二者即分叉；本方案禁止任何 `repeat`/`yoyo`/无限时间线，保证同一 t 唯一对应一帧（见决策 #3、第 21 章）
- 每镜一个 `clip` DOM，通过 `display` 切换，避免 100 个镜头同时参与合成
- 本章模板必须与第 21 章「确定性渲染硬契约」一起实施；8.2 只保证 DOM/时间线层正确，浏览器层与工程层约束由 21 章规定

### 8.2 模板骨架（`composition.html.j2`）

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body { margin:0; padding:0; background:#000; overflow:hidden; }
  #stage { position:relative; width:{{ width }}px; height:{{ height }}px; overflow:hidden; transform-origin:50% 50%; }
  #flash-layer {
    position:absolute; inset:0; background:#fff; opacity:0;
    pointer-events:none; mix-blend-mode:screen;
  }
  .clip { position:absolute; inset:0; display:none; }
  .clip img {
    width:100%; height:100%; object-fit:cover;
    will-change: transform, clip-path, opacity;
    transform-origin: {{ clip.pivot }};   /* 由 pivot_x/y 生成 */
  }
  .caption {
    position:absolute; left:0; right:0; bottom:{{ text_bottom }}px;
    text-align:center; font-size:{{ text_size }}px; color:#fff;
    font-family: "{{ font_family }}", sans-serif;
    text-shadow: 0 2px 12px rgba(0,0,0,.7);
    will-change: transform, opacity;
  }
</style>
</head>
<body>
<div id="stage">
  {% for clip in clips %}
  <div class="clip" id="clip-{{ clip.index }}">
    <img src="{{ clip.image }}" alt="">
    <div class="caption">{{ clip.text }}</div>
  </div>
  {% endfor %}
  <div id="flash-layer"></div>
</div>

<script src="./vendor/gsap.min.js"></script>
<script src="./vendor/CustomEase.min.js"></script>
<script src="./vendor/spring.js"></script>
<script>
const TIMELINE = {{ timeline_json | safe }};
const FPS = {{ fps }};
const IMPACT_PROFILE = {{ impact_profile | default('normal') }};
const MASTER = gsap.timeline({ paused: true });

/* 确定性渲染：timeline 保持 paused，仅由 renderAtTime 驱动。
   不要移除 gsap.ticker._tick（私有 API，且 paused timeline 本就不自驱），见 18.3。 */

function buildClip(clip) {
  const root = document.getElementById('clip-' + clip.index);
  const img  = root.querySelector('img');
  const cap  = root.querySelector('.caption');
  const T    = clip.duration;
  const Tin  = Math.min(clip.mask.duration, T * 0.5);
  const Tout = Math.min(clip.mask.duration, T * 0.5);
  const spring = makeSpringEase(clip.image_spring.stiffness, clip.image_spring.damping, clip.image_spring.mass);

  /* 0：镜头出现（仅该镜可见） */
  MASTER.set(root, { display: 'block' }, clip.start)
        .set(root, { display: 'none'  }, clip.end);

  /* 1：入场 —— 蒙版展开 + 弹簧缩放（同一 ease、同一时长，天然同步） */
  MASTER.fromTo(img,
    { clipPath: maskStart(clip.mask.type), scale: 0.75, opacity: 0 },
    { clipPath: maskEnd(clip.mask.type),   scale: 1,    opacity: 1,
      duration: Tin, ease: spring, immediateRender: false },
    clip.start);

  /* 2：稳态 —— Ken Burns 持续运镜，避开入场与退场 */
  if (clip.kenburns && clip.kenburns.enable) {
    MASTER.fromTo(img,
      { scale: 1, xPercent: clip.kenburns.start_x, yPercent: clip.kenburns.start_y },
      { scale: clip.kenburns.end_scale,
        xPercent: clip.kenburns.end_x,
        yPercent: clip.kenburns.end_y,
        duration: Math.max(0, T - Tin - Tout),
        ease: clip.kenburns.ease || 'none', immediateRender: false },
      clip.start + Tin);
  }

  /* 3：退场 —— 反向蒙版收缩 + 弹簧缩回 */
  MASTER.to(img,
    { clipPath: maskStart(clip.mask.type), scale: 0.85, opacity: 0,
      duration: Tout, ease: spring },
    clip.end - Tout);

  /* 4：文字 —— 延迟入场，独立 spring */
  const tEase = makeSpringEase(clip.text_anim.stiffness, clip.text_anim.damping, 1);
  MASTER.fromTo(cap,
    { opacity: 0, y: clip.text_anim.y_offset || 60 },
    { opacity: 1, y: 0, duration: Tin, ease: tEase, immediateRender: false },
    clip.start + (clip.text_anim.delay || 0));
  MASTER.to(cap, { opacity: 0, duration: Tout, ease: 'power2.in' }, clip.end - Tout);
}

function maskStart(type) {
  switch (type) {
    case 'circle':          return 'circle(0% at center)';
    case 'ellipse':         return 'ellipse(0% 0% at center)';
    case 'inset-rounded':   return 'inset(50% round 40px)';
    case 'horizontal-wipe': return 'inset(0 100% 0 0)';
    case 'vertical-wipe':   return 'inset(100% 0 0 0)';
    default:                return 'inset(0 100% 0 0)';
  }
}
function maskEnd(type) {
  switch (type) {
    case 'circle':          return 'circle(100% at center)';
    case 'ellipse':         return 'ellipse(100% 100% at center)';
    case 'inset-rounded':   return 'inset(0% round 40px)';
    case 'horizontal-wipe': return 'inset(0 0% 0 0)';
    case 'vertical-wipe':   return 'inset(0% 0 0 0)';
    default:                return 'inset(0 0% 0 0)';
  }
}

TIMELINE.clips.forEach(buildClip);
/* 节拍冲击层见 18.2：在此调用 buildImpacts(TIMELINE.beat_impacts, document.getElementById('stage')) */
/* 不再调用 MASTER.totalDuration(...)：tween 已排布到目标时长，再设会改变 timeScale（见 18.3） */

/* 确定性 seek 接口（决策 #3 / 第 21 章）：
   - 统一用 totalTime(t, false)，禁用 repeat/yoyo；
   - t 先量化到帧，避免浮点误差造成相邻帧像素差异；
   - 禁止 rAF 自驱动，时间只能由外部传入。 */
window.renderAtTime = (t) => {
  const tq = Math.round(t * FPS) / FPS;
  MASTER.totalTime(tq, false);
};
window.renderAtFrame = (f) => { window.renderAtTime(f / FPS); window.__ready = true; };

/* 首帧就绪标记：headless 截图前等待该 flag（ready gate 见 21.1） */
window.renderAtFrame(0);
</script>
</body>
</html>
```

### 8.3 渲染调用（原则）

- 逐帧截图：`for f in 0..total_frames: renderAtFrame(f) → screenshot`，帧率与 fps 严格一致；**必须用 `HeadlessExperimental.beginFrame` 原子截图，不得直接 `Page.captureScreenshot`**（见 21.1 / 21.2）
- 截图前必须通过 ready gate：等待 `window.__ready` **且**字体加载完成；禁止用 `page.waitForFunction`（它依赖 rAF，在 BeginFrame 冻结事件循环时永不返回），改为 Node 侧 `page.evaluate()` + `setTimeout()` 轮询（见 21.2）
- 音频合成：帧渲染完成后由 ffmpeg 混入 BGM；**不要用 `-shortest`**，须按总帧数以 `-t {N/fps}` 精确截断，见 18.3
- **严格复现必须在 Linux Docker**：固定 Chromium 版本、字体集合与 FFmpeg 编码器（官方 `npx hyperframes render --docker --output output.mp4`）；Windows 本地渲染可用于开发调试，但不作为像素级验收环境（见 21.1）
- 具体 CLI 参数以 HyperFrames 官方文档（https://hyperframes.heygen.com/ ）为准，本模板只保证 DOM/时间线层面正确；浏览器层与工程层约束见第 21 章

### 8.4 常见坑与规避

| 坑 | 现象 | 规避 |
|---|---|---|
| 动画自驱动 | 帧率不准、seek 后画面漂移 | timeline 保持 `paused`，只走 `renderAtTime`（见 18.3） |
| 用 `time()` 而非 `totalTime()`，或启用 `repeat`/`yoyo` | 有子时间线/重复时同一 t 对应多帧，确定性回归失败 | 统一 `MASTER.totalTime(t, false)`；**禁止 `repeat`/`yoyo`/无限时间线**（决策 #3、21.1） |
| `clip-path: polygon()` 两端字符串不同构 | 动画跳变、点数不足处被补 0、直接不插值 | **四同 + 显式两端**：同函数、同点数、同单位（推荐全 `%`）、同顺序；用 `fromTo` 同时写死起止字符串，不依赖 computed style（见 19.2、21.3） |
| `ease: "spring(...)"` 无效 | 完全没有回弹，只有线性趋近 | GSAP 无原生 spring；改用 `makeSpringEase()`（见 18.1） |
| 截图竞态 | 首帧文字未渲染、SVG 仍是默认样式、视频显示默认 300×150 | 用 `beginFrame` 原子捕获 + ready gate；禁止裸 `captureScreenshot`（见 21.2 坑 1） |
| 字体在 BeginFrame 下永不就绪 | `document.fonts.ready` 挂起、渲染超时 | warmup loop：加载期每 33ms `beginFrame(noDisplayUpdates:true)`；字体本地化（见 21.2 坑 2） |
| 图片用绝对路径 | 无头浏览器加载失败、黑屏 | 全部拷贝进工程目录用相对路径 |
| 入场与运镜抢 scale | 画面先缩后弹再跳 | 三段窗口严格不重叠 |
| 蒙版与缩放不同步 | 蒙版开了图还是小的 | 共享 progress / 同一 ease 同一时长 |
| 镜头过短 | 动画被截断 | builder 强制校验 `T ≥ 2×T_in` |
| DOM 空档黑闪 | 切点闪黑 | 退场与入场同时启动，容器常黑底 |

---

## 9. 节拍检测与人工微调

### 9.1 检测参数（librosa）

```python
import librosa, numpy as np

def detect_beats(audio_path, start_time=0.0, end_time=None, beat_group=1):
    y, sr = librosa.load(audio_path, sr=22050, mono=True,
                         offset=start_time, duration=(end_time - start_time) if end_time else None)
    y = librosa.effects.percussive(y)                     # 强化鼓点，提升稳定度
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr, units='frames')
    beats = librosa.frames_to_time(frames, sr=sr)
    beats = np.unique(np.round(beats, 3))
    grouped = beats[::beat_group]
    # 间隔标准差 → 置信度
    if len(beats) > 3:
        std = float(np.std(np.diff(beats)))
        conf = "high" if std < 0.06 else ("medium" if std < 0.15 else "low")
    else:
        conf = "low"
    return {"bpm": float(np.atleast_1d(tempo)[0]), "raw_beats": beats.tolist(),
            "grouped_beats": grouped.tolist(), "confidence": conf}
```

### 9.2 微调策略（行业标准补偿方案）

| 场景 | 手段 |
|---|---|
| 整体慢半拍 | `adjust_timeline(mode="offset", offset=-0.08)` |
| 个别拍点不准 | `mode="set_times"` 手填 |
| 漏拍 | `mode="merge_split", action="split"` |
| 节奏太密（每拍都切太碎） | `beat_group=2` 或 3 |
| 纯氛围音乐无鼓点 | 直接 `set_times` 按固定 BPM 等分生成 |

---

## 10. 素材校验与静态资源规范

### 10.1 校验项

| 对象 | 校验 | 失败处理 |
|---|---|---|
| 音频 | 有音频流、时长 ≥ `min_duration`、采样率 | `INVALID_AUDIO` |
| 图片 | 可解码、宽高 ≥ 输出尺寸的合理比例 | `INVALID_IMAGE` |
| 字体 | 输出字体在系统可用 | 降级 + warning |
| 路径 | 存在且可读 | `FILE_NOT_FOUND` |

### 10.2 静态资源规范（强制）

1. 所有图片**拷贝**进 `composition/assets/`，模板只写相对路径
2. 禁止 `file://`、绝对路径、远程 URL 直接引用（无头环境易失败）
3. 文件名归一化（去空格/中文/特殊字符 → `img_001.jpg`），避免 URL 编码问题
4. 同名冲突时加 `task_id` 前缀，防跨任务覆盖

---

## 11. 子进程安全与渲染规范

```python
def run_cmd(args, cwd, timeout=600):
    proc = subprocess.run(
        args,                      # 必须 list，禁止 shell=True
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=safe_env(),            # 白名单环境变量
    )
    return proc.returncode, proc.stdout, proc.stderr
```

规范：
1. `shell=False`，参数列表传参，杜绝命令注入
2. 全部设超时，超时返回 `RENDER_TIMEOUT` 并保留日志
3. stdout/stderr 落盘到 `logs/`，返回 `log_path` 供 Agent 排查
4. 渲染前后校验产物：文件存在、时长误差 < 0.1s、首帧非纯黑
5. workspace 清理提供独立工具，默认保留 24 小时便于排错

---

## 12. Agent 标准调用链路

```text
① validate_assets         校验 BGM 与图片
        │
② extract_audio_beats     取 beats（必要时设 beat_group）
        │
③ build_timeline          绑定图片 / 文字 / 特效参数
        │
④ adjust_timeline         可选：节拍微调（confidence=low 时必做）
        │
⑤ generate_composition    生成工程（拷贝素材 + 填模板）
        │
⑥ preview_project         可选：浏览器调 spring 参数
        │
⑦ render_video            渲染 MP4 并混入 BGM
```

### 给 Agent 的任务指令模板

```text
任务：节拍卡点图文短视频
素材：bgm.mp3，images/ 内图片，每张图对应一句文案
参数：1080×1920，30fps，每 2 拍换一张图

1. validate_assets 校验素材，有 warning 先回报
2. extract_audio_beats，beat_group=2；confidence 非 high 时先 adjust_timeline 微调
3. build_timeline，统一特效：
   - mask: circle, duration 0.32
   - image_spring: stiffness 240, damping 17
   - text_anim: spring-up-fade, delay 0.08
   - kenburns: enable, 1.0 → 1.15（缓慢拉近），ease none
4. generate_composition 后 preview_project，确认首帧与切点
5. render_video 输出 MP4，混入 BGM
```

---

## 13. 部署与接入

### 13.1 环境前置（MCP 不负责自动安装）

| 依赖 | 版本建议 |
|---|---|
| Python | 3.11+ |
| Node.js | **22+**（HyperFrames 官方 README 要求；Remotion 仅作工程备选时按其自身要求） |
| ffmpeg | 6.1+，需全局可调用 |
| 字体 | 中文字体（Noto Sans SC 等）预装；**字体集必须固定**，Docker 镜像内锁定（见 21.1） |
| Docker（仅确定性验收） | Linux + chrome-headless-shell 固定版本，用于像素级复现回归（见 21.1 / 21.3）；Windows 可用于开发调试 |

### 13.2 Python 依赖

```text
mcp>=1.0.0
pydantic>=2.0
librosa>=0.10.1
numpy
jinja2>=3.1
pyyaml
```

### 13.3 Cursor / Claude Desktop 配置

```json
{
  "mcpServers": {
    "beat-slideshow-mcp": {
      "command": "python",
      "args": ["/absolute/path/beat-slideshow-mcp/main.py"],
      "env": { "BEAT_MCP_HOME": "/absolute/path/beat-slideshow-mcp" }
    }
  }
}
```

---

## 14. 已知局限性（客观陈述）

1. **librosa 节拍检测存在固有漂移**：弱鼓点、氛围音乐、变速曲目上会漏拍/偏拍。方案以 `adjust_timeline` 人工微调 + `confidence` 提示作为标准补偿，不承诺全自动 100% 精准。
2. **CSS clip-path 限制**：原生只支持几何形状蒙版；v2 起通过 19.2 的 `polygon()` / SVG `clipPath` 实现不规则裁切与路径揭示，但**动态主体跟踪抠像**仍不支持。
3. **无真 3D / 真光学景深**：HTML 管线不具备光学景深；v2 起可用 19.3 的模糊背景层 + 19.6 的 WebGL 近似景深，但非真实光学，Ken Burns 本质仍是 2D 缩放平移。
4. **渲染耗时随分辨率与时长线性增长**：高分辨率长视频耗时明显，可通过降低并发、分片渲染缓解。
5. **字体依赖环境**：未安装指定字体将降级，文字观感会变。
6. **HyperFrames 生态仍较新，但核心风险已降低（recon 实证）**：官方仓库 `heygen-com/hyperframes` 真实活跃（约 85k stars），LICENSE 逐字 Apache 2.0，CLI 本地渲染与逐帧 seek 均已实证，确定性渲染已被 HeyGen 生产验证；案例与排错资料仍少于 Remotion，**首次落地必须实测本地环境**（Node 22+ / FFmpeg / 字体 / Docker），合规备选为 Revideo（MIT），后端接口保持可插拔。

> 注：spring 缓动缺失、首帧黑场、交叉转场受限、帧对齐阈值过松等落地问题已在第 18 章给出修正；不规则裁切、碎裂炸现、闪入、光效调色已在第 19 章补齐；群像主体锁定推近已在第 20 章补齐。仍存在的真实局限：真光学景深、真 3D 重建、动态主体跟踪（静态图无跟踪问题）、以及遮挡/小目标的检测准确率，见 20.8。

---

## 15. 演进路线

| 版本 | 内容 |
|---|---|
| v1（本方案 + 第 18 章） | 节拍卡点 + spring 入场 + clip-path 蒙版 + 文字动效 + Ken Burns + 节拍冲击层 + 渲染预览全链路 |
| v1.1 | 逐字文字动画、多图层叠加（贴纸/角标）、转场类型扩展 |
| v2（第 19 章） | **电影级图片特效层**：四层合成 + 不规则裁切 + 碎裂炸现 + 闪入 + 光效调色 + 风格预设 |
| v2.1 | 多后端可插拔（HyperFrames / Remotion / Revideo），配置切换 |
| v2.2 | WebGL 增强层：真粒子 / 近似景深 / 体积光（按需开启） |
| v3（第 20 章） | **主体感知聚焦**：群像主体检测、文字/参考图/索引锁定、自动推近居中、多主体漫游、分层视差 |
| v3 | SRT 字幕导出、分层音频（BGM + 音效）、多语言字体自动匹配 |
| v4 | 素材智能适配（自动裁切主体、按主体自动设 pivot 锚点） |

---

## 16. 附录：参数速查表

### 16.1 spring 预设

| 场景 | stiffness | damping | 观感 |
|---|---|---|---|
| 柔和商务 | 160 | 22 | 微弱回弹，稳 |
| 通用默认 | 240 | 17 | 轻微回弹 |
| 强节奏（电子/舞蹈） | 300 | 12 | 明显弹跳 |
| 文字（克制） | 220 | 20 | 干脆上浮 |

### 16.2 运镜配方

| 效果 | start_scale → end_scale | x/y 位移 | ease |
|---|---|---|---|
| 缓慢拉近（聚焦中心） | 1.0 → 1.15 | 0 → 0 | none |
| 缓慢拉远（收景） | 1.15 → 1.0 | 0 → 0 | none |
| 焦点偏上主体 | 1.0 → 1.12 | y 0 → -3 | none |
| 缓慢左摇 | 1.05 → 1.05 | x 0 → -3 | power1.inOut |
| 推近 + 微移 | 1.0 → 1.2 | x 0 → -2, y 0 → -2 | none |

### 16.3 蒙版选择建议

| 节奏/风格 | 推荐蒙版 |
|---|---|
| 通用/温柔 | `circle` |
| 硬切/科技感 | `horizontal-wipe` |
| 竖版短视频上下揭示 | `vertical-wipe` |
| 卡片/圆角海报感 | `inset-rounded` |
| 肖像/圆形头像式 | `ellipse` |
| 不规则电影感揭示 | `irregular`（见 19.2） |
| 百叶窗条带揭示 | `slats`（见 19.2） |
| 碎裂炸现 | `shatter`（见 19.3） |

### 16.4 时长预算

| 项 | 建议值 |
|---|---|
| 入场 T_in | 0.25 – 0.40s |
| 退场 T_out | 0.25 – 0.35s |
| 单镜最短 | ≥ 0.6s（低于则合并 beat） |
| 文字 delay | 0.05 – 0.15s |
| 稳态运镜最短 | ≥ 0.5s（否则关闭 Ken Burns） |

---

## 17. 落地前复核清单

以下为需在动手前用官方文档复核确认的项。**2026-11-17 recon 已完成 4 项关键假设的实证，勾选并标注来源；其余项仍需在目标环境实测**。完整证据见 `docs/技术侦察报告.md`。

- [x] HyperFrames 官方仓库 LICENSE 与 CLI 用法 —— **recon 实证，见技术侦察报告.md**：仓库 `heygen-com/hyperframes` 真实存在，LICENSE 逐字 Apache 2.0（`Copyright 2026 HeyGen, Inc.`），CLI 命令 `npx hyperframes init/preview/render`，官方域名 `hyperframes.heygen.com`
- [x] Remotion 许可与阈值 —— **recon 实证，见技术侦察报告.md**：自定义双轨许可（非 MIT/Apache），个人 / ≤3 people 的公司 / 非营利免费；超出后 Automators `$0.01/render, $100/月最低`，Creators `$25/月/席`，Enterprise `$500/月` 起；禁止套壳转售。第三方流传的 `$1M 营收/融资` 门槛未在官方页出现，不予采信
- [x] GSAP 无原生 `spring()` 缓动（属 Framer Motion/React Spring 语法）——已确认，按 18.1 用 `CustomEase` 实现
- [x] `CustomEase` 许可条款 —— **recon 实证，见技术侦察报告.md**：GSAP 3.13（2025-04-29）起 100% 免费含全部原 Club/bonus 插件，允许商用；官方标准许可明确 commercial use covered，AI 生成代码不是 Prohibited Use。红线：不得构建与 Webflow 竞争的无代码可视化动画工具
- [x] GSAP 其余所用插件在「本地自动化渲染」场景的许可范围 —— **recon 实证，见技术侦察报告.md**：3.13 起整个 GSAP toolset（含全部 bonus 插件）免费可商用，无会员插件门槛；注意外部素材（字体/音乐/画面）保留各自许可
- [ ] 18.2 冲击层所用 `keyframes` 语法与 `mix-blend-mode` 在目标 Chromium 版本下的支持情况
- [x] 19 章依赖：`clip-path: polygon()` 的 GSAP 字符串插值 —— **recon 实证，见技术侦察报告.md**：可插值，但须满足「四同 + fromTo 显式两端」（同函数/同点数/同单位/同顺序，推荐全 `%`）；变点数场景需拆成同点数时间线
- [ ] 19 章依赖：`filter` 动画、`mix-blend-mode` 在目标 Chromium 版本的支持（仍需实测）
- [ ] 预渲染颗粒 PNG / 光晕素材的生成流程（仍待实测）；**许可部分已核验 → `docs/许可核验报告.md` §1 第 6 项**：自产可商用，外部素材未知、引入前须逐项核验
- [ ] 6×6 碎片 + 模糊背景层在 1080×1920 下的单帧渲染耗时实测（19.9 验收项）
- [ ] 若启用 WebGL 增强层：`--use-gl=angle --use-angle=swiftshader` 支持（仍待实测）；**PixiJS 许可已核验 → `docs/许可核验报告.md` §1 第 7 项**：MIT 可商用
- [x] 第 20 章视觉模型许可 —— **2026-11-17 已核验，见 `docs/许可核验报告.md` §1 第 1/2/3/8 项**：RT-DETR / SAM / SAM2 / Grounding DINO 为 Apache-2.0 可商用；Depth Anything V2（`Depth-Anything-V2-Small-hf`）模型卡 `license: apache-2.0` 可商用；**人脸 embedding 权重（InsightFace `buffalo_l`/`buffalo_s`/`antelopev2`）官方明确"预训练模型仅供非商业学术研究，商用需另行授权"→ 结论「需授权」，当前 `anchor_region="face"` 返回 BINARY_NOT_FOUND 不启用**；DeepFace 封装权重结论「未知」未使用。字体（Noto Sans SC = SIL OFL 1.1 可商用）/ 音乐（当前自产 CC0 可商用、外部未知）见报告 §1 第 4/5 项
- [ ] 视觉分析层运行环境：PyTorch 或 ONNX Runtime 版本、是否需要 GPU、冷启动耗时（不进入渲染循环）
- [ ] `text` selector 在目标图片风格上的 IoU ≥ 0.5 实测；不达标时默认走 `index` 人工兜底
- [x] HyperFrames 原生逐帧 seek 接口 —— **recon 实证，见技术侦察报告.md**：renderer 在 headless Chrome 中逐帧 seek（Puppeteer + FFmpeg），GSAP 通过 frame adapter 以 `paused + totalTime(t,false)` 接入；frame-accurate 依赖可 seek 的动画运行时
- [ ] 目标运行环境实测：Node 22+、Chromium（chrome-headless-shell）版本、字体清单、FFmpeg 与 Docker 镜像固定（**recon 已给出要求，但本地环境仍须实测**）

---

## 18. 关键修正与增强实现（v1 落地必读）

> 本章由落地复核产生：**18.1 / 18.3 修正**第 8、11 节中会直接导致观感打折或验收失准的问题；**18.2 新增** P1「节拍冲击特效层」，把方案从"整齐卡点"提升到"有冲击感的卡点"。原方案的架构分层与合规选型不变。

### 18.1 spring 缓动的正确实现

**问题**：GSAP 内置缓动只有 `power*` / `elastic` / `back` / `bounce` / `circ` / `expo` / `sine`，**没有 `spring(stiffness, damping, mass)`**。原 8.2 模板中的 `ease: spring(240,17,1)` 是 Framer Motion / React Spring 的语法，在 GSAP 中会被当作无效 ease，回弹观感直接丢失——而弹簧回弹正是 R3/R5 的核心卖点。

**解决**：用 `CustomEase`（GSAP 3.11+ 已免费）把阻尼谐振子的解析解采样成贝塞尔曲线，`stiffness / damping / mass` 保持与 Remotion 同义。

**`vendor/spring.js`**（新增，随工程分发）：

```js
/* 依赖：gsap.min.js + CustomEase.min.js（GSAP 3.11+） */
(function (global) {
  const cache = new Map();

  function springPath(k, c, m, samples) {
    const w0 = Math.sqrt(k / m);                          // 固有角频率
    const zeta = c / (2 * Math.sqrt(k * m));              // 阻尼比
    const wd = w0 * Math.sqrt(Math.abs(1 - zeta * zeta)); // 阻尼振荡角频率
    const n = samples || 64;
    const pts = [];

    for (let i = 0; i <= n; i++) {
      const u = i / n;                                    // GSAP 横轴：归一化进度
      let v;
      if (zeta < 0.999) {                                 // 欠阻尼：真实回弹
        v = 1 - Math.exp(-zeta * w0 * u) *
              (Math.cos(wd * u) + (zeta * w0 / wd) * Math.sin(wd * u));
      } else {                                            // 临界/过阻尼近似：无回弹
        v = 1 - (1 + w0 * u) * Math.exp(-w0 * u);
      }
      pts.push([u, v]);
    }

    // Catmull-Rom 转 Cubic Bezier：x 严格单调，y 允许 overshoot > 1
    let d = `M${pts[0][0].toFixed(5)},${pts[0][1].toFixed(5)}`;
    for (let i = 0; i < n; i++) {
      const p0 = pts[Math.max(0, i - 1)], p1 = pts[i];
      const p2 = pts[i + 1],             p3 = pts[Math.min(n, i + 2)];
      const c1x = p1[0] + (p2[0] - p0[0]) / 6;
      const c1y = p1[1] + (p2[1] - p0[1]) / 6;
      const c2x = p2[0] - (p3[0] - p1[0]) / 6;
      const c2y = p2[1] - (p3[1] - p1[1]) / 6;
      d += ` C${c1x.toFixed(5)},${c1y.toFixed(5)} ${c2x.toFixed(5)},${c2y.toFixed(5)} ${p2[0].toFixed(5)},${p2[1].toFixed(5)}`;
    }
    return d;
  }

  global.makeSpringEase = function (stiffness, damping, mass) {
    const k = stiffness || 240, c = damping || 17, m = mass || 1;
    const id = `${k}_${c}_${m}`;
    if (!cache.has(id)) {
      gsap.registerPlugin(CustomEase);
      const name = 'spring_' + id.replace(/\./g, '_');
      CustomEase.create(name, springPath(k, c, m));
      cache.set(id, name);
    }
    return cache.get(id);
  };
})(window);
```

**接入**：8.2 模板已引入 `CustomEase.min.js` 与 `spring.js`，图片/文字分别调用 `makeSpringEase(...)`。同一组参数全局只生成一次，命中缓存。

**时长与观感的权衡（重要）**：这条曲线把弹簧响应压缩进 `T_in`（默认 0.32s）。`bouncy`（300/12）在 0.32s 内约完成 2 次振荡，观感偏"抖"而非"弹"。若要 Remotion 那种绵长回弹，二选一：把 `mask.duration` 提到 0.5–0.6s（会缩短稳态 Ken Burns），或把 stiffness 降到 160–200。生成器应在 `warnings` 里提示该权衡。

**无 CustomEase 时的 fallback**：用 `elastic.out(1, p)` 近似，`p` 取 0.35（bouncy）/ 0.5（standard）/ 0.7（soft）；并在返回中明确标注"近似弹簧，非物理精确"。

**验证方法**：`preview_project` 下逐帧 seek 到 `clip.start + 0.1 / +0.2 / +0.3`，观察 scale 是否出现 `>1` 的 overshoot 再回落；若始终单调趋近 1，说明 spring 未生效。

### 18.2 节拍冲击特效层（beat impact）

**定位**：让画面不只是"踩点切换"，而是"随节拍脉冲"。全部由 CSS/GSAP 实现，不越出 1.2 节的 2D 边界，不引入粒子/光效引擎。

**隔离原则（必须遵守）**：冲击只作用于 `#stage` 容器或独立 overlay，**绝不作用于 clip 内的 `img`**——否则会和 7.1 节的入场 spring / Ken Burns 抢 `transform`，出现跳帧。同一 beat 只能选一种 stage 级冲击（`punch` / `shake` / `chromatic` 互斥）；`flash` 是独立图层，可叠加。

**数据契约**：`timeline.json` 新增字段，由 `build_timeline` 基于 `raw_beats` 生成：

```json
{
  "impact_profile": "normal",
  "beat_impacts": [
    { "t": 0.213, "frame": 6,  "type": "flash", "intensity": 0.55, "duration": 0.12, "strong": true },
    { "t": 0.645, "frame": 19, "type": "shake", "intensity": 0.30, "duration": 0.10, "strong": false },
    { "t": 1.077, "frame": 32, "type": "punch", "intensity": 0.45, "duration": 0.16, "strong": true }
  ]
}
```

生成规则：
- `raw_beats` 中落在 `grouped_beats`（强拍）上的点 → `strong=true`，默认分配 `flash` 或 `punch`；其余弱拍 → `shake`，强度减半。
- `frame = round(t * fps)`，冲击严格落在帧边界，不做亚帧插值。
- `profile` 倍率：`subtle` 0.5 / `normal` 1.0 / `aggressive` 1.6；最终强度 = `intensity × 倍率`，clamp 到 0.9。

**四种冲击**：

| type | 视觉 | 实现 |
|---|---|---|
| `flash` | 强拍闪白 | `#flash-layer` opacity 0 → A → 0，`mix-blend-mode:screen`，0.08–0.15s |
| `punch` | 瞬间推近再弹回 | stage scale `1 → 1+0.05A → 1`，回程用 `makeSpringEase(300,14,1)` |
| `shake` | 轻微震屏（衰减） | stage x/y 五段 keyframes，幅度 ±8A px，0.1s 内衰减归零 |
| `chromatic` | RGB 分离闪烁 | stage `filter: drop-shadow(红) drop-shadow(青)` 瞬时出现再淡出 |

**模板追加**（DOM 与 CSS 已在 8.2 加好；下列代码追加到 8.2 的 `<script>` 内，并在 `TIMELINE.clips.forEach(buildClip)` 之后调用 `buildImpacts(TIMELINE.beat_impacts, document.getElementById('stage'))`）：

```js
/* 冲击层与 MASTER 共用同一时钟，受 renderAtTime 统一驱动 */
function buildImpacts(impacts, stage) {
  const flash = document.getElementById('flash-layer');
  const GAIN = { subtle: 0.5, normal: 1.0, aggressive: 1.6 }[IMPACT_PROFILE] || 1;

  impacts.forEach((hit) => {
    const A = Math.min(hit.intensity * GAIN, 0.9);
    const t = hit.frame / FPS;          // 帧对齐，避开 beat 时间戳的小数漂移
    const d = hit.duration;

    if (hit.type === 'flash') {
      MASTER.fromTo(flash, { opacity: 0 },
        { opacity: A, duration: d * 0.25, ease: 'power2.out' }, t)
        .to(flash, { opacity: 0, duration: d * 0.75, ease: 'power2.in' }, t + d * 0.25);
    } else if (hit.type === 'punch') {
      MASTER.fromTo(stage, { scale: 1 },
        { scale: 1 + 0.05 * A, duration: d * 0.3, ease: 'power3.out' }, t)
        .to(stage, { scale: 1, duration: d * 0.7,
             ease: makeSpringEase(300, 14, 1) }, t + d * 0.3);
    } else if (hit.type === 'shake') {
      const dx = 8 * A, dy = 5 * A;
      MASTER.fromTo(stage, { x: 0, y: 0 }, { keyframes: [
        { x:  dx,      y: -dy,  duration: d * 0.2 },
        { x: -dx,      y:  dy,  duration: d * 0.2 },
        { x:  dx * .5, y: 0,    duration: d * 0.2 },
        { x: -dx * .25,y: 0,    duration: d * 0.2 },
        { x:  0,       y: 0,    duration: d * 0.2 }
      ], ease: 'none' }, t);
    } else if (hit.type === 'chromatic') {
      const px = 6 * A;
      MASTER.fromTo(stage, { filter: 'none' },
        { filter: `drop-shadow(${px}px 0 0 rgba(255,0,0,.55)) drop-shadow(${-px}px 0 0 rgba(0,255,255,.55))`,
          duration: d * 0.2, ease: 'power2.out' }, t)
        .to(stage, { filter: 'none', duration: d * 0.8 }, t + d * 0.2);
    }
  });
}
```

**参数建议**：闪白 0.10–0.15s、punch 0.16–0.20s、shake 0.10s、chromatic 0.08s；强拍闪白建议 ≤ 每 2 拍一次，否则易视觉疲劳。`subtle` 适合商务/知识类，`aggressive` 适合电子/舞蹈。

**与转场配合**：flash 的 `t` 若与 `grouped_beats` 重合，会掩盖转场黑闪，属于正向叠加，建议默认开启。

**Agent 调用变化**：`build_timeline` 增加可选参数 `impact_profile`（默认 `normal`，传 `"off"` 关闭），`preview_project` / `render_video` 无需改动；`confidence=low` 时冲击层随 `adjust_timeline` 微调后的时间线重新生成。

### 18.3 帧对齐、黑场与转场修正

**1) 帧对齐（收紧 11 节"时长误差 < 0.1s"）**：卡点视频不能以 0.1s 作为验收阈值（30fps 下是 3 帧）。改为：

- 总帧数 `N = round(audio_duration * fps)`；渲染帧 `f` 的时间恒为 `t = f / fps`。
- 所有 beat 时间戳先量化到帧 `f_beat = round(beat * fps)` 再写入时间线（18.2 已按此处理冲击）。
- ffmpeg 合成去掉 `-shortest`，改用 `-framerate {fps} -i frames/%06d.png -i bgm.mp3 -t {N/fps} -c:v libx264 -pix_fmt yuv420p -c:a aac`，避免音频被截断或多出静音尾帧。
- 验收改为：**beat 对应帧的偏差 ≤ 1 帧**，总时长误差 ≤ 1 帧。

**2) 首帧黑场（修正 8.2 / 5.3）**：`build_timeline` 把首镜 `start` 强制归零，原 `0 ~ grouped_beats[0]` 的空档并入首镜时长（Ken Burns 相应拉长），首帧不再是纯黑。若需保留前奏氛围，改为插入 pre-roll 层显示首图静态帧，而不是留空。

**3) 交叉溶解转场（修正 7.4 / 8.2）**：当前 `display:none/block` 切换使同一帧只有一张图可见，无法实现真正的 crossfade。若需要：相邻 clip 允许 `T_cross`（0.08–0.15s）重叠，改用 `opacity` + `zIndex` 控制可见性，旧镜退场尾部与新镜入场头部同时参与合成。这与 7.4 节"入场窗口不重叠"不冲突——重叠的是退场与入场的交叠段，不是两个完整入场窗口。默认可保持当前黑底收缩方案，把 crossfade 作为 `mask.type` 的枚举扩展。

**4) ticker 与 totalDuration（修正 8.2）**：

- 删除 `gsap.ticker.remove(gsap.ticker._tick)`（依赖私有属性且无必要；`paused` timeline 本就不自驱），仅靠 `MASTER.totalTime(t, false)` 驱动（统一用 `totalTime`，禁用 `repeat`/`yoyo`，见 8.1 与 21.1）。
- 删除 `MASTER.totalDuration({{ total_duration }})`：clips 的 tween 已排布到目标时长，再设一次会改变 `timeScale`，导致整体时序被缩放。

---

## 19. 电影级图片特效层（v2 增强）

> 本章响应 R13：实现**图片动态裁切、碎裂炸现、闪入（曝光过冲）、光效与调色**。这些效果原被 1.2 节排除，现重新纳入。核心结论：**不需要 AE 插件，不需要闭源许可**，用 SVG / Canvas 2D 图层合成即可做到电影感，真照片级景深与粒子物理仍需 WebGL 增强层（可选）。

### 19.1 渲染模型：从单层升级为四层合成

原 8.2 模板是「一张 img + 一个 caption」，物理上无法承载裁切/碎裂/光效叠加。v2 改为：

```text
┌─────────────────────────────────────────┐
│ L4  FX Overlay   光效/颗粒/暗角/闪白      │  mix-blend-mode 合成，不写业务动画
├─────────────────────────────────────────┤
│ L3  Caption      文字层（18.2 不变）      │
├─────────────────────────────────────────┤
│ L2  Subject      主图层：裁切/碎裂/闪入   │  可拆为 N 个 shard
├─────────────────────────────────────────┤
│ L1  Backdrop     背景层（模糊版/填充色）   │  碎片间隙露出，避免黑底
└─────────────────────────────────────────┘
```

**铁律不变**：所有层的动画仍只挂在 `MASTER` 一条时间线上，由 `renderAtTime(t)` 统一驱动，禁止 `rAF` 自驱动。L4 的滤镜参数若需动画，用 GSAP tween 一个 proxy 对象 + `onUpdate` 写回属性，保证同一 t 必出同一帧。

### 19.2 动态裁切（Cinematic Mask）

CSS 原生 `clip-path: polygon()` 支持 GSAP 字符串数值插值（GSAP 按数字位置逐项插值），无需 SVG 即可做不规则揭示。**硬约束（recon 实证，见技术侦察报告.md 假设 3 / 8.4）**：起止字符串必须满足「四同 + 显式两端」——同函数、同点数、同单位（推荐全 `%`，禁止 `%` 与无单位 `0` 混用）、同顺序；必须用 `fromTo` 同时写死起止字符串，不要依赖 `getComputedStyle`（浏览器会压缩重复值，导致缺失位被补 0）。下列 `irregularPolygon` 已满足四同（固定 12 点、全 `%`、同顺序），改动时必须保持。

**四种模式**：

| 模式 | 视觉 | 实现要点 |
|---|---|---|
| `iris` | 规则圆形/椭圆虹膜展开（原 circle 升级版） | `circle(r%)` 插值，r 加顶点扰动 |
| `irregular` | 不规则多边形揭示，边缘高低错落 | 固定 12 顶点，每点半径带 seed 扰动 |
| `slats` | 百叶窗式条带依次揭示 | 拆成 N 条 div，各自 `inset()` stagger |
| `path` | 沿自定义 SVG path 揭示 | SVG `clipPath` + `stroke-dasharray` 驱动 |

**`irregular` 可执行实现**（追加到 8.2 的 `<script>`）：

```js
/* 确定性半径扰动：同一 seed 永远得到同一形状，保证可复现 */
function irregularPolygon(p, seed, points = 12) {
  const pts = [];
  const grow = Math.min(1, p * 1.45);          // 略微过冲到画面外
  for (let i = 0; i < points; i++) {
    const a = (i / points) * Math.PI * 2;
    const jitter = 0.78 + 0.22 * Math.sin(seed * 13.7 + i * 7.3);
    const r = grow * jitter * 68;               // 68% 可覆盖竖屏角落
    pts.push(`${(50 + Math.cos(a) * r).toFixed(2)}% ${(50 + Math.sin(a) * r).toFixed(2)}%`);
  }
  return `polygon(${pts.join(',')})`;
}

function buildCinematicMask(clip, img) {
  if (clip.mask.mode !== 'irregular') return;
  const proxy = { p: 0 };
  const Tin = Math.min(clip.mask.duration, clip.duration * 0.5);
  MASTER.to(proxy, {
    p: 1, duration: Tin, ease: makeSpringEase(240, 17, 1),
    immediateRender: false,
    onUpdate: () => { img.style.clipPath = irregularPolygon(proxy.p, clip.index); }
  }, clip.start);
}
```

**`slats` 模式**：把 L2 拆成 N 个等高 div，第 i 条用 `clip-path: inset(0 0 ${(1-p)*100}% 0)`，起点 `clip.start + i*0.02`，共用同一张图并通过 `background-position` 对齐（对齐算法见 19.3）。

### 19.3 碎裂炸现（Shatter Reveal）

把图片切成 N×M 个矩形 shard，各碎片从散开状态带旋转回弹归位——这就是"炸现"。

**关键：碎片必须与完整图严丝合缝**。图片经 `object-fit:cover` 裁切过，直接用 `background-size:cover` 会导致碎片错位。先算 cover 后的真实尺寸：

```js
function coverRect(imgW, imgH, stageW, stageH) {
  const s = Math.max(stageW / imgW, stageH / imgH);
  return { w: imgW * s, h: imgH * s,
           x: (stageW - imgW * s) / 2, y: (stageH - imgH * s) / 2 };
}
```

**构建碎片 + 炸现时间线**：

```js
function buildShatter(clip, root, imgSrc, meta, stageW, stageH) {
  const cols = clip.fx.shatter.cols || 6;
  const rows = clip.fx.shatter.rows || 6;
  const cov = coverRect(meta.width, meta.height, stageW, stageH);
  const cellW = stageW / cols, cellH = stageH / rows;
  const Tin = Math.min(clip.mask.duration, clip.duration * 0.5);
  const spring = makeSpringEase(240, 17, 1);
  const shards = [];

  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const d = document.createElement('div');
      d.className = 'shard';
      d.style.cssText = `
        position:absolute;
        left:${c * cellW}px; top:${r * cellH}px;
        width:${cellW}px; height:${cellH}px;
        background-image:url(${imgSrc});
        background-size:${cov.w}px ${cov.h}px;
        background-position:${cov.x - c * cellW}px ${cov.y - r * cellH}px;
        will-change:transform,opacity;`;
      root.appendChild(d);
      shards.push(d);

      const ang = ((r * cols + c) * 137.5) * Math.PI / 180;   // 黄金角散开
      const dist = 50 + ((r * cols + c) % 5) * 18;
      const tw = {
        x: Math.cos(ang) * dist, y: Math.sin(ang) * dist,
        rotation: (c % 2 ? 1 : -1) * 14, scale: 0.55, opacity: 0
      };
      MASTER.fromTo(d, tw,
        { x: 0, y: 0, rotation: 0, scale: 1, opacity: 1,
          duration: Tin, ease: spring, immediateRender: false },
        clip.start + (c % cols) * 0.018);                     // stagger
    }
  }
  return shards;
}
```

**参数与约束**：
- 碎片数上限 **36/镜**（6×6），再多 DOM 与逐帧截图成本陡增。
- `stagger` 总和必须 `< Tin - 0.08`，否则最后几片入场被截断；builder 强制校验。
- 退场复用同一批 shard 反向 tween，不要重建 DOM。
- 背景层 L1 用**同图的高斯模糊版**（`filter: blur(24px) brightness(.5)`），碎片间隙露出的是模糊画面而非纯黑，这是"电影感"与"廉价感"的分界线。

### 19.4 闪入（Flash-in / Exposure Punch）

不是简单 `opacity 0→1`，而是**模糊 + 过曝 + 微缩放**三者同时回落：

```js
MASTER.fromTo(img,
  { scale: 1.12, opacity: 0,
    filter: 'blur(14px) brightness(2.4) contrast(1.35)' },
  { scale: 1, opacity: 1,
    filter: 'blur(0px) brightness(1) contrast(1)',
    duration: 0.22, ease: 'power4.out', immediateRender: false },
  clip.start);
```

**残影（motion blur 近似）**：CSS 无真运动模糊，用 3 层 ghost 近似——复制 img 三次，透明度 0.35/0.2/0.1，位移 `x: -6/-12/-18`，在入场窗口内随主图一起归零。对高速推近镜头，这一招观感接近电影级 motion blur。

**过曝白闪**：与 18.2 的 `flash` 冲击层叠加使用——先白闪 0.08s，再从过曝状态回落到图像，是最典型的"闪入"节奏。

### 19.5 光效与调色层

| 效果 | 实现 | 成本 |
|---|---|---|
| 扫光（light sweep） | 倾斜白色渐变条 `linear-gradient(105deg, transparent 40%, rgba(255,255,255,.45) 50%, transparent 60%)`，`mix-blend-mode:overlay`，xPercent -120 → 120 | 极低 |
| 镜头光晕（lens flare） | 预生成 PNG/SVG 光斑素材，beat 触发 scale+opacity 动画 | 低，需素材 |
| 胶片颗粒 | SVG `feTurbulence` + `feColorMatrix type="saturate" values="0"`，overlay 混合 | **高**，见下 |
| 暗角 | `radial-gradient(ellipse, transparent 55%, rgba(0,0,0,.55) 100%)` 常驻覆盖 | 极低 |
| LUT 调色 | `filter: sepia() saturate() hue-rotate()` 组合，或 SVG `feColorMatrix` | 低 |

**性能警告**：`feTurbulence` 每帧重算会显著拖慢 headless 渲染。颗粒必须**预渲染成一张静态 PNG 纹理**（平铺 L4 层），只用 opacity 做轻微呼吸，不要逐帧生成噪声。

### 19.6 WebGL 增强层（可选，按需启用）

以下效果不要硬用 CSS 做，直接上 WebGL，否则性能与质量都不达标：

- 真粒子（烟雾、尘埃、火花）
- 真实景深虚化
- 复杂体积光 / 流体扭曲

**接入方式**：在 L4 之上叠一个 `<canvas>`，用 PixiJS（MIT 许可）或 raw WebGL；暴露 `window.renderShaderFrame(t)`，由 8.2 的 `renderAtFrame(f)` 一并调用，shader uniform 全部由 t 计算。headless Chromium 需加 `--use-gl=angle --use-angle=swiftshader`，且必须在复核清单确认目标环境支持。

**代价**：引入 JS 库、GPU/软件渲染依赖、逐帧渲染时间增加。默认关闭，`config.yaml` 里 `fx.webgl.enabled: false`，只有明确需要时才开。

### 19.7 风格预设（一键在"电影感"与"极平淡"间切换）

参数齐全但组合繁琐，故增加 `style_preset`，由 `build_timeline` 一键展开成整套参数：

| preset | impact | spring | mask | reveal | grade |
|---|---|---|---|---|---|
| `minimal` | `off` | soft 160/22 | `iris` 0.4s | fade 0.3s | `none` |
| `standard` | `normal` | standard 240/17 | `irregular` | flash-in 0.22s | `film` 轻颗粒 |
| `cinematic` | `subtle` | standard 240/17 | `irregular` + 模糊 L1 | flash-in + 残影 | `teal-orange` + 暗角 |
| `aggressive` | `aggressive` | bouncy 300/12 | `shatter` 6×6 | flash-in + punch | `noir` 高对比 |

`cinematic` 是"电影级但克制"的推荐档：不炸、不闪瞎，靠裁切、碎片、景深模糊和调色出质感。

### 19.8 数据契约扩展

`build_timeline` 入参新增（与既有 `effects` 合并，冲突时以 `cinematic_fx` 为准）：

```json
{
  "style_preset": "cinematic",
  "cinematic_fx": {
    "mask": { "mode": "irregular", "duration": 0.38, "seed_from": "clip.index" },
    "shatter": { "cols": 6, "rows": 6, "stagger": 0.018 },
    "reveal": { "type": "flash-in", "blur_px": 14, "brightness": 2.4,
                "overshoot": 1.12, "duration": 0.22, "ghosts": 3 },
    "backdrop": { "type": "blur", "blur_px": 24, "brightness": 0.5 },
    "grade": { "preset": "teal-orange", "grain_png": "assets/grain.png",
               "vignette": 0.45 },
    "light": { "sweep": true, "flare_at_beat": [0, 4], "flare_asset": "assets/flare.png" }
  }
}
```

`validate_assets` 相应新增校验：`grain_png` / `flare_asset` 存在性，`imgW/imgH` 元信息必须返回（19.3 cover 对齐依赖它）。

### 19.9 验收标准

- **对齐**：碎片复原后与完整图逐像素对比，切点不得出现缝隙或错位。
- **确定性**：同一 t 连续渲染两次，截图 hash 一致（proxy `onUpdate`、`feTurbulence` 预渲染都要过这一关）。
- **无黑闪**：碎裂/切片退场期间，间隙由 L1 模糊背景填充，不得露黑底。
- **性能**：6×6 碎片 + 静态颗粒纹理下，1080×1920 单帧渲染耗时需实测并记录到 `render_video` 返回。
- **风格跨度**：`minimal` 与 `cinematic` 两档必须都能从同一套模板渲染，不得为不同风格维护两套代码。

---

## 20. 主体感知聚焦与分层运镜（v3 增强）

> 本章响应 R14：解决"一张 10 人群像，镜头自动推近到其中某一个人"这类需求。这是基础卡点工具与行业先进视频工具的分水岭。**核心不是 AI 生成，而是 AI 分析 + 确定性渲染**：AI 只在离线阶段输出主体框/分割/深度，真正的逐帧渲染仍走 `renderAtTime(t)`，不引入任何在线推理到渲染循环。

### 20.1 架构：视觉分析层作为可选 Sidecar

```text
┌──────────────────────────────────────────────────┐
│ 新增：src/vision/  视觉分析层（可选，离线一次性）    │
│  detect_subjects   → 人/脸/物体框 + 编号            │
│  lock_subject      → 文字 / 参考图 / 索引 三种锁定   │
│  segment_subject   → SAM 抠出主体 PNG（透明）        │
│  estimate_depth    → 单目深度图（辅助视差/雾化）     │
└────────────────────┬─────────────────────────────┘
                     │ 输出 subject.json / subject.png / depth.png（落盘）
┌────────────────────▼─────────────────────────────┐
│ 既有：build_timeline 把 bbox 换算成 focus 轨迹       │
│ 既有：generate_composition 把分层图写进第 19 章四层   │
│ 既有：render_video 逐帧确定性渲染（无 AI 推理）       │
└──────────────────────────────────────────────────┘
```

**降级契约**：视觉分析层依赖重（PyTorch / ONNX Runtime），作为**独立可选 MCP 工具**提供。未安装时 `analyze_subjects` 返回 `BINARY_NOT_FOUND`，Agent 退化为 R6 手工 `pivot_x/y`，主流程不中断。所有分析结果**落盘到 workspace**，渲染阶段只读文件、不跑模型。

### 20.2 模型选型（按需装，不强制全装）

| 能力 | 推荐 | 备选 | 许可提醒 |
|---|---|---|---|
| 人/物体检测 | RT-DETR | YOLOv8 | YOLO 系列注意 AGPL / Commons Clause，商用需核对；RT-DETR 更宽容 |
| 人脸聚类锁定 | InsightFace (ArcFace) | DeepFace | 模型权重多为非商用研究授权，**商用前必须核对** |
| 文字锁定 | Grounding DINO | OWL-ViT | Apache 2.0 系为主，核对具体权重 |
| 主体分割 | SAM / SAM2 | — | Apache 2.0，质量是分层视差的关键 |
| 单目深度 | Depth Anything v2 | MiDaS | 用于视差强度与雾化，非真光学景深 |

> 许可复核已加入第 17 节清单。默认只启用检测 + 分割（质量收益最大、许可最清晰），人脸聚类与深度按需开启。

### 20.3 三种锁定方式

| selector | 入参 | 适用场景 |
|---|---|---|
| `text` | `{"type":"text","query":"穿红衣服的女人"}` | Agent 自动化，最常用；Grounding DINO 直接出框 |
| `reference` | `{"type":"reference","image":"ref.jpg"}` | 用户给一张目标人脸照片，embedding 比对 |
| `index` | `{"type":"index","value":3}` | `analyze_subjects` 返回候选列表，人工/Agent 点选 |

**`analyze_subjects` 出参**（候选列表带 `index` 供后续点选，同时写 `subjects.json`）：

```json
{
  "image": "assets/group.jpg",
  "image_size": [1080, 1440],
  "subjects": [
    { "index": 0, "label": "person", "bbox": [0.08, 0.21, 0.19, 0.63], "confidence": 0.97 },
    { "index": 1, "label": "person", "bbox": [0.31, 0.18, 0.44, 0.61], "confidence": 0.95 },
    { "index": 3, "label": "person", "bbox": [0.62, 0.15, 0.77, 0.66], "confidence": 0.93 }
  ],
  "count": 10,
  "artifacts": { "depth": "assets/depth.png", "mask_dir": "assets/masks/" }
}
```

### 20.4 核心：bbox → 聚焦运镜参数（可执行换算）

这是把"AI 看到的框"变成"GSAP 可渲染的 scale/x/y"的关键公式，必须精确，否则推近会跑偏。

```js
/* cov 来自 19.3 的 coverRect(imgW,imgH,stageW,stageH)
   bbox 为归一化 [x1,y1,x2,y2]，相对原图 */
function bboxToFocus(bbox, cov, stageW, stageH, fill = 0.62, fit = 'contain') {
  const [x1, y1, x2, y2] = bbox;

  // 主体在舞台坐标系中的位置与尺寸
  const cx = cov.x + (x1 + x2) / 2 * cov.w;
  const cy = cov.y + (y1 + y2) / 2 * cov.h;
  const sw = (x2 - x1) * cov.w;
  const sh = (y2 - y1) * cov.h;

  // contain=主体完整入画（推荐，避免切头）；cover=填满画面（可能裁切）
  const scale = fit === 'cover'
    ? Math.max(stageW * fill / sw, stageH * fill / sh)
    : Math.min(stageW * fill / sw, stageH * fill / sh);

  // 以画面中心为原点缩放后，平移使主体中心回到画面中心
  const tx = (stageW / 2 - cx) * scale;
  const ty = (stageH / 2 - cy) * scale;
  return { scale, x: tx, y: ty, transformOrigin: '50% 50%', subjectCenter: [cx, cy] };
}
```

**接入 Ken Burns**：把结果填进 8.2 模板的稳态段，`start_scale=1, end_scale=focus.scale, start_x/y=0, end_x/y=focus.x/y`，`ease: 'power2.inOut'`，时长取镜长的 60%–80%，最后 20% 停留（hold）让观众看清目标。入场/退场仍走第 7/18 章三段时间线，互不冲突。

**边界保护**：builder 必须校验 `scale ≤ 4.0`（超过 4 倍原图会糊），且推近后主体框不能越出画面（`tx/ty` 使 bbox 四角落在舞台内），否则降低 `fill` 并写 `warnings`。

**实测洞察（Wave 6，真实 RT-DETR 权重实证）**：`fit='contain'` 只保证"主体完整入画"，**并不保证 scale > 1**。用 `PekingU/rtdetr_r50vd` 在 1536×1024 群像图上实测：中央人物的**全身框**高度约为画高的 0.85（`bbox=[0.4096,0.1462,0.6053,0.9984]`），在舞台 1080×1920、`fill=0.62` 下 `stageH*fill/sh` 成为 min 项，算出 **`scale=0.727`——镜头被拉远**，与"推近到某人"的诉求正好相反。这不是公式错误：`contain` 必须把整条腿塞进 62% 画高。正确做法是在 `bboxToFocus` 之前，从全身框**派生上半身子框（头+肩，如取框高的前 45%）**再走 `contain/fill`，得到 **`scale=1.1878` 的真推近**。两者居中误差均为 **0.0%**，`contain` 四角均不越界。结论：**推近人物时应默认按上半身区域计算 focus（见 20.6 `anchor_region`），不要直接把全身检测框喂给 `bboxToFocus`。** 实测数据见 `fixtures/reports/detection_report.json`。

### 20.5 多主体漫游与分层视差

**漫游（tour）**：一个镜头内从主体 A 推到主体 B，生成两个 `bboxToFocus` 结果，GSAP 在两点间插值：

```js
MASTER.fromTo(img,
  { scale: focusA.scale, x: focusA.x, y: focusA.y },
  { scale: focusB.scale, x: focusB.x, y: focusB.y,
    duration: T * 0.8, ease: 'power2.inOut', immediateRender: false },
  clip.start);
```

**分层视差（让静态图有空间感）**：用 SAM 把主体抠成透明 PNG，与背景拆成两层，前景推近快、背景推近慢，形成纵深错觉：

```text
L1 背景层（原图 + blur(8px)）   scale: 1 → 1 + (focus.scale-1) × 0.35
L2 主体层（SAM 抠出的 PNG）      scale: 1 → focus.scale，x/y 同 bboxToFocus
```

深度图可进一步做**雾化**：距离越远的区域叠一层半透明白，模拟空气透视。这是静态图能做到的最接近电影航拍推镜的效果。

### 20.6 数据契约扩展

`build_timeline` 的 clip 新增 `focus` 字段（与 `kenburns` 二选一，`focus` 优先）：

```json
{
  "focus": {
    "mode": "subject",
    "selector": { "type": "text", "query": "穿红衣服的女人" },
    "subject_index": 1,
    "bbox": [0.31, 0.18, 0.44, 0.61],
    "fill": 0.62,
    "fit": "contain",
    "anchor_region": "upper_body",
    "hold_ratio": 0.2,
    "parallax": { "enable": true, "bg_ratio": 0.35, "haze": 0.15 },
    "tour_to": { "subject_index": 7, "bbox": [0.70, 0.20, 0.82, 0.65] }
  }
}
```

`anchor_region`（可选，枚举 `full | upper_body | face`，**默认 `upper_body`**）：指定把检测框交给 `bboxToFocus` 前取哪一块区域计算 focus——
- `full`：使用检测框原样；对全身人物会如 20.4 实测那样被 `contain` 拉远（`scale<1`），仅用于明确要拍全身的镜头；
- `upper_body`：从全身框派生头+肩区域（推荐取框高前 45%），得到 `scale>1` 的真推近且避免切头，是**推近人物时的默认行为**；
- `face`：仅取面部区域，需要人脸/关键点检测，许可风险见 20.2 红字警告，默认不启用。

该字段只影响**离线分析阶段**的 bbox 派生，不改变渲染循环的确定性语义。实现状态：文档先行（CODING_STANDARDS 第 6 节），代码待补。

新增三个 MCP 工具（签名遵循第 5 章统一 `{ok,data,error}`）：

| 工具 | 入参 | 出参 |
|---|---|---|
| `analyze_subjects` | `image_path`, `detect[]`, `with_depth` | 候选列表 + 落盘路径 |
| `lock_subject` | `analysis_json`, `selector`, `fill`, `fit` | 锁定的 bbox + `focus` 数据块 |
| `generate_focus_path` | `focus`, `timeline_clip`, `cov` | 写入 clip 的 scale/x/y 轨迹 + warnings |

### 20.7 验收标准

- **锁定正确率**：`text` / `reference` selector 在标准测试集上目标框 IoU ≥ 0.5；错误时返回候选列表让 Agent 改用 `index`。
- **居中误差**：推近结束后主体中心与画面中心偏差 ≤ 画面宽/高的 5%。
- **完整入画**：`contain` 模式下主体 bbox 四角不得越界，头部不得被切。
- **确定性**：同一 `focus` 数据连续渲染两次，逐帧截图 hash 一致（AI 推理不在渲染循环内）。
- **降级可用**：未装视觉依赖时，`render_video` 不因缺少 `focus` 而失败，按 R6 手工锚点出片。

### 20.8 已知边界（如实说明）

- 静态图 + 单目深度只能做**近似视差**，不是真 3D 重建；真正的多视角运镜（NeRF / 3DGS）不在本方案范围。
- 严重遮挡、侧脸、小于画面 5% 的目标，检测与聚类准确率下降；此时必须走 `index` 人工兜底。
- 人脸比对的模型权重许可各异，商用部署前必须逐项核对（见 17 节清单）。

---

## 21. 确定性渲染硬契约（渲染 agent 施工依据）

> 本章是**渲染层施工规范**，不是建议清单。全部条款来自 recon 一手实证（官方确定性文档 + HeyGen 工程博客），证据见 `docs/技术侦察报告.md` 假设 4 与补充侦察节。
> 官方依据：https://hyperframes.heygen.com/concepts/determinism ；https://www.heygen.com/research/html-to-video
> 核心保证：**Same input, identical output. Every time.** 任何一条契约被破坏，同一 t 就可能出不同帧。

### 21.0 前置铁律（与决策 #3 一致）

- 所有层动画只挂**一条** `MASTER` 时间线，保持 `paused`，唯一驱动方式是 `MASTER.totalTime(t, false)`（禁止 `time()`，禁止 `repeat` / `yoyo` / 无限时间线，禁止 `totalDuration()` 二次设置，见 8.1 / 8.2 / 18.3）。
- 渲染循环内**零 AI 推理**；AI 只在离线分析阶段运行。
- 源码中出现 `requestAnimationFrame` 即为 bug（`CODING_STANDARDS.md` 第 2 节）。

### 21.1 七条硬契约

| # | 契约 | 可执行要求 | 违反后果 |
|---|---|---|---|
| C1 | **时间量化 / 整数帧时钟** | 引擎时间恒为 `time = floor(frame) / fps`；本项目 `renderAtTime(t)` 内做 `Math.round(t*FPS)/FPS`；所有 beat 时间戳先量化到帧再写入时间线（18.3）；`fps`/`width`/`height` 在 frame 0 前锁定，渲染中不得改变 | 浮点误差导致相邻帧像素差异；两次渲染 hash 不一致 |
| C2 | **ready gate（资源门控）** | 所有资源（字体、图片、预解码视频帧）必须在第一帧前加载完成；截图前同时等待 `window.__ready` 与 `document.fonts.ready`；**禁止 `page.waitForFunction`**（依赖 rAF，BeginFrame 下永不返回），改为 Node 侧 `page.evaluate()` + `setTimeout()` 轮询；preview/render 共用同一 runtime bundle 并以 sha256 校验 | 首帧文字未渲染、黑图、或渲染器 generic timeout |
| C3 | **beginFrame 原子截图** | 用 CDP `HeadlessExperimental.beginFrame` 一次完成 layout→paint→composite→screenshot；**禁止裸 `Page.captureScreenshot`**；一帧 = 一次 `seek(t)` + 一次 `beginFrame` + 一张截图；worker 数、分块边界、seek 顺序不得改变像素 | 截图竞态：字体未加载、GSAP 最终样式未提交、GPU 未完成绘制即被捕获 |
| C4 | **Docker 固定环境** | 严格复现必须在 Linux Docker，固定 Chromium（`chrome-headless-shell`）版本、字体集合、FFmpeg 编码器：`npx hyperframes render --docker --output output.mp4`；Windows/macOS 仅用于开发调试，不作为像素级验收环境 | 跨机器字体/浏览器版本差异造成 1 像素偏移 |
| C5 | **视频预解码** | `<video>` 不在渲染时播放；用 FFmpeg 按目标 fps 预抽成编号 JPEG 帧；渲染时隐藏原 `<video>`，插入对应帧的 `<img>` 并复制 computed styles，使 transform / opacity / object-fit 继续由时间线驱动 | 浏览器异步解码与 display clock 让同一 t 产生不同画面 |
| C6 | **禁用 motion blur** | 官方明确 motion blur 是确定性的例外；关闭浏览器/合成器层真运动模糊与未在契约内的滤镜模糊。19.4 的 3 层 ghost「残影」是几何 + 透明度的确定性近似，允许使用；启用真模糊必须单独做 hash 回归 | 运动模糊依赖历史帧，破坏「单帧只由 t 决定」 |
| C7 | **禁用隐式时间源与随机** | 渲染路径禁止 `Date.now()`、`performance.now()`、系统定时器、未 seed 的 `Math.random()`、网络 fetch；所有随机必须 seed（如 19.2 用 `clip.index`）；所有素材预置本地；渲染层不得引入在线推理 | 每次运行得到不同帧；不可复现 |

### 21.2 四大坑与解法（生产实证）

**坑 1：截图与渲染竞态。** `Page.captureScreenshot` 在合成器愿意交出图像时即返回，不等于「布局完成 + 字体加载 + GSAP 最终样式提交 + GPU 绘制完成」。表现：首帧文字未渲染、SVG 仍为默认样式、`<video>` 显示 300×150 默认尺寸。
**解法**：生产/CI 走 Linux Docker + `chrome-headless-shell` + `HeadlessExperimental.beginFrame` 原子捕获；非 Linux 环境只能走 `Page.captureScreenshot` + 启发式等待（`fonts.ready`、computed styles、pixel hash 比较），**不得作为验收依据**。

**坑 2：BeginFrame 冻结事件循环，`fonts.ready` 永久挂起。** 开启 `--enable-begin-frame-control` 后主线程不再自动 tick，rAF / `setTimeout` / microtask 不推进，`document.fonts.ready` 永不 resolve，GSAP 脚本虽加载但页面无法完成准备。
**解法**：warmup loop——加载期间每 33ms 发送一次 `HeadlessExperimental.beginFrame`，带 `noDisplayUpdates: true`，只推进事件循环、不产出真实帧；直到 composition ready 且字体加载完成才停止；真实 capture 从 warmup 之后的 frame time 开始，避免时间倒退。把 Google Fonts `@import` 改为本地 base64 字体（如 `@fontsource`），消除网络波动。

**坑 3：浏览器与 GSAP 各依赖自己的时钟。** `<video>`、CSS 动画、canvas/rAF、异步解码、display clock 都会让「同一时间点」产生不同画面；浏览器天生为流畅播放优化，不为逐帧确定性设计。
**解法**：**Seek, don't play**——每个 composition 暴露 `window.__hf = { duration, seek(t) }`；渲染器从不调用 `play()`，逐帧调用 `seek(i/fps)`；GSAP 作为默认 runtime 用 `timeline.pause() + timeline.totalTime(t, false)`；其他动画库通过 FrameAdapter 适配，生命周期固定 `init → seekFrame(frame)×N → destroy`，且 `seekFrame` 必须可乱序、同帧同结果、帧提交后无异步解析。

**坑 4：`waitForFunction` 与浏览器内部时钟在确定性模式下失效。** `page.waitForFunction` 底层用页面内 rAF 轮询，BeginFrame 模式下 rAF 不触发，等待 ready 挂起后以 generic timeout 告终；普通 Chrome 的 threaded animation / scrolling / image decoding / surface timing 也会引入非确定性。
**解法**：改用 Node 侧 `page.evaluate()` + `setTimeout()` 手动轮询 ready；渲染使用固定 flags：`--deterministic-mode`、`--enable-begin-frame-control`、`--run-all-compositor-stages-before-draw`、`--disable-threaded-animation`、`--disable-threaded-scrolling`、`--enable-surface-synchronization`；通过 `frameTimeTicks` + `interval` 控制虚拟时间；`--deterministic-mode` 下 `performance.now()` 也由 frame ticks 驱动而非系统时钟。

### 21.3 验收门槛（不通过则不得宣称可用）

1. **确定性回归**：同一 composition、同一 `t`，在 Linux Docker 中渲染两次，逐帧截图 SHA256 完全一致（`CODING_STANDARDS.md` 第 5 节）。
2. **产物校验**：产物存在、时长误差 ≤ 1 帧、首帧非纯黑（`CODING_STANDARDS.md` 第 2 节 `render_video` 不变量）。
3. **runtime 一致性**：preview 与 render 使用同一 runtime bundle，sha256 一致；否则「预览看到的」不是「生成 MP4 的」。
4. **静态扫描**：渲染路径源码不得出现 `requestAnimationFrame`、`Date.now()`、未 seed 的 `Math.random()`、fetch 调用；时间线不得出现 `repeat: true` / `yoyo: true`。
5. **失败排查顺序**：字体与资源门控 → 隐式时间源 → 媒体预解码 → Docker/浏览器版本 → motion blur。**禁止未经排查就更换后端**；HyperFrames 的确定性能力已实证，问题几乎总在 composition 或运行环境。

### 21.4 落地路径（开发 / 验收环境分工）

1. **开发在 Windows**：HyperFrames 官方 Quickstart（https://hyperframes.heygen.com/quickstart ）确认支持 Windows（含 Windows on Arm）。安装 Node.js 22+ 与 FFmpeg 后，`npx hyperframes init` / `preview` / `render` 即可本地迭代。该环境**只用于开发调试，不作为像素级验收依据**。
2. **像素级回归在 Linux Docker**：固定 Chromium（`chrome-headless-shell`）版本、字体集合与 FFmpeg 编码器，命令 `npx hyperframes render --docker --output output.mp4`；preview 与 render 必须共用同一 runtime bundle，并以 sha256 校验一致。
3. **验收门槛**：同一 composition、同一 `t` 在 Linux Docker 中渲染两次，逐帧截图 SHA256 必须一致；同时满足产物存在、时长误差 ≤ 1 帧、首帧非纯黑。不通过时按 21.3 第 5 条顺序排查，**禁止直接更换后端**。

---

**文档结束**

> （注：部分内容由豆包 AI 生成）