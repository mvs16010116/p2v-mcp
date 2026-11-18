# GPU 自适应渲染评估：无显卡可跑、有显卡充分利用

> 评估日期：2026-11-18。触发：用户要求"按没有显卡的环境开发，但有显卡时要能充分利用"。
> 结论先行：**目标可直接达成，且大部分能力 HyperFrames CLI 已内置**。需改的是我们自己的 WebGL 增强层默认策略 + 确定性分档验收。本文先给实证发现，再给方案与实施步骤。

---

## 1. 实证发现（推翻此前的保守假设）

此前 WAR_MAP/对标报告标注"headless WebGL 不可用、不可控、默认关闭"。本次用真实渲染日志 + 对照实验核验，结论修正如下：

### 发现 A：HyperFrames CLI 原生具备 GPU 自动探测与降级
CLI `render --help` 明确提供（v0.9.59 实证）：

| 参数 / 环境变量 | 作用 |
|---|---|
| `--browser-gpu` | 强制主机 GPU 加速 Chrome/WebGL 捕获 |
| `--no-browser-gpu` | 强制软件渲染（SwiftShader） |
| **默认 `auto`（未传时）** | **首次启动探测，有 GPU 用 GPU，无 GPU 自动回退软件** |
| `--gpu` | GPU 视频编码（与浏览器渲染 GPU 是两回事） |
| `--resolution=4k` 等 | 通过 deviceScaleFactor 提分辨率 |
| `--page-side-compositing`（默认 on） | 用 Chrome 页内 WebGL canvas 做转场，~6× 更快 |

**这正是用户要的"有显卡用显卡、没显卡用软件"——CLI 默认行为已满足。**

### 发现 B：本机渲染日志证明走的是硬件 GPU
`logs/cmd_956fa0ca..._hyperframes.log` 原文：
```
GPU: browser GPU (auto-detect)
browserGpuMode probe → hardware (WebGL renderer vendor="Google Inc. (Intel)"
  renderer="ANGLE (Intel, Intel(R) UHD Graphics 730 ... D3D11)")
screenshot capture · hardware gpu · compile 5.8s · extract 0.0s ...
Render start: "browserGpuMode":"auto" ...
```
即本机（用户以为"没有显卡"）实测有 Intel UHD 730 核显，CLI 已自动识别并启用硬件 WebGL。
**注**：用户主观感受"没显卡"通常指"没有独立显卡"，但核显也是 GPU，WebGL 可用。真正的无显卡环境（纯 CPU/服务器/SwiftShader）由 `--no-browser-gpu` 覆盖。

### 发现 C：我们自己的 WebGL 特效层在硬件 GPU 下真能产出像素
对照实验（同一 composition，仅 `config.fx.webgl.enabled` 不同，体积光强度调到 0.9 便于测量）：
```
ON  (enabled=true)  sha=78ecac0c8b1562f29adc...
OFF (enabled=false) sha=e7c4a4dc825058f20057...
DIFFERENT=True   MEAN_ABS_DIFF=2.083
```
证明粒子/体积光 shader 在硬件 GPU 下**确实执行并改变了画面**，不是空转降级。
证据帧：`fixtures/reports/webgl_shader_on.png` / `webgl_shader_off.png`（本地留存，被 .gitignore 排除）。

---

## 2. 问题重新定义

目标不是"让 WebGL 能用"（它已经能用），而是三件事：

1. **默认策略**：无显卡环境开箱即用（软件渲染，确定性），有显卡时自动启用我们的画质增强层（粒子/体积光）——即从 `enabled=false` 改为**运行时按 GPU 能力自动决策**。
2. **确定性分档**：硬件 GPU 与软件 SwiftShader 的浮点实现不同，同 t 像素可能不同。需把"确定性"定义从"跨设备一致"收敛为"**同设备、同 GPU 模式、同 t 一致**"，并在 CI（软件）与本机（硬件）各验各的。
3. **可控性**：把 CLI 已有的 `--browser-gpu` / `--no-browser-gpu` / `--resolution` 能力接进我们的 backend，让调用方能显式选择或交给 auto。

---

## 3. 方案设计

### 3.1 渲染模式三档（backend 层）

在 `HyperFramesBackend.render()` 增加 `gpu_mode: Literal["auto","on","off"] = "auto"`：
- `auto`（默认）：不传 flag，交给 CLI 探测——有显卡用显卡，无显卡回退软件；
- `on`：追加 `--browser-gpu`，强制硬件（无显卡会失败并明确报错，不静默降级）；
- `off`：追加 `--no-browser-gpu`，强制 SwiftShader 软件渲染（CI/无显卡开发用）。

不改 CLI 版本、不动时间线语义、不影响 ffmpeg 混音。参数只允许三个枚举值，杜绝臆造 flag。

### 3.2 WebGL 增强层自动决策（模板层）

当前 `installWebGL()` 只看 `WEBGL.enabled`（config 硬编码 false）。改为三级决策：

```
决策顺序：
1. config.fx.webgl.mode = off    → 永远 no-op（保守/调试用）
                       = auto   → 走第 2 步（推荐默认）
                       = on     → 强制尝试，失败才降级
2. 探测 WebGL context + UNMASKED_RENDERER_WEBGL：
   - 拿到 context 且 shader 编译成功 → 启用粒子/体积光，并记录实际 renderer 到 window.__WEBGL_INFO__
   - 拿不到 / 编译失败 → canvas 隐藏 + no-op（现有 degrade 逻辑不变，首帧非黑红线不破）
3. auto 模式下：无论硬件还是软件 WebGL，只要能编译就启用
   ——软件 WebGL 也确定性（同一 Chrome 版本 + SwiftShader 是确定的）
```

- 保留整数 seed + `p0+v0*t` 闭式解 + 单时钟，确定性机制不变；
- `window.__WEBGL_INFO__ = {enabled, renderer, mode}` 供诊断工具 `diagnose_webgl` 读取，形成可观测闭环；
- `config.yaml` 的 `fx.webgl.enabled` 升级为 `mode: auto|on|off`（保留 enabled 作向后兼容别名）。

### 3.3 确定性分档验收（测试/CI 层）

| 场景 | GPU 模式 | 验收命令 | 期望 |
|---|---|---|---|
| CI 软件档 | `off`（SwiftShader） | 现有 Docker CI（run 44099644422 已 PASS） | 同 t 两次 SHA256 一致 |
| 本机硬件档 | `on`/`auto`（本机 UHD 730） | 新增本机双渲染 e2e | 同 t 两次 SHA256 一致 |
| 跨档比较 | off vs on | **不做一致性断言** | 允许像素不同（画质增强本就该改变画面） |

关键原则：**确定性是"同档内可复现"，不是"跨 GPU 型号同像素"**。这与 GPU 浮点实现差异的客观事实一致，也不降低验收标准——每个档位内部仍是严格的逐帧 SHA256 一致。

---

## 4. 实施步骤（按依赖顺序）

1. **backend 加 `gpu_mode` 参数**：`render()` 增加枚举参数，追加对应 flag；加单元测试断言三个枚举值生成的 args 正确、非法值被拒。
2. **config/契约升级**：`fx.webgl.enabled` → `fx.webgl.mode`（auto 默认），保留 enabled 兼容；`WebGLRenderCtx` 加 `mode` 字段。
3. **模板 installWebGL 改造**：按 §3.2 三级决策，写 `window.__WEBGL_INFO__`；保留失败降级。
4. **诊断工具增强**：`diagnose_webgl()` 返回 config.mode + 本次渲染实际 renderer（若可读日志/信息）。
5. **确定性 e2e**：新增本机硬件档双渲染 SHA256 测试（仿 `e2e_webgl_render.py`，但用 mode=auto 且断言 `__WEBGL_INFO__.enabled`）；软件档沿用 CI。
6. **文档同步**：WAR_MAP 决策 #8 补充"auto 模式"；对标报告画质维度待实测后评估；本评估文档归档结论。

预计代码改动：backend ~20 行、contracts ~10 行、模板 ~40 行、测试 ~80 行、文档 4 处。风险低，均可本机实测（本机有硬件 GPU 可验 on/auto，CLI `--no-browser-gpu` 可验 off）。

---

## 5. 风险与红线

| 风险 | 缓解 |
|---|---|
| 硬件 GPU 浮点差异导致同 t 不一致 | 分档验收，档内严格 SHA256；不跨档比较 |
| 无显卡环境 WebGL context 创建失败 | auto 模式失败即 degrade no-op，首帧非黑红线不变 |
| Docker CI 无 GPU 时开着 WebGL 变慢/不稳 | CI 保持 `mode=off` 或依赖 CLI 自动回退，不强制 on |
| GPU 模式切换改变既有出片像素 | 这是预期行为（画质增强），通过档位隔离 + 明确文档告知 |
| 误把"软件 WebGL"当"硬件"宣称 | 一律以 `__WEBGL_INFO__.renderer` 与 CLI 日志为准，不臆测 |

**不做**：真 3D 重建、光学景深、AI 生成、realtime（方案 1.2 排除项）；不臆造 CLI flag；不为了分数在无显卡环境强制开 WebGL。

---

## 6. 对评分的影响（预期，需实测确认）

- 若 §4 全部落地且本机硬件档 e2e PASS、可归因画质对比成立 → 画质上限 **2 → 3**，均分 **4.0 → 4.1**（达成用户目标）。
- 若只是把 CLI auto 能力接进 backend、WebGL 层仍默认保守 → 功能可用性提升，但画质分不变。
- 到 4.5 仍需画质进一步跃升（4K/景深/锐化可归因）+ 第三方生态背书，属长期项。

---

## 7. 待用户决策

1. 是否按 §4 实施（我建议实施，风险低、可本机实测）？
2. 默认 `fx.webgl.mode` 设为 `auto`（推荐）还是 `off`（最保守）？
3. 本机硬件档的画质对比是否要结合 `--resolution=4k` 一起做（分辨率提升对"画质上限"评分更直接）？
