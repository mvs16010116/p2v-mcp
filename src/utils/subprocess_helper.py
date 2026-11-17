"""安全子进程封装（CODING_STANDARDS 第 3 节 / 方案第 11 节）。

- subprocess.run(args, shell=False, cwd=..., timeout=..., env=safe_env())，args 必须是 list。
- 所有外部命令必须有超时；超时抛 CommandTimeoutError（RENDER_TIMEOUT）并保留日志到 logs/。
- stdout/stderr 必须落盘，不允许只留在内存。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from src.contracts import CommandResult, ErrorCode

DEFAULT_ALLOWED_ENV: tuple[str, ...] = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "SYSTEMROOT",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
)


class SubprocessError(RuntimeError):
    """子进程执行失败异常，携带统一错误码。"""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CommandTimeoutError(SubprocessError):
    """超时异常，对应 RENDER_TIMEOUT。"""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.RENDER_TIMEOUT, message)


class BinaryNotFoundError(SubprocessError):
    """命令不存在异常，对应 BINARY_NOT_FOUND。"""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.BINARY_NOT_FOUND, message)


def safe_env(allowed: Iterable[str] = DEFAULT_ALLOWED_ENV) -> dict[str, str]:
    """白名单环境变量，避免把完整宿主机环境传给子进程。"""
    return {key: os.environ[key] for key in allowed if key in os.environ}


def _decode(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _write_log(log_path: Path, args: list[str], stdout: str, stderr: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    body = (
        f"time: {stamp}\n"
        f"cmd: {json.dumps(args, ensure_ascii=False)}\n"
        f"--- stdout ---\n{stdout}\n"
        f"--- stderr ---\n{stderr}\n"
    )
    log_path.write_text(body, encoding="utf-8")


def run_command(
    args: list[str],
    *,
    cwd: str | Path,
    timeout: float,
    log_dir: str | Path,
    task_id: str,
    allowed_env: Iterable[str] = DEFAULT_ALLOWED_ENV,
) -> CommandResult:
    """执行外部命令，返回结构化结果；stdout/stderr 落盘，超时/不存在抛异常。"""
    if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
        raise ValueError("args 必须是非空字符串 list，禁止 shell=True 与字符串拼接")
    if timeout <= 0:
        raise ValueError("timeout 必须为正数")

    log_path = Path(log_dir).resolve() / f"cmd_{task_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    try:
        proc = subprocess.run(
            args,
            shell=False,
            cwd=str(cwd),
            capture_output=True,
            # 不用 text=True：Windows 子进程输出编码各异（GBK/UTF-8 混杂），
            # 交给 _decode 统一按 UTF-8 容错解码，避免 _readerthread 抛
            # UnicodeDecodeError 导致 stdout/stderr 丢失。
            timeout=timeout,
            env=safe_env(allowed_env),
        )
    except subprocess.TimeoutExpired as exc:
        _write_log(log_path, args, _decode(exc.stdout), _decode(exc.stderr))
        raise CommandTimeoutError(f"命令超时（{timeout}s）: {args[0]}") from exc
    except FileNotFoundError as exc:
        _write_log(log_path, args, "", str(exc))
        raise BinaryNotFoundError(f"命令不存在: {args[0]}") from exc

    duration = time.monotonic() - started
    stdout = _decode(proc.stdout)
    stderr = _decode(proc.stderr)
    _write_log(log_path, args, stdout, stderr)
    return CommandResult(
        ok=proc.returncode == 0,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        log_path=str(log_path),
        duration_sec=duration,
    )
