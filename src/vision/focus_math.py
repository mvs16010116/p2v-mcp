"""方案 20.4 bboxToFocus() 与 19.3 coverRect() 的 Python 纯函数实现。

- 零 ML 依赖、零 I/O，可独立单测。
- 公式逐字照搬方案文档（见 docs/节拍卡点图文视频MCP方案.md 20.4 / 19.3）：
    cx = cov.x + (x1 + x2) / 2 * cov.w
    cy = cov.y + (y1 + y2) / 2 * cov.h
    sw = (x2 - x1) * cov.w
    sh = (y2 - y1) * cov.h
    scale = min/max(stageW*fill/sw, stageH*fill/sh)   # contain=min, cover=max
    tx = (stageW/2 - cx) * scale
    ty = (stageH/2 - cy) * scale
- 边界保护：scale ≤ 4.0；推近后 bbox 四角须落在舞台内，否则逐步降低 fill 并写 warnings。
- bbox 归一化 [0,1]；坐标单位为像素；本模块不涉及时间。
"""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from src.contracts import ToolResponse

from ._common import binary_not_found
from .models import StrictModel

MAX_SCALE = 4.0
DEFAULT_TOUR_IMAGE_SIZE = (1080, 1920)  # tour_focus 默认图像尺寸；真实调用应显式传 image_size
UPPER_BODY_HEIGHT_RATIO = 0.45
MIN_FILL = 0.1
FILL_REDUCE_FACTOR = 0.9
MAX_REDUCE_ITER = 24
_EPS = 1e-9
_TOL = 1e-6


class CoverRect(StrictModel):
    """coverRect 的返回：图片按 cover 放入舞台后的位置与尺寸（像素）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    w: float = Field(gt=0.0, description="缩放后图片宽")
    h: float = Field(gt=0.0, description="缩放后图片高")
    x: float = Field(description="图片左上角 x（可为负，表示被裁切）")
    y: float = Field(description="图片左上角 y（可为负，表示被裁切）")


class FocusResult(StrictModel):
    """bboxToFocus 的返回，字段名对齐方案 20.4 的 JS 对象。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scale: float = Field(gt=0.0, description="推近倍数，已受 max_scale 钳制")
    x: float = Field(description="GSAP 平移 x（像素）")
    y: float = Field(description="GSAP 平移 y（像素）")
    transform_origin: str = Field(default="50% 50%", description="与方案 20.4 一致")
    subject_center: tuple[float, float] = Field(description="舞台坐标系中的主体中心 [cx, cy]")
    fill: float = Field(gt=0.0, le=1.0, description="最终生效的 fill（可能因越界被降低）")
    warnings: list[str] = Field(default_factory=list, description="钳制/越界降级告警")


def _validate_positive(name: str, value: float) -> None:
    if value <= 0.0 or value != value or value == float("inf"):
        raise ValueError(f"{name} 必须为正有限数，收到 {value!r}")


def _validate_bbox(bbox: tuple[float, float, float, float] | list[float]) -> tuple[float, float, float, float]:
    if len(bbox) != 4:
        raise ValueError(f"bbox 必须为 [x1,y1,x2,y2] 四元组，收到 {len(bbox)} 个值")
    x1, y1, x2, y2 = (float(v) for v in bbox)
    for name, value in (("x1", x1), ("y1", y1), ("x2", x2), ("y2", y2)):
        if value < 0.0 or value > 1.0:
            raise ValueError(f"bbox.{name}={value} 越出归一化区间 [0,1]")
    if x1 >= x2 or y1 >= y2:
        raise ValueError("bbox 须满足 x1 < x2 且 y1 < y2")
    return x1, y1, x2, y2


def cover_rect(
    img_w: float, img_h: float, stage_w: float, stage_h: float
) -> CoverRect:
    """方案 19.3 coverRect：图片居中铺满舞台（可能裁切）。

    s = max(stageW/imgW, stageH/imgH)
    return { w: imgW*s, h: imgH*s, x: (stageW-imgW*s)/2, y: (stageH-imgH*s)/2 }
    """
    _validate_positive("img_w", img_w)
    _validate_positive("img_h", img_h)
    _validate_positive("stage_w", stage_w)
    _validate_positive("stage_h", stage_h)
    s = max(stage_w / img_w, stage_h / img_h)
    w = img_w * s
    h = img_h * s
    return CoverRect(w=w, h=h, x=(stage_w - w) / 2.0, y=(stage_h - h) / 2.0)


def _geometry(
    bbox: tuple[float, float, float, float],
    cov: CoverRect,
    stage_w: float,
    stage_h: float,
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    cx = cov.x + (x1 + x2) / 2.0 * cov.w
    cy = cov.y + (y1 + y2) / 2.0 * cov.h
    sw = (x2 - x1) * cov.w
    sh = (y2 - y1) * cov.h
    if sw <= _EPS or sh <= _EPS:
        raise ValueError("主体框在舞台坐标系中的宽/高必须大于 0")
    return cx, cy, sw, sh


def _scale_for(
    fit: Literal["contain", "cover"],
    sw: float,
    sh: float,
    stage_w: float,
    stage_h: float,
    fill: float,
) -> float:
    op = max if fit == "cover" else min
    return op(stage_w * fill / sw, stage_h * fill / sh)


def _translation(
    cx: float, cy: float, scale: float, stage_w: float, stage_h: float
) -> tuple[float, float]:
    return (stage_w / 2.0 - cx) * scale, (stage_h / 2.0 - cy) * scale


def _projected_corners(
    bbox: tuple[float, float, float, float],
    cov: CoverRect,
    stage_w: float,
    stage_h: float,
    scale: float,
    tx: float,
    ty: float,
) -> tuple[float, float, float, float]:
    """变换后 bbox 在舞台坐标系中的 [minx, miny, maxx, maxy]。

    图片经 coverRect 后中心恰好在舞台中心，因此 scale 围绕舞台中心进行：
        p' = center + (p - center) * scale + t
    """
    x1, y1, x2, y2 = bbox
    px = (cov.x + x1 * cov.w, cov.x + x2 * cov.w)
    py = (cov.y + y1 * cov.h, cov.y + y2 * cov.h)
    cx0, cy0 = stage_w / 2.0, stage_h / 2.0
    fx = [cx0 + (p - cx0) * scale + tx for p in px]
    fy = [cy0 + (p - cy0) * scale + ty for p in py]
    return min(fx), min(fy), max(fx), max(fy)


def _is_inside(
    corners: tuple[float, float, float, float], stage_w: float, stage_h: float
) -> bool:
    minx, miny, maxx, maxy = corners
    return (
        minx >= -_TOL
        and miny >= -_TOL
        and maxx <= stage_w + _TOL
        and maxy <= stage_h + _TOL
    )


def bbox_to_focus(
    bbox: tuple[float, float, float, float] | list[float],
    cov: CoverRect,
    stage_w: float,
    stage_h: float,
    fill: float = 0.62,
    fit: Literal["contain", "cover"] = "contain",
    max_scale: float = MAX_SCALE,
) -> FocusResult:
    """把归一化主体框换算成 GSAP 可用的 {scale, x, y}（方案 20.4 逐字实现 + 边界保护）。

    contain（默认）：scale = min(stageW*fill/sw, stageH*fill/sh)，主体完整入画不切头。
    cover：scale = max(...)，可能裁切，用于填满画面的强冲击镜头。
    """
    box = _validate_bbox(bbox)
    if not isinstance(cov, CoverRect):
        raise TypeError("cov 必须是 CoverRect，请先调用 cover_rect()")
    _validate_positive("stage_w", stage_w)
    _validate_positive("stage_h", stage_h)
    _validate_positive("fill", fill)
    if fill > 1.0:
        raise ValueError("fill 不得大于 1.0")
    if fit not in ("contain", "cover"):
        raise ValueError("fit 只能是 'contain' 或 'cover'")
    _validate_positive("max_scale", max_scale)

    cx, cy, sw, sh = _geometry(box, cov, stage_w, stage_h)
    warnings: list[str] = []

    scale = _scale_for(fit, sw, sh, stage_w, stage_h, fill)
    if scale > max_scale:
        warnings.append(
            f"scale={scale:.3f} 超过上限 {max_scale:.1f}，已钳制（超过 4 倍原图会糊）"
        )
        scale = max_scale

    tx, ty = _translation(cx, cy, scale, stage_w, stage_h)
    corners = _projected_corners(box, cov, stage_w, stage_h, scale, tx, ty)

    if not _is_inside(corners, stage_w, stage_h):
        original_fill = fill
        reduced = False
        for _ in range(MAX_REDUCE_ITER):
            next_fill = max(MIN_FILL / 2.0, fill * FILL_REDUCE_FACTOR)
            if next_fill >= fill:
                break
            fill = next_fill
            scale = _scale_for(fit, sw, sh, stage_w, stage_h, fill)
            if scale > max_scale:
                scale = max_scale
            tx, ty = _translation(cx, cy, scale, stage_w, stage_h)
            corners = _projected_corners(box, cov, stage_w, stage_h, scale, tx, ty)
            reduced = True
            if _is_inside(corners, stage_w, stage_h):
                break
        detail = "，contain 模式下头部可能被切" if fit == "contain" else ""
        warnings.append(
            f"推近后主体框越出画面{detail}，fill 已从 {original_fill:.3f} 降至 {fill:.3f}"
        )
        if not _is_inside(corners, stage_w, stage_h):
            warnings.append("降低 fill 后仍可能越界，建议改用人工锚点（R6）")

    return FocusResult(
        scale=scale,
        x=tx,
        y=ty,
        subject_center=(cx, cy),
        fill=fill,
        warnings=warnings,
    )


def project_bbox(
    bbox: tuple[float, float, float, float] | list[float],
    cov: CoverRect,
    stage_w: float,
    stage_h: float,
    focus: FocusResult,
) -> tuple[float, float, float, float]:
    """纯函数：按 focus 变换后主体框在舞台坐标系中的 [x1,y1,x2,y2]。

    用于验收「推近后主体居中误差 ≤5%」「contain 四角不越界」。
    """
    box = _validate_bbox(bbox)
    _validate_positive("stage_w", stage_w)
    _validate_positive("stage_h", stage_h)
    minx, miny, maxx, maxy = _projected_corners(
        box, cov, stage_w, stage_h, focus.scale, focus.x, focus.y
    )
    return minx, miny, maxx, maxy


def derive_anchor_bbox(
    full_bbox: tuple[float, float, float, float] | list[float],
    anchor_region: Literal["full", "upper_body", "face"],
    image_size: tuple[int, int] | list[int],
) -> ToolResponse:
    """在调用 bboxToFocus 之前，从检测器给出的全身框派生 focus 取框区域（方案 20.6）。

    - `full`：原样返回（全身人物在 contain 下可能被拉远，见 20.4 实测洞察）。
    - `upper_body`：取框高的前 45%（头+肩，y 方向从顶部算），得到 scale>1 的真推近且不切头。
    - `face`：本层未启用人脸/关键点检测；按许可红线返回 BINARY_NOT_FOUND，不实现、不 mock。

    纯函数：无 I/O、无 ML 依赖，结果只由入参决定。bbox 归一化 [0,1]。
    """
    box = _validate_bbox(full_bbox)
    if anchor_region not in ("full", "upper_body", "face"):
        raise ValueError("anchor_region 只能是 'full' / 'upper_body' / 'face'")
    if len(image_size) != 2 or any(not isinstance(v, int) or v <= 0 for v in image_size):
        raise ValueError("image_size 须为 (正整数 width, 正整数 height)")

    if anchor_region == "face":
        return binary_not_found(
            "anchor_region='face' 需要人脸/关键点检测能力，当前视觉分析层未启用",
            {
                "anchor_region": "face",
                "reason": "人脸 embedding 权重多为非商用研究授权，许可红线见方案 20.2 / src/vision/README.md",
                "hint": "改用 upper_body（默认）或 full；如需 face 须先完成权重许可复核并另行实现检测器",
            },
        )

    if anchor_region == "full":
        derived = list(box)
    else:
        x1, y1, x2, y2 = box
        derived = [x1, y1, x2, y1 + UPPER_BODY_HEIGHT_RATIO * (y2 - y1)]

    return ToolResponse(
        ok=True,
        data={
            "bbox": derived,
            "anchor_region": anchor_region,
            "image_size": list(image_size),
            "full_bbox": list(box),
        },
        error=None,
    )


# ---------------------------------------------------------------------------
# 方案 20.5 / 20.7：多主体漫游 + 分层视差 + IoU 验收（纯函数，零 ML / 零 I/O）
# ---------------------------------------------------------------------------
class FocusPoint(StrictModel):
    """GSAP 可用的单主体焦点 {scale, x, y}（方案 20.4 输出的子集）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scale: float = Field(gt=0.0, description="推近倍数")
    x: float = Field(description="GSAP 平移 x（像素）")
    y: float = Field(description="GSAP 平移 y（像素）")


class TourPath(StrictModel):
    """方案 20.5 两点漫游：镜头内从主体 A 的焦点推到主体 B 的焦点。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    from_focus: FocusPoint = Field(description='主体 A 焦点（tween 起点）')
    to_focus: FocusPoint = Field(description='主体 B 焦点（tween 终点）')

    def interpolate(self, p: float) -> FocusPoint:
        """线性插值进度 p∈[0,1] 的焦点；与 GSAP fromTo 的线性插值语义一致。

        不引入随机 / 时间 / I/O：同一 (path, p) 必得同一结果，可用于静态断言。
        """
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"tour 进度 p 必须在 [0,1]，收到 {p!r}")
        return FocusPoint(
            scale=self.from_focus.scale + (self.to_focus.scale - self.from_focus.scale) * p,
            x=self.from_focus.x + (self.to_focus.x - self.from_focus.x) * p,
            y=self.from_focus.y + (self.to_focus.y - self.from_focus.y) * p,
        )


def tour_focus(
    bbox_a: tuple[float, float, float, float] | list[float],
    bbox_b: tuple[float, float, float, float] | list[float],
    cov: CoverRect,
    stage_w: float,
    stage_h: float,
    fill: float = 0.62,
    fit: Literal["contain", "cover"] = "contain",
    *,
    anchor_region: Literal["full", "upper_body", "face"] = "upper_body",
    image_size: tuple[int, int] | list[int] = DEFAULT_TOUR_IMAGE_SIZE,
    max_scale: float = MAX_SCALE,
) -> TourPath:
    """方案 20.5：两个真实检测框 → 两点 scale/x/y 插值路径（供模板 fromTo 消费）。

    两端各自套用与单点 focus 相同的 anchor_region 派生 + bboxToFocus 边界保护，
    故 tour 的两个终点与 Wave 10 稳态推近语义完全一致；face 因许可红线降级。
    """
    a = derive_anchor_bbox(bbox_a, anchor_region, image_size)
    b = derive_anchor_bbox(bbox_b, anchor_region, image_size)
    if not a.ok or not b.ok:
        detail = (a.error or b.error)
        raise ValueError(
            "tour 两端 bbox 的 anchor_region 派生失败"
            + (f": {detail.message}" if detail is not None else "")
        )
    plan = build_tour_path(
        bbox_a, bbox_b, cov, (stage_w, stage_h), fill, fit,
        anchor_region=anchor_region, image_size=image_size, max_scale=max_scale,
    )
    return TourPath(from_focus=plan.keyframes[0], to_focus=plan.keyframes[1])


class TourPathPlan(StrictModel):
    """team-lead 交接结构：两点关键帧 + 边界保护 warnings，Pydantic 承载（禁止裸 dict 跨层）。

    `plan.model_dump(mode="json")["keyframes"]` 即 `[{scale,x,y}, {scale,x,y}]`，
    builder 可直接用作 GSAP `fromTo` 的两端；`warnings` 为两端 bboxToFocus 的降 fill /
    scale 钳制告警（带 A/B 前缀），不得丢弃。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    keyframes: list[FocusPoint] = Field(min_length=2, max_length=2, description="[A, B] 两点关键帧")
    warnings: list[str] = Field(default_factory=list, description="两端的边界保护告警")


def build_tour_path(
    bbox_a: tuple[float, float, float, float] | list[float],
    bbox_b: tuple[float, float, float, float] | list[float],
    cov: CoverRect,
    stage: tuple[float, float] | list[float],
    fill: float = 0.62,
    fit: Literal["contain", "cover"] = "contain",
    *,
    anchor_region: Literal["full", "upper_body", "face"] = "upper_body",
    image_size: tuple[int, int] | list[int] = DEFAULT_TOUR_IMAGE_SIZE,
    max_scale: float = MAX_SCALE,
) -> TourPathPlan:
    """方案 20.5 / team-lead 交接签名：两个真实 bbox → 两点 {scale,x,y} + warnings。

    - 两端各自派生 anchor_region（各一次），再走与 Wave 10 相同的 bboxToFocus 边界保护；
    - face 因许可红线返回 BINARY_NOT_FOUND → 此处转 ValueError，由调用方降级为单点 focus；
    - 纯函数、零 I/O、零推理，同一入参永远得到同一关键帧。
    """
    if len(stage) != 2:
        raise ValueError("stage 须为 (width, height)")
    stage_w, stage_h = float(stage[0]), float(stage[1])
    a = derive_anchor_bbox(bbox_a, anchor_region, image_size)
    b = derive_anchor_bbox(bbox_b, anchor_region, image_size)
    if not a.ok or not b.ok:
        detail = (a.error or b.error)
        raise ValueError(
            "tour 两端 bbox 的 anchor_region 派生失败"
            + (f": {detail.message}" if detail is not None else "")
        )
    fa = bbox_to_focus(a.data["bbox"], cov, stage_w, stage_h, fill, fit, max_scale)
    fb = bbox_to_focus(b.data["bbox"], cov, stage_w, stage_h, fill, fit, max_scale)
    warnings = [f"A: {w}" for w in fa.warnings] + [f"B: {w}" for w in fb.warnings]
    return TourPathPlan(
        keyframes=[
            FocusPoint(scale=fa.scale, x=fa.x, y=fa.y),
            FocusPoint(scale=fb.scale, x=fb.x, y=fb.y),
        ],
        warnings=warnings,
    )


class ParallaxParams(StrictModel):
    """方案 20.5 分层视差参数：前景全速、背景按比例慢速推近 + 模糊。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enable: bool = Field(description="是否启用分层视差")
    fg_scale: float = Field(gt=0.0, description="前景层推近倍数（等于 focus.scale）")
    bg_scale: float = Field(gt=0.0, description="背景层推近倍数：1+(fg_scale-1)*bg_ratio")
    bg_ratio: float = Field(ge=0.0, le=1.0, description="背景推近比例，默认 0.35")
    haze: float = Field(ge=0.0, le=1.0, description="远景雾化强度，默认 0.15")


def parallax_params(
    focus_scale: float,
    bg_ratio: float = 0.35,
    haze: float = 0.15,
    *,
    enable: bool = True,
) -> ParallaxParams:
    """背景层倍数用线性插值 1 → 1+(focus.scale-1)*bg_ratio（方案 20.5）。

    bg_ratio=0 → 背景不动；bg_ratio=1 → 背景与前景同速（无纵深）。
    """
    _validate_positive("focus_scale", focus_scale)
    if not 0.0 <= bg_ratio <= 1.0:
        raise ValueError("bg_ratio 必须在 [0,1]")
    if not 0.0 <= haze <= 1.0:
        raise ValueError("haze 必须在 [0,1]")
    return ParallaxParams(
        enable=enable,
        fg_scale=focus_scale,
        bg_scale=1.0 + (focus_scale - 1.0) * bg_ratio,
        bg_ratio=bg_ratio,
        haze=haze,
    )


class ParallaxLayer(StrictModel):
    """单层视差参数：GSAP 可直接消费的 scale/x/y（像素平移）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scale: float = Field(gt=0.0, description="该层推近倍数")
    x: float = Field(description="该层 GSAP 平移 x（像素）")
    y: float = Field(description="该层 GSAP 平移 y（像素）")


class ParallaxPlan(StrictModel):
    """team-lead 交接结构：前景/背景各自的 scale/x/y + 近似 mask + warnings。

    `plan.model_dump(mode="json")` 即可交给 builder：
      - foreground：全速，等于 bboxToFocus 结果；
      - background：慢速，scale=1+(fg.scale-1)*bg_ratio，x/y 按同比例慢移；
      - fg_inset：SAM 权重缺失时的近似矩形 mask（CSS inset）；SAM 可用时由调用方替换成抠图 PNG。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    foreground: ParallaxLayer = Field(description="前景（主体）层")
    background: ParallaxLayer = Field(description="背景层")
    bg_ratio: float = Field(ge=0.0, le=1.0, description="背景推近比例")
    haze: float = Field(ge=0.0, le=1.0, description="远景雾化强度")
    fg_inset: str | None = Field(default=None, description="近似矩形 mask；非 SAM 抠图（如实标注）")
    warnings: list[str] = Field(default_factory=list, description="近似 mask / 上游 focus 告警")


def build_parallax_plan(
    focus_result: FocusResult,
    bg_ratio: float = 0.35,
    haze: float = 0.15,
    *,
    bbox: tuple[float, float, float, float] | list[float] | None = None,
    cov: CoverRect | None = None,
    stage: tuple[float, float] | list[float] | None = None,
) -> ParallaxPlan:
    """方案 20.5 / team-lead 交接签名：focus 结果 → 前景/背景各自 scale/x/y。

    前景沿用 focus_result 的全速 scale/x/y；背景按 bg_ratio 做慢速 scale 与同比例平移。
    给定 bbox/cov/stage 时附带近似矩形 mask（SAM 缺失降级，报告须标注"非 SAM"）。
    """
    if not isinstance(focus_result, FocusResult):
        raise TypeError("focus_result 须为 FocusResult，请先调用 bbox_to_focus()")
    params = parallax_params(focus_result.scale, bg_ratio, haze)
    foreground = ParallaxLayer(
        scale=focus_result.scale, x=focus_result.x, y=focus_result.y
    )
    background = ParallaxLayer(
        scale=params.bg_scale,
        x=focus_result.x * bg_ratio,
        y=focus_result.y * bg_ratio,
    )
    inset: str | None = None
    warnings = list(focus_result.warnings)
    if bbox is not None and cov is not None and stage is not None:
        if len(stage) != 2:
            raise ValueError("stage 须为 (width, height)")
        inset = foreground_inset(bbox, cov, float(stage[0]), float(stage[1]))
        warnings.append("前景 mask 为近似矩形（非 SAM 抠图），仅缺 SAM 权重时降级使用")
    return ParallaxPlan(
        foreground=foreground,
        background=background,
        bg_ratio=params.bg_ratio,
        haze=params.haze,
        fg_inset=inset,
        warnings=warnings,
    )


def foreground_inset(
    bbox: tuple[float, float, float, float] | list[float],
    cov: CoverRect,
    stage_w: float,
    stage_h: float,
) -> str:
    """把主体框换算成 CSS `inset(top% right% bottom% left%)`，作 SAM 缺失时的近似矩形 mask。

    仅用离线 bbox 计算，不含任何在线推理；坐标 clamp 到 [0,100]%，保证 CSS 合法。
    """
    box = _validate_bbox(bbox)
    px = (cov.x + box[0] * cov.w, cov.x + box[2] * cov.w)
    py = (cov.y + box[1] * cov.h, cov.y + box[3] * cov.h)
    pct = lambda v, total: max(0.0, min(100.0, v / total * 100.0))
    return "inset({:.3f}% {:.3f}% {:.3f}% {:.3f}%)".format(
        pct(stage_h - py[1], stage_h),
        pct(stage_w - px[1], stage_w),
        pct(py[0], stage_h),
        pct(px[0], stage_w),
    )


def iou(
    a: tuple[float, float, float, float] | list[float],
    b: tuple[float, float, float, float] | list[float],
) -> float:
    """方案 20.7 验收：归一化 bbox 的交并比（IoU），值域 [0,1]。"""
    ax1, ay1, ax2, ay2 = _validate_bbox(a)
    bx1, by1, bx2, by2 = _validate_bbox(b)
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > _EPS else 0.0


# 方案文档使用 camelCase；同时提供 Pythonic 别名，便于下游逐字对照。
coverRect = cover_rect
bboxToFocus = bbox_to_focus
deriveAnchorBbox = derive_anchor_bbox
