# beat-slideshow-mcp

节拍卡点图文视频生成 MCP Server。基于 HyperFrames 开源 CLI（Apache 2.0）做薄封装，
AI 只在离线分析阶段运行、渲染循环内零推理，以单条 GSAP 时间线 + 时间量化保证确定性出片。

> ⚠️ **项目用途：仅供研究 / 学习 / 演示，不得商用。** 演示用群像/人像图为 AI 生成素材，
> 项目方已确认不可商用（2026-11-18）。详见 `docs/许可核验报告.md` 末尾声明。若要商用，
> 请先替换全部演示图片为已授权素材并重新核验许可。

> **当前状态（2026-11-18，权威源为 `WAR_MAP.md` 顶部）**：代码全部就位，真实端到端出片已实证，
> 全量 pytest **214 passed / 4 skipped**（skips 均为环境缺口：ML 真实权重 / 渲染环境，明确 skip 不 mock），
> 模板 11/11 校验 PASS，对标均分 **4.125 / 5**（已越过用户 4.1 目标，详见 `docs/业界对标评估.md`）。
> 核心能力已真实端到端实证（非 mock）：群像 10 人锁定红夹克目标（RT-DETR 真实权重）、端到端真实出片
> （HyperFrames CLI + ffmpeg，1080×1920/8s MP4）、主体推近进成片（focus_render scale=1.188）、转场黑闪修复、
> 九类特效进成片、多主体漫游 + 分层视差、**GPU 自适应 WebGL 真激活**（有显卡自动用、无显卡降级，双档确定性 PASS）。
> **像素级严格复现已在 CI 实跑 PASS**：`.github/workflows/render-determinism.yml`（run `44099644422`）在
> Linux Docker 上同 t 渲染两次、5 帧 SHA256 全一致；本机 Windows 无 Docker，本地脚本明确 SKIP，未伪造 PASS。

## 工程结构

```
p2v/
├── main.py                     # MCP 入口：20 个工具注册、统一 {ok,data,error}
├── config.yaml                 # 后端选择、超时、分辨率、WebGL 模式(auto/on/off)、workspace 保留时长
├── pyproject.toml              # 项目元数据 + 固定版本依赖
├── requirements.txt
├── src/
│   ├── contracts.py            # 全部 Pydantic v2 数据契约（方案第 6/18/19/20 章）
│   ├── analyzer/               # 音频节拍检测 + 素材校验（librosa / ffprobe）
│   ├── timeline/               # beat → timeline 编排 + 微调
│   ├── backend/                # 渲染后端抽象接口 + HyperFrames CLI 实现（--docker）
│   ├── builder/                # 工程生成器 + composition.html.j2 模板（totalTime/帧量化/特效）
│   ├── service/                # MCP 服务层：preview / pipeline
│   ├── vision/                 # 可选视觉分析 Sidecar（RT-DETR/SAM/深度，惰性导入）
│   └── utils/                  # task_id 沙箱 + 安全子进程封装
├── tests/                      # 契约/分析/编排/渲染确定性/视觉/管线 回归测试
├── fixtures/                   # 真实可再生成素材 + 验收脚本（见 fixtures/README.md）
│   ├── audio/benchmark/        # 节拍标准测试集（90/120/140 BPM + 弱鼓点 + 变速）
│   ├── verify_beat.py          # 单段 BGM 节拍验收
│   ├── verify_beat_benchmark.py# 节拍标准测试集对齐率验收
│   ├── verify_determinism.py   # Docker 像素复现回归（本地检测，无 Docker 则 SKIP）
│   └── e2e_*_render.py         # 真实出片实证脚本（出片/推近/特效）
├── .github/workflows/          # render-determinism.yml（Linux Docker 像素回归 CI）
├── docs/                       # 方案（21 章）/ 技术侦察 / 业界对标 / 许可核验报告
├── workspace/                  # 运行时生成：{task_id}/ 隔离目录
└── logs/                       # 运行时生成：子进程 stdout/stderr 落盘
```

各层职责边界见 `WAR_MAP.md`；编码约束见 `CODING_STANDARDS.md`；需求与字段定义
的权威来源是 `docs/节拍卡点图文视频MCP方案.md`（21 章）。

## 环境前置依赖（MCP 不负责自动安装）

- Python 3.11+（本项目锁定依赖见 `requirements.txt` / `pyproject.toml`）
- Node.js 22+（HyperFrames 官方推荐）
- ffmpeg 6.1+（全局可调用）
- 中文字体（Noto Sans SC 等；字体集在 Docker 镜像内固定以保证确定性）
- Docker（**仅像素级确定性 CI 验收需要**，Linux；本机 Windows 无 Docker 可正常开发渲染，CI 已实跑 PASS）

## 安装与测试

```powershell
python -m pip install -r requirements.txt
python -m pytest
```

仅测数据契约：

```powershell
python -m pytest tests/test_contracts.py
```

> 工程锁定版本优先于方案 13.2 的建议版本：`pyproject.toml` / `requirements.txt`
> 用 `==` 固定版本以保证可复现，方案 13.2 的 `>=` 仅为引导性写法。

> 依赖缺失的测试显式 skip 并写明原因，禁止用 mock 掩盖（CODING_STANDARDS 第 5 节）。
> 渲染确定性回归见 `fixtures/verify_determinism.py` 与 `tests/test_render_determinism.py`：
> 本机无 Docker 时明确 SKIP，CI 已在 Linux runner 实跑 PASS（run `44099644422`）。

## 专项验收脚本

```powershell
# 节拍标准测试集（5 段合成音频，产出对齐率表）
python fixtures/audio/benchmark/generate_benchmark.py
python fixtures/verify_beat_benchmark.py    # 报告 fixtures/reports/beat_benchmark.json

# 像素级确定性回归（本机无 Docker → 明确 SKIP；CI 已在 Linux Docker 实跑 PASS）
python fixtures/verify_determinism.py

# GPU 自适应 WebGL 实证（真机探测 + 双档确定性 + 开/关像素差）
python fixtures/e2e_webgl_render.py
```

## GPU 自适应渲染

渲染按"无显卡可跑、有显卡充分利用"设计，由 HyperFrames CLI 原生的 GPU 探测驱动：

- **WebGL 模式**（`config.yaml` 的 `fx.webgl.mode`；MCP `render_video` 的 `gpu_mode` 参数）：
  - `auto`（默认）：不传 GPU flag，CLI 首启探测——有硬件 GPU 自动启用 WebGL 粒子 / 体积光增强层，无 GPU 自动降级 SwiftShader 软件渲染；
  - `on`：强制 `--browser-gpu`（Docker 模式下明确拒绝，避免无 GPU 环境卡死）；
  - `off`：强制 `--no-browser-gpu`，关闭显卡增强。
  - 旧 `fx.webgl.enabled: true/false` 仍兼容，分别映射为 `on` / `off`。
- **分辨率**：默认 1080×1920 / 9:16 / 30fps（短视频平台原生交付规格）；MCP `render_video` 的 `resolution` 参数可按需透传 4K 等预设。
- **可观测性**：浏览器端 `window.__WEBGL_INFO__` 记录 `{enabled, mode, renderer, reason}`；`diagnose_webgl` 工具可读取当前配置、模板能力、CLI GPU 支持与最近一次渲染的 GPU probe 日志。
- **确定性口径**：硬件档与软件档各自严格一致（同设备 / 同 GPU 模式 / 同 t 两次 SHA256 相同），跨档允许像素不同——这是"同档确定性"，不是跨设备逐像素一致。
- 实证与 4K 专家裁决见 `docs/GPU自适应渲染评估.md`。

## 许可

渲染栈与视觉模型许可核验见 `docs/许可核验报告.md`：HyperFrames / RT-DETR / SAM / Grounding DINO /
Depth Anything V2 可商用；Noto Sans SC = SIL OFL 1.1 可商用；**人脸 embedding 权重（InsightFace
`buffalo_l` 等）官方限定非商用研究，商用需另行授权**——本项目 `anchor_region="face"` 不启用。
