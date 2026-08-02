# mineru-local

本地 MinerU 文档解析。**不上传文件、不用 MINERU_TOKEN、不接远端 API**。

## 它做什么

- 把 PDF / 图片 / DOCX / PPTX / XLSX 解析成 Markdown
- 跨 Windows / WSL-Ubuntu 自动选环境
- 拒绝 mineru.net 云端 API、拒绝 MINERU_TOKEN

## 系统要求（必读）

**基本解析（pipeline）**：
- Python 3.10+
- mineru 3.4+：`pip install "mineru[all]"`
- 无 GPU 也能跑（CPU 慢）

**高准确度（hybrid-engine）**：
- 以上全部
- **vLLM**（具体版本看 [MinerU 官方扩展模块安装](https://github.com/opendatalab/MinerU/blob/master/docs/zh/quick_start/extension_modules.md)）
- **GPU 加速**（vLLM 主要支持 NVIDIA；具体兼容看 [MinerU 官方 FAQ](https://github.com/opendatalab/MinerU)）

> **Windows 用户**：推荐 WSL Ubuntu（GPU 透传）。  
> **Linux/macOS 用户**：直接在系统装 mineru 即可。

> **没有 vLLM？** 你的解析质量会明显差于"原生 hybrid-engine"。skill 仍可用，报告里 `fallback.used=true` 会说明。

## 安装

`./install.sh` 或 `powershell -ExecutionPolicy Bypass -File install.ps1`

会输出 JSON 报告，含 validate / unit_tests / mineru_doctor 三个 step 的状态。

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
