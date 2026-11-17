"""Depth Anything v2 单目深度估计（方案 20.5 辅助视差 / 雾化，实验性）。

默认关闭（WAR_MAP：深度为实验性能力，不进入默认链路）。开启后输出灰度深度图
供编排层做视差强度与空气透视；这是静态图的近似，不是真 3D（方案 20.8）。

重依赖（torch / transformers / PIL / numpy）一律惰性导入；缺依赖或权重未缓存
时返回 BINARY_NOT_FOUND，不抛到主流程。权重只用 local_files_only 读取，不联网。
"""
from __future__ import annotations

from pathlib import Path

from src.contracts import ToolResponse

from ._common import (
    VisionDependencyError,
    binary_not_found,
    ensure_dir,
    file_not_found,
    import_or_raise,
    read_image_size,
)
from .models import DepthResult

DEFAULT_MODEL = "depth-anything/Depth-Anything-V2-Small-hf"
EXPERIMENTAL_WARNING = "单目深度仅近似视差/雾化，非真光学景深（方案 20.8）；默认关闭"


def estimate_depth(
    image_path: str | Path,
    output_path: str | Path | None = None,
    *,
    enable: bool = False,
    model_name: str = DEFAULT_MODEL,
) -> ToolResponse:
    """输出深度图 PNG；enable=False（默认）时直接返回未启用的结构化结果。"""
    image = Path(image_path).expanduser()

    if not enable:
        result = DepthResult(
            image=str(image),
            enabled=False,
            depth_path=None,
            model=model_name,
            warnings=[EXPERIMENTAL_WARNING, "设置 enable=True 才会加载模型"],
        )
        return ToolResponse(ok=True, data=result.model_dump(mode="json"), error=None)

    try:
        if not image.is_file():
            raise FileNotFoundError(f"图片不存在：{image}")
        width, height = read_image_size(image)

        torch = import_or_raise("torch", "pip install torch（CPU 版即可）")
        transformers = import_or_raise("transformers", "pip install transformers>=4.41")
        image_module = import_or_raise("PIL.Image", "pip install Pillow")
        np = import_or_raise("numpy", "pip install numpy")
        processor_cls = getattr(transformers, "AutoImageProcessor", None)
        model_cls = getattr(transformers, "DepthAnythingForDepthEstimation", None)
        if processor_cls is None or model_cls is None:
            raise VisionDependencyError(
                "当前 transformers 版本缺少 DepthAnythingForDepthEstimation / AutoImageProcessor",
                {"hint": "pip install -U transformers>=4.41", "model": model_name},
            )

        processor = processor_cls.from_pretrained(model_name, local_files_only=True)
        model = model_cls.from_pretrained(model_name, local_files_only=True)
        model.eval()

        with image_module.open(image) as im:
            rgb = im.convert("RGB")
        inputs = processor(images=rgb, return_tensors="pt")
        with torch.no_grad():
            outputs = model(**inputs)
        predicted = processor.post_process_depth_estimation(
            outputs, target_sizes=[(height, width)]
        )[0]["predicted_depth"]
        depth = (predicted - predicted.min()) / (predicted.max() - predicted.min() + 1e-6)
        arr = (depth.squeeze().detach().cpu().numpy() * 255.0).astype("uint8")

        target = (
            Path(output_path).expanduser()
            if output_path is not None
            else image.parent / "analysis" / "depth.png"
        )
        ensure_dir(target.parent)
        image_module.fromarray(arr, mode="L").save(target)

        result = DepthResult(
            image=str(image),
            enabled=True,
            depth_path=str(target),
            model=model_name,
            warnings=[EXPERIMENTAL_WARNING, "深度值已归一化到 0..255 灰度"],
        )
        return ToolResponse(ok=True, data=result.model_dump(mode="json"), error=None)

    except VisionDependencyError as exc:
        return binary_not_found(exc.message, exc.detail)
    except FileNotFoundError as exc:
        return file_not_found(str(exc))
    except Exception as exc:  # 权重未缓存 / 运行时不兼容：明示原因
        return binary_not_found(
            "Depth Anything v2 离线推理失败（权重未缓存或运行时不兼容）",
            {"reason": f"{type(exc).__name__}: {exc}", "model": model_name, "hint": "预下载权重到本地 HF 缓存"},
        )


__all__ = ["estimate_depth", "DEFAULT_MODEL", "EXPERIMENTAL_WARNING"]
