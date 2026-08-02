---
name: mineru-local
description: 本地 MinerU 文档解析，跨 Windows 与 WSL-Ubuntu 自动选择执行环境。PDF、图片、DOCX、PPTX、XLSX 一键转 Markdown，自动优先使用 WSL 中的 vLLM + hybrid-engine 高准确率解析；不调用 mineru.net 云 API、不上传文件、不使用 MINERU_TOKEN。使用场景：(1) 本地解析 PDF/Word/PPT/Excel/图片到 Markdown；(2) 扫描件 OCR 提取文字与公式；(3) 提取论文/教材中的表格、公式与图片资源；(4) 批量解析一个目录下的全部文档；(5) 用本地 vLLM 服务替代云端 VLM 进行高质量解析。
---

# MinerU Local

本地 MinerU 解析 wrapper，跨 **Windows conda `mineru`** 与 **WSL Ubuntu-24.04 conda `mineru`** 自动选环境。

## 关键不变式

- **永远本地**：不向 `mineru.net` 上传文件，不读取或传递 `MINERU_TOKEN`，不接受 `vlm-http-client` / `hybrid-http-client` 指向远端 URL。
- **自动环境选择**：默认 WSL（带 vLLM），WSL 不可用时回退到 Windows（仅 pipeline）。
- **可验证成功**：包装器只报告 `returncode==0` **且**至少存在一个非空 Markdown 产物时为成功；否则返回错误码 5。

## 工作流

1. **确认输入**。用户提供的文件路径必须存在，且后缀为以下之一：
   - `.pdf .png .jpg .jpeg .jp2 .webp .gif .bmp`
   - `.doc .docx .ppt .pptx .xls .xlsx`
   完成标准：`Path(input).is_file()` 且 `input.suffix.lower() in 支持列表`。
2. **运行 doctor**。执行：
   ```bash
   python C:\Users\azuresky\.codex\skills\mineru-local\scripts\mineru_local.py doctor
   ```
   完成标准：退出码 `0`，`selected_environment` 不为 `null`。
3. **选择环境与后端**。按 doctor 输出的 `selected_environment` / `selected_backend`；如用户显式传 `--env` 或 `--backend`，优先用户。Office 文件强制 `pipeline`；扫描件可设 `--method ocr`。
4. **执行解析**。运行：
   ```bash
   python C:\Users\azuresky\.codex\skills\mineru-local\scripts\mineru_local.py parse --path <abs> --output <abs> [options]
   ```
   或批量：
   ```bash
   python C:\Users\azuresky\.codex\skills\mineru-local\scripts\mineru_local.py parse-dir --path <abs-dir> --output <abs-dir>
   ```
   完成标准：退出码 `0`。
5. **验证产物**。报告必须含 `artifacts.markdown`（至少 1 个非空文件）。把报告里的 `host_path` 转交给用户；不要把 `/mnt/...` 这种 WSL 路径直接给到 Windows 用户。

## 解析调用示例

单个 PDF（默认 WSL + hybrid-engine）：

```powershell
python C:\Users\azuresky\.codex\skills\mineru-local\scripts\mineru_local.py parse `
  --path "C:\eReading\pika资源共享\开源资料\代数学基础\A4noindex2.pdf" `
  --output "C:\Users\azuresky\Documents\mineru-out"
```

Windows pipeline（不需要 vLLM）：

```powershell
python C:\Users\azuresky\.codex\skills\mineru-local\scripts\mineru_local.py parse `
  --path "C:\path\paper.pdf" `
  --output "C:\path\out" `
  --env windows `
  --backend pipeline
```

扫描件 OCR：

```powershell
python C:\Users\azuresky\.codex\skills\mineru-local\scripts\mineru_local.py parse `
  --path "C:\path\scan.pdf" `
  --output "C:\path\out" `
  --method ocr `
  --lang ch
```

批量目录：

```powershell
python C:\Users\azuresky\.codex\skills\mineru-local\scripts\mineru_local.py parse-dir `
  --path "D:\docs" `
  --output "D:\docs-out"
```

## 失败与回退

- **WSL 不可用**：doctor 返回 `wsl.available=false` → 回退 Windows + pipeline。报告里会有 `reason` 字段说明。
- **hybrid-engine 报 CUDA kernel 错误**：包装器不会自动切换云端；请改用 `--env windows --backend pipeline` 或升级 `lmdeploy`。
- **MINERU_TOKEN / MINERU_API_URL 出现在环境**：包装器会主动从子进程环境移除，不会把它们透传。
- **远端 `--api-url`**：被拒（hostname 解析后必须落在 127.0.0.0/8 或 ::1）。要使用 vLLM 外部服务时，请把服务启动在 `127.0.0.1`，并通过 `--env wsl --backend hybrid-http-client` 之外的方式接入；本 skill 不支持。
- **OCR 后 VLM 二次校验**：本 skill 第一版不内置；如需 VLM 校验，请用 vLLM 或 Ollama 单独读产物图片。

## 子命令

| 子命令 | 用途 |
|--------|------|
| `doctor` | 探测 Windows/WSL/Ollama，输出 JSON 决策 |
| `parse` | 解析单个文件或目录，返回 JSON 报告 |
| `parse-dir` | 递归解析目录下所有受支持文件 |

详细参数表与产物目录结构见 `references/local_cli.md`；CUDA、模型缓存、WSL GPU 不可见等故障排查见 `references/troubleshooting.md`。