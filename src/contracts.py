"""数据契约（方案文档第 6 / 18 / 19 / 20 章）。

所有跨层传递的数据必须是本模块定义的 Pydantic v2 model，禁止裸 dict
（CODING_STANDARDS 第 1 节）。时间统一用「秒」（float），帧换算只在渲染层做。

字段逐字对齐方案文档：
- 第 6 章：mask / image_spring / text_anim / kenburns / 统一错误码
- 第 18.2 章：impact_profile / beat_impacts
- 第 19.8 章：style_preset / cinematic_fx（mask/shatter/reveal/backdrop/grade/light）
- 第 20.6 章：focus（selector/bbox/fill/fit/hold_ratio/parallax/tour_to）
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    """所有契约 model 的基类：禁止未声明字段，避免 typo 被静默吞掉。"""

    model_config = ConfigDict(extra="forbid")


class ErrorCode(str, Enum):
    """统一错误码，严格取方案文档第 6.5 节枚举值（共 9 个）。

    第 6.5 节是唯一权威源：不得出现第 10 个错误码，新增必须先改文档
    （CODING_STANDARDS 第 1 节）。tests/test_contracts.py 有 9 值一致性断言。
    """

    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    INVALID_AUDIO = "INVALID_AUDIO"
    AUDIO_TOO_SHORT = "AUDIO_TOO_SHORT"
    INVALID_IMAGE = "INVALID_IMAGE"
    EMPTY_TIMELINE = "EMPTY_TIMELINE"
    BINARY_NOT_FOUND = "BINARY_NOT_FOUND"
    BROWSER_LAUNCH_FAILED = "BROWSER_LAUNCH_FAILED"
    RENDER_TIMEOUT = "RENDER_TIMEOUT"
    FFMPEG_MUX_FAILED = "FFMPEG_MUX_FAILED"


# 归一化 bbox：[x1, y1, x2, y2]，相对原图，取值 0..1（第 20.3 / 20.6 章）
BboxCoords = Annotated[
    list[Annotated[float, Field(ge=0.0, le=1.0)]],
    Field(min_length=4, max_length=4),
]


def _check_bbox_order(bbox: list[float]) -> list[float]:
    if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        raise ValueError("bbox 须为 [x1,y1,x2,y2] 且满足 x1 < x2、y1 < y2")
    return bbox


# ---------------------------------------------------------------------------
# 第 6 章：特效数据契约
# ---------------------------------------------------------------------------


class MaskConfig(StrictModel):
    """蒙版配置（方案 6.1）。入场展开与退场收缩共用 duration/ease。"""

    type: Literal["circle", "ellipse", "inset-rounded", "horizontal-wipe", "vertical-wipe"] = Field(
        default="circle", description="蒙版类型，对应 clip-path 形状；默认 circle（方案 16.3）"
    )
    duration: float = Field(
        default=0.32,
        ge=0.2,
        le=0.5,
        description="展开/收缩时长（秒），同时决定入场与退场窗口；默认 0.32（方案 8.2）",
    )
    ease: Literal["spring", "power2.out"] = Field(
        default="spring", description="缓动；spring 与 image_spring 共享参数"
    )


class ImageSpring(StrictModel):
    """图片弹簧入场参数（方案 6.2）。

    预设：soft=160/22，standard=240/17（默认），bouncy=300/12。
    """

    stiffness: float = Field(
        default=240, ge=120, le=320, description="刚度，越大回弹越快；默认 standard 240"
    )
    damping: float = Field(
        default=17, ge=12, le=28, description="阻尼，越小回弹越明显；默认 standard 17"
    )
    mass: float = Field(ge=0.1, le=10, default=1.0, description="质量，一般固定 1")


class TextAnim(StrictModel):
    """文字动画参数（方案 6.3）。文字入场晚于图片，观感更专业。"""

    type: Literal["spring-up-fade", "spring-scale", "fade-in", "per-char"] = Field(
        default="spring-up-fade", description="文字动画类型；默认 spring-up-fade"
    )
    stiffness: float = Field(ge=120, le=320, default=220, description="同 image_spring")
    damping: float = Field(ge=12, le=28, default=20, description="同 image_spring")
    delay: float = Field(ge=0.0, le=0.3, default=0.08, description="文字晚于图片入场的延迟（秒）")
    y_offset: float = Field(ge=0.0, default=60.0, description="上浮起始位移（px）")
    stroke: bool = Field(default=False, description="是否描边")
    shadow: bool = Field(default=False, description="是否投影")


class KenBurns(StrictModel):
    """Ken Burns 2D 运镜参数（方案 6.4）。

    稳态段从 T_in 结束运行至 T_out 开始，避免与入场 spring 抢 scale。
    """

    enable: bool = Field(default=False, description="运镜开关")
    start_scale: float = Field(gt=0, default=1.0, description="起始缩放，>1 拉近，<1 拉远")
    end_scale: float = Field(gt=0, default=1.15, description="结束缩放")
    start_x: float = Field(default=0.0, description="起始 xPercent 平移")
    start_y: float = Field(default=0.0, description="起始 yPercent 平移")
    end_x: float = Field(default=0.0, description="结束 xPercent 平移")
    end_y: float = Field(default=0.0, description="结束 yPercent 平移")
    ease: Literal["none", "power1.inOut", "power2.inOut"] = Field(
        default="none", description="none 匀速；power1.inOut 慢起慢停"
    )
    pivot_x: str = Field(
        default="50%", pattern=r"^\d+(?:\.\d+)?%$", description="聚焦锚点 x，形如 50%"
    )
    pivot_y: str = Field(
        default="50%", pattern=r"^\d+(?:\.\d+)?%$", description="聚焦锚点 y，形如 35%"
    )


# ---------------------------------------------------------------------------
# 第 18 章：节拍冲击层
# ---------------------------------------------------------------------------


class BeatImpact(StrictModel):
    """单次节拍冲击（方案 18.2）。

    frame = round(t * fps)，必须落在帧边界；同一 beat 只能选一种 stage 级冲击。
    """

    t: float = Field(ge=0.0, description="冲击时间戳（秒）")
    frame: int = Field(ge=0, description="量化后的帧号，frame = round(t * fps)")
    type: Literal["flash", "punch", "shake", "chromatic"] = Field(description="冲击类型")
    intensity: float = Field(ge=0.0, le=1.0, description="强度；最终强度 = intensity × profile 倍率，clamp 到 0.9")
    duration: float = Field(gt=0.0, le=1.0, description="冲击持续时长（秒）")
    strong: bool = Field(default=False, description="是否落在 grouped_beats 强拍上")


# ---------------------------------------------------------------------------
# 第 19 章：电影级图片特效层（方案 19.8）
# ---------------------------------------------------------------------------


class CinematicMask(StrictModel):
    """动态裁切配置（方案 19.2 / 19.8）。"""

    mode: Literal["iris", "irregular", "slats", "path"] = Field(
        default="irregular", description="裁切模式"
    )
    duration: float = Field(gt=0.0, le=1.0, default=0.38, description="揭示时长（秒）")
    seed_from: str = Field(
        default="clip.index", description="不规则裁切扰动 seed 来源，保证同一 clip 可复现"
    )


class Shatter(StrictModel):
    """碎裂炸现配置（方案 19.3 / 19.8）。碎片总数上限 36（6×6）。"""

    enable: bool = Field(default=False, description="是否启用碎裂炸现进成片；默认关闭保证兼容")
    cols: int = Field(ge=1, le=6, default=6, description="横向碎片数")
    rows: int = Field(ge=1, le=6, default=6, description="纵向碎片数")
    stagger: float = Field(ge=0.0, default=0.018, description="碎片入场错峰（秒）")

    @model_validator(mode="after")
    def _check_shard_budget(self) -> Shatter:
        if self.cols * self.rows > 36:
            raise ValueError("碎片总数不得超过 36（方案 19.3：6×6 上限）")
        return self


class Reveal(StrictModel):
    """闪入配置（方案 19.4 / 19.8）：模糊 + 过曝 + 微缩放三者回落。"""

    type: Literal["flash-in", "fade"] = Field(default="flash-in", description="揭示类型")
    blur_px: float = Field(ge=0.0, default=14.0, description="起始模糊半径（px）")
    brightness: float = Field(ge=0.0, default=2.4, description="起始过曝亮度倍数")
    overshoot: float = Field(ge=1.0, default=1.12, description="起始微缩放倍数")
    duration: float = Field(gt=0.0, default=0.22, description="回落时长（秒）")
    ghosts: int = Field(ge=0, le=5, default=3, description="运动模糊残影层数")


class Backdrop(StrictModel):
    """背景层 L1（方案 19.3 / 19.8）：碎片间隙露出，避免纯黑底。"""

    type: Literal["blur", "color"] = Field(default="blur", description="背景类型")
    blur_px: float = Field(ge=0.0, default=24.0, description="背景模糊半径（px）")
    brightness: float = Field(ge=0.0, default=0.5, description="背景亮度倍数")


class Grade(StrictModel):
    """光效与调色层 L4 的静态配置（方案 19.5 / 19.8）。"""

    preset: Literal["none", "film", "teal-orange", "noir"] = Field(
        default="none", description="调色预设"
    )
    grain_png: str | None = Field(
        default=None, description="预渲染颗粒纹理相对路径；必须是静态 PNG，禁止逐帧生成噪声"
    )
    vignette: float = Field(ge=0.0, le=1.0, default=0.0, description="暗角强度")


class Light(StrictModel):
    """扫光与镜头光晕配置（方案 19.5 / 19.8）。"""

    sweep: bool = Field(default=False, description="是否启用扫光")
    flare_at_beat: list[int] = Field(
        default_factory=list, description="触发光晕的 beat 序号列表"
    )
    flare_asset: str | None = Field(default=None, description="光晕素材相对路径")


class CinematicFx(StrictModel):
    """电影级特效集合（方案 19.8），与既有 effects 冲突时以本字段为准。"""

    mask: CinematicMask = Field(default_factory=CinematicMask, description="动态裁切")
    shatter: Shatter = Field(default_factory=Shatter, description="碎裂炸现")
    reveal: Reveal = Field(default_factory=Reveal, description="闪入")
    backdrop: Backdrop = Field(default_factory=Backdrop, description="背景层")
    grade: Grade = Field(default_factory=Grade, description="调色层")
    light: Light = Field(default_factory=Light, description="光效层")


# ---------------------------------------------------------------------------
# 第 20 章：主体感知聚焦（方案 20.6）
# ---------------------------------------------------------------------------


class Selector(StrictModel):
    """主体锁定选择器（方案 20.3 / 20.6），三种 type 互斥，字段按 type 必填。"""

    type: Literal["text", "reference", "index"] = Field(description="锁定方式")
    query: str | None = Field(default=None, description="type=text 时必填：Grounding DINO 查询文本")
    image: str | None = Field(default=None, description="type=reference 时必填：参考图相对路径")
    value: int | None = Field(default=None, ge=0, description="type=index 时必填：候选序号")

    @model_validator(mode="after")
    def _check_exclusive_fields(self) -> Selector:
        required = {"text": "query", "reference": "image", "index": "value"}[self.type]
        for field in ("query", "image", "value"):
            present = getattr(self, field) is not None
            if field == required and not present:
                raise ValueError(f"selector.type={self.type} 时 {field} 必填")
            if field != required and present:
                raise ValueError(f"selector.type={self.type} 时不应提供 {field}")
        return self


class Parallax(StrictModel):
    """分层视差（方案 20.5 / 20.6）：前景推近快、背景推近慢。"""

    enable: bool = Field(default=False, description="是否启用分层视差")
    bg_ratio: float = Field(ge=0.0, le=1.0, default=0.35, description="背景层推近比例")
    haze: float = Field(ge=0.0, le=1.0, default=0.15, description="远景雾化强度")


class TourTo(StrictModel):
    """多主体漫游终点（方案 20.5 / 20.6）：镜头内从当前主体推到另一主体。"""

    subject_index: int = Field(ge=0, description="目标主体序号")
    bbox: BboxCoords = Field(description="目标主体框 [x1,y1,x2,y2]，归一化")

    @field_validator("bbox")
    @classmethod
    def _check_order(cls, v: list[float]) -> list[float]:
        return _check_bbox_order(v)


class Focus(StrictModel):
    """主体聚焦配置（方案 20.6），与 kenburns 二选一，focus 优先。"""

    mode: Literal["subject", "manual"] = Field(default="subject", description="聚焦模式")
    selector: Selector | None = Field(default=None, description="主体锁定选择器")
    subject_index: int | None = Field(default=None, ge=0, description="锁定的主体序号")
    bbox: BboxCoords | None = Field(default=None, description="主体框 [x1,y1,x2,y2]，归一化")
    fill: float = Field(ge=0.1, le=1.0, default=0.62, description="主体占画面比例")
    fit: Literal["contain", "cover"] = Field(
        default="contain", description="contain 完整入画（推荐）；cover 填满可能裁切"
    )
    anchor_region: Literal["full", "upper_body", "face"] = Field(
        default="upper_body",
        description=(
            "方案 20.6：指定把检测框交给 bboxToFocus 前取哪一块区域计算 focus——"
            "full 使用检测框原样（全身人物会被 contain 拉远）；"
            "upper_body 从全身框派生头+肩区域（取框高前 45%），得到 scale>1 的真推近且避免切头，默认；"
            "face 仅取面部区域，需要人脸/关键点检测，许可风险见 20.2，默认不启用"
        ),
    )
    hold_ratio: float = Field(ge=0.0, le=1.0, default=0.2, description="推近结束后的停留占比")
    parallax: Parallax = Field(default_factory=Parallax, description="分层视差")
    tour_to: TourTo | None = Field(default=None, description="漫游终点，可选")

    @field_validator("bbox")
    @classmethod
    def _check_order(cls, v: list[float] | None) -> list[float] | None:
        return None if v is None else _check_bbox_order(v)


# ---------------------------------------------------------------------------
# 镜头与时间线
# ---------------------------------------------------------------------------


class Clip(StrictModel):
    """单个镜头（方案 5.3 出参 + 18/19/20 章扩展字段）。

    时间单位统一为秒；duration 必须等于 end - start（容差 1e-3）。
    focus 与 kenburns 二选一，focus 优先。
    """

    index: int = Field(ge=0, description="镜头序号")
    start: float = Field(ge=0.0, description="镜头起始时间（秒）")
    end: float = Field(gt=0.0, description="镜头结束时间（秒）")
    duration: float = Field(gt=0.0, description="镜头时长（秒）")
    image: str = Field(min_length=1, description="图片相对路径，如 assets/img_001.jpg")
    text: str = Field(default="", description="镜头文案")
    mask: MaskConfig = Field(default_factory=MaskConfig, description="蒙版")
    image_spring: ImageSpring = Field(default_factory=ImageSpring, description="图片弹簧入场")
    text_anim: TextAnim = Field(default_factory=TextAnim, description="文字动画")
    kenburns: KenBurns = Field(default_factory=KenBurns, description="Ken Burns 运镜")
    focus: Focus | None = Field(default=None, description="主体感知聚焦，优先于 kenburns")
    # 双级 beat_impacts 约定：Clip 级是单镜冲击，Timeline 级是全局默认。
    # 生成时 Timeline 级作为 fallback；下游不得在两处写不同值。
    beat_impacts: list[BeatImpact] = Field(
        default_factory=list,
        description="单镜冲击；未显式给出时回退到 Timeline.beat_impacts（方案 18.2）",
    )
    cinematic_fx: CinematicFx | None = Field(default=None, description="电影级特效覆盖")

    @model_validator(mode="after")
    def _check_times(self) -> Clip:
        if self.end <= self.start:
            raise ValueError("end 必须大于 start")
        if abs(self.duration - (self.end - self.start)) > 1e-3:
            raise ValueError("duration 必须等于 end - start")
        return self


class Timeline(StrictModel):
    """完整时间线（方案 5.3 出参 + 18.2 顶层冲击 + 19.7 风格预设）。

    由 build_timeline 生成；编排层输出统一为秒，禁止出现帧号（CODING_STANDARDS 1）。
    """

    task_id: str = Field(min_length=1, description="任务 id（uuid4 字符串）")
    fps: int = Field(default=30, ge=1, le=240, description="帧率，帧换算只在渲染层使用")
    total_duration: float = Field(gt=0.0, description="总时长（秒）")
    clips: list[Clip] = Field(min_length=1, description="镜头序列，至少 1 个")
    impact_profile: Literal["off", "subtle", "normal", "aggressive"] = Field(
        default="normal", description="冲击强度档位；off 关闭（方案 18.2）"
    )
    # 与 Clip.beat_impacts 配套：此处是全局默认，作为单镜未配置时的 fallback；
    # 下游不得在两处写不同值（见 Clip 字段注释）。
    beat_impacts: list[BeatImpact] = Field(
        default_factory=list,
        description="全局默认冲击，作为 Clip 级缺失时的 fallback（方案 18.2）",
    )
    style_preset: Literal["minimal", "standard", "cinematic", "aggressive"] = Field(
        default="standard", description="风格预设，一键展开整套特效参数（方案 19.7）"
    )
    warnings: list[str] = Field(
        default_factory=list, description="合并 beat / 关闭 Ken Burns 等降级告警"
    )

    @model_validator(mode="after")
    def _check_clip_bounds(self) -> Timeline:
        last_end = max(clip.end for clip in self.clips)
        if last_end > self.total_duration + 1e-3:
            raise ValueError("任一 clip 的 end 不得超过 total_duration")
        return self


# ---------------------------------------------------------------------------
# 工具出参 model
# ---------------------------------------------------------------------------


class AudioInfo(StrictModel):
    """音频元信息（方案 5.1 出参 audio）。"""

    duration: float = Field(ge=0.0, description="音频时长（秒）")
    sample_rate: int = Field(gt=0, description="采样率（Hz）")
    channels: int = Field(ge=1, description="声道数")
    codec: str = Field(min_length=1, description="编码，如 mp3")


class ImageInfo(StrictModel):
    """图片元信息（方案 5.1 出参 images 元素）。imgW/imgH 为 19.3 cover 对齐必需。"""

    path: str = Field(min_length=1, description="图片相对路径")
    width: int = Field(gt=0, description="像素宽")
    height: int = Field(gt=0, description="像素高")
    valid: bool = Field(default=True, description="是否可解码")


class AssetValidation(StrictModel):
    """validate_assets 出参（方案 5.1）。坏文件必须在渲染前被拦截。"""

    audio: AudioInfo = Field(description="音频校验结果")
    images: list[ImageInfo] = Field(default_factory=list, description="图片校验结果")
    warnings: list[str] = Field(default_factory=list, description="宽高比、字体降级等告警")


class BeatAnalysis(StrictModel):
    """extract_audio_beats 出参（方案 5.2）。"""

    bpm: float = Field(gt=0.0, description="估计 BPM")
    raw_beats: list[float] = Field(default_factory=list, description="librosa 原始鼓点时间（秒）")
    grouped_beats: list[float] = Field(
        default_factory=list, description="合并后的镜头切换点（秒）"
    )
    audio_duration: float = Field(ge=0.0, description="音频时长（秒）")
    confidence: Literal["high", "medium", "low"] = Field(
        description="基于 beat 间隔标准差；low 建议走人工微调"
    )


class RenderResult(StrictModel):
    """render_video 出参（方案 5.7）。"""

    output_path: str = Field(min_length=1, description="输出 MP4 路径")
    duration: float = Field(ge=0.0, description="成片时长（秒），误差 ≤ 1 帧（18.3）")
    resolution: str = Field(
        pattern=r"^\d+x\d+$", description="分辨率，如 1080x1920"
    )
    fps: int = Field(gt=0, description="帧率")
    render_time_sec: float = Field(ge=0.0, description="渲染耗时（秒）")
    log_path: str = Field(min_length=1, description="渲染日志路径，便于排错")
    avg_frame_render_ms: float | None = Field(
        default=None, ge=0.0, description="单帧平均渲染耗时（ms），19.9 性能验收项"
    )


class ErrorInfo(StrictModel):
    """统一错误对象（方案第 5 章）。code 只取 ErrorCode 枚举。"""

    code: ErrorCode = Field(description="错误码")
    message: str = Field(default="", description="人类可读错误描述")
    detail: dict[str, Any] = Field(default_factory=dict, description="结构化附加信息")


class ToolResponse(StrictModel):
    """所有 MCP 工具的统一返回包装：{ok, data, error}（方案第 5 章）。"""

    ok: bool = Field(description="是否成功")
    data: dict[str, Any] | None = Field(default=None, description="成功时的数据载荷")
    error: ErrorInfo | None = Field(default=None, description="失败时的错误对象")

    @model_validator(mode="after")
    def _check_shape(self) -> ToolResponse:
        if self.ok and self.error is not None:
            raise ValueError("ok=true 时 error 必须为 null")
        if not self.ok:
            if self.data is not None:
                raise ValueError("ok=false 时 data 必须为 null")
            if self.error is None:
                raise ValueError("ok=false 时 error 不可为 null")
        return self


class CommandResult(StrictModel):
    """安全子进程的返回结果（供 src/utils/subprocess_helper.py 使用）。

    输入输出均结构化，避免裸 dict 跨函数边界。
    """

    ok: bool = Field(description="returncode == 0")
    returncode: int = Field(description="子进程退出码；超时/未启动时为 -1")
    stdout: str = Field(default="", description="标准输出（同时已落盘）")
    stderr: str = Field(default="", description="标准错误（同时已落盘）")
    log_path: str = Field(default="", description="日志文件路径")
    duration_sec: float = Field(ge=0.0, default=0.0, description="执行耗时（秒）")
