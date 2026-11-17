# vendor 运行时说明

本目录只放**获取说明**，不提交任何第三方二进制（JS 压缩产物）进仓库。
渲染工程生成时，`src/backend/hyperframes_backend.py` 会把本目录中的
`gsap.min.js` 与 `CustomEase.min.js` 拷贝到每个 task 的 `composition/vendor/`，
模板通过 `./vendor/gsap.min.js` 等**相对路径**引用，符合 CODING_STANDARDS 第 4 节。

## 必需文件

| 文件 | 来源 | 许可 | 用途 |
|---|---|---|---|
| `gsap.min.js` | npm 包 `gsap`（≥ 3.13）的 `dist/gsap.min.js` | GSAP Standard "No Charge" License，允许商用 | 主时间线 `gsap.timeline({paused:true})` |
| `CustomEase.min.js` | 同一 npm 包的 `dist/CustomEase.min.js` | 同上（3.13 起全部原 Club/bonus 插件免费可商用） | 方案 18.1 自行构造 spring 缓动 |

**许可依据（recon 实证，2026-11-17）**：
- https://gsap.com/blog/3-13/ —— "GSAP IS NOW 100% FREE ... including ALL of the bonus plugins ... even for commercial use"
- https://gsap.com/standard-license —— "Commercial usage is covered under the standard license"
- 红线：不得构建与 Webflow 视觉动画构建能力竞争的无代码可视化动画工具；本项目是 Agent 代码驱动工具，不落入该禁区。

`spring.js` 已内联在 `src/builder/template/composition.html.j2` 中（方案 18.1 的阻尼谐振子采样实现），无需单独获取。

## 获取命令（PowerShell）

```powershell
# 固定版本，避免动态升级破坏确定性
npm pack gsap@3.13.0
tar -xzf gsap-3.13.0.tgz
Copy-Item .\package\dist\gsap.min.js .\vendor\gsap.min.js
Copy-Item .\package\dist\CustomEase.min.js .\vendor\CustomEase.min.js
Remove-Item -Recurse -Force .\package, .\gsap-3.13.0.tgz

# 校验（应无报错）
node -e "require('fs').accessSync('vendor/gsap.min.js'); require('fs').accessSync('vendor/CustomEase.min.js'); console.log('vendor ok')"
```

## 获取命令（bash / CI）

```bash
npm pack gsap@3.13.0
tar -xzf gsap-3.13.0.tgz
cp package/dist/gsap.min.js vendor/gsap.min.js
cp package/dist/CustomEase.min.js vendor/CustomEase.min.js
rm -rf package gsap-3.13.0.tgz
```

建议 CI 在拉取文件后校验 sha256 并把值固化到流水线配置中（第 21.3 节要求
preview 与 render 使用同一 runtime bundle）。

## HyperFrames CLI（Node.js 22+）

```powershell
# 本地开发
npx --yes hyperframes@0.9.59 init my-video
npx --yes hyperframes@0.9.59 preview
npx --yes hyperframes@0.9.59 render --output video.mp4

# 像素级确定性回归（Linux Docker，固定 Chromium / 字体 / FFmpeg）
npx --yes hyperframes@0.9.59 render --docker --output output.mp4
```

生产 CI 建议全局安装固定版本并把后端 `cli_command` 覆盖为 `("hyperframes",)`，
避免 `npx` 在渲染时联网取包——渲染中禁止 fetch（第 21.1 节 C7）。

官方资料：
- 仓库 https://github.com/heygen-com/hyperframes
- LICENSE https://raw.githubusercontent.com/heygen-com/hyperframes/main/LICENSE （Apache 2.0）
- FAQ https://hyperframes.heygen.com/faq
- CLI 用法 https://hyperframes.heygen.com/quickstart
