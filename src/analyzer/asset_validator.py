"""素材前置校验（方案第 10 章 / 5.1 节）。

用 ffprobe 校验音频流、时长、采样率与图片可解码性/宽高；坏文件必须在渲染前
被拦截。所有外部命令走 src.utils.subprocess_helper.run_command（shell=False、
list args、超时、白名单环境、日志落盘）。
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from src.contracts import AssetValidation, AudioInfo, ErrorCode, ImageInfo
from src.utils.subprocess_helper import SubprocessError, run_command


class AssetValidationError(RuntimeError):
    """素材校验失败，携带统一错误码。"""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _probe(path: str | Path, error_code: ErrorCode = ErrorCode.INVALID_IMAGE) -> dict:
    """调用 ffprobe 并解析 JSON；路径不存在与命令失败分别映射错误码。"""
    file_path = Path(path)
    if not file_path.is_file():
        raise AssetValidationError(ErrorCode.FILE_NOT_FOUND, f"文件不存在: {path}")
    args = [
        "ffprobe",
        "-v", "error",
        "-show_streams", "-show_format",
        "-of", "json",
        str(file_path),
    ]
    try:
        result = run_command(
            args,
            cwd=str(file_path.parent),
            timeout=30,
            log_dir="logs",
            task_id=str(uuid4()),
        )
    except SubprocessError as exc:
        raise AssetValidationError(exc.code, f"ffprobe 执行失败: {exc.message}") from exc
    if not result.ok:
        raise AssetValidationError(error_code, f"ffprobe 无法解析: {file_path.name}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssetValidationError(error_code, "ffprobe 输出非法 JSON") from exc


def _validate_audio(audio_path: str | Path, min_duration: float) -> AudioInfo:
    if min_duration < 0:
        raise ValueError("min_duration 必须 ≥ 0")
    probe = _probe(audio_path, ErrorCode.INVALID_AUDIO)
    streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise AssetValidationError(ErrorCode.INVALID_AUDIO, f"无音频流: {audio_path}")
    stream = streams[0]

    duration = float(probe.get("format", {}).get("duration") or stream.get("duration") or 0)
    if duration < min_duration:
        raise AssetValidationError(
            ErrorCode.AUDIO_TOO_SHORT,
            f"音频时长 {duration:.3f}s < min_duration {min_duration:.3f}s",
        )
    sample_rate = int(stream.get("sample_rate") or 0)
    channels = int(stream.get("channels") or 0)
    if sample_rate <= 0 or channels <= 0:
        raise AssetValidationError(ErrorCode.INVALID_AUDIO, "音频采样率/声道信息无效")
    codec = str(stream.get("codec_name") or "")
    if not codec:
        raise AssetValidationError(ErrorCode.INVALID_AUDIO, "音频编码信息缺失")

    return AudioInfo(duration=duration, sample_rate=sample_rate, channels=channels, codec=codec)


def _validate_image(image_path: str | Path) -> ImageInfo:
    probe = _probe(image_path)
    streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        raise AssetValidationError(ErrorCode.INVALID_IMAGE, f"图片不可解码: {image_path}")
    width = int(streams[0].get("width") or 0)
    height = int(streams[0].get("height") or 0)
    if width <= 0 or height <= 0:
        raise AssetValidationError(ErrorCode.INVALID_IMAGE, f"图片宽高无效: {image_path}")
    return ImageInfo(path=str(image_path), width=width, height=height, valid=True)


def validate_assets(
    audio_path: str | Path,
    image_paths: list[str | Path],
    min_duration: float = 1.0,
) -> AssetValidation:
    """校验 BGM 与图片，返回 AssetValidation（方案 5.1 出参）。"""
    audio = _validate_audio(audio_path, min_duration)
    images = [_validate_image(path) for path in image_paths]

    warnings: list[str] = []
    for image in images:
        ratio = image.width / image.height
        if 0.9 <= ratio <= 1.1:
            warnings.append(f"{Path(image.path).name} 宽高比 1:1，将被 object-fit:cover 裁切")

    return AssetValidation(audio=audio, images=images, warnings=warnings)
