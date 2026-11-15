---
name: mineru-local
description: 本地 MinerU PDF/Office 解析 wrapper：使用显式用户配置，支持 Windows 或 WSL、native/staged I/O，并输出可校验的 MinerUArtifactManifest。Use when user wants to parse PDF, DOCX, PPTX, XLSX, or images to Markdown locally; mentions MinerU, PDF parsing, OCR, formulas, or local document conversion.
---

# MinerU Local

本地 `mineru` CLI 的 wrapper。它不上传文件、不使用远端 API，并把每次解析结果整理成 schema v1 的 `MinerUArtifactManifest`。

## 配置

配置保存在 Skill 载荷之外的平台配置目录。先登记实际可执行文件，不依赖源码中的用户路径：

```powershell
$MineruScript = Join-Path $env:CODEX_HOME "skills\mineru-local\scripts\mineru_local.py"

python $MineruScript config set `
  --wsl-executable "/home/<user>/miniconda3/envs/mineru/bin/mineru" `
  --wsl-distro "Ubuntu-24.04" `
  --default-env wsl `
  --io-policy auto

python $MineruScript doctor
```

配置优先级固定为：显式 CLI > 环境变量 > 持久化配置 > 明确失败。

## 常用命令

```powershell
# 单文件；--output 必须是新目录或空目录
python $MineruScript parse `
  --path "C:\docs\lecture.pdf" `
  --output "C:\temp\lecture-raw" `
  --env wsl `
  --backend hybrid-engine

# 批量目录；stdout 是一个 JSON 文档
python $MineruScript parse-dir `
  --path "C:\docs" `
  --output "C:\temp\batch-raw"

# 从已有 raw tree 离线重建 manifest
python $MineruScript manifest `
  --path "C:\docs\lecture.pdf" `
  --output "C:\temp\lecture-raw" `
  --env wsl `
  --backend hybrid-engine `
  --method auto `
  --io-mode staged
```

## 子命令

| 子命令 | 用途 |
|---|---|
| `doctor` | 探测配置、环境和可用 backend |
| `parse` | 解析单个文件并写出 manifest |
| `parse-dir` | 批量解析，结果嵌入单个 JSON 的 `results` |
| `manifest` | 从已有 raw output tree 重建 manifest |
| `config show` | 显示配置路径、原始配置和解析来源 |
| `config set` | 持久化 WSL/Windows 可执行文件、distro、默认环境和 I/O 策略 |

## 执行与产物规则

- 自动环境选择优先 WSL；显式 `--env` 不会静默回退。
- `io_policy: auto` 在跨 Windows/WSL 边界时解析为 `staged`。
- staged 输入在复制前计算 SHA256，复制后验证 WSL 副本，再启动 MinerU。
- WSL 临时目录 cleanup 失败是显式错误。
- `--output` 非空时拒绝解析，避免旧产物污染 manifest。
- 成功标准：child returncode 为 0，manifest 有且仅有一个非空 primary Markdown。
- 可恢复的 stdout/stderr 诊断保留为 warning；非零退出是 blocking error。
- Office 输入强制 `pipeline`，并拒绝 `--method ocr`。

## 详细参考

- CLI、输出结构和退出码：`references/local_cli.md`
- 配置、CUDA、WSL 与 GPU 排查：`references/troubleshooting.md`
