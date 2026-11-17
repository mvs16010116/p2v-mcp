"""分析层测试：合成 WAV 测节拍检测、ffprobe 测素材校验。

依赖缺失时显式 skip 并说明原因（CODING_STANDARDS 第 5 节），不碰 fixtures/。
"""
from __future__ import annotations

import base64
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from src.analyzer.asset_validator import AssetValidationError, validate_assets
from src.analyzer.beat_detector import BeatDetectionError, _confidence_from_beats, detect_beats
from src.contracts import AssetValidation, BeatAnalysis, ErrorCode

# 最小合法 1x1 PNG，宽高比 1:1 用于触发 warning
_1PX_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def _write_click_wav(path: Path, seconds: float = 6.0, sr: int = 22050, bpm: int = 120) -> None:
    """合成 120BPM 点击音轨：每拍一个衰减瞬态，供 librosa 检测。"""
    count = int(seconds * sr)
    y = np.zeros(count, dtype=float)
    period = 60.0 / bpm
    for beat in range(int(seconds / period) + 1):
        start = int(beat * period * sr)
        n = min(int(0.06 * sr), count - start)
        if n <= 0:
            continue
        envelope = np.exp(-np.linspace(0, 9, n))
        y[start : start + n] += 0.9 * envelope * np.sin(2 * np.pi * 1500 * np.arange(n) / sr)
    y = np.clip(y, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes((y * 32767).astype("<i2").tobytes())


def test_confidence_thresholds() -> None:
    """confidence 三档阈值照抄方案 9.1。"""
    assert _confidence_from_beats(np.array([0.0, 0.5, 1.0, 1.5])) == "high"
    assert _confidence_from_beats(np.array([0.0, 0.58, 1.05, 1.72])) == "medium"
    assert _confidence_from_beats(np.array([0.0, 0.9, 1.05, 2.3])) == "low"
    assert _confidence_from_beats(np.array([0.0, 0.5])) == "low"


def test_detect_beats_missing_file(tmp_path: Path) -> None:
    """文件不存在必须在加载重依赖前报 FILE_NOT_FOUND。"""
    with pytest.raises(BeatDetectionError) as exc:
        detect_beats(tmp_path / "missing.wav")
    assert exc.value.code == ErrorCode.FILE_NOT_FOUND


@pytest.mark.parametrize(
    "kwargs",
    [
        {"beat_group": 0},
        {"start_time": -0.1},
        {"start_time": 1.0, "end_time": 0.5},
    ],
)
def test_detect_beats_invalid_params(tmp_path: Path, kwargs: dict) -> None:
    """非法入参必须被拒绝。"""
    audio = tmp_path / "a.wav"
    _write_click_wav(audio)
    with pytest.raises(ValueError):
        detect_beats(audio, **kwargs)


def test_detect_beats_synthetic_wav(tmp_path: Path) -> None:
    """合成 WAV 真实跑 librosa 检测，返回合法 BeatAnalysis。"""
    pytest.importorskip("librosa", reason="librosa 未安装，跳过节拍检测集成测试")
    audio = tmp_path / "click.wav"
    _write_click_wav(audio)
    result = detect_beats(audio, beat_group=2)
    assert isinstance(result, BeatAnalysis)
    assert result.bpm > 0
    assert result.audio_duration > 0
    assert len(result.raw_beats) >= 2
    assert len(result.grouped_beats) >= 2
    assert result.confidence in {"high", "medium", "low"}
    assert np.all(np.diff(result.raw_beats) > 0)


def test_detect_beats_missing_librosa_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """librosa 缺失时映射为 BINARY_NOT_FOUND，而非裸 ImportError。"""
    audio = tmp_path / "click.wav"
    _write_click_wav(audio)
    import builtins

    real_import = builtins.__import__

    def blocked_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "librosa":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    with pytest.raises(BeatDetectionError) as exc:
        detect_beats(audio)
    assert exc.value.code == ErrorCode.BINARY_NOT_FOUND


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe 未安装，跳过素材校验测试")
def test_validate_assets_valid(tmp_path: Path) -> None:
    """合法音频 + 图片返回 AssetValidation；1:1 图片触发 warning。"""
    audio = tmp_path / "bgm.wav"
    image = tmp_path / "square.png"
    _write_click_wav(audio)
    image.write_bytes(_1PX_PNG)
    result = validate_assets(audio, [image], min_duration=1.0)
    assert isinstance(result, AssetValidation)
    assert result.audio.duration >= 1.0
    assert result.audio.sample_rate == 22050
    assert result.images[0].width == 1 and result.images[0].height == 1
    assert any("1:1" in warning for warning in result.warnings)


def test_validate_assets_file_not_found(tmp_path: Path) -> None:
    """路径不存在报 FILE_NOT_FOUND。"""
    with pytest.raises(AssetValidationError) as exc:
        validate_assets(tmp_path / "missing.wav", [])
    assert exc.value.code == ErrorCode.FILE_NOT_FOUND


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe 未安装，跳过素材校验测试")
def test_validate_assets_too_short(tmp_path: Path) -> None:
    """音频时长不足报 AUDIO_TOO_SHORT。"""
    audio = tmp_path / "short.wav"
    _write_click_wav(audio, seconds=2.0)
    with pytest.raises(AssetValidationError) as exc:
        validate_assets(audio, [], min_duration=100.0)
    assert exc.value.code == ErrorCode.AUDIO_TOO_SHORT


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe 未安装，跳过素材校验测试")
def test_validate_assets_invalid_image(tmp_path: Path) -> None:
    """损坏图片报 INVALID_IMAGE。"""
    audio = tmp_path / "bgm.wav"
    broken = tmp_path / "broken.jpg"
    _write_click_wav(audio)
    broken.write_text("not an image", encoding="utf-8")
    with pytest.raises(AssetValidationError) as exc:
        validate_assets(audio, [broken], min_duration=1.0)
    assert exc.value.code == ErrorCode.INVALID_IMAGE
