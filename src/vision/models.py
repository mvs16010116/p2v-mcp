"""视觉分析层的跨层数据契约（Pydantic v2，方案 20.3 / 20.6）。

不重复定义 src.contracts 中已有的 model：bbox 复用 BboxCoords，工具返回复用
ToolResponse / ErrorInfo / ErrorCode（CODING_STANDARDS 第 1 节）。
"""
from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from src.contracts import BboxCoords, StrictModel


def _check_bbox_order(bbox: list[float]) -> list[float]:
    if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        raise ValueError("bbox 须为 [x1,y1,x2,y2] 且满足 x1 < x2、y1 < y2")
    return bbox


class Subject(StrictModel):
    """单个候选主体（方案 20.3）。bbox 为归一化坐标，取值 0..1。"""

    index: int = Field(ge=0, description="候选序号，对应 subjects 列表位置，供 index selector 点选")
    label: str = Field(min_length=1, description="类别名，如 person / 红衣服的人")
    bbox: BboxCoords = Field(description="归一化主体框 [x1,y1,x2,y2]")
    confidence: float = Field(ge=0.0, le=1.0, description="检测/匹配置信度")

    @field_validator("bbox")
    @classmethod
    def _check_order(cls, v: list[float]) -> list[float]:
        return _check_bbox_order(v)


class ArtifactPaths(StrictModel):
    """离线分析落盘产物路径（方案 20.1：渲染阶段只读文件）。"""

    subjects_json: str | None = Field(default=None, description="subjects.json 路径")
    depth: str | None = Field(default=None, description="深度图 PNG 路径，实验性功能产物")
    mask_dir: str | None = Field(default=None, description="SAM 主体透明 PNG 目录")


class SubjectAnalysis(StrictModel):
    """detect_subjects 出参（方案 20.3）。落盘即 subjects.json。"""

    image: str = Field(min_length=1, description="输入图片相对/绝对路径")
    image_size: tuple[int, int] = Field(description="图片像素尺寸 (width, height)")
    subjects: list[Subject] = Field(default_factory=list, description="候选主体列表")
    count: int = Field(ge=0, description="候选数量，必须等于 len(subjects)")
    artifacts: ArtifactPaths = Field(default_factory=ArtifactPaths, description="落盘产物路径")

    @field_validator("image_size")
    @classmethod
    def _check_size(cls, v: tuple[int, int]) -> tuple[int, int]:
        if len(v) != 2 or v[0] <= 0 or v[1] <= 0:
            raise ValueError("image_size 须为 (正整数 width, 正整数 height)")
        return v

    @model_validator(mode="after")
    def _check_count_and_indices(self) -> SubjectAnalysis:
        if self.count != len(self.subjects):
            raise ValueError("count 必须等于 len(subjects)")
        indices = [s.index for s in self.subjects]
        if len(set(indices)) != len(indices):
            raise ValueError("subject.index 必须唯一")
        return self


class DepthResult(StrictModel):
    """estimate_depth 出参（方案 20.5 辅助视差/雾化，实验性）。"""

    image: str = Field(min_length=1, description="输入图片路径")
    enabled: bool = Field(description="本次调用是否真正执行了深度估计")
    depth_path: str | None = Field(default=None, description="深度图 PNG 路径；未启用时为 None")
    model: str = Field(min_length=1, description="使用的模型标识")
    warnings: list[str] = Field(default_factory=list, description="降级/近似说明")


class FocusLock(StrictModel):
    """lock_subject 出参里的焦点数据块（方案 20.6 focus 字段）。"""

    bbox: BboxCoords = Field(description="锁定的归一化主体框")
    subject_index: int = Field(ge=0, description="锁定的候选序号")
    label: str = Field(min_length=1, description="锁定主体类别/描述")
    confidence: float = Field(ge=0.0, le=1.0, description="锁定置信度")
    fill: float = Field(ge=0.1, le=1.0, description="主体占画面比例")
    fit: str = Field(description="contain / cover")
    scale: float = Field(gt=0.0, description="推近倍数，已受 ≤4 上限保护")
    x: float = Field(description="GSAP xPercent 平移")
    y: float = Field(description="GSAP yPercent 平移")
    transform_origin: str = Field(default="50% 50%", description="与方案 20.4 一致")
    subject_center: tuple[float, float] = Field(description="舞台坐标系中的主体中心")
    warnings: list[str] = Field(default_factory=list, description="越界降 fill / scale 钳制告警")
    extra: dict[str, Any] = Field(default_factory=dict, description="selector 回显等附加信息")

    @field_validator("bbox")
    @classmethod
    def _check_order(cls, v: list[float]) -> list[float]:
        return _check_bbox_order(v)
