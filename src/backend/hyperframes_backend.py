"""HyperFrames 本地开源 CLI 渲染后端（方案 2.2 / 5.5-5.7 / 11 / 第 21 章）。

铁律：
- 单条 GSAP 时间线由模板中的 MASTER.totalTime(t,false) 驱动，本模块不改时间线语义；
- 所有外部命令走 src/utils/subprocess_helper.py（list args、shell=False、超时、日志落盘）；
- 时间统一为秒，帧换算只在渲染层（本模块）进行；
- 严格复现走 Linux Docker（--docker），Windows 本地仅用于开发调试。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal

from src.backend.base import RenderBackend
from src.builder.project_generator import build_project_bundle, render_composition_html
from src.contracts import (
    ErrorCode,
    ErrorInfo,
    RenderResult,
    StrictModel,
    Timeline,
    ToolResponse,
)
from src.utils.subprocess_helper import (
    BinaryNotFoundError,
    CommandTimeoutError,
    SubprocessError,
    run_command,
)
from src.utils.workspace import WorkspaceError, WorkspaceManager

# 首次侦察（2026-11-17）观测到的 npx 最新解析版本。生产 CI 应安装该固定版本并
# 把 cli_command 覆盖为 ("hyperframes",)，避免 npx 动态取包破坏确定性。
# Windows 上 npx 的实际可执行文件是 npx.cmd；shell=False 时 Python 不会自动补后缀，
# 必须显式给出，否则 subprocess.run 抛 FileNotFoundError（BINARY_NOT_FOUND）。
_NPX = "npx.cmd" if sys.platform == "win32" else "npx"
DEFAULT_CLI: tuple[str, ...] = (_NPX, "--yes", "hyperframes@0.9.59")
REQUIRED_VENDOR: tuple[str, ...] = ("gsap.min.js", "CustomEase.min.js")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class RenderConfig(StrictModel):
    """生成工程时固化的渲染参数，避免从 HTML 反推。"""

    composition_id: str
    width: int
    height: int
    fps: int
    total_duration: float
    total_frames: int


class _QuietHandler(SimpleHTTPRequestHandler):
    """预览服务：静默访问日志，避免污染 stdout。"""

    def log_message(self, fmt: str, *args: object) -> None:
        return


def _ok(data: dict[str, object]) -> ToolResponse:
    return ToolResponse(ok=True, data=data)


def _fail(code: ErrorCode, message: str, detail: dict[str, object] | None = None) -> ToolResponse:
    return ToolResponse(
        ok=False,
        error=ErrorInfo(code=code, message=message, detail=detail or {}),
    )


class HyperFramesBackend(RenderBackend):
    """RenderBackend 的 HyperFrames 实现。"""

    def __init__(
        self,
        *,
        workspace_root: str | Path = "workspace",
        logs_dir: str | Path = "logs",
        template_path: str | Path | None = None,
        vendor_dir: str | Path = "vendor",
        cli_command: tuple[str, ...] | list[str] = DEFAULT_CLI,
        use_docker: bool = True,
        preview_host: str = "127.0.0.1",
    ) -> None:
        self.workspace = WorkspaceManager(workspace_root)
        self.logs_dir = Path(logs_dir).resolve()
        self.template_path = (
            Path(template_path).resolve()
            if template_path is not None
            else Path(__file__).resolve().parents[1] / "builder" / "template" / "composition.html.j2"
        )
        self.vendor_dir = Path(vendor_dir).resolve()
        self.cli_command = list(cli_command)
        self.use_docker = use_docker
        self.preview_host = preview_host
        self._preview_server: ThreadingHTTPServer | None = None

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_executable(args: list[str]) -> list[str]:
        """Windows 下 npx/npm 是 .CMD shim，shell=False 启动需解析为全路径。"""
        if not args:
            return args
        found = shutil.which(args[0])
        return [found, *args[1:]] if found else list(args)

    @staticmethod
    def _font_available(font_family: str) -> bool:
        """粗粒度字体存在性检查；未命中时降级为浏览器 sans-serif 并写 warning。"""
        needle = font_family.strip().lower().replace(" ", "").replace("-", "")
        if not needle:
            return False
        bases: list[Path] = []
        system_root = os.environ.get("SYSTEMROOT")
        if system_root:
            bases.append(Path(system_root) / "Fonts")
        bases.extend([Path("/usr/share/fonts"), Path.home() / "Library" / "Fonts"])
        for base in bases:
            if not base.is_dir():
                continue
            try:
                for path in base.rglob("*"):
                    if path.is_file() and needle in path.stem.lower().replace(" ", "").replace("-", ""):
                        return True
            except OSError:
                continue
        return False

    def _copy_vendor_runtime(self, project_dir: Path) -> list[str] | None:
        """把本地 vendor 中的 GSAP 运行时拷进工程；缺失时返回缺件清单。"""
        missing = [name for name in REQUIRED_VENDOR if not (self.vendor_dir / name).is_file()]
        if missing:
            return missing
        target = project_dir / "vendor"
        target.mkdir(parents=True, exist_ok=True)
        for name in REQUIRED_VENDOR:
            shutil.copy2(self.vendor_dir / name, target / name)
        return None

    # ------------------------------------------------------------------
    # RenderBackend 三方法
    # ------------------------------------------------------------------
    def generate_composition(
        self,
        timeline: Timeline,
        *,
        width: int,
        height: int,
        fps: int,
        audio_path: str,
        font_family: str,
        output_dir: str | None = None,
    ) -> ToolResponse:
        """把 Timeline 填充为渲染工程：素材归一化拷贝 + 模板渲染 + runtime 拷入。"""
        if not _SAFE_ID.fullmatch(timeline.task_id):
            return _fail(ErrorCode.FILE_NOT_FOUND, "task_id 只允许字母/数字/下划线/连字符")
        if not Path(audio_path).is_file():
            return _fail(ErrorCode.FILE_NOT_FOUND, f"音频不存在: {audio_path}")
        try:
            task_dir = self.workspace.task_dir(timeline.task_id)
        except WorkspaceError as exc:
            return _fail(ErrorCode.FILE_NOT_FOUND, str(exc))

        project_dir = Path(output_dir).resolve() if output_dir else task_dir / "composition"
        try:
            project_dir.mkdir(parents=True, exist_ok=True)
            assets_dir = project_dir / "assets"
            assets_dir.mkdir(parents=True, exist_ok=True)

            image_paths: dict[int, str] = {}
            image_sizes: dict[int, tuple[int, int]] = {}
            for clip in timeline.clips:
                try:
                    source = self.workspace.resolve_relative(timeline.task_id, clip.image)
                except WorkspaceError as exc:
                    return _fail(ErrorCode.FILE_NOT_FOUND, f"镜头 {clip.index} 素材路径非法: {exc}")
                if not source.is_file():
                    return _fail(ErrorCode.FILE_NOT_FOUND, f"镜头 {clip.index} 素材不存在: {source}")
                name = self.workspace.normalize_filename(source.name, clip.index)
                dest = assets_dir / name
                if source.resolve() != dest.resolve():
                    shutil.copy2(source, dest)
                image_paths[clip.index] = f"assets/{name}"
                # 主体聚焦需要原图尺寸做 coverRect 换算；Pillow 缺失或图片损坏时
                # 跳过尺寸，focus 自动降级为 Ken Burns（不阻断出片）。
                if clip.focus is not None:
                    try:
                        from PIL import Image  # 运行时可选依赖
                        with Image.open(source) as im:
                            image_sizes[clip.index] = (int(im.width), int(im.height))
                    except Exception:
                        pass

            missing_vendor = self._copy_vendor_runtime(project_dir)
            if missing_vendor:
                return _fail(
                    ErrorCode.BINARY_NOT_FOUND,
                    "GSAP 运行时未就绪，无法生成可渲染工程",
                    {
                        "missing": missing_vendor,
                        "instructions": "按 vendor/README.md 获取 gsap.min.js 与 CustomEase.min.js 后放入 vendor/；禁止把二进制提交进仓库",
                    },
                )

            bundle = build_project_bundle(
                timeline,
                composition_id=timeline.task_id,
                width=width,
                height=height,
                font_family=font_family,
                image_paths=image_paths,
                image_sizes=image_sizes,
            )
            html = render_composition_html(bundle.context, self.template_path)
            (project_dir / "index.html").write_text(html, encoding="utf-8")
            (project_dir / "timeline.json").write_text(
                json.dumps(timeline.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (project_dir / "asset_manifest.json").write_text(
                bundle.manifest.model_dump_json(indent=2), encoding="utf-8"
            )
            total_frames = round(timeline.total_duration * fps)
            config = RenderConfig(
                composition_id=timeline.task_id,
                width=width,
                height=height,
                fps=fps,
                total_duration=timeline.total_duration,
                total_frames=total_frames,
            )
            (project_dir / "render_config.json").write_text(
                config.model_dump_json(indent=2), encoding="utf-8"
            )

            warnings = list(timeline.warnings)
            if not self._font_available(font_family):
                warnings.append(f"字体 {font_family} 未在系统中检测到，浏览器将降级为 sans-serif")
            return _ok(
                {
                    "project_dir": str(project_dir),
                    "composition_id": timeline.task_id,
                    "assets": [e.relative_path for e in bundle.manifest.entries],
                    "total_frames": total_frames,
                    "warnings": warnings,
                }
            )
        except (OSError, ValueError) as exc:
            return _fail(ErrorCode.FILE_NOT_FOUND, f"生成工程失败: {exc}")

    def preview(self, project_dir: str, port: int = 0) -> ToolResponse:
        """启动线程内静态服务；port=0 由系统分配空闲端口。"""
        path = Path(project_dir).resolve()
        if not (path / "index.html").is_file():
            return _fail(ErrorCode.FILE_NOT_FOUND, f"工程缺少 index.html: {path}")
        if self._preview_server is not None:
            server = self._preview_server
            return _ok(
                {
                    "url": f"http://{self.preview_host}:{server.server_port}/index.html",
                    "port": server.server_port,
                    "project_dir": str(path),
                    "already_running": True,
                }
            )
        try:
            handler = partial(_QuietHandler, directory=str(path))
            server = ThreadingHTTPServer((self.preview_host, port), handler)
        except OSError as exc:
            return _fail(ErrorCode.BROWSER_LAUNCH_FAILED, f"预览服务启动失败: {exc}")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._preview_server = server
        return _ok(
            {
                "url": f"http://{self.preview_host}:{server.server_port}/index.html",
                "port": server.server_port,
                "project_dir": str(path),
                "already_running": False,
            }
        )

    def stop_preview(self) -> None:
        """关闭预览服务（供调用方清理）。"""
        if self._preview_server is not None:
            self._preview_server.shutdown()
            self._preview_server.server_close()
            self._preview_server = None

    # 白名单：CLI v0.9.59 render --help 实证支持的分辨率预设（--resolution）。
    # 默认 None = 不改分辨率（composition 自身 1080×1920，业界短视频原生交付规格）。
    _RESOLUTION_PRESETS = frozenset({
        "landscape", "portrait", "landscape-4k", "portrait-4k", "square", "square-4k",
        "landscape-1440p", "portrait-1440p", "square-1440p",
        "1080p", "4k", "uhd", "1440p", "qhd",
        "1080p-square", "square-1080p", "4k-square", "square-4k",
        "4k-portrait", "portrait-4k", "1440p-square", "square-1440p",
    })
    _GPU_MODES = frozenset({"auto", "on", "off"})

    def _build_render_command(
        self,
        *,
        silent: Path,
        use_docker: bool,
        gpu_mode: str,
        resolution: str | None,
    ) -> list[str] | ToolResponse:
        """纯函数式构造 CLI args（无副作用，便于单元测试）。

        只拼接 CLI v0.9.59 --help 实证支持的 flag，非法参数返回失败响应，
        绝不把用户输入拼成臆造 flag。
        """
        if gpu_mode not in self._GPU_MODES:
            return _fail(ErrorCode.BROWSER_LAUNCH_FAILED,
                         f"gpu_mode 参数非法: {gpu_mode!r}，仅允许 auto/on/off")
        if resolution is not None and resolution not in self._RESOLUTION_PRESETS:
            return _fail(ErrorCode.BROWSER_LAUNCH_FAILED,
                         f"resolution 参数非法: {resolution!r}，仅允许 CLI v0.9.59 支持的预设")
        if use_docker and gpu_mode == "on":
            return _fail(ErrorCode.BROWSER_LAUNCH_FAILED,
                         "gpu_mode=on 与 docker 渲染冲突（容器内无主机 GPU）；"
                         "改用 gpu_mode=auto 或本地渲染")

        args = [*self.cli_command, "render"]
        if use_docker:
            args.append("--docker")
        # auto 不传 flag，交给 CLI 首启探测（有 GPU 用 GPU，无 GPU 回退 SwiftShader）。
        if gpu_mode == "on":
            args.append("--browser-gpu")
        elif gpu_mode == "off":
            args.append("--no-browser-gpu")
        # 默认 None 不改分辨率（1080×1920 交付规格）；按需显式升 4K/1440p。
        if resolution is not None:
            args.extend(["--resolution", resolution])
        args.extend(["--output", str(silent)])
        return args

    def render(
        self,
        project_dir: str,
        composition_id: str,
        audio_path: str,
        output_mp4: str,
        *,
        concurrency: int = 4,
        timeout_sec: int = 600,
        docker: bool | None = None,
        gpu_mode: Literal["auto", "on", "off"] = "auto",
        resolution: str | None = None,
    ) -> ToolResponse:
        """CLI 确定性渲染 → ffmpeg 精确混音 → 三项产物校验。

        concurrency 仅记录：HyperFrames CLI 自行管理 worker 数，未公开 --concurrency
        参数，禁止臆造 flag（worker 数按 21.1 C3 不得改变像素）。

        gpu_mode（CLI v0.9.59 实证支持，见 render --help）：
          - auto（默认）：CLI 首启探测，有 GPU 用 GPU、无 GPU 自动回退 SwiftShader；
          - on：追加 --browser-gpu 强制硬件；off：追加 --no-browser-gpu 强制软件。
        resolution（可选）：CLI --resolution 白名单预设，如 portrait-4k；默认 None
          = 保持 composition 的 1080×1920（业界短视频平台原生交付规格，默认不开 4K）。
        """
        path = Path(project_dir).resolve()
        config_path = path / "render_config.json"
        if not config_path.is_file():
            return _fail(ErrorCode.FILE_NOT_FOUND, f"工程缺少 render_config.json: {path}")
        try:
            config = RenderConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            return _fail(ErrorCode.FILE_NOT_FOUND, f"render_config 解析失败: {exc}")
        if composition_id != config.composition_id:
            return _fail(
                ErrorCode.FILE_NOT_FOUND,
                "composition_id 与工程配置不一致",
                {"expected": config.composition_id, "got": composition_id},
            )
        if not (path / "index.html").is_file():
            return _fail(ErrorCode.FILE_NOT_FOUND, f"工程缺少 index.html: {path}")
        audio = Path(audio_path).resolve()
        if not audio.is_file():
            return _fail(ErrorCode.FILE_NOT_FOUND, f"音频不存在: {audio_path}")

        output = Path(output_mp4).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        expected_duration = config.total_frames / config.fps
        silent = path / "render_silent.mp4"

        use_docker = self.use_docker if docker is None else docker
        cmd = self._build_render_command(
            silent=silent, use_docker=use_docker, gpu_mode=gpu_mode, resolution=resolution
        )
        if isinstance(cmd, ToolResponse):
            return cmd  # 参数校验失败
        args = cmd

        try:
            cli_result = run_command(
                self._resolve_executable(args),
                cwd=path,
                timeout=timeout_sec,
                log_dir=self.logs_dir,
                task_id=f"{config.composition_id}_hyperframes",
            )
        except CommandTimeoutError as exc:
            return _fail(ErrorCode.RENDER_TIMEOUT, exc.message, {"log_dir": str(self.logs_dir)})
        except BinaryNotFoundError as exc:
            return _fail(ErrorCode.BINARY_NOT_FOUND, exc.message)
        except SubprocessError as exc:
            return _fail(ErrorCode.BROWSER_LAUNCH_FAILED, exc.message)

        if not cli_result.ok:
            return _fail(
                ErrorCode.BROWSER_LAUNCH_FAILED,
                "HyperFrames CLI 渲染失败",
                {"log_path": cli_result.log_path, "stderr": cli_result.stderr[-2000:]},
            )
        if not silent.is_file() or silent.stat().st_size == 0:
            return _fail(
                ErrorCode.BROWSER_LAUNCH_FAILED,
                "HyperFrames CLI 未产出有效视频",
                {"log_path": cli_result.log_path},
            )

        # 18.3：禁止 -shortest，按总帧数以 -t N/fps 精确截断。
        mux = [
            "ffmpeg", "-y",
            "-i", str(silent),
            "-i", str(audio),
            "-t", f"{expected_duration:.6f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-movflags", "+faststart",
            str(output),
        ]
        try:
            mux_result = run_command(
                self._resolve_executable(mux),
                cwd=path,
                timeout=timeout_sec,
                log_dir=self.logs_dir,
                task_id=f"{config.composition_id}_mux",
            )
        except CommandTimeoutError as exc:
            return _fail(ErrorCode.RENDER_TIMEOUT, exc.message, {"stage": "ffmpeg_mux"})
        except SubprocessError as exc:
            return _fail(ErrorCode.FFMPEG_MUX_FAILED, exc.message)

        if not mux_result.ok:
            return _fail(
                ErrorCode.FFMPEG_MUX_FAILED,
                "ffmpeg 混音失败",
                {"log_path": mux_result.log_path, "stderr": mux_result.stderr[-2000:]},
            )

        probe = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(output),
        ]
        try:
            probe_result = run_command(
                self._resolve_executable(probe),
                cwd=path,
                timeout=60,
                log_dir=self.logs_dir,
                task_id=f"{config.composition_id}_probe",
            )
        except SubprocessError as exc:
            return _fail(ErrorCode.FFMPEG_MUX_FAILED, f"ffprobe 读取时长失败: {exc.message}")

        try:
            measured = float(probe_result.stdout.strip().splitlines()[0])
        except (IndexError, ValueError) as exc:
            return _fail(ErrorCode.FFMPEG_MUX_FAILED, f"ffprobe 时长解析失败: {exc}")

        tolerance = 1 / config.fps + 1e-6
        if abs(measured - expected_duration) > tolerance:
            return _fail(
                ErrorCode.FFMPEG_MUX_FAILED,
                "成片时长误差超过 1 帧",
                {
                    "measured": measured,
                    "expected": expected_duration,
                    "tolerance": tolerance,
                },
            )

        # 首帧非纯黑：抽出灰度 rawvideo 后检查最大亮度，不依赖 PIL。
        gray_path = path / "first_frame.gray"
        extract = [
            "ffmpeg", "-y", "-i", str(output),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray",
            str(gray_path),
        ]
        try:
            frame_result = run_command(
                self._resolve_executable(extract),
                cwd=path,
                timeout=60,
                log_dir=self.logs_dir,
                task_id=f"{config.composition_id}_firstframe",
            )
        except SubprocessError as exc:
            return _fail(ErrorCode.FFMPEG_MUX_FAILED, f"首帧抽取失败: {exc.message}")
        if not frame_result.ok or not gray_path.is_file():
            return _fail(ErrorCode.FFMPEG_MUX_FAILED, "首帧抽取未产出文件")
        pixels = gray_path.read_bytes()
        if not pixels or max(pixels) <= 8:
            return _fail(ErrorCode.FFMPEG_MUX_FAILED, "首帧为纯黑，渲染不通过")

        render_time = cli_result.duration_sec + mux_result.duration_sec
        result = RenderResult(
            output_path=str(output),
            duration=measured,
            resolution=f"{config.width}x{config.height}",
            fps=config.fps,
            render_time_sec=render_time,
            log_path=cli_result.log_path,
            avg_frame_render_ms=1000 * render_time / max(1, config.total_frames),
        )
        return _ok(
            {
                **result.model_dump(),
                "concurrency": concurrency,
                "concurrency_note": "CLI 自行管理 worker；该参数仅作记录，不传给 CLI",
                "docker": use_docker,
                "stages": {
                    "cli": cli_result.log_path,
                    "mux": mux_result.log_path,
                    "probe": probe_result.log_path,
                },
            }
        )
