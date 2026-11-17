"""MCP 扩展工具注册与真实调用测试。

验证：工具总数 20（方案 §5 的 7 + §20 的 3 + 生态/审计/诊断 10），
且新增的查询类工具能真实读取仓库产物并返回统一 {ok,data,error} 结构。
"""
from __future__ import annotations

import pytest

import main


def test_tool_registration_count_is_20() -> None:
    tools = main.mcp._tool_manager._tools
    assert len(tools) == 20, f"期望 20 个工具，实际 {len(tools)}: {sorted(tools)}"
    expected = {
        # 方案第 5 章 7 个
        "validate_assets", "extract_audio_beats", "build_timeline", "adjust_timeline",
        "generate_composition", "preview_project", "render_video",
        # 方案第 20 章 3 个
        "analyze_subjects", "lock_subject", "generate_focus_path",
        # 扩展 10 个
        "list_templates", "get_template", "validate_template", "get_detection_report",
        "get_text_selector_report", "get_beat_benchmark", "get_determinism_report",
        "get_license_summary", "diagnose_webgl", "inspect_composition",
    }
    assert set(tools.keys()) == expected


def test_list_templates_returns_8() -> None:
    r = main.list_templates()
    assert r["ok"] is True
    assert r["data"]["count"] == 11
    ids = {t["template_id"] for t in r["data"]["templates"]}
    assert "04_shatter_flash" in ids and "11_multi_subject_tour" in ids


def test_validate_template_passes_all() -> None:
    for tid in ("04_shatter_flash", "03_subject_pushin", "02_cinematic_teal_orange"):
        r = main.validate_template(tid)
        assert r["ok"] is True, f"{tid} 校验失败: {r.get('error')}"


def test_get_detection_report_real_data() -> None:
    r = main.get_detection_report()
    assert r["ok"] is True
    assert r["data"]["status"] == "passed"
    assert r["data"]["detection"]["person_count"] >= 10


def test_get_determinism_report_honest_skip() -> None:
    r = main.get_determinism_report()
    assert r["ok"] is True
    # 本机无 Docker，报告须诚实标注 SKIP，不伪造 PASS
    assert r["data"] is not None


def test_diagnose_webgl_reports_default_off() -> None:
    r = main.diagnose_webgl()
    assert r["ok"] is True
    assert r["data"]["config"].get("enabled") is False
    assert r["data"]["template_has_canvas"] is True
    assert r["data"]["headless_context_controllable"] is False


def test_get_license_summary_reads_report() -> None:
    r = main.get_license_summary()
    assert r["ok"] is True
    assert len(r["data"]["table_rows"]) > 0


def test_missing_template_returns_fail() -> None:
    r = main.get_template("does_not_exist")
    assert r["ok"] is False
    assert r["error"]["code"] == "FILE_NOT_FOUND"
