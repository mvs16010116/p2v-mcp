# 确定性渲染回归报告

- 状态：**skipped**
- 时间（UTC）：2026-11-17T12:41:32.588363+00:00
- 复合结论：环境不满足，未执行渲染（SKIP 不是 PASS，未 mock）

## 环境指纹

```json
{
  "platform": "Windows-11-10.0.26100-SP0",
  "python": "3.13.12",
  "is_linux": false,
  "docker_cli": null,
  "ffmpeg": "C:\\Users\\user\\ffmpeg\\ffmpeg.EXE",
  "ffprobe": "C:\\Users\\user\\ffmpeg\\ffprobe.EXE",
  "hyperframes_cli": null,
  "npx": "C:\\Program Files\\nodejs\\npx.CMD",
  "vendor": {
    "gsap.min.js": true,
    "CustomEase.min.js": true
  }
}
```

## 逐帧 SHA256（同一 composition 同一 t，两次 Docker 渲染）

（未执行渲染，无帧 hash；不得据此宣称通过）

## runtime bundle sha256

（未采集）

## 原因 / 缺口

- 当前平台 win32 非 Linux；21.1 C4 要求严格复现走 Linux Docker
- docker 不在 PATH

## 复现方式

GitHub Actions 实跑：push 到 main 触发 .github/workflows/render-determinism.yml，在 ubuntu-24.04 runner 上执行 `python fixtures/verify_determinism.py`。本地等价（需 Linux + Docker）：npm pack gsap@3.13.0 && tar -xzf gsap-3.13.0.tgz && cp package/dist/gsap.min.js vendor/gsap.min.js && cp package/dist/CustomEase.min.js vendor/CustomEase.min.js && npm install -g hyperframes@0.9.59 && python fixtures/verify_determinism.py

> 注：本报告由 `fixtures/verify_determinism.py` 真实产出；SKIP 仅表示本机无 Docker，不降低 21.3 验收门槛，也未 mock 任何像素结果。
