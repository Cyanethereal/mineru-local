# MinerU 本地 CLI 速查

仅记录 `mineru_local.py` 包装器所透传到 `mineru.exe` 的参数。详细官方文档见 [MinerU 官方文档](https://github.com/opendatalab/MinerU/tree/master/docs/zh/usage)。

## 子命令总览

| 子命令 | 用途 |
|--------|------|
| `doctor` | 探测 Windows conda 与 WSL conda `mineru` 环境 |
| `parse` | 解析单文件或单目录 |
| `parse-dir` | 递归解析目录下所有受支持文件 |

## parse / parse-dir 通用参数

| 参数 | 默认 | 含义 |
|------|------|------|
| `--path` | 必填 | 输入文件或目录的绝对路径 |
| `--output` | 临时目录 | 输出目录绝对路径；缺省写入 `%TEMP%\mineru-local-<ts>` |
| `--env` | `auto` | 强制 `windows` / `wsl` / `auto` |
| `--backend` | 决策表 | `pipeline` / `vlm-engine` / `hybrid-engine` |
| `--method` | `auto` | `auto` / `txt` / `ocr`（Office 不接受 `ocr`） |
| `--lang` | `ch` | OCR 语言（见下表） |
| `--start` | 0 | 起始页（0 起） |
| `--end` | 末页 | 结束页 |
| `--formula` | true | 是否做公式识别 |
| `--table` | true | 是否做表格识别 |
| `--image-analysis` | true | 是否做图像/图表分析 |
| `--effort` | `medium` | hybrid 后端的 effort；`medium` 或 `high` |
| `--api-url` | （空） | 仅当用户主动提供时校验；只接受 127.0.0.1/localhost/::1 |

## OCR 语言

```text
ch ch_server korean ta te ka th el arabic east_slavic cyrillic devanagari
```

## 后端选择决策表（与 SKILL.md 一致）

| 输入 | 行为 |
|------|------|
| 默认 | WSL + `hybrid-engine` |
| WSL 不可用 | 回退 Windows + `pipeline` |
| Office 输入 | 强制 `pipeline`（无论 WSL/Windows） |
| `--env windows` | 强制 Windows conda |
| `--env wsl` | 强制 WSL conda |
| `--backend pipeline/vlm-engine/hybrid-engine` | 覆盖默认 |
| `--no-hybrid` | 等价于 `--backend pipeline` |

## 输出目录结构（来自 `mineru` 3.4.4）

```text
<output>/
└── <document-stem>/
    └── <method>/                  # txt 或 ocr
        ├── <stem>.md
        ├── <stem>_content_list.json
        ├── <stem>_middle.json
        ├── <stem>_layout.pdf
        ├── <stem>_model.pdf
        ├── <stem>_origin.pdf
        ├── <stem>_span.pdf
        └── images/
            ├── <sha>.png
            └── ...
```

`parse` 的 JSON 报告 `artifacts` 字段会分别返回 `markdown / json / images` 的绝对路径列表。

## 路径转换规则

- Windows → WSL：`D:\foo\bar.pdf` 转为 `/mnt/d/foo/bar.pdf`
- WSL → Windows：通过 `wslpath -w <path>` 转回 `C:\...` 形式
- 报告里的 `host_path` 字段已是 Windows 路径

## 严禁的设置

- `--api-url http://<remote-host>`：拒绝
- `--backend vlm-http-client` / `hybrid-http-client` 指向非本机：拒绝
- 把 `MINERU_TOKEN` / `MINERU_API_URL` 传给子进程：主动 `unset`

## 覆盖路径

| 变量 | 默认 | 说明 |
|------|------|------|
| `MINERU_WINDOWS_EXECUTABLE` | `C:\Users\YOUR_USER\.conda\envs\mineru\Scripts\mineru.exe` | Windows conda mineru 路径 |
| `MINERU_WSL_EXECUTABLE` | `/home/YOUR_USER/miniconda3/envs/mineru/bin/mineru` | WSL conda mineru 路径 |
| `MINERU_WSL_DISTRO` | `Ubuntu-24.04` | WSL distro 名 |

## 输出目录约定

`--output` 缺省时，wrapper 自动选用 `Path.cwd() / "<YYYY-MM-DD> minerUToMd"`。
可通过 `MINERU_LOCAL_OUTPUT_DIR` 覆盖父目录（绝对或相对路径）。
同日多次跑自动追加 `-2`、`-3` 后缀。

报告字段：

```json
{
  "output": "<实际父目录>",
  "output_layout": {
    "parent": "<父目录>",
    "note":   "<一行说明 minerU 内部嵌套 + 是否显式>",
    "example": "<parent>/<doc-stem>/<method>/<doc-stem>.md"
  }
}
```

`output_layout.example` 始终使用占位符 `<doc-stem>`、`<method>`，不会写死文件名。

## JSON 报告字段

```json
{
  "local": true,
  "environment": "wsl",
  "executable": "/home/azuresky/miniconda3/envs/mineru/bin/mineru",
  "mineru_version": "3.4.4",
  "input": "...",
  "output": "...",
  "backend": "hybrid-engine",
  "method": "auto",
  "returncode": 0,
  "wsl": true,
  "wsl_distro": "Ubuntu-24.04",
  "host_path": "C:\\Users\\azuresky\\...\\output",
  "artifacts": { "markdown": [], "json": [], "images": [] },
  "command": "wsl -d Ubuntu-24.04 -- /home/.../mineru ...",
  "stdout_tail": "...",
  "stderr_tail": "..."
}
```

退出码：

| Code | 含义 |
|------|------|
| 0 | 成功 |
| 1 | parse-dir 部分失败 |
| 2 | 参数错误 |
| 3 | 环境不可用 |
| 4 | 后端在该环境不可用 |
| 5 | `returncode==0` 但没有产物 |
| 其他 | 透传 `mineru` 退出码 |