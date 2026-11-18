# MinerU Local Troubleshooting

## Configuration cannot be found

Run:

```powershell
$MineruScript = Join-Path $env:CODEX_HOME "skills\mineru-local\scripts\mineru_local.py"
python $MineruScript config show
python $MineruScript doctor
```

Check that `sources` reports `config:<path>#...` or an explicit CLI/environment override. No source-tree user path is used.

## WSL executable missing

```powershell
wsl -d Ubuntu-24.04 -- bash -lc "command -v mineru; mineru --version"
```

If MinerU is installed in a conda env but not on PATH, record its absolute WSL path:

```powershell
python $MineruScript config set `
  --wsl-executable "/home/<user>/miniconda3/envs/mineru/bin/mineru" `
  --wsl-distro "Ubuntu-24.04"
```

## vLLM or CUDA diagnostic

A successful parse may still emit recoverable backend diagnostics. They remain visible in the manifest as warnings. A warning such as `SM 12.x requires CUDA >= 12.9` means the runtime compatibility should be reviewed; it is not silently removed.

If the child exits non-zero, the same diagnostics become blocking manifest errors.

## hybrid-engine is unusable

Use an explicit fallback:

```powershell
python $MineruScript parse `
  --path "C:\docs\file.pdf" `
  --output "C:\temp\file-raw" `
  --env wsl `
  --backend pipeline
```

Do not let the wrapper silently switch to a remote service.

## Staged I/O failure

For WSL runs, `io_policy: auto` stages inputs and outputs. Check:

- the source Handout is readable;
- WSL has enough free space under `/tmp`;
- the destination output directory is new or empty;
- `doctor` reports a usable WSL distro and executable.

Cleanup failure is reported as an explicit execution error, not a success.

## Output directory is not empty

`parse` rejects a non-empty `--output` directory. Use a new path for every run so the manifest cannot include stale artifacts.

## Remote API is rejected

Only loopback endpoints are allowed for optional local API modes. Remote MinerU/OpenAI-compatible endpoints are forbidden. Bind any local helper to `127.0.0.1`.

## Office input

Office inputs use `pipeline` and reject `--method ocr`. A requested non-pipeline backend is recorded as `backend_override` in the report.
