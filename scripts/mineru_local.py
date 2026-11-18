"""mineru_local.py

Local-only wrapper around the on-disk `mineru` CLI (Windows conda env and
WSL Ubuntu-24.04 conda env). It refuses to call any remote/cloud backend and
returns a structured JSON report.

Subcommands:
    doctor    - probe Windows + WSL conda `mineru` envs and Ollama, return JSON
    parse     - parse a single file or directory
    parse-dir - recursively parse every supported file in a directory

The wrapper never forwards `--api-url` to a non-loopback host and never lets
`MINERU_TOKEN` / `MINERU_API_URL` leak into the child process. A successful
parse is only reported when at least one non-empty Markdown artifact is found
on disk.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import io
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Optional

import mineru_config as ml_config
import mineru_manifest as ml_manifest

TOOL_VERSION = ml_manifest.TOOL_VERSION
SUPPORTED_BACKENDS = ("pipeline", "vlm-engine", "hybrid-engine")
FORBIDDEN_BACKENDS = ("vlm-http-client", "hybrid-http-client")
SUPPORTED_METHODS = ("auto", "txt", "ocr")
SUPPORTED_EFFORTS = ("medium", "high")
SUPPORTED_LANGS = (
    "ch",
    "ch_server",
    "korean",
    "ta",
    "te",
    "ka",
    "th",
    "el",
    "arabic",
    "east_slavic",
    "cyrillic",
    "devanagari",
)
SUPPORTED_SUFFIXES = (
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".jp2",
    ".webp",
    ".gif",
    ".bmp",
    ".docx",
    ".pptx",
    ".xls",
    ".xlsx",
)
LEGACY_CONVERSION_SUFFIXES = (".doc", ".ppt")
OFFICE_SUFFIXES = (".docx", ".pptx", ".xls", ".xlsx")
REMOTE_TOKEN_VARS = ("MINERU_TOKEN", "MINERU_API_URL", "MINERU_API_TOKEN")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit(payload: dict[str, Any], *, code: int = 0) -> int:
    """Print JSON to stdout and return a process exit code."""
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return code


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def _run(cmd: list[str], *, timeout: float = 60.0,
         env: Optional[dict[str, str]] = None,
         cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=cwd,
    )


def _run_text(cmd: list[str], *, timeout: float = 60.0,
              env: Optional[dict[str, str]] = None,
              input_text: Optional[str] = None) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        cp = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            input=input_text,
        )
        return cp.returncode, cp.stdout or "", cp.stderr or ""
    except FileNotFoundError as exc:
        return 127, "", f"command not found: {exc}"
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return 124, stdout, stderr + "\n[timeout]"


def _wsl_translate_cmd(distro: str, inner: str) -> list[str]:
    return ["wsl", "-d", distro, "--", "bash", "-lc", inner]


def _wsl_path_to_windows(distro: str, wsl_path: str) -> Optional[str]:
    """Translate a WSL path to a Windows path via `wslpath -w`."""
    code, out, err = _run_text(_wsl_translate_cmd(distro, f"wslpath -w {shlex.quote(wsl_path)}"), timeout=20.0)
    if code != 0 or not out.strip():
        return None
    return out.strip().splitlines()[-1].strip()


def _wsl_to_windows_path(distro: str, wsl_path: str) -> str:
    """Convert /mnt/<drive>/... paths back to Windows form; fall back to
    returning the input unchanged if wslpath fails."""
    translated = _wsl_path_to_windows(distro, wsl_path)
    if translated:
        return translated
    return wsl_path


def _windows_to_wsl_path(win_path: str) -> str:
    """Convert a Windows path (D:\\foo\\bar) to a WSL path (/mnt/d/foo/bar).

    Handles UNC paths and mixed separators. Returns the input unchanged when
    it already looks like a POSIX path."""
    if not win_path:
        return win_path
    text = win_path.replace("\\", "/")
    if text.startswith("//") or text.startswith("\\\\"):
        # UNC path - leave as-is; WSL would need extra plumbing.
        return text
    m = re.match(r"^([A-Za-z]):/(.*)$", text)
    if m:
        drive = m.group(1).lower()
        rest = m.group(2)
        return f"/mnt/{drive}/{rest}"
    if text.startswith("/"):
        return text
    return text


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------


@dataclass
class EnvironmentReport:
    available: bool
    executable: str
    python: Optional[str] = None
    mineru_version: Optional[str] = None
    vllm: bool = False
    vllm_version: Optional[str] = None
    lmdeploy: bool = False
    lmdeploy_version: Optional[str] = None
    backends_usable: list[str] = field(default_factory=list)
    reason: Optional[str] = None
    distro: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _probe_windows(executable: str) -> EnvironmentReport:
    if not executable or not str(executable).strip():
        return EnvironmentReport(
            available=False,
            executable="",
            reason="Windows MinerU executable is not configured",
        )
    if not Path(executable).exists():
        return EnvironmentReport(
            available=False,
            executable=executable,
            reason="executable not found",
        )
    report = EnvironmentReport(available=True, executable=executable)
    code, out, _ = _run_text([executable, "--version"], timeout=20.0)
    if code == 0:
        m = re.match(r"mineru, version (\S+)", out.strip())
        if m:
            report.mineru_version = m.group(1)
    py_exe = str(Path(executable).parent.parent / "python.exe")
    if Path(py_exe).exists():
        report.python = _detect_python_version([py_exe, "-V"])
        vllm, vv = _detect_pip_package([py_exe, "-m", "pip", "show", "vllm"])
        lmdeploy, lv = _detect_pip_package([py_exe, "-m", "pip", "show", "lmdeploy"])
        report.vllm = vllm
        report.vllm_version = vv
        report.lmdeploy = lmdeploy
        report.lmdeploy_version = lv
    usable = ["pipeline"]
    if report.vllm:
        usable.extend(["vlm-engine", "hybrid-engine"])
    elif report.lmdeploy:
        report.reason = "lmdeploy detected; hybrid-engine capability is unverified"
    report.backends_usable = usable
    return report

def _detect_python_version(cmd: list[str]) -> Optional[str]:
    code, out, _ = _run_text(cmd, timeout=15.0)
    if code == 0 and out:
        m = re.search(r"Python\s+(\S+)", out)
        if m:
            return m.group(1)
    return None


def _detect_pip_package(cmd: list[str]) -> tuple[bool, Optional[str]]:
    code, out, _ = _run_text(cmd, timeout=30.0)
    if code != 0:
        return False, None
    m = re.search(r"^Version:\s*(\S+)", out, re.MULTILINE)
    return True, m.group(1) if m else None


def _probe_wsl(distro: str, executable: str) -> EnvironmentReport:
    report = EnvironmentReport(
        available=False,
        executable=executable or "",
        distro=distro or None,
    )
    if not distro or not executable:
        report.reason = "WSL distro or MinerU executable is not configured"
        return report
    pybin = shlex.quote(
        str(__import__("posixpath").join(
            str(__import__("posixpath").dirname(executable)),
            "python",
        ))
    )
    exe_q = shlex.quote(executable)
    vllm_q = shlex.quote(
        "import importlib.util; print('VLLM=' + ('yes' if importlib.util.find_spec('vllm') is not None else 'no'))"
    )
    lmd_q = shlex.quote(
        "import importlib.util; print('LMDEPLOY=' + ('yes' if importlib.util.find_spec('lmdeploy') is not None else 'no'))"
    )
    inner = (
        f"if [ ! -x {exe_q} ]; then echo STATUS=MISSING; exit 0; fi; "
        f"{exe_q} --version 2>/dev/null | head -1 | sed 's/^/MINERU=/'; "
        f"{pybin} -V 2>/dev/null | sed 's/^/PYVER=/'; "
        f"{pybin} -c {vllm_q} 2>/dev/null; "
        f"{pybin} -c {lmd_q} 2>/dev/null"
    )
    code, out, _ = _run_text(
        _wsl_translate_cmd(distro, inner),
        timeout=60.0,
        env=_build_env(os.environ, wsl=True),
    )
    if code != 0:
        report.reason = "WSL probe failed"
        return report
    saw = False
    for line in out.splitlines():
        if line.startswith("STATUS=MISSING"):
            report.reason = "executable not found in WSL"
            return report
        if line.startswith("MINERU="):
            saw = True
            m = re.match(r"mineru, version (\S+)", line[len("MINERU="):])
            if m:
                report.mineru_version = m.group(1)
        elif line.startswith("PYVER="):
            m = re.search(r"Python\s+(\S+)", line[len("PYVER="):])
            if m:
                report.python = m.group(1)
        elif line == "VLLM=yes":
            report.vllm = True
            report.vllm_version = "detected"
        elif line == "LMDEPLOY=yes":
            report.lmdeploy = True
            report.lmdeploy_version = "detected"
    report.available = saw
    if not saw:
        report.reason = "executable not found in WSL"

    usable: list[str] = ["pipeline"]
    if report.vllm:
        usable.extend(["vlm-engine", "hybrid-engine"])
    if report.lmdeploy and "hybrid-engine" not in usable:
        usable.append("hybrid-engine")
    report.backends_usable = usable
    return report

def _probe_ollama() -> dict[str, Any]:
    exe = _which("ollama")
    if not exe:
        return {"available": False, "reason": "ollama not on PATH"}
    code, out, err = _run_text([exe, "list"], timeout=20.0)
    if code != 0:
        return {"available": True, "reason": f"`ollama list` failed: {err.strip()}",
                "text_models": [], "vl_models": []}
    text_models: list[str] = []
    vl_models: list[str] = []
    for line in out.splitlines()[1:]:
        cols = line.split()
        if not cols:
            continue
        name = cols[0]
        # Heuristic: VL families we know how to flag.
        if any(tag in name.lower() for tag in ("vl", "vision", "llava", "minicpm-v", "qwen-vl", "qwen2-vl", "qwen2.5-vl", "internvl")):
            vl_models.append(name)
        else:
            text_models.append(name)
    return {"available": True, "text_models": text_models, "vl_models": vl_models}


def _config_error_payload(exc: Exception, *, command: str) -> dict[str, Any]:
    return {
        "local": True,
        "command": command,
        "error": str(exc),
        "error_code": "invalid_config",
    }


def doctor(args: argparse.Namespace) -> int:
    try:
        settings = ml_config.resolve_settings(args)
    except ml_config.ConfigError as exc:
        return _emit(_config_error_payload(exc, command="doctor"), code=2)

    requested_env = args.env or settings.default_env
    win = _probe_windows(settings.windows_executable or "")
    wsl = _probe_wsl(settings.wsl_distro or "", settings.wsl_executable or "")
    ollama = _probe_ollama()

    selected_env: Optional[str] = None
    selected_backend: Optional[str] = None
    diagnostics: list[dict[str, Any]] = []
    if requested_env == "windows":
        if win.available:
            selected_env = "windows"
            selected_backend = args.backend or "pipeline"
        else:
            diagnostics.append({
                "code": "windows_unavailable",
                "message": win.reason or "Windows MinerU is unavailable",
            })
    elif requested_env == "wsl":
        if wsl.available:
            selected_env = "wsl"
            if args.backend:
                selected_backend = args.backend
            else:
                selected_backend = "hybrid-engine" if "hybrid-engine" in wsl.backends_usable else "pipeline"
        else:
            diagnostics.append({
                "code": "wsl_unavailable",
                "message": wsl.reason or "WSL MinerU is unavailable",
            })
    else:
        if wsl.available:
            selected_env = "wsl"
            selected_backend = (
                args.backend
                or ("hybrid-engine" if "hybrid-engine" in wsl.backends_usable else "pipeline")
            )
        elif win.available:
            selected_env = "windows"
            selected_backend = args.backend or "pipeline"
        else:
            diagnostics.append({
                "code": "no_usable_environment",
                "message": "No configured MinerU environment is available",
            })

    if selected_env == "windows" and selected_backend not in (win.backends_usable or []):
        diagnostics.append({
            "code": "backend_unavailable",
            "message": f"Windows cannot run backend {selected_backend!r}",
            "details": {"usable": win.backends_usable},
        })
        selected_env = None
        selected_backend = None
    if selected_env == "wsl" and selected_backend not in (wsl.backends_usable or []):
        diagnostics.append({
            "code": "backend_unavailable",
            "message": f"WSL cannot run backend {selected_backend!r}",
            "details": {"usable": wsl.backends_usable},
        })
        selected_env = None
        selected_backend = None

    if not settings.config_path.exists():
        diagnostics.append({
            "code": "config_missing",
            "message": (
                f"persisted config not found at {settings.config_path}; "
                "supply --config, environment variables, or run `config set`"
            ),
        })

    payload = {
        "local": True,
        "command": "doctor",
        "tool_version": TOOL_VERSION,
        "config": settings.to_dict(),
        "config_exists": settings.config_path.exists(),
        "requested_environment": requested_env,
        "environments": {
            "windows": win.to_dict(),
            "wsl": wsl.to_dict(),
        },
        "ollama": ollama,
        "selected_environment": selected_env,
        "selected_backend": selected_backend,
        "diagnostics": diagnostics,
    }
    return _emit(payload, code=0)

def _check_loopback_url(value: str) -> str:
    if not value:
        return value
    m = re.match(r"^https?://([^/]+)(/.*)?$", value)
    if not m:
        raise ValueError(f"only http(s) URLs are accepted, got: {value!r}")
    host = m.group(1)
    # strip port; handle IPv6 literal [::1]:port
    if host.startswith("["):
        bracket = host.find("]")
        if bracket == -1:
            raise ValueError(f"malformed IPv6 host in url: {value!r}")
        host_only = host[:bracket + 1]
    else:
        host_only = host.split(":", 1)[0]

    if host_only not in ("127.0.0.1", "localhost", "::1"):
        try:
            infos = socket.getaddrinfo(host_only, None)
        except socket.gaierror as exc:
            raise ValueError(f"cannot resolve host {host_only!r}: {exc}") from exc
        for info in infos:
            sockaddr = info[4]
            ip = sockaddr[0]
            if not (ip.startswith("127.") or ip == "::1"):
                raise ValueError(
                    f"api-url must point to a loopback address, "
                    f"got {value!r} (resolves to {ip})"
                )
    return value


def _validate_backend(backend: str) -> str:
    if backend in FORBIDDEN_BACKENDS:
        raise ValueError(
            f"backend {backend!r} is forbidden in this skill: "
            "it requires a remote OpenAI-compatible server. Run the server "
            "locally on 127.0.0.1 if you must use http-client backends."
        )
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(
            f"unsupported backend {backend!r}; "
            f"choose one of {', '.join(SUPPORTED_BACKENDS)}"
        )
    return backend


def _effective_backend_for_input(
    input_path: Path, requested_backend: Optional[str]
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """Apply MinerU's Office policy before environment/backend selection."""
    if input_path.suffix.lower() not in OFFICE_SUFFIXES or requested_backend == "pipeline":
        return requested_backend, None
    override = {
        "used": True,
        "requested_backend": requested_backend,
        "actual_backend": "pipeline",
        "reason": "Office inputs are parsed with MinerU pipeline; method=ocr remains invalid",
    }
    return "pipeline", override


def _build_argv(
    *,
    executable: list[str],
    backend: str,
    method: str,
    input_path: str,
    output_dir: str,
    lang: Optional[str],
    start_page: Optional[int],
    end_page: Optional[int],
    formula: Optional[bool],
    table: Optional[bool],
    image_analysis: Optional[bool],
    effort: Optional[str],
) -> list[str]:
    argv = list(executable)
    argv += ["-p", input_path, "-o", output_dir, "-b", backend, "-m", method]
    if lang:
        argv += ["-l", lang]
    if start_page is not None:
        argv += ["-s", str(start_page)]
    if end_page is not None:
        argv += ["-e", str(end_page)]
    if formula is not None:
        argv += ["-f", "true" if formula else "false"]
    if table is not None:
        argv += ["-t", "true" if table else "false"]
    if image_analysis is not None:
        argv += ["--image-analysis", "true" if image_analysis else "false"]
    if backend.startswith("hybrid") and effort:
        argv += ["--effort", effort]
    return argv


def _build_env(base: dict[str, str], *, wsl: bool) -> dict[str, str]:
    env = dict(base)
    for var in REMOTE_TOKEN_VARS:
        env.pop(var, None)
    env.pop("MINERU_API_URL", None)
    if not env.get("OMP_NUM_THREADS"):
        env["OMP_NUM_THREADS"] = "1"
    if wsl:
        # ensure WSL child doesn't try to inherit Windows-only envs
        for k in list(env):
            if k.startswith("ANACONDA_") or k.startswith("CONDA_"):
                # OK to keep, harmless
                pass
    return env


# ---------------------------------------------------------------------------
# Parse / scan
# ---------------------------------------------------------------------------


def _list_artifacts(root: Path) -> dict[str, list[str]]:
    md: list[str] = []
    js: list[str] = []
    images: list[str] = []
    image_suffixes = (".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp", ".svg")
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        suf = p.suffix.lower()
        if suf == ".md":
            md.append(str(p))
        elif suf == ".json":
            js.append(str(p))
        elif suf in image_suffixes:
            images.append(str(p))
    return {"markdown": sorted(md), "json": sorted(js), "images": sorted(images)}


def _looks_non_empty(md_path: Path) -> bool:
    try:
        return md_path.stat().st_size > 0
    except OSError:
        return False


def _sanitize_report_extras(extras: dict[str, str]) -> dict[str, str]:
    """Strip anything resembling a remote token or URL from log/command text."""
    scrubbed: dict[str, str] = {}
    for k, v in extras.items():
        text = v or ""
        text = re.sub(r"MINERU_TOKEN=\S+", "MINERU_TOKEN=<redacted>", text)
        text = re.sub(r"https?://(?!127\.0\.0\.1|localhost|\[::1\])[^\s'\"\\]+",
                      "<remote-url-redacted>", text)
        scrubbed[k] = text
    return scrubbed


@dataclass
class ExecutionResult:
    returncode: int
    stdout: str
    stderr: str
    io_mode: str
    command: str
    input_used: str
    output_used: str
    input_sha256: str
    input_size_bytes: int


class RuntimeConfigError(RuntimeError):
    """Raised when configuration cannot resolve a runnable environment."""


def _configured_executable(settings: ml_config.RuntimeSettings, env_name: str) -> str:
    value = settings.windows_executable if env_name == "windows" else settings.wsl_executable
    if not value:
        raise RuntimeConfigError(f"{env_name} MinerU executable is not configured")
    return value


def _fingerprint_input(input_path: Path) -> tuple[str, int]:
    """Hash the source before staging and reject a file that changes mid-read."""
    before = input_path.stat()
    digest = ml_manifest.sha256_file(input_path)
    after = input_path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeConfigError(f"input changed while computing SHA256: {input_path}")
    return digest, after.st_size


def _make_wsl_temp(distro: str) -> str:
    code, out, err = _run_text(
        _wsl_translate_cmd(distro, "mktemp -d /tmp/mineru-local.XXXXXX"),
        timeout=30.0,
        env=_build_env(os.environ, wsl=True),
    )
    value = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if code != 0 or not re.fullmatch(r"/tmp/mineru-local\.[A-Za-z0-9]+", value):
        raise RuntimeConfigError(f"cannot create WSL temp directory: {err.strip() or out.strip()}")
    return value


def _copy_to_wsl(distro: str, host_source: Path, wsl_destination: str) -> None:
    source = _windows_to_wsl_path(str(host_source))
    inner = f"cp -f {shlex.quote(source)} {shlex.quote(wsl_destination)}"
    code, _out, err = _run_text(
        _wsl_translate_cmd(distro, inner),
        timeout=120.0,
        env=_build_env(os.environ, wsl=True),
    )
    if code != 0:
        raise RuntimeConfigError(f"cannot stage input into WSL: {err.strip()}")


def _verify_wsl_file_sha256(distro: str, wsl_path: str, expected_sha256: str) -> None:
    """Verify the staged WSL input before MinerU consumes it."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise RuntimeConfigError("expected staged input SHA256 is not a lowercase hex digest")
    if not re.fullmatch(r"/tmp/mineru-local\.[A-Za-z0-9]+/[^\r\n]+", wsl_path):
        raise RuntimeConfigError(f"refusing to hash unexpected WSL staged input: {wsl_path}")
    code, out, err = _run_text(
        _wsl_translate_cmd(distro, f"sha256sum -- {shlex.quote(wsl_path)}"),
        timeout=60.0,
        env=_build_env(os.environ, wsl=True),
    )
    if code != 0:
        raise RuntimeConfigError(f"cannot verify staged WSL input hash: {err.strip() or out.strip()}")
    first_line = out.strip().splitlines()[0] if out.strip() else ""
    match = re.match(r"^([0-9A-Fa-f]{64})\s+", first_line)
    if not match:
        raise RuntimeConfigError(f"cannot parse staged WSL input SHA256 output: {first_line[:200]}")
    actual = match.group(1).lower()
    if actual != expected_sha256:
        raise RuntimeConfigError(
            "staged WSL input SHA256 mismatch: "
            f"expected {expected_sha256}, got {actual}"
        )


def _copy_from_wsl(distro: str, wsl_source: str, host_destination: Path) -> None:
    if not re.fullmatch(r"/tmp/mineru-local\.[A-Za-z0-9]+/output", wsl_source):
        raise RuntimeConfigError(f"refusing to copy from unexpected WSL path: {wsl_source}")
    host_destination.mkdir(parents=True, exist_ok=True)
    destination = _windows_to_wsl_path(str(host_destination))
    inner = f"cp -a {shlex.quote(wsl_source + '/.')} {shlex.quote(destination + '/')}"
    code, _out, err = _run_text(
        _wsl_translate_cmd(distro, inner),
        timeout=300.0,
        env=_build_env(os.environ, wsl=True),
    )
    if code != 0:
        raise RuntimeConfigError(f"cannot copy staged output back from WSL: {err.strip()}")


def _cleanup_wsl_temp(distro: str, wsl_temp: str) -> None:
    if not re.fullmatch(r"/tmp/mineru-local\.[A-Za-z0-9]+", wsl_temp):
        raise RuntimeConfigError(f"refusing to remove unexpected WSL path: {wsl_temp}")
    code, out, err = _run_text(
        _wsl_translate_cmd(distro, f"rm -rf -- {shlex.quote(wsl_temp)}"),
        timeout=60.0,
        env=_build_env(os.environ, wsl=True),
    )
    if code != 0:
        raise RuntimeConfigError(
            f"cannot clean up staged WSL temp directory {wsl_temp}: "
            f"{err.strip() or out.strip() or f'exit {code}'}"
        )


def _command_repr(env_name: str, settings: ml_config.RuntimeSettings, argv: list[str]) -> str:
    rendered = " ".join(shlex.quote(part) for part in argv)
    if env_name == "wsl":
        return f"wsl -d {shlex.quote(settings.wsl_distro or '')} -- {rendered}"
    return rendered


def _execute_parse(
    *,
    env_name: str,
    settings: ml_config.RuntimeSettings,
    backend: str,
    method: str,
    input_path: Path,
    output_path: Path,
    lang: Optional[str],
    start_page: Optional[int],
    end_page: Optional[int],
    formula: Optional[bool],
    table: Optional[bool],
    image_analysis: Optional[bool],
    effort: Optional[str],
) -> ExecutionResult:
    input_sha256, input_size_bytes = _fingerprint_input(input_path)
    executable = _configured_executable(settings, env_name)
    requested_io = settings.io_policy
    io_mode = "native"
    wsl_temp: Optional[str] = None
    input_arg = str(input_path)
    output_arg = str(output_path)
    primary_error: Optional[BaseException] = None

    try:
        if env_name == "wsl":
            if not settings.wsl_distro:
                raise RuntimeConfigError("WSL distro is not configured")
            if requested_io == "native":
                io_mode = "native"
                input_arg = _translate_input_for("wsl", input_path)
                output_arg = _translate_output_for("wsl", output_path)
            else:
                io_mode = "staged"
                wsl_temp = _make_wsl_temp(settings.wsl_distro)
                staged_input = f"{wsl_temp}/{input_path.name}"
                output_arg = f"{wsl_temp}/output"
                _copy_to_wsl(settings.wsl_distro, input_path, staged_input)
                _verify_wsl_file_sha256(
                    settings.wsl_distro, staged_input, input_sha256
                )
                code, _out, err = _run_text(
                    _wsl_translate_cmd(settings.wsl_distro, f"mkdir -p {shlex.quote(output_arg)}"),
                    timeout=30.0,
                    env=_build_env(os.environ, wsl=True),
                )
                if code != 0:
                    raise RuntimeConfigError(f"cannot create staged WSL output directory: {err.strip()}")
                input_arg = staged_input

        argv = _build_argv(
            executable=[executable],
            backend=backend,
            method=method,
            input_path=input_arg,
            output_dir=output_arg,
            lang=lang,
            start_page=start_page,
            end_page=end_page,
            formula=formula,
            table=table,
            image_analysis=image_analysis,
            effort=effort,
        )
        rc, stdout, stderr = _run_argv(
            env_name,
            argv,
            cwd=None,
            distro=settings.wsl_distro,
        )
        if env_name == "wsl" and io_mode == "staged" and wsl_temp:
            _copy_from_wsl(settings.wsl_distro or "", f"{wsl_temp}/output", output_path)
        return ExecutionResult(
            returncode=rc,
            stdout=stdout,
            stderr=stderr,
            io_mode=io_mode,
            command=_command_repr(env_name, settings, argv),
            input_used=input_arg,
            output_used=output_arg,
            input_sha256=input_sha256,
            input_size_bytes=input_size_bytes,
        )
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if env_name == "wsl" and wsl_temp and settings.wsl_distro:
            try:
                _cleanup_wsl_temp(settings.wsl_distro, wsl_temp)
            except RuntimeConfigError as cleanup_exc:
                if primary_error is not None:
                    raise RuntimeConfigError(
                        f"{primary_error}; additionally, {cleanup_exc}"
                    ) from primary_error
                raise


def _smoke_test(
    env_name: str,
    backend: str,
    *,
    settings: ml_config.RuntimeSettings,
) -> tuple[bool, str]:
    """Probe whether (env_name, backend) can actually parse a file."""
    smoke_pdf = Path(__file__).resolve().parent / "tests" / "smoke.pdf"
    if not smoke_pdf.is_file():
        return (False, f"smoke.pdf not found at {smoke_pdf}")
    tmp_out = Path(tempfile.mkdtemp(prefix="mineru-smoke-"))
    try:
        result = _execute_parse(
            env_name=env_name,
            settings=settings,
            backend=backend,
            method="auto",
            input_path=smoke_pdf,
            output_path=tmp_out,
            lang=None,
            start_page=None,
            end_page=None,
            formula=None,
            table=None,
            image_analysis=None,
            effort=None,
        )
        if result.returncode != 0:
            last = [ln for ln in result.stderr.splitlines() if ln.strip()]
            return (False, f"exit {result.returncode}: {last[-1][:120] if last else ''}")
        md = list(tmp_out.rglob("*.md"))
        if not md:
            return (False, "no markdown output")
        if all(not _looks_non_empty(p) for p in md):
            return (False, "empty markdown output")
        return (True, "")
    except (OSError, RuntimeConfigError) as exc:
        return (False, str(exc))
    finally:
        shutil.rmtree(tmp_out, ignore_errors=True)


def _default_backend_for(report: EnvironmentReport, requested: Optional[str]) -> str:
    if requested:
        return requested
    if report.distro and "hybrid-engine" in (report.backends_usable or []):
        return "hybrid-engine"
    return "pipeline"


def _select_runtime(
    env_hint: str,
    backend: Optional[str],
    *,
    settings: Optional[ml_config.RuntimeSettings] = None,
) -> tuple[Optional[str], Optional[EnvironmentReport], Optional[EnvironmentReport], Optional[dict]]:
    """Resolve execution target without guessing across unavailable paths."""
    settings = settings or ml_config.resolve_settings()
    win = _probe_windows(settings.windows_executable or "")
    wsl = _probe_wsl(settings.wsl_distro or "", settings.wsl_executable or "")
    requested_env = env_hint
    requested_backend = backend

    if env_hint == "windows":
        if not win.available:
            raise RuntimeConfigError("--env windows requested but Windows mineru is not available")
        if backend and backend not in (win.backends_usable or []):
            raise RuntimeConfigError(
                f"--env windows + backend {backend!r} requested but Windows cannot run it; "
                f"Windows backends: {win.backends_usable}"
            )
        return ("windows", win, wsl, None)

    if env_hint == "wsl":
        if not wsl.available:
            raise RuntimeConfigError("--env wsl requested but WSL mineru is not available")
        if backend and backend not in (wsl.backends_usable or []):
            raise RuntimeConfigError(
                f"--env wsl + backend {backend!r} requested but WSL cannot run it; "
                f"WSL backends: {wsl.backends_usable}"
            )
        return ("wsl", win, wsl, None)

    attempts: list[dict[str, Any]] = []
    wsl_failure_reason = "WSL unavailable or failed smoke test"

    def _fallback(actual_env: str, actual_backend: str, reason: str) -> dict[str, Any]:
        return {
            "used": True,
            "requested_env": requested_env,
            "actual_env": actual_env,
            "requested_backend": requested_backend,
            "actual_backend": actual_backend,
            "reason": reason,
            "smoke_attempts": attempts,
        }

    wsl_usable = wsl.available and (
        not backend or backend in (wsl.backends_usable or [])
    )
    if wsl_usable:
        wsl_backend = _default_backend_for(wsl, backend)
        ok, err = _smoke_test("wsl", wsl_backend, settings=settings)
        attempts.append({"env": "wsl", "ok": ok, "error": err or None})
        if ok:
            return ("wsl", win, wsl, None)
        wsl_failure_reason = f"WSL smoke failed ({err or 'unknown error'})"
    elif not wsl.available:
        wsl_failure_reason = "WSL unavailable"
        attempts.append({"env": "wsl", "ok": False, "error": wsl.reason or "WSL unavailable"})
    else:
        wsl_failure_reason = f"WSL cannot run backend {backend!r}"
        attempts.append({
            "env": "wsl",
            "ok": False,
            "error": f"backend {backend!r} not in {wsl.backends_usable}",
        })

    win_backend: Optional[str] = None
    if win.available:
        if backend in (win.backends_usable or []):
            win_backend = backend
        elif "pipeline" in (win.backends_usable or []):
            win_backend = "pipeline"
    if win_backend:
        ok, err = _smoke_test("windows", win_backend, settings=settings)
        attempts.append({"env": "windows", "ok": ok, "error": err or None})
        if ok:
            reason = f"{wsl_failure_reason}; using Windows {win_backend}"
            return ("windows", win, wsl, _fallback("windows", win_backend, reason))
    else:
        attempts.append({
            "env": "windows",
            "ok": False,
            "error": win.reason or f"no usable Windows backend (have {win.backends_usable})",
        })

    return (
        None,
        win,
        wsl,
        _fallback(
            "none",
            "none",
            "no usable mineru environment (Windows+WSL both unavailable or smoke tests failed)",
        ),
    )

def _run_argv(
    env_name: str,
    argv: list[str],
    *,
    cwd: Optional[str],
    distro: Optional[str] = None,
) -> tuple[int, str, str]:
    if env_name == "windows":
        return _run_text(argv, timeout=None, env=_build_env(os.environ, wsl=False))
    if not distro:
        return 2, "", "WSL distro is not configured"
    inner = " ".join(shlex.quote(a) for a in argv)
    if cwd:
        inner = f"cd {shlex.quote(cwd)} && {inner}"
    return _run_text(
        _wsl_translate_cmd(distro, inner),
        timeout=None,
        env=_build_env(os.environ, wsl=True),
    )


def _executable_for(
    env_name: str,
    settings: Optional[ml_config.RuntimeSettings] = None,
) -> list[str]:
    settings = settings or ml_config.resolve_settings()
    return [_configured_executable(settings, env_name)]


def _translate_input_for(env_name: str, path: Path) -> str:
    p = str(path)
    if env_name == "wsl":
        return _windows_to_wsl_path(p)
    return p


def _translate_output_for(env_name: str, path: Path) -> str:
    return _translate_input_for(env_name, path)

DEFAULT_OUTPUT_BASENAME = "{date} minerUToMd"


def default_output_base() -> Path:
    """Resolve the default output parent directory.

    Precedence:
      1. $env:MINERU_LOCAL_OUTPUT_DIR (absolute path) - used as parent
      2. $env:MINERU_LOCAL_OUTPUT_DIR (relative path) - joined with cwd
      3. Path.cwd() (if writable) / "<date> minerUToMd"
      4. Fallback: tempfile.gettempdir() / "<date> minerUToMd"

    Auto-appends -2, -3, ... when the dated dir already exists.
    Does NOT create the directory; the caller does mkdir.
    """
    date = _dt.datetime.now().strftime("%Y-%m-%d")
    base_name = DEFAULT_OUTPUT_BASENAME.format(date=date)

    env_dir = os.environ.get("MINERU_LOCAL_OUTPUT_DIR")
    if env_dir:
        p = Path(env_dir)
        if not p.is_absolute():
            p = Path.cwd() / p
        if not p.is_dir():
            raise FileNotFoundError(
                f"MINERU_LOCAL_OUTPUT_DIR={p} is set but does not exist; "
                "create the directory or unset the env var."
            )
        parent = p
    else:
        if os.access(str(Path.cwd()), os.W_OK):
            parent = Path.cwd()
        else:
            parent = Path(tempfile.gettempdir())

    candidate = parent / base_name
    if not candidate.exists():
        return candidate
    n = 2
    while n < 1000:
        candidate = parent / f"{base_name}-{n}"
        if not candidate.exists():
            return candidate
        n += 1
    raise RuntimeError(f"too many {base_name}-* collisions in {parent}")


def _prepare_output_root(output_path: Path) -> tuple[bool, Optional[str]]:
    """Create a new/empty parse root; reject stale output contamination."""
    output_path = output_path.expanduser().resolve()
    if output_path.exists():
        if not output_path.is_dir():
            return False, f"output path is not a directory: {output_path}"
        try:
            if any(output_path.iterdir()):
                return False, (
                    f"output directory is not empty: {output_path}; "
                    "use a new --output path to avoid mixing runs"
                )
        except OSError as exc:
            return False, f"cannot inspect output directory {output_path}: {exc}"
    else:
        output_path.mkdir(parents=True, exist_ok=True)
    return True, None

def _build_output_layout(parent: Path, *, explicit: bool, batch: bool, fallback: Optional[dict] = None) -> dict:
    """Build the output_layout block for the JSON report."""
    parent_str = str(parent)
    if explicit:
        note = (
            "用户显式指定 --output，未做规范化。minerU 仍会嵌一层 "
            "<doc-stem>/<method>/。"
        )
    elif batch:
        note = (
            "目录下每个文件再嵌一层 <doc-stem>/<method>/；"
            "同日多次跑自动加 -2 / -3。"
        )
    else:
        note = "minerU 会把每个文件再嵌一层 <doc-stem>/<method>/。"
    if fallback and fallback.get("used"):
        note = note + f" 注意：{fallback.get('reason', 'fallback used')}"
    example = str(parent / "<doc-stem>" / "<method>" / "<doc-stem>.md")
    return {"parent": parent_str, "note": note, "example": example}


def _allocate_batch_output_dir(
    base_out: Path, relative_path: Path, used: set[str]
) -> Path:
    """Return a deterministic, case-aware, collision-safe per-input directory."""
    seed = base_out / relative_path.parent / relative_path.name
    candidate = seed
    index = 2
    while index < 1000:
        key = os.path.normcase(str(candidate.resolve(strict=False)))
        if key not in used and not candidate.exists():
            used.add(key)
            return candidate
        candidate = seed.with_name(f"{seed.name}-{index}")
        index += 1
    raise RuntimeConfigError(f"too many batch output collisions under {base_out}")


def _capture_parse_report(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Run the single-file command while capturing its one JSON report."""
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            rc = parse(args)
    except Exception as exc:  # Keep batch output parseable even when one item fails.
        return 1, {
            "error": f"parse raised {type(exc).__name__}: {exc}",
            "error_code": "parse_exception",
        }
    raw = buffer.getvalue()
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        return 1, {
            "error": f"parse did not emit exactly one valid JSON document: {exc}",
            "error_code": "invalid_parse_report",
            "raw_stdout_tail": raw[-2000:],
        }
    if not isinstance(payload, dict):
        return 1, {
            "error": "parse JSON report must be an object",
            "error_code": "invalid_parse_report",
        }
    return rc, payload


def parse(args: argparse.Namespace) -> int:
    try:
        settings = ml_config.resolve_settings(args)
    except ml_config.ConfigError as exc:
        return _emit(_config_error_payload(exc, command="parse"), code=2)

    requested_backend: Optional[str] = None
    try:
        requested_backend = _validate_backend(args.backend) if args.backend else None
        backend = requested_backend
    except ValueError as exc:
        return _emit({"error": str(exc)}, code=2)
    if args.api_url:
        try:
            _check_loopback_url(args.api_url)
        except ValueError as exc:
            return _emit({"error": str(exc)}, code=2)

    input_path = Path(args.path).expanduser().resolve()
    if not input_path.exists() or not input_path.is_file():
        return _emit({"error": f"input not found: {input_path}"}, code=2)
    suffix = input_path.suffix.lower()
    if suffix in LEGACY_CONVERSION_SUFFIXES:
        return _emit({
            "error": "legacy Office format requires explicit conversion before parsing",
            "error_code": "legacy_conversion_required",
            "suffix": suffix,
            "suggested_formats": [".pdf", ".docx", ".pptx"],
            "input": str(input_path),
        }, code=2)
    if suffix not in SUPPORTED_SUFFIXES:
        return _emit({"error": f"unsupported suffix: {input_path.suffix}"}, code=2)

    backend, backend_override = _effective_backend_for_input(input_path, backend)

    output_explicit = bool(args.output)
    output_path = (
        Path(args.output).expanduser().resolve()
        if output_explicit
        else default_output_base()
    )
    output_ready, output_error = _prepare_output_root(output_path)
    if not output_ready:
        return _emit({
            "error": output_error,
            "error_code": "output_not_empty",
            "output": str(output_path),
        }, code=2)

    env_hint = args.env or settings.default_env
    try:
        env_name, win, wsl, fallback_info = _select_runtime(
            env_hint,
            backend,
            settings=settings,
        )
    except RuntimeConfigError as exc:
        return _emit({
            "error": str(exc),
            "error_code": "runtime_unavailable",
            "config": settings.to_dict(),
        }, code=3)
    if env_name is None:
        payload: dict[str, Any] = {
            "error": "no usable mineru environment; run `doctor` for details",
            "error_code": "runtime_unavailable",
            "returncode": 6,
            "config": settings.to_dict(),
        }
        if fallback_info:
            payload["fallback"] = {"used": True, **fallback_info}
        return _emit(payload, code=6)

    report_env = wsl if env_name == "wsl" else win
    assert report_env is not None
    backend = _default_backend_for(report_env, backend)
    if backend not in (report_env.backends_usable or []):
        return _emit({
            "error": (
                f"backend {backend!r} is not usable in the {env_name} env. "
                f"Usable: {report_env.backends_usable}. "
                "Run `doctor` for details or pass --env / --backend."
            ),
            "error_code": "backend_unavailable",
        }, code=4)

    method = args.method
    if method not in SUPPORTED_METHODS:
        return _emit({"error": f"unsupported method {method!r}"}, code=2)
    if input_path.suffix.lower() in OFFICE_SUFFIXES and method == "ocr":
        return _emit({
            "error": "Office files do not accept method=ocr; use --method auto or txt"
        }, code=2)
    if args.lang and args.lang not in SUPPORTED_LANGS:
        return _emit({"error": f"unsupported lang {args.lang!r}"}, code=2)
    if args.effort and args.effort not in SUPPORTED_EFFORTS:
        return _emit({"error": f"unsupported effort {args.effort!r}"}, code=2)

    try:
        result = _execute_parse(
            env_name=env_name,
            settings=settings,
            backend=backend,
            method=method,
            input_path=input_path,
            output_path=output_path,
            lang=args.lang or None,
            start_page=args.start,
            end_page=args.end,
            formula=args.formula,
            table=args.table,
            image_analysis=args.image_analysis,
            effort=args.effort or None,
        )
    except (OSError, RuntimeConfigError) as exc:
        return _emit({
            "error": str(exc),
            "error_code": "execution_failed",
            "config": settings.to_dict(),
            "output": str(output_path),
        }, code=6)

    sanitized = _sanitize_report_extras({
        "stdout_tail": "\n".join((result.stdout or "").splitlines()[-30:]),
        "stderr_tail": "\n".join((result.stderr or "").splitlines()[-30:]),
    })
    execution = {
        "environment": env_name,
        "requested_environment": env_hint,
        "backend": backend,
        "method": method,
        "io_mode": result.io_mode,
        "wsl_distro": settings.wsl_distro if env_name == "wsl" else None,
        "mineru_version": report_env.mineru_version,
        "executable": _configured_executable(settings, env_name),
        "command": result.command,
        "returncode": result.returncode,
        "fallback": (
            {"used": True, **fallback_info}
            if fallback_info
            else {"used": False, "smoke_attempts": []}
        ),
        "config_sources": settings.sources,
        "stdout_tail": sanitized["stdout_tail"],
        "stderr_tail": sanitized["stderr_tail"],
    }
    if backend_override:
        execution["backend_override"] = backend_override

    try:
        manifest = ml_manifest.build_manifest(
            input_path=input_path,
            output_root=output_path,
            execution=execution,
            returncode=result.returncode,
            stderr=result.stderr,
            stdout=result.stdout,
            input_sha256=result.input_sha256,
            input_size_bytes=result.input_size_bytes,
        )
        manifest_path = ml_manifest.write_manifest(manifest, output_path)
    except (OSError, ValueError) as exc:
        return _emit({
            "error": f"failed to build MinerUArtifactManifest: {exc}",
            "error_code": "manifest_failed",
            "output": str(output_path),
            "returncode": result.returncode,
        }, code=7)

    artifacts = _list_artifacts(output_path)
    md_non_empty = [
        p for p in artifacts["markdown"]
        if Path(p).name not in {ml_manifest.MANIFEST_FILENAME, ml_manifest.OUTPUT_TREE_FILENAME}
        and _looks_non_empty(Path(p))
    ]
    errors = manifest.get("errors", [])
    success = result.returncode == 0 and not errors and manifest["markdown"]["primary"] is not None
    returncode = result.returncode
    if not success and returncode == 0:
        returncode = 5

    payload = {
        "local": True,
        "command": "parse",
        "tool_version": TOOL_VERSION,
        "environment": env_name,
        "requested_environment": env_hint,
        "executable": _configured_executable(settings, env_name),
        "mineru_version": report_env.mineru_version,
        "input": str(input_path),
        "output": str(output_path),
        "backend": backend,
        "requested_backend": requested_backend,
        "backend_override": backend_override,
        "method": method,
        "io_mode": result.io_mode,
        "returncode": returncode,
        "wsl": env_name == "wsl",
        "wsl_distro": settings.wsl_distro if env_name == "wsl" else None,
        "host_path": str(output_path),
        "config": settings.to_dict(),
        "artifacts": {
            "markdown": md_non_empty,
            "json": artifacts["json"],
            "images": artifacts["images"],
        },
        "manifest": {
            "path": str(manifest_path),
            "schema_version": ml_manifest.MANIFEST_SCHEMA_VERSION,
            "status": manifest.get("status"),
            "primary_markdown": (
                manifest["markdown"]["primary"]["path"]
                if manifest["markdown"].get("primary") else None
            ),
            "warning_count": len(manifest.get("warnings", [])),
            "error_count": len(manifest.get("errors", [])),
        },
        "output_layout": _build_output_layout(
            output_path,
            explicit=output_explicit,
            batch=False,
            fallback=fallback_info,
        ),
        "fallback": execution["fallback"],
        "command_repr": result.command,
        "stdout_tail": sanitized["stdout_tail"],
        "stderr_tail": sanitized["stderr_tail"],
    }
    if manifest.get("warnings"):
        payload["warning_samples"] = manifest["warnings"][:10]
    return _emit(payload, code=0 if success else returncode)


def manifest_from_existing(args: argparse.Namespace) -> int:
    """Regenerate a manifest from an existing raw output tree (offline path)."""
    if args.io_mode not in ml_config.RESOLVED_IO_MODES:
        return _emit({
            "error": "--io-mode must be one of: native, staged",
            "error_code": "invalid_io_mode",
        }, code=2)
    input_path = Path(args.path).expanduser().resolve()
    output_root = Path(args.output).expanduser().resolve()
    if not input_path.is_file():
        return _emit({"error": f"input not found: {input_path}"}, code=2)
    if not output_root.is_dir():
        return _emit({"error": f"raw output root not found: {output_root}"}, code=2)
    stdout = ""
    stderr = ""
    stdout_file = getattr(args, "stdout_file", None)
    stderr_file = getattr(args, "stderr_file", None)
    if stdout_file:
        try:
            stdout = Path(stdout_file).expanduser().resolve().read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError as exc:
            return _emit({"error": f"cannot read stdout sample: {exc}"}, code=2)
    if stderr_file:
        try:
            stderr = Path(stderr_file).expanduser().resolve().read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError as exc:
            return _emit({"error": f"cannot read stderr sample: {exc}"}, code=2)
    execution = {
        "environment": args.env or "unknown",
        "requested_environment": args.env or "unknown",
        "backend": args.backend or "unknown",
        "method": args.method or "unknown",
        "io_mode": args.io_mode,
        "wsl_distro": args.wsl_distro,
        "mineru_version": None,
        "executable": None,
        "command": None,
        "returncode": args.returncode,
        "fallback": {"used": False, "smoke_attempts": []},
        "config_sources": {"source": "offline-manifest"},
        "stdout_tail": _sanitize_report_extras({"stdout": stdout})["stdout"],
        "stderr_tail": _sanitize_report_extras({"stderr": stderr})["stderr"],
    }
    try:
        manifest = ml_manifest.build_manifest(
            input_path=input_path,
            output_root=output_root,
            execution=execution,
            returncode=args.returncode,
            stderr=stderr,
            stdout=stdout,
        )
        manifest_path = ml_manifest.write_manifest(manifest, output_root)
    except (OSError, ValueError) as exc:
        return _emit({"error": f"failed to build manifest: {exc}"}, code=7)
    primary = manifest["markdown"].get("primary")
    return _emit({
        "local": True,
        "command": "manifest",
        "manifest": {
            "path": str(manifest_path),
            "schema_version": ml_manifest.MANIFEST_SCHEMA_VERSION,
            "status": manifest["status"],
            "primary_markdown": primary["path"] if primary else None,
            "candidate_count": len(manifest["markdown"]["candidates"]),
            "asset_count": len(manifest["assets"]),
            "warning_count": len(manifest["warnings"]),
            "error_count": len(manifest["errors"]),
        },
        "manifest_document": manifest,
    }, code=0 if manifest["status"] != "failed" else 5)

def parse_dir(args: argparse.Namespace) -> int:
    folder = Path(args.path).expanduser().resolve()
    if not folder.is_dir():
        return _emit({"error": f"not a directory: {folder}"}, code=2)
    legacy_files = sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in LEGACY_CONVERSION_SUFFIXES
    )
    files = sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
    legacy_relative = [p.relative_to(folder).as_posix() for p in legacy_files]
    if not files:
        return _emit({
            "error": "no directly supported files under folder",
            "error_code": (
                "legacy_conversion_required"
                if legacy_files
                else "unsupported_input_format"
            ),
            "legacy_conversion_required": legacy_relative,
            "input": str(folder),
        }, code=2)
    base_out_explicit = bool(args.output)
    if base_out_explicit:
        base_out = Path(args.output).expanduser().resolve()
    else:
        base_out = default_output_base()
    base_out.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    failures = len(legacy_files)
    used_outputs: set[str] = set()
    for file_path in files:
        relative_path = file_path.relative_to(folder)
        try:
            per_out = _allocate_batch_output_dir(base_out, relative_path, used_outputs)
        except RuntimeConfigError as exc:
            results.append({
                "input": str(file_path),
                "relative_path": relative_path.as_posix(),
                "output": None,
                "returncode": 1,
                "report": {
                    "error": str(exc),
                    "error_code": "batch_output_collision",
                },
            })
            failures += 1
            continue
        sub_args = argparse.Namespace(**vars(args))
        sub_args.path = str(file_path)
        sub_args.output = str(per_out)
        rc, report = _capture_parse_report(sub_args)
        if rc != 0:
            failures += 1
        results.append({
            "input": str(file_path),
            "relative_path": relative_path.as_posix(),
            "output": str(per_out),
            "returncode": rc,
            "report": report,
        })

    summary = {
        "local": True,
        "command": "parse-dir",
        "input": str(folder),
        "output": str(base_out),
        "output_layout": _build_output_layout(
            base_out, explicit=base_out_explicit, batch=True
        ),
        "files": [str(p) for p in files],
        "results": results,
        "failures": failures,
        "legacy_conversion_required": legacy_relative,
    }
    return _emit(summary, code=0 if failures == 0 else 1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def config_command(args: argparse.Namespace) -> int:
    action = getattr(args, "config_action", None)
    if action == "show":
        try:
            settings = ml_config.resolve_settings(args)
            raw = ml_config.load_config(settings.config_path)
        except ml_config.ConfigError as exc:
            return _emit(_config_error_payload(exc, command="config show"), code=2)
        return _emit({
            "local": True,
            "command": "config show",
            "config_path": str(settings.config_path),
            "config_exists": settings.config_path.exists(),
            "config": raw,
            "resolved": settings.to_dict(),
        })

    if action == "set":
        updates = ml_config.config_updates_from_args(args)
        if not updates:
            return _emit({
                "error": "config set requires at least one config field"
            }, code=2)
        try:
            path = ml_config.save_config(updates, path=args.config)
            raw = ml_config.load_config(path)
        except ml_config.ConfigError as exc:
            return _emit(_config_error_payload(exc, command="config set"), code=2)
        return _emit({
            "local": True,
            "command": "config set",
            "config_path": str(path),
            "updated": updates,
            "config": raw,
        })

    return _emit({"error": f"unknown config action: {action!r}"}, code=2)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mineru_local")
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=None, help="Path to config.json")
    common.add_argument("--windows-executable", default=None)
    common.add_argument("--wsl-executable", default=None)
    common.add_argument("--wsl-distro", default=None)
    common.add_argument("--default-env", choices=("auto", "windows", "wsl"), default=None)
    common.add_argument("--io-policy", choices=("auto", "native", "staged"), default=None)

    p_doc = sub.add_parser("doctor", parents=[common])
    p_doc.add_argument("--env", choices=("auto", "windows", "wsl"), default=None)
    p_doc.add_argument("--backend", choices=SUPPORTED_BACKENDS + ("",), default="")
    p_doc.set_defaults(func=doctor)

    p_parse = sub.add_parser("parse", parents=[common])
    p_parse.add_argument("--path", required=True)
    p_parse.add_argument("--output")
    p_parse.add_argument("--env", choices=("auto", "windows", "wsl"), default=None)
    p_parse.add_argument("--backend", choices=SUPPORTED_BACKENDS, default="")
    p_parse.add_argument("--method", choices=SUPPORTED_METHODS, default="auto")
    p_parse.add_argument("--lang", choices=SUPPORTED_LANGS, default="")
    p_parse.add_argument("--start", type=int, default=None)
    p_parse.add_argument("--end", type=int, default=None)
    p_parse.add_argument("--formula", type=lambda v: v.lower() in ("1", "true", "yes"), default=None)
    p_parse.add_argument("--table", type=lambda v: v.lower() in ("1", "true", "yes"), default=None)
    p_parse.add_argument("--image-analysis", type=lambda v: v.lower() in ("1", "true", "yes"), default=None)
    p_parse.add_argument("--effort", choices=SUPPORTED_EFFORTS, default="")
    p_parse.add_argument("--api-url", default="")
    p_parse.set_defaults(func=parse)

    p_dir = sub.add_parser("parse-dir", parents=[common])
    p_dir.add_argument("--path", required=True)
    p_dir.add_argument("--output")
    p_dir.add_argument("--env", choices=("auto", "windows", "wsl"), default=None)
    p_dir.add_argument("--backend", choices=SUPPORTED_BACKENDS, default="")
    p_dir.add_argument("--method", choices=SUPPORTED_METHODS, default="auto")
    p_dir.add_argument("--lang", choices=SUPPORTED_LANGS, default="")
    p_dir.add_argument("--start", type=int, default=None)
    p_dir.add_argument("--end", type=int, default=None)
    p_dir.add_argument("--formula", type=lambda v: v.lower() in ("1", "true", "yes"), default=None)
    p_dir.add_argument("--table", type=lambda v: v.lower() in ("1", "true", "yes"), default=None)
    p_dir.add_argument("--image-analysis", type=lambda v: v.lower() in ("1", "true", "yes"), default=None)
    p_dir.add_argument("--effort", choices=SUPPORTED_EFFORTS, default="")
    p_dir.add_argument("--api-url", default="")
    p_dir.set_defaults(func=parse_dir)

    p_manifest = sub.add_parser("manifest", parents=[common])
    p_manifest.add_argument("--path", required=True)
    p_manifest.add_argument("--output", required=True, help="Existing raw output root")
    p_manifest.add_argument("--env", choices=("windows", "wsl", "unknown"), default="unknown")
    p_manifest.add_argument("--backend", choices=SUPPORTED_BACKENDS + ("unknown",), default="unknown")
    p_manifest.add_argument("--method", choices=SUPPORTED_METHODS + ("unknown",), default="unknown")
    p_manifest.add_argument("--returncode", type=int, default=0)
    p_manifest.add_argument("--stdout-file")
    p_manifest.add_argument("--stderr-file")
    p_manifest.add_argument("--io-mode", choices=ml_config.RESOLVED_IO_MODES, default="native")
    p_manifest.set_defaults(func=manifest_from_existing)

    p_config = sub.add_parser("config")
    config_sub = p_config.add_subparsers(dest="config_action", required=True)
    p_show = config_sub.add_parser("show", parents=[common])
    p_show.set_defaults(func=config_command)
    p_set = config_sub.add_parser("set", parents=[common])
    p_set.set_defaults(func=config_command)

    return parser

def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
