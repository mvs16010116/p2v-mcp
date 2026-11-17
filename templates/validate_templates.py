"""离线校验 templates/*.json：每个模板的参数必须能被 src.contracts 的 Pydantic v2 model 接受。

不依赖 HyperFrames CLI / ffmpeg / 浏览器，不渲染任何帧。
用法：python templates/validate_templates.py
成功退出码 0 并打印 RESULT: TEMPLATE VALIDATION PASSED (8/8)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import Field, ValidationError  # noqa: E402

from src.contracts import (  # noqa: E402
    BeatImpact,
    CinematicFx,
    Focus,
    ImageSpring,
    KenBurns,
    MaskConfig,
    StrictModel,
    TextAnim,
    Timeline,
)

TEMPLATES_DIR = Path(__file__).resolve().parent


class CompositionParams(StrictModel):
    """main.py GenerateCompositionInput 的离线镜像。

    不 import main.py：那会拉起 FastMCP 并触发服务层依赖，违背"离线校验"约束。
    字段与 main.py GenerateCompositionInput 逐字一致；backend 仅 hyperframes 的
    业务约束由 service 层负责，这里只校验字段类型与非法字段。
    """

    backend: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0)
    audio_path: str = Field(min_length=1)
    font_family: str = Field(min_length=1)
    output_dir: str | None = None


EFFECT_MODELS = {
    "mask": MaskConfig,
    "image_spring": ImageSpring,
    "text_anim": TextAnim,
    "kenburns": KenBurns,
    "focus": Focus,
    "cinematic_fx": CinematicFx,
}
CLIP_KEYS = (*EFFECT_MODELS, "beat_impacts")


def _check_template(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        tpl = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"JSON 读取/解析失败: {exc}"]

    if tpl.get("schema_version") != 1:
        errors.append("schema_version 必须为 1")
    if tpl.get("template_id") != path.stem:
        errors.append(f"template_id={tpl.get('template_id')!r} 与文件名 {path.stem!r} 不一致")

    for section in ("bgm_profile", "build_timeline", "timeline_defaults", "generate_composition"):
        if not isinstance(tpl.get(section), dict):
            errors.append(f"缺少对象字段 {section}")

    effects = tpl.get("build_timeline", {}).get("effects")
    if not isinstance(effects, list) or not effects:
        errors.append("build_timeline.effects 必须是非空数组")
        effects = []

    for i, effect in enumerate(effects):
        for key, model in EFFECT_MODELS.items():
            value = effect.get(key)
            if value is not None:
                try:
                    model.model_validate(value)
                except ValidationError as exc:
                    errors.append(f"effects[{i}].{key} 校验失败: {exc.errors()}")
        for j, impact in enumerate(effect.get("beat_impacts") or []):
            try:
                BeatImpact.model_validate(impact)
            except ValidationError as exc:
                errors.append(f"effects[{i}].beat_impacts[{j}] 校验失败: {exc.errors()}")

        # 合成一个 Clip + Timeline，校验 impact_profile / style_preset 与整体不变量。
        clip: dict[str, object] = {
            "index": 0,
            "start": 0.0,
            "end": 1.0,
            "duration": 1.0,
            "image": "assets/img_000.jpg",
            "text": "模板离线校验",
        }
        for key in CLIP_KEYS:
            value = effect.get(key)
            if value is not None:
                clip[key] = value
        defaults = tpl.get("timeline_defaults", {})
        try:
            Timeline.model_validate(
                {
                    "task_id": "template-offline-check",
                    "fps": defaults.get("fps", 30),
                    "total_duration": 1.0,
                    "clips": [clip],
                    "impact_profile": defaults.get("impact_profile", "normal"),
                    "style_preset": defaults.get("style_preset", "standard"),
                }
            )
        except ValidationError as exc:
            errors.append(f"timeline_defaults / Clip 组合校验失败: {exc.errors()}")

    try:
        comp = CompositionParams.model_validate(tpl.get("generate_composition", {}))
        if comp.backend != "hyperframes":
            errors.append("generate_composition.backend 仅支持 hyperframes")
    except ValidationError as exc:
        errors.append(f"generate_composition 校验失败: {exc.errors()}")

    # BeatImpact 自检：即使模板未显式给出，也确认该契约 model 被真正调用过。
    try:
        BeatImpact.model_validate(
            {
                "t": 1.0,
                "frame": 30,
                "type": "flash",
                "intensity": 0.8,
                "duration": 0.12,
                "strong": True,
            }
        )
    except ValidationError as exc:
        errors.append(f"BeatImpact 自检失败: {exc.errors()}")

    return errors


def main() -> int:
    files = sorted(TEMPLATES_DIR.glob("[0-9][0-9]_*.json"))
    print(f"发现 {len(files)} 个模板文件")
    if len(files) != 11:
        print(f"FAIL: 期望 11 个模板，实际 {len(files)} 个")
        return 1
    failed = 0
    for path in files:
        errors = _check_template(path)
        if errors:
            failed += 1
            print(f"[FAIL] {path.name}")
            for err in errors:
                print(f"    - {err}")
        else:
            print(f"[PASS] {path.name}")
    if failed:
        print(f"RESULT: TEMPLATE VALIDATION FAILED ({failed}/{len(files)})")
        return 1
    print(f"RESULT: TEMPLATE VALIDATION PASSED ({len(files)}/{len(files)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
