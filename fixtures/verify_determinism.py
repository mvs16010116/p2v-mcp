#!/usr/bin/env python3
"""Linux Docker 像素级确定性回归（方案第 21 章 / WAR_MAP 唯一剩余验收项）。

同一 composition、同一 t，在 Linux Docker 中渲染两次，逐帧 SHA256 对比；同时把
preview/render 共用的 runtime bundle（vendor/gsap.min.js、CustomEase.min.js）sha256
写进报告（21.3 第 3 条）。报告落盘：

  fixtures/reports/determinism_report.json
  fixtures/reports/determinism_report.md

退出码：
  0 = PASS（逐帧 hash 全一致，产物三项校验通过）
  1 = FAIL（hash 不一致或产物校验不通过）—— 真实失败，绝不当作通过
  2 = ERROR（执行异常：缺 Python 依赖、CLI 报错、无输出等）
  3 = SKIP（环境不满足：非 Linux / 无 Docker 守护进程 / 缺 CLI 或 vendor），
      附 CI 复现命令；skip 是环境声明，不是 workaround，也未 mock/伪造 hash。

本机 Windows 无 Docker：本脚本明确 SKIP，不伪造任何像素证据。
"""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REPORT_DIR = ROOT / "fixtures" / "reports"
JSON_REPORT = REPORT_DIR / "determinism_report.json"
MD_REPORT = REPORT_DIR / "determinism_report.md"
VENDOR_DIR = ROOT / "vendor"
REQUIRED_VENDOR = ("gsap.min.js", "CustomEase.min.js")
FPS = 30
DURATION_SEC = 1.0
PROBE_FRAMES = (0, 7, 15, 22, 29)  # 1s @ 30fps  covers 0 .. 29
TASK_ID = "00000000-0000-4000-8000-0000000000d1"
CI_REPRO = (
    "GitHub Actions 实跑：push 到 main 触发 .github/workflows/render-determinism.yml，"
    "在 ubuntu-24.04 runner 上执行 `python fixtures/verify_determinism.py`。"
    "本地等价（需 Linux + Docker）："
    "npm pack gsap@3.13.0 && tar -xzf gsap-3.13.0.tgz && "
    "cp package/dist/gsap.min.js vendor/gsap.min.js && "
    "cp package/dist/CustomEase.min.js vendor/CustomEase.min.js && "
    "npm install -g hyperframes@0.9.59 && "
    "python fixtures/verify_determinism.py"
)
Status = Literal["passed", "failed", "error", "skipped"]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _preflight() -> tuple[bool, list[str], dict[str, Any]]:
    """只检查、不安装、不下载。返回 (ready, reasons, environment_fingerprint)。"""
    reasons: list[str] = []
    env: dict[str, Any] = {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "is_linux": sys.platform == "linux",
        "docker_cli": shutil.which("docker"),
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "hyperframes_cli": shutil.which("hyperframes"),
        "npx": shutil.which("npx"),
        "vendor": {n: (VENDOR_DIR / n).is_file() for n in REQUIRED_VENDOR},
    }

    if sys.platform != "linux":
        reasons.append(f"当前平台 {sys.platform} 非 Linux；21.1 C4 要求严格复现走 Linux Docker")

    docker = env["docker_cli"]
    if docker is None:
        reasons.append("docker 不在 PATH")
    else:
        try:
            probe = subprocess.run(
                [docker, "info"], capture_output=True, text=True, timeout=30, shell=False
            )
            env["docker_info_ok"] = probe.returncode == 0
            if probe.returncode != 0:
                reasons.append("docker 守护进程不可用（docker info 失败）")
        except (OSError, subprocess.TimeoutExpired) as exc:
            env["docker_info_ok"] = False
            reasons.append(f"docker info 探测失败: {exc}")

    for key in ("ffmpeg", "ffprobe"):
        if env[key] is None:
            reasons.append(f"{key} 不在 PATH")

    if env["hyperframes_cli"] is None and env["npx"] is None:
        reasons.append("HyperFrames CLI 与 npx 均不在 PATH（CI 应 npm install -g hyperframes@0.9.59）")

    for name, present in env["vendor"].items():
        if not present:
            reasons.append(f"vendor/{name} 缺失（见 vendor/README.md 获取命令）")

    return (not reasons), reasons, env


def _write_report(report: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# 确定性渲染回归报告",
        "",
        f"- 状态：**{report['status']}**",
        f"- 时间（UTC）：{report['generated_at']}",
        f"- 复合结论：{report['summary']}",
        "",
        "## 环境指纹",
        "",
        "```json",
        json.dumps(report["environment"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 逐帧 SHA256（同一 composition 同一 t，两次 Docker 渲染）",
        "",
    ]
    hashes: dict[str, dict[str, str]] = report.get("frame_hashes", {})
    if hashes:
        lines += ["| frame | run 1 | run 2 | 一致 |", "|---|---|---|---|"]
        for frame, pair in hashes.items():
            ok = "YES" if pair["run1"] == pair["run2"] else "NO"
            lines.append(f"| {frame} | `{pair['run1'][:16]}…` | `{pair['run2'][:16]}…` | {ok} |")
    else:
        lines.append("（未执行渲染，无帧 hash；不得据此宣称通过）")
    lines += [
        "",
        "## runtime bundle sha256",
        "",
    ]
    bundle = report.get("runtime_bundle_sha256", {})
    if bundle:
        for name, digest in bundle.items():
            lines.append(f"- `{name}`: `{digest}`")
    else:
        lines.append("（未采集）")
    lines += [
        "",
        "## 原因 / 缺口",
        "",
    ]
    lines += [f"- {r}" for r in report.get("reasons", [])] or ["- 无"]
    lines += [
        "",
        "## 复现方式",
        "",
        report.get("reproduction", ""),
        "",
        "> 注：本报告由 `fixtures/verify_determinism.py` 真实产出；"
        "SKIP 仅表示本机无 Docker，不降低 21.3 验收门槛，也未 mock 任何像素结果。",
        "",
    ]
    MD_REPORT.write_text("\n".join(lines), encoding="utf-8")


def _new_report(status: Status, summary: str, **extra: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": status,
        "summary": summary,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "frame_hashes": {},
        "runtime_bundle_sha256": {},
        "reasons": [],
        "environment": {},
        "reproduction": CI_REPRO,
    }
    report.update(extra)
    return report


def _ffmpeg(*args: str, timeout: int = 60) -> None:
    subprocess.run(
        ["ffmpeg", "-y", *args], check=True, capture_output=True, timeout=timeout, shell=False
    )


def _frame_hash(video: Path, frame_index: int, workdir: Path) -> str:
    raw = workdir / f"frame_{frame_index:06d}.gray"
    _ffmpeg(
        "-i", str(video),
        "-vf", f"select=eq(n\\,{frame_index})",
        "-vsync", "0", "-frames:v", "1",
        "-f", "rawvideo", "-pix_fmt", "gray", str(raw),
    )
    return _sha256(raw)


def _execute() -> tuple[Status, str, dict[str, Any]]:
    """真实执行两次 Docker 渲染并逐帧对比；任何异常按 ERROR 处理。"""
    from src.backend.hyperframes_backend import HyperFramesBackend
    from src.contracts import Clip, Timeline
    from src.utils.workspace import WorkspaceManager

    with tempfile.TemporaryDirectory(prefix="p2v_determinism_") as tmp:
        work = Path(tmp)
        workspace_root = work / "workspace"
        logs_dir = work / "logs"
        workspace_root.mkdir()
        logs_dir.mkdir()

        img = work / "source.jpg"
        audio = work / "bgm.wav"
        _ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=1", "-frames:v", "1", str(img))
        _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(audio))

        ws = WorkspaceManager(workspace_root)
        tid = ws.create_task()
        shutil.copy2(img, ws.task_dir(tid) / "assets" / "img_000.jpg")
        timeline = Timeline(
            task_id=tid,
            fps=FPS,
            total_duration=DURATION_SEC,
            impact_profile="subtle",
            clips=[
                Clip(
                    index=0,
                    start=0.0,
                    end=DURATION_SEC,
                    duration=DURATION_SEC,
                    image="assets/img_000.jpg",
                    text="determinism",
                )
            ],
        )
        backend = HyperFramesBackend(
            workspace_root=workspace_root,
            logs_dir=logs_dir,
            vendor_dir=VENDOR_DIR,
            cli_command=("hyperframes",),  # CI 全局安装固定版本，渲染中禁止取包
            use_docker=True,               # 21.1 C4
        )
        generated = backend.generate_composition(
            timeline,
            width=1080,
            height=1920,
            fps=FPS,
            audio_path=str(audio),
            font_family="sans-serif",
        )
        if not generated.ok:
            return ("error", "生成 composition 失败", {"reasons": [str(generated.error)]})
        project_dir = generated.data["project_dir"]

        outputs: list[Path] = []
        for run in (1, 2):
            out = work / f"out_{run}.mp4"
            response = backend.render(
                project_dir, tid, str(audio), str(out), timeout_sec=900, docker=True
            )
            if not response.ok:
                return (
                    "error",
                    f"第 {run} 次 Docker 渲染失败",
                    {"reasons": [str(response.error)]},
                )
            outputs.append(out)

        bundle_hashes = {name: _sha256(VENDOR_DIR / name) for name in REQUIRED_VENDOR}
        frame_hashes: dict[str, dict[str, str]] = {}
        for frame in PROBE_FRAMES:
            h1 = _frame_hash(outputs[0], frame, work)
            h2 = _frame_hash(outputs[1], frame, work)
            frame_hashes[str(frame)] = {"run1": h1, "run2": h2}

        mismatches = [f for f, p in frame_hashes.items() if p["run1"] != p["run2"]]
        extra = {"frame_hashes": frame_hashes, "runtime_bundle_sha256": bundle_hashes}
        if mismatches:
            return (
                "failed",
                f"同一 t 两次渲染帧 hash 不一致：{', '.join(mismatches)}",
                {**extra, "reasons": ["按 21.3 第 5 条排查：字体/资源门控 → 隐式时间源 → 媒体预解码 → Docker 版本 → motion blur"]},
            )
        return (
            "passed",
            f"Linux Docker 同 t 两次渲染逐帧 SHA256 全部一致（{len(PROBE_FRAMES)} 帧），产物三项校验通过",
            extra,
        )


def main() -> int:
    ready, reasons, env = _preflight()
    if not ready:
        report = _new_report("skipped", "环境不满足，未执行渲染（SKIP 不是 PASS，未 mock）",
                             reasons=reasons, environment=env)
        _write_report(report)
        print("SKIP: 本机不满足 Linux Docker 确定性回归环境，未执行渲染、未伪造 hash。")
        for r in reasons:
            print(f"  - {r}")
        print(f"CI 复现命令：{CI_REPRO}")
        print(f"报告：{JSON_REPORT} / {MD_REPORT}")
        return 3

    try:
        status, summary, extra = _execute()
    except Exception as exc:  # 真实异常按 ERROR，不吞、不伪造通过
        report = _new_report("error", f"执行异常: {type(exc).__name__}: {exc}",
                             reasons=[str(exc)], environment=env)
        _write_report(report)
        print(f"ERROR: {report['summary']}")
        return 2

    report = _new_report(status, summary, environment=env, **extra)
    _write_report(report)
    print(f"{status.upper()}: {summary}")
    print(f"报告：{JSON_REPORT} / {MD_REPORT}")
    return {"passed": 0, "failed": 1, "error": 2, "skipped": 3}[status]


if __name__ == "__main__":
    sys.exit(main())
