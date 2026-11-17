# src/vision — 视觉分析层（可选 Sidecar）

对应方案第 20 章 R14「主体感知聚焦」与 WAR_MAP 决策 #5：**未安装时降级为 R6 手工锚点，不阻断出片**。

铁律：
1. AI 只在**离线分析阶段**运行；渲染循环内零推理（`renderAtTime(t)` 必须确定性）。
2. 所有重依赖（torch / transformers / groundingdino / insightface / cv2 / SAM / PIL / numpy）**函数内惰性导入**；缺依赖或未配置本地权重时统一返回 `ToolResponse(ok=false, error.code=BINARY_NOT_FOUND)`，绝不向上抛异常打断主流程。
3. 权重只读本地（`local_files_only=True`），**不隐式联网下载**。
4. bbox 一律归一化 `[0,1]`；坐标单位像素；时间单位秒（本层不产生时间）。

## 文件

| 文件 | 职责 | 默认 |
|---|---|---|
| `focus_math.py` | 方案 20.4 `bboxToFocus()` / 19.3 `coverRect()` 纯函数；scale ≤ 4、contain 不切头、越界降 fill 写 warnings | 始终可用（仅依赖 pydantic） |
| `models.py` | 本层 Pydantic v2 契约（Subject / SubjectAnalysis / FocusLock / DepthResult） | 始终可用 |
| `subject_analyzer.py` | `detect_subjects` / `lock_subject` / `segment_subject` | 检测 + 分割启用；reference 实验性 |
| `depth_estimate.py` | Depth Anything v2 深度图（视差 / 雾化辅助） | **默认关闭**，`enable=True` 才加载 |
| `_common.py` | 惰性导入、权重校验、BINARY_NOT_FOUND 统一返回 | 始终可用 |

## 模型清单（按需装，不强制全装）

| 能力 | 推荐模型 | 本层默认 | 许可提醒 |
|---|---|---|---|
| 人/物体检测 | RT-DETR（`PekingU/rtdetr_r50vd`） | **启用** | Apache 2.0 系为主，仍须核对权重页 |
| 文字锁定 | Grounding DINO | 调用 `text` selector 时启用 | Apache 2.0 系为主，核对具体权重 |
| 参考图锁定 | InsightFace / ArcFace | **实验性关闭** | ⚠️ 见下方红字 |
| 主体分割 | SAM / SAM2（`vit_b`/`vit_l`/`vit_h`） | **启用** | Apache 2.0（SAM2 亦为 Apache 2.0） |
| 单目深度 | Depth Anything v2 | **实验性关闭** | 模型权重许可需核对；仅近似视差 |

> <font color="red">**许可红线（方案 20.2 / 20.8）：人脸 embedding 权重（InsightFace ArcFace、DeepFace 等）多为**非商用研究授权**，商用部署前必须逐项核对授权条款；不要用「模型代码是开源」推断「权重可商用」。同理，YOLOv8 注意 AGPL / Commons Clause，商用需核对；本层默认采用许可更清晰的 RT-DETR + SAM。任何模型在商用前都要复核权重页与第 17 节清单。</font>

## 环境变量（本地权重路径，未设置即降级）

| 变量 | 用途 |
|---|---|
| `P2V_RTDETR_CHECKPOINT` | RT-DETR 本地权重目录/文件（transformers 格式） |
| `P2V_GROUNDING_DINO_CONFIG` / `P2V_GROUNDING_DINO_CHECKPOINT` | Grounding DINO 配置 `.py` 与权重 `.pth` |
| `P2V_SAM_CHECKPOINT` / `P2V_SAM_MODEL_TYPE` | SAM 权重文件与 `vit_b` / `vit_l` / `vit_h` |
| `P2V_INSIGHTFACE_MODEL_ROOT` | InsightFace 模型包（如 `buffalo_l`）所在根目录 |

## 按需安装

```bash
# 公共运行时（CPU 即可，离线分析不要求 GPU）
python -m pip install "torch>=2.2" "transformers>=4.45" Pillow numpy

# 主体分割（默认启用）
python -m pip install segment-anything opencv-python-headless

# 文字锁定（可选）
python -m pip install groundingdino-py

# 参考图锁定（实验性，许可红线见上）
python -m pip install insightface opencv-python-headless

# 权重预下载（示例，必须落到本地后再设置上面的环境变量）
pip install -U "huggingface_hub[cli]"
hf download PekingU/rtdetr_r50vd --local-dir ./models/rtdetr_r50vd
hf download depth-anything/Depth-Anything-V2-Small-hf --local-dir ./models/depth-anything-v2-small
```

## 使用与降级

```python
from src.vision.subject_analyzer import detect_subjects, lock_subject, segment_subject
from src.contracts import Selector

analysis = detect_subjects("fixtures/images/group_10people.jpg")   # 落盘 analysis/subjects.json
if not analysis.ok:
    ...  # error.code == BINARY_NOT_FOUND → 走 R6 手工 pivot_x/y
locked = lock_subject(analysis.data, Selector(type="index", value=0), fill=0.62, fit="contain")
# locked.data["focus"] = {scale, x, y, transformOrigin, subjectCenter, fill, fit, warnings}
# 默认 anchor_region="upper_body"：先把全身框派生为头+肩区域再算 focus，
# 避免 contain 把镜头拉远（方案 20.4 实测洞察）。
# locked.data 另含 bbox（派生框）、full_bbox（原始检测框）、anchor_region。
# anchor_region="face" 需人脸检测，当前返回 BINARY_NOT_FOUND（许可红线）。
mask = segment_subject("fixtures/images/group_10people.jpg", locked.data["full_bbox"])
```

`text` / `reference` 锁定失败时，`error.detail.candidates` 附带候选列表，Agent 应改用 `index`。
非法 index / 无匹配目标属于调用参数错误，抛 `ValueError`（不占统一错误码）。

## 阶段 B 新增纯函数（供 builder 直接消费，零推理）

```python
from src.vision.focus_math import build_tour_path, build_parallax_plan

# 方案 20.5 多主体漫游：两个真实检测框 → 两点关键帧 + warnings
tour = build_tour_path(bbox_a, bbox_b, cov, stage, fill=0.62, fit="contain",
                       anchor_region="upper_body", image_size=(1536, 1024))
tour.model_dump(mode="json")["keyframes"]   # [{"scale":..,"x":..,"y":..}, {...}]
tour.warnings                                # A/B 两端降 fill / scale 钳制告警

# 方案 20.5 分层视差：focus 结果 → 前景/背景各自 scale/x/y
plan = build_parallax_plan(focus_result, bg_ratio=0.35, haze=0.15,
                           bbox=bbox_a, cov=cov, stage=(1080, 1920))
plan.model_dump(mode="json")                 # {foreground, background, bg_ratio, haze, fg_inset, warnings}
```

`fg_inset` 是 SAM 权重缺失时的**近似矩形 mask（非 SAM 抠图）**，warnings 会如实标注；
只降级素材来源，确定性断言不 mock。SAM 可用时由调用方替换为透明 PNG 图层。

`text` selector 走 transformers 原生 Grounding DINO（`IDEA-Research/grounding-dino-tiny`，Apache 2.0），
`P2V_GROUNDING_DINO_CHECKPOINT` 指向本地模型目录；缺权重仍返回 BINARY_NOT_FOUND + candidates。
真实 IoU 复核脚本：`python fixtures/verify_text_selector.py`（报告 `fixtures/reports/text_selector_iou_report.json`）。

## 验收

```bash
python -m pytest tests/test_focus_math.py tests/test_vision_models.py tests/test_vision2_extensions.py -q
```

依赖/权重缺失的 ML 测试明确 `skip` 并写明原因，不用 mock 掩盖缺失；纯几何测试与降级测试始终运行。
方案 20.7 验收点：居中误差 ≤ 5%、contain 四角不越界、scale ≤ 4、同输入确定性、缺依赖不阻断出片。
