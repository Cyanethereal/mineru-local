# MinerU 本地 CLI 速查

本文只描述 `scripts/mineru_local.py` 的实际行为。所有子命令都只向 stdout 写 JSON；不要把 stdout 当作普通日志流。

## 子命令

| 子命令 | 行为 |
|---|---|
| `doctor` | 探测 Windows/WSL MinerU、可用 backend 与配置来源 |
| `parse` | 解析一个受支持文件 |
| `parse-dir` | 递归解析目录，最终只输出一个汇总 JSON |
| `manifest` | 从已有 raw output 目录重建 schema v1 manifest |
| `config show` | 显示持久化配置与最终解析值 |
| `config set` | 写入可移植配置字段 |

## 配置与优先级

执行目标：

```text
显式 --env windows|wsl
> MINERU_DEFAULT_ENV
> config.default_env
> auto
```

可执行文件与 WSL distro 的解析顺序始终是：

```text
CLI > 环境变量 > 配置文件
```

因此，显式 `--env windows|wsl` 仍可工作，即使 `MINERU_DEFAULT_ENV` 本身无效；无效配置不会因另一个环境变量而变得有效。`--env auto` 不会覆盖无效的 `MINERU_DEFAULT_ENV`。

`doctor`、`parse`、`parse-dir` 和 `config` 支持以下通用参数：

| 参数 | 含义 |
|---|---|
| `--config PATH` | 指定配置文件；不会创建文件 |
| `--windows-executable PATH` | Windows MinerU 可执行文件 |
| `--wsl-executable PATH` | WSL 内 MinerU 可执行文件 |
| `--wsl-distro NAME` | WSL distro 名称 |
| `--default-env auto|windows|wsl` | 持久化默认目标 |
| `--io-policy auto|native|staged` | I/O 策略 |

配置路径可通过 `MINERU_LOCAL_CONFIG` 覆盖；均未设置时使用平台默认配置目录。

## `doctor`

```powershell
python scripts/mineru_local.py doctor
python scripts/mineru_local.py doctor --env wsl --backend hybrid-engine
```

`doctor` 返回探测结果、`config`、`config_sources`、`selected_environment`、`selected_backend` 和 `diagnostics`。即使没有可用环境，命令本身通常仍返回 exit 0；调用方必须检查 `selected_environment`。

## `parse`

```powershell
python scripts/mineru_local.py parse --path C:\docs\lecture.pdf --output C:\out
```

`--path` 必须是一个已存在的文件。支持后缀：

```text
.pdf .png .jpg .jpeg .jp2 .webp .gif .bmp
.doc .docx .ppt .pptx .xls .xlsx
```

`--output` 必须是新的或空目录；非空目录会被拒绝，避免混入旧产物。缺省输出规则见“输出目录”。

### parse 参数

| 参数 | 默认 | 实际行为 |
|---|---|---|
| `--path` | 必填 | 单个输入文件 |
| `--output` | 自动目录 | 新/空输出根目录 |
| `--env` | 配置值，最终可为 `auto` | `auto` / `windows` / `wsl` |
| `--backend` | 自动选择 | `pipeline` / `vlm-engine` / `hybrid-engine` |
| `--method` | `auto` | `auto` / `txt` / `ocr` |
| `--lang` | 未设置 | OCR 语言；不传就不向 MinerU 转发 |
| `--start` | 未设置 | 起始页 |
| `--end` | 未设置 | 结束页 |
| `--formula` | 未设置 | 传值时转成 `true` / `false` |
| `--table` | 未设置 | 传值时转成 `true` / `false` |
| `--image-analysis` | 未设置 | 传值时转成 `true` / `false` |
| `--effort` | 未设置 | 仅 hybrid backend 转发 `medium` / `high` |
| `--api-url` | 未设置 | 只做 loopback 安全校验，不转发给 MinerU |

OCR 语言：

```text
ch ch_server korean ta te ka th el arabic east_slavic cyrillic devanagari
```

### 环境与 backend

- 显式 `--env windows` 或 `--env wsl` 是硬约束；不可用时不跨环境回退。
- `auto` 先尝试 WSL；WSL smoke test 失败后可用 Windows `pipeline` 回退。
- 不支持 `vlm-http-client`、`hybrid-http-client` 等远程 backend。
- Office 输入的有效 backend 始终是 `pipeline`。若请求了其他 backend，报告中会记录 `requested_backend` 和非空 `backend_override`。
- Office 输入不接受 `--method ocr`。

### I/O

- Windows 目标使用 `native`。
- WSL 目标在 `--io-policy native` 时使用 `native`。
- WSL 目标在 `--io-policy auto|staged` 时使用 `staged`。
- staged 流程会先计算源文件 SHA256，再复制到 WSL，并在调用 MinerU 前再次计算 WSL 副本的 SHA256。
- manifest 使用 staging 前已验证的 SHA256；运行期间源文件变化不会悄悄改变 provenance。
- WSL 临时目录清理失败会成为显式 `execution_failed`，不会静默成功。

## `parse-dir`

```powershell
python scripts/mineru_local.py parse-dir --path C:\docs --output C:\out
```

- 递归扫描受支持文件。
- 每个输入的输出目录由相对路径和完整文件名决定，例如 `C:\docs\a\lecture.pdf` 对应 `C:\out\a\lecture.pdf`。
- 同名或已存在目录会追加 `-2`、`-3`，不会把两个输入写入同一目录。
- 子解析的 JSON 不会直接写到 stdout，而是嵌入最终汇总的 `results`。
- 整个命令只输出一个 JSON document。

汇总结构：

```json
{
  "local": true,
  "command": "parse-dir",
  "input": "C:\\docs",
  "output": "C:\\out",
  "output_layout": {"parent": "C:\\out", "note": "...", "example": "..."},
  "files": ["C:\\docs\\a\\lecture.pdf"],
  "results": [
    {
      "input": "C:\\docs\\a\\lecture.pdf",
      "relative_path": "a/lecture.pdf",
      "output": "C:\\out\\a\\lecture.pdf",
      "returncode": 0,
      "report": {"local": true, "command": "parse", "...": "..."}
    }
  ],
  "failures": 0
}
```

`parse-dir` exit code：全部成功为 `0`，任一文件失败为 `1`。

## `manifest`

```powershell
python scripts/mineru_local.py manifest ^
  --path C:\docs\lecture.pdf ^
  --output C:\out ^
  --env wsl --backend pipeline --method auto ^
  --io-mode staged --returncode 0 ^
  --stdout-file C:\logs\mineru.stdout.log ^
  --stderr-file C:\logs\mineru.stderr.log
```

`--output` 必须是已存在的 raw output 根目录。`--io-mode` 只接受已解析值：

```text
native | staged
```

`auto` 只属于运行策略，不是合法 manifest `io_mode`。`manifest` 也继承通用参数以保持 CLI 兼容，但通用 executable/distro 参数不参与离线 manifest 重建。

### manifest 内容

- 写出 `mineru-artifact-manifest.json`。
- 写出 `mineru-output-tree.txt`，其元数据回到 `manifest.output_tree`。
- `manifest_schema_version` 固定为 `1`，schema 位于 `scripts/schemas/mineru-artifact-manifest.schema.json`。
- 所有 artifact 路径使用相对 POSIX 路径。
- 路径必须拒绝：开头 `/`、盘符路径、反斜杠/UNC、`//`、任何 `..` 段。
- `markdown.primary` 最多一个；有候选但无法确定性选择时写 blocking error。
- `assets` 包含全部图片，以及被 Markdown 引用的本地非图片文件。
- 缺失的本地引用和越出 raw output root 的引用是 manifest `errors`。
- 未被 Markdown 引用的图片只产生 `unreferenced_assets_present` warning。
- stdout/stderr 都会采集 warning/error-like sample。
- child exit 0 时，error-like sample 仍是 warning。
- child exit 非 0 时，error-like sample 与 `mineru_returncode_nonzero` 都是 blocking errors。
- `status=ok` 表示无 errors；有 errors 但有 Markdown 时通常为 `partial`；无 Markdown 时为 `failed`。

`manifest` 命令的 stdout JSON 包含摘要、`manifest_document` 和 `manifest.path`。exit code：`ok|partial` 为 `0`，`failed` 为 `5`。

## `config`

```powershell
python scripts/mineru_local.py config show
python scripts/mineru_local.py config set --wsl-distro Ubuntu-24.04
python scripts/mineru_local.py config set --default-env wsl --io-policy auto
```

`config show` 同时返回原始配置与 `resolved` 值。`config set` 至少需要一个配置字段。

## 输出目录

未显式传 `--output` 时：

1. 若 `MINERU_LOCAL_OUTPUT_DIR` 已设置，它必须指向已存在目录，并作为父目录。
2. 否则使用当前工作目录（若可写）。
3. 当前工作目录不可写时回退到系统 temp。
4. 最终目录名为 `<YYYY-MM-DD> minerUToMd`，冲突时追加 `-2`、`-3`。

MinerU 会在该目录内继续创建自己的 `<doc-stem>/<method>/` 结构。

## `parse` 报告要点

```json
{
  "local": true,
  "command": "parse",
  "environment": "wsl",
  "requested_environment": "wsl",
  "backend": "pipeline",
  "requested_backend": "hybrid-engine",
  "backend_override": {"used": true, "actual_backend": "pipeline"},
  "method": "auto",
  "io_mode": "staged",
  "returncode": 0,
  "input": "C:\\docs\\lecture.pdf",
  "output": "C:\\out",
  "artifacts": {"markdown": [], "json": [], "images": []},
  "manifest": {
    "path": "C:\\out\\mineru-artifact-manifest.json",
    "schema_version": 1,
    "status": "ok",
    "primary_markdown": "lecture/auto/lecture.md"
  },
  "fallback": {"used": false, "smoke_attempts": []},
  "stdout_tail": "...",
  "stderr_tail": "..."
}
```

若存在 manifest warnings，报告中还会出现最多 10 条 `warning_samples`。

## 退出码

| Code | 含义 |
|---|---|
| 0 | 成功，或 manifest 为 `ok/partial` |
| 1 | `parse-dir` 至少一个文件失败 |
| 2 | 参数、输入、输出、配置或 manifest `--io-mode` 错误 |
| 3 | 显式环境不可用，或环境解析失败 |
| 4 | 所选环境不能运行所选 backend |
| 5 | child 看似成功但没有可用 Markdown / manifest `failed` |
| 6 | 没有可用环境或执行失败（含 staged cleanup 失败） |
| 7 | manifest 构建/写入失败 |
| 其他 | MinerU child 非零退出码透传 |

## 禁止项

- 远程 MinerU API 或远程 token。
- 非 loopback 的 `--api-url`。
- 把 `MINERU_TOKEN` / `MINERU_API_URL` / `MINERU_API_TOKEN` 传入 child。
- 用 `--env auto` 替代 manifest 的 `--io-mode auto`；后者不存在。
