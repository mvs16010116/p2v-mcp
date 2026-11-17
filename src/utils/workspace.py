"""task_id 沙箱目录管理（CODING_STANDARDS 第 4 节 / 方案第 4、10.2 节）。

规则：
- 每次调用生成 task_id = uuid4()，所有产物写在 workspace/{task_id}/ 内。
- 模板内只写相对路径；图片必须拷贝进工程目录。禁止 file://、绝对路径、远程 URL。
- 文件名归一化：去空格/中文/特殊字符 → img_001.jpg。
- 不得跨任务读写；所有路径经 resolve() 校验仍在 task 目录内。
"""
from __future__ import annotations

import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SAFE_EXT = re.compile(r"^\.[A-Za-z0-9]{1,5}$")


class WorkspaceError(ValueError):
    """沙箱路径非法或越界。"""


class WorkspaceManager:
    """管理 workspace 根目录下以 task_id 命名的隔离子目录。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create_task(self) -> str:
        """新建任务沙箱，返回 task_id（uuid4）。"""
        task_id = str(uuid.uuid4())
        task_dir = self.root / task_id
        for sub in ("assets", "composition"):
            (task_dir / sub).mkdir(parents=True, exist_ok=True)
        return task_id

    def task_dir(self, task_id: str) -> Path:
        """校验并返回任务目录；目录不存在时报错。"""
        task_dir = self._checked(task_id)
        if not task_dir.is_dir():
            raise WorkspaceError(f"任务目录不存在: {task_id}")
        return task_dir

    def _checked(self, task_id: str) -> Path:
        try:
            uuid.UUID(task_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise WorkspaceError("task_id 必须为 uuid4 字符串") from exc
        task_dir = (self.root / task_id).resolve()
        if not task_dir.is_relative_to(self.root):
            raise WorkspaceError("任务路径越出 workspace 根目录")
        return task_dir

    def normalize_filename(self, filename: str, index: int) -> str:
        """归一化为 img_001.jpg 形式：去空格/中文/特殊字符，仅保留安全扩展名。"""
        if index < 0:
            raise WorkspaceError("index 必须非负")
        ext = Path(filename).suffix.lower()
        if not _SAFE_EXT.match(ext):
            ext = ".bin"
        return f"img_{index:03d}{ext}"

    def copy_asset(self, task_id: str, src: str | Path, index: int) -> str:
        """把素材拷贝进任务 assets/，返回模板可用的相对路径。

        同名冲突时加 task_id 前缀，防止跨任务覆盖（方案 10.2-4）。
        """
        source = Path(src).resolve()
        if not source.is_file():
            raise WorkspaceError(f"素材不存在: {src}")
        assets_dir = self.task_dir(task_id) / "assets"
        name = self.normalize_filename(source.name, index)
        dest = assets_dir / name
        if dest.exists():
            dest = assets_dir / f"{task_id}_{name}"
        shutil.copy2(source, dest)
        return f"assets/{dest.name}"

    def resolve_relative(self, task_id: str, rel: str) -> Path:
        """校验模板内的相对路径：拒绝绝对路径 / URL，且不得越出 task 沙箱。"""
        if not rel or Path(rel).is_absolute() or "://" in rel:
            raise WorkspaceError("模板内只接受相对路径，禁止绝对路径 / file:// / 远程 URL")
        base = self.task_dir(task_id)
        resolved = (base / rel).resolve()
        if not resolved.is_relative_to(base):
            raise WorkspaceError("相对路径越出 task 沙箱")
        return resolved

    def cleanup(self, task_id: str) -> None:
        """删除单个任务沙箱。"""
        shutil.rmtree(self.task_dir(task_id))

    def cleanup_expired(self, retain_hours: float = 24.0) -> list[str]:
        """清理超过保留时长的任务目录，返回被删除的 task_id 列表。"""
        if retain_hours < 0:
            raise WorkspaceError("retain_hours 必须非负")
        cutoff = datetime.now(timezone.utc).timestamp() - retain_hours * 3600
        removed: list[str] = []
        for entry in self.root.iterdir():
            if not entry.is_dir():
                continue
            try:
                uuid.UUID(entry.name)
            except ValueError:
                continue
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry)
                removed.append(entry.name)
        return removed
