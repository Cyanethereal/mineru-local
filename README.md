# mineru-local

本地 `mineru` 命令的 wrapper：调用本机或 WSL Linux 中已安装的 `mineru` CLI，把 PDF / 图片 / DOCX / PPTX / XLSX 解析成 Markdown。**不上传文件、不用 MINERU_TOKEN、不接远端 API**。

## 它做什么

- 调用本机或 WSL Linux conda env 中已安装的 `mineru` CLI，把 PDF / 图片 / DOCX / PPTX / XLSX 解析成 Markdown
- 跨 Windows / WSL Linux 选择环境与 backend（pipeline / vlm-engine / hybrid-engine）；WSL distro 与可执行文件由 CLI、环境变量或持久化配置显式提供
- 守护本地不变式：拒绝 `mineru.net` 云端 API、拒绝 `MINERU_TOKEN`、拒绝指向远端的 `--api-url`

## 输入格式预检

- 可直接解析：.pdf、图片、.docx、.pptx、.xls、.xlsx。
- 旧版 Office：.doc、.ppt 必须先进行显式格式转换；wrapper 返回 legacy_conversion_required，不会调用 MinerU。
- 不支持 SWF 或其他非文档格式；不会尝试自动抽取动画。
- parse-dir 会报告需要转换的 legacy 文件，而不是执意调用 MinerU。

## 系统要求（必读）

**基本解析（pipeline）**：
- Python 3.10+
- mineru 3.4+：`pip install "mineru[all]"`
- 无 GPU 也能跑（CPU 慢）

**高准确度（hybrid-engine）**：
- 以上全部
- **vLLM**（具体版本看 [MinerU 官方扩展模块安装](https://github.com/opendatalab/MinerU/blob/master/docs/zh/quick_start/extension_modules.md)）
- **GPU 加速**（vLLM 主要支持 NVIDIA；具体兼容看 [MinerU 官方 FAQ](https://github.com/opendatalab/MinerU)）

> **Windows 用户**：推荐 WSL Linux（GPU 透传，默认 `Ubuntu-24.04`）。
> **Linux/macOS 用户**：当前 wrapper 只支持 Windows 主机与 Windows/WSL MinerU，不支持 macOS 或本机 Linux 直接执行。

> **没有 vLLM？** 你的解析质量会明显差于"原生 hybrid-engine"。skill 仍可用，报告里 `fallback.used=true` 会说明。

## 安装

`./install.sh` 或 `powershell -ExecutionPolicy Bypass -File install.ps1`

会输出 JSON 报告，含 validate / unit_tests / mineru_doctor 三个 step 的状态。

`install` 只做前置校验（找 Python → 跑 `quick_validate.py` → 跑 unit tests → 跑 `mineru_doctor`），**不会复制文件到 `$CODEX_HOME/skills/`**，因此重跑始终幂等，不会破坏已有 skill 目录。

**如何更新**：Codex skill 的“安装”本质上是把整个 `mineru-local` 目录放到 `$CODEX_HOME/skills/mineru-local/`，因此更新方式取决于当初的安装来源：
- 若通过 skill-installer 从 git 克隆：用 skill-installer 重新安装，或直接在 skill 目录里 `git pull`
- 若手动 copy：重新拷贝新版 skill 目录覆盖即可
- 若手动 git checkout：在 skill 目录里 `git pull`，然后再跑一次 `install.ps1` 重新校验依赖

## 关键环境变量

| 变量 | 作用 | 默认 |
|------|------|------|
| `PYTHONUTF8` | 强制 Python 用 UTF-8 | `install.ps1` 自动设 `1` |
| `PYTHONIOENCODING` | Python stdout/stderr 编码 | 自动设 `utf-8` |
| `PYTHON` | install 脚本优先用此 Python | PATH 首个 `python` |
| `CODEX_HOME` | Codex skills 根目录 | `$env:USERPROFILE/.codex` |
| `MINERU_MODEL_SOURCE` | 透传给 mineru 子进程 | 用户原值 |
| `CUDA_VISIBLE_DEVICES` | 透传给 mineru 子进程 | 用户原值 |

**不会**透传给 mineru 子进程：`MINERU_TOKEN`、`MINERU_API_URL`、`MINERU_API_TOKEN`

## 覆盖路径

`MINERU_WINDOWS_EXECUTABLE` 、`MINERU_WSL_EXECUTABLE` 、`MINERU_WSL_DISTRO` 环境变量可覆盖默认路径，详见 README 原文。

## 错误诊断

`install` 与 `mineru_doctor` 报告会带 `diagnostics` 数组，每条都引用 MinerU 官方文档错误区（如 `libGL.so.1`、`no kernel image`、CJK 字体等）。

**v1.2.0.1 新增**：报告中多出 `fallback` 字段。当 `fallback.used=true` 时，`smoke_attempts` 会详细记录哪个环境的 smoke test 是否通过、为什么。

## 常见问题

**Q: 我请求 hybrid-engine 但报告里 fallback.used=true？**
A: skill 的 auto 模式先跑 smoke test；smoke 失败的 env 会被跳过。看 `fallback.smoke_attempts`：
  - 若 WSL smoke 失败 → 看 `smoke_attempts[0].error` 修 WSL
  - 若 Windows smoke 失败 → 看 `smoke_attempts[1].error` 修 Windows mineru
  - 两个都失败 → exit 6，按 stderr 排查

**Q: 我不想要 auto？**
A: 用 `--env windows` 或 `--env wsl`。指定了 WSL 但 WSL 不可用会**硬错误**（exit 3），不会默默跳 Windows。

**Q: macOS 呢？**
A: 不支持。MinerU 在 macOS 上 Docker 不能用 MPS/MLX，本 skill 的 WSL 路径也不适用。

## 延展阅读

- [references/local_cli.md](references/local_cli.md) — CLI 参数速查
- [references/troubleshooting.md](references/troubleshooting.md) — 深掘错（CUDA、模型、WSL GPU）
