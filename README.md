# beat-slideshow-mcp

节拍卡点图文视频生成 MCP Server。基于 HyperFrames 开源 CLI（Apache 2.0）做薄封装，
AI 只在离线分析阶段运行、渲染循环内零推理，以单条 GSAP 时间线 + 时间量化保证确定性出片。

> ⚠️ **项目用途：仅供研究 / 学习 / 演示，不得商用。** 演示用群像/人像图为 AI 生成素材，
> 项目方已确认不可商用（2026-11-18）。详见 `docs/许可核验报告.md` 末尾声明。若要商用，
> 请先替换全部演示图片为已授权素材并重新核验许可。

> **当前状态（2026-11-17，权威源为 `WAR_MAP.md` 顶部）**：代码全部就位，真实端到端出片已实证，
> 全量 pytest **177 passed / 4 skipped**（skips 均为环境缺口：ML 真实权重 / 渲染环境，明确 skip 不 mock）。
> 五个核心能力已真实端到端实证（非 mock）：① 群像 10 人锁定红夹克目标（RT-DETR 真实权重）；
> ② 端到端真实出片（HyperFrames CLI + ffmpeg，1080×1920/8s MP4）；③ 主体推近进成片（focus_render
> scale=1.188）；④ 转场切点纯黑闪修复（模糊背景兜底）；⑤ 电影级特效子集进成片（irregular 裁切 +
> flash-in + 四档调色，抽帧像素级证据）。
> **唯一剩余验收项**：像素级严格复现（同 t 两次渲染 SHA256 一致）需 Linux Docker 验证——
> CI 配置已就位（`.github/workflows/render-determinism.yml`），本机 Windows 无 Docker，脚本明确 SKIP，未伪造 PASS。

## 工程结构

```
p2v/
├── main.py                     # MCP 入口：10 个工具注册、统一 {ok,data,error}
├── config.yaml                 # 后端选择、超时、分辨率、并发、workspace 保留时长
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
- Docker（**仅像素级确定性验收需要**，Linux；Windows/macOS 可用于开发调试）

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
> 本机无 Docker 时明确 SKIP，CI 在 Linux runner 上实跑。

## 专项验收脚本

```powershell
# 节拍标准测试集（5 段合成音频，产出对齐率表）
python fixtures/audio/benchmark/generate_benchmark.py
python fixtures/verify_beat_benchmark.py    # 报告 fixtures/reports/beat_benchmark.json

# 像素级确定性回归（本机无 Docker → 明确 SKIP 并打印 CI 复现命令）
python fixtures/verify_determinism.py
```

## 许可

渲染栈与视觉模型许可核验见 `docs/许可核验报告.md`：HyperFrames / RT-DETR / SAM / Grounding DINO /
Depth Anything V2 可商用；Noto Sans SC = SIL OFL 1.1 可商用；**人脸 embedding 权重（InsightFace
`buffalo_l` 等）官方限定非商用研究，商用需另行授权**——本项目 `anchor_region="face"` 不启用。
