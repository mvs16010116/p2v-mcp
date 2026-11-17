"""音频节拍检测（方案第 9.1 节）。

按文档代码骨架实现：percussive 强化 → beat_track → frames_to_time →
np.unique(np.round(beats, 3)) → beat_group 切片；confidence 三档阈值照抄文档。

librosa 为本模块专用重依赖，采用函数内导入：未安装时素材校验等其他分析功能
仍可正常使用；节拍检测调用会明确抛出 BINARY_NOT_FOUND。
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import numpy as np

from src.contracts import BeatAnalysis, ErrorCode


class BeatDetectionError(RuntimeError):
    """节拍检测失败，携带统一错误码。"""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _confidence_from_beats(beats: np.ndarray) -> Literal["high", "medium", "low"]:
    """按方案 9.1 的间隔标准差阈值估算置信度（纯函数，便于确定测试）。"""
    if len(beats) > 3:
        std = float(np.std(np.diff(beats)))
        return "high" if std < 0.06 else ("medium" if std < 0.15 else "low")
    return "low"


def detect_beats(
    audio_path: str | Path,
    start_time: float = 0.0,
    end_time: float | None = None,
    beat_group: int = 1,
) -> BeatAnalysis:
    """检测节拍并返回 BeatAnalysis（方案 5.2 / 9.1）。

    Args:
        audio_path: 音频文件路径。
        start_time: 分析起始偏移（秒）。
        end_time: 分析结束时间（秒）；None 表示到文件末尾。
        beat_group: 每 N 拍一镜，必须 ≥ 1。

    Raises:
        BeatDetectionError: FILE_NOT_FOUND / INVALID_AUDIO / BINARY_NOT_FOUND / EMPTY_TIMELINE。
        ValueError: 参数非法。
    """
    if not Path(audio_path).is_file():
        raise BeatDetectionError(ErrorCode.FILE_NOT_FOUND, f"音频文件不存在: {audio_path}")
    if beat_group < 1:
        raise ValueError("beat_group 必须 ≥ 1")
    if start_time < 0:
        raise ValueError("start_time 必须 ≥ 0")
    if end_time is not None and end_time <= start_time:
        raise ValueError("end_time 必须大于 start_time")

    try:
        import librosa
    except ImportError as exc:
        raise BeatDetectionError(ErrorCode.BINARY_NOT_FOUND, "缺少 librosa，无法执行节拍检测") from exc

    duration = (end_time - start_time) if end_time is not None else None
    try:
        y, sr = librosa.load(str(audio_path), sr=22050, mono=True, offset=start_time, duration=duration)
        audio_duration = float(len(y) / sr)
        y = librosa.effects.percussive(y)
        tempo, frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
        beats = librosa.frames_to_time(frames, sr=sr)
    except Exception as exc:
        raise BeatDetectionError(ErrorCode.INVALID_AUDIO, f"音频解码/节拍分析失败: {exc}") from exc

    beats = np.unique(np.round(beats, 3))
    grouped = beats[::beat_group]
    if len(grouped) < 2:
        raise BeatDetectionError(ErrorCode.EMPTY_TIMELINE, "合并后的节拍不足 2 个，无法编排时间线")

    return BeatAnalysis(
        bpm=float(np.atleast_1d(tempo)[0]),
        raw_beats=[float(v) for v in beats],
        grouped_beats=[float(v) for v in grouped],
        audio_duration=audio_duration,
        confidence=_confidence_from_beats(beats),
    )
