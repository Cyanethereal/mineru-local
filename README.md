# mineru-local

本地 MinerU 文档解析 skill，跨 Windows 与 WSL-Ubuntu 自动选环境。

> **本目录是给 operator 看的安装/运维文档。**
> 真正的 skill 入口与触发词在 `SKILL.md`，Codex 加载 skill 时只看那份。

## 这是什么

- 真正本地地调用 `mineru` 解析 PDF/图片/DOCX/PPTX/XLSX
- 自动选环境：默认走 WSL（带 vLLM 的 conda `mineru`），回退到 Windows（仅 pipeline）
- 不向 `mineru.net` 上传文件，不使用 `MINERU_TOKEN`，不接受指向远端的 `--api-url`

## 系统要求

| 必需 | 可选 | 说明 |
|------|------|------|
| Python 3.10+ | | 跑 install/validate/doctor |
| Codex 已装好 | | `~/.codex` 下有 `skills/.system/skill-creator/scripts/quick_validate.py` |
| 一个 conda `mineru` 环境 | | Windows 或 WSL 中任一即可 |
| | WSL Ubuntu + vLLM 0.20+ | 启用 `hybrid-engine` 高准确率解析 |
| | NVIDIA GPU (Volta+) | 否则只能用 `pipeline` 后端 |

## 安装

### 一键安装

```bash
# POSIX
./install.sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File install.ps1
```

期望输出 JSON 报告，含以下 step 状态：

| step | 含义 |
|------|------|
| `validate_skill` | 调 Codex 系统 `quick_validate.py` 验证 `SKILL.md` 合法 |
| `unit_tests`      | 跑 `scripts/tests/` 下所有 unittest |
| `mineru_doctor`   | 调 `mineru_local.py doctor` 检查 Windows/WSL conda 环境 |

整体退出码 0 表示核心步骤全部通过；`mineru_doctor` 失败**不阻塞**安装。

### 仅校验

```bash
powershell -ExecutionPolicy Bypass -File validate.ps1
```

只跑 `quick_validate.py`，不跑测试与 doctor。

## 覆盖路径

mineru 的 Windows / WSL 可执行路径默认是占位（`C:\Users\YOUR_USER\.conda\envs\mineru\Scripts\mineru.exe` 与 `/home/YOUR_USER/miniconda3/envs/mineru/bin/mineru`）。
通过环境变量覆盖即可，无需改代码：

```powershell
$env:MINERU_WINDOWS_EXECUTABLE = "C:\Users\me\.conda\envs\mineru\Scripts\mineru.exe"
$env:MINERU_WSL_EXECUTABLE    = "/home/me/miniconda3/envs/mineru/bin/mineru"
$env:MINERU_WSL_DISTRO        = "Ubuntu-24.04"   # 默认就是这个
```

```bash
export MINERU_WINDOWS_EXECUTABLE=/c/Users/me/.conda/envs/mineru/Scripts/mineru.exe
export MINERU_WSL_EXECUTABLE=/home/me/miniconda3/envs/mineru/bin/mineru
export MINERU_WSL_DISTRO=Ubuntu-24.04
```

doctor 报告会显示当前使用的实际路径：

```text
environments.windows.executable  = <MINERU_WINDOWS_EXECUTABLE 覆盖后的值>
environments.wsl.executable       = <MINERU_WSL_EXECUTABLE 覆盖后的值>
environments.wsl.distro           = <MINERU_WSL_DISTRO 覆盖后的值>
```

## 关键环境变量

| 变量 | 作用 | 默认 |
|------|------|------|
| `PYTHONUTF8` | 强制 Python 用 UTF-8，避免 Windows GBK 读取 `SKILL.md` 失败 | `install.ps1` 自动设 `1` |
| `PYTHONIOENCODING` | Python stdout/stderr 编码 | 自动设 `utf-8` |
| `PYTHON` | install 脚本会优先用此 Python 解释器 | PATH 第一个 `python` |
| `CODEX_HOME` | Codex skills 根目录 | `$env:USERPROFILE/.codex` |
| `MINERU_MODEL_SOURCE` | 透传给 mineru 子进程；可选 `huggingface` / `modelscope` | 用户原值 |
| `CUDA_VISIBLE_DEVICES` | 透传给 mineru 子进程 | 用户原值 |

**不会**透传给 mineru 子进程的变量：

- `MINERU_TOKEN`
- `MINERU_API_URL`
- `MINERU_API_TOKEN`

## 错误诊断

`install` 与 `mineru_doctor` 报告会附带 `diagnostics` 数组，每条都引用 MinerU 官方文档锚点。已支持的诊断模式：

| 触发条件 | 官方文档锚点 |
|----------|--------------|
| `ImportError: libGL.so.1: ...` | [WSL2 Ubuntu 22.04 libgl](https://github.com/opendatalab/MinerU/blob/master/docs/zh/faq/index.md#wsl2-ubuntu2204-libgl) |
| `no kernel image is available for execution on the device` | [Windows CUDA 加速](https://github.com/opendatalab/MinerU/blob/master/docs/zh/faq/index.md#windows-cuda-acceleration) |
| 推理慢 / CUDA 未启用 | [Windows CUDA 加速](https://github.com/opendatalab/MinerU/blob/master/docs/zh/faq/index.md#windows-cuda-acceleration) |
| 缺 CJK 字体 | [Linux 缺 CJK 字体](https://github.com/opendatalab/MinerU/blob/master/docs/zh/faq/index.md#linux-missing-text-cjk-fonts) |
| vllm + lmdeploy 都没装 | [扩展模块安装](https://github.com/opendatalab/MinerU/blob/master/docs/zh/quick_start/extension_modules.md) |
| Docker 在 macOS 上失败 | [Docker 部署](https://github.com/opendatalab/MinerU/blob/master/docs/en/quick_start/docker_deployment.md) |
| `ModuleNotFoundError: vllm` | [扩展模块安装](https://github.com/opendatalab/MinerU/blob/master/docs/zh/quick_start/extension_modules.md) |

注意：

- `diagnose` 只在**确实观测到**子进程输出 / 环境探测中出现该错误时触发；不"猜测"
- 每条诊断只列官方已确认的**原因**和**文档链接**，不替用户做后端/环境选择
- 想新增诊断模式必须先在 MinerU 官方文档里找到对应章节

## 路径搜索顺序

`lib_install.py` 内部按以下顺序探测关键路径：

- **Python**：`$env:PYTHON` → PATH 上的 `python`/`py` → `~/miniconda3/python.exe` → `C:\ProgramData\anaconda3\python.exe` → `~/.conda/envs/mineru/python.exe`
- **Codex home**：`$env:CODEX_HOME` → `~/.codex`
- **quick_validator**：`<codex_home>/skills/.system/skill-creator/scripts/quick_validate.py`
- **skill root**：从 `lib_install.py` 所在目录向上找含 `SKILL.md` 的父目录

## 延展阅读

- [SKILL.md](SKILL.md) — Codex 加载的入口（触发词、工作流、子命令）
- [references/local_cli.md](references/local_cli.md) — CLI 参数速查与 JSON 报告 schema
- [references/troubleshooting.md](references/troubleshooting.md) — 深排错（CUDA、模型缓存、WSL GPU、路径）

## 输出目录约定

`parse` / `parse-dir` 不传 `--output` 时，按以下顺序决定默认父目录：

1. `$env:MINERU_LOCAL_OUTPUT_DIR`（绝对路径直接用；相对路径与 cwd 拼接）
2. `Path.cwd() / "<date> minerUToMd"`
3. cwd 不可写时回退到 `%TEMP% / "<date> minerUToMd"`

同一日多次跑自动加 `-2`、`-3` 后缀。minerU 会再嵌一层 `<doc-stem>/<method>/`，
具体路径在报告的 `output_layout.example` 字段给出占位符说明。

用户显式传 `--output` 时不规范化；报告的 `output_layout.note` 字段会注明此情况。

## 已知限制

- **macOS**：MinerU Docker 部署不支持 macOS（无法访问 MPS/MLX）。本 skill 也无法在 macOS 直接运行 vllm/lmdeploy。请参考 MinerU 官方 macOS 加速方案（MLX）。
- **Blackwell (RTX 50 系) on Windows**：lmdeploy 0.11.1 + turbomind 可能没有匹配的 kernel，会报 `no kernel image`。本 skill 不会自动切到云端；只提示官方文档。
- **WSL 必须支持 GPU 透传**：用 `wsl -d Ubuntu -- nvidia-smi` 验证可见 GPU。