# 编码规范（所有 agent 必须遵守）

> 目的：让 agent 落在"成功陷阱"（pit of success）——做对的事容易，做错的事困难。
> 原则：**禁止 workaround**。任何"先这样绕过，以后再改"的代码都不接受。

## 1. 数据结构优先

- 所有跨层传递的数据必须是 **Pydantic v2 model**，禁止裸 `dict` 跨函数边界。
- 时间统一用「秒」（float），帧换算只在渲染层做。禁止在编排层出现帧号。
- 所有工具返回统一结构：`{ok: bool, data: dict|null, error: {code, message, detail}|null}`。
- 错误码只允许使用方案文档第 6.5 节枚举值 + 本文件新增项；新增错误码必须先写进文档。

## 2. 强制不变量（由代码断言，不只靠文档）

- `validate_assets`：图片/音频不存在 → 必须在渲染前报错，不允许渲染层才发现。
- `build_timeline`：每镜时长 `T ≥ 2 × T_in`，违反则合并 beat 并写 warnings，不允许生成违法时间线。
- `render_video`：产物存在、时长误差 ≤ 1 帧、首帧非纯黑，三项全过才算成功。
- `MASTER.time(t, false)` 是唯一驱动动画的方式；代码中出现 `requestAnimationFrame` 即为 bug。

## 3. 子进程安全

- `subprocess.run(args, shell=False, cwd=..., timeout=..., env=safe_env())`，args 必须是 list。
- 所有外部命令必须有超时；超时返回 `RENDER_TIMEOUT` 并保留日志到 `logs/`。
- stdout/stderr 必须落盘，不允许只留在内存。

## 4. 沙箱与路径

- 每次调用生成 `task_id = uuid4()`，所有产物写在 `workspace/{task_id}/` 内。
- 模板内只写**相对路径**；图片必须拷贝进工程目录。禁止 `file://`、绝对路径、远程 URL 直接引用。
- 文件名归一化：去空格/中文/特殊字符 → `img_001.jpg`。

## 5. 测试要求

- 每个 Pydantic model 必须有对应的校验测试（合法 + 非法各一例）。
- 确定性渲染必须有回归测试：同一 t 渲染两次，截图 hash 一致。
- 禁止在测试里用 mock 掩盖真实依赖缺失；依赖缺失时测试应明确 skip 并说明。

## 6. 文档同步

- 代码实现与方案文档冲突时，**先更新方案文档，再改代码**，并在 WAR_MAP.md 交接记录写明。
- 禁止"代码和文档不一致但先这样"的情况。

## 7. 提交纪律

- 不自动 git commit，除非用户明确要求。
- 每个 agent 完成工作后在 WAR_MAP.md 交接记录追加一行，写明结论与产物路径。
