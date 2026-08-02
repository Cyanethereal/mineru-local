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
import datetime as _dt
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_WSL_DISTRO = os.environ.get("MINERU_WSL_DISTRO", "Ubuntu-24.04")
DEFAULT_WINDOWS_EXECUTABLE = os.environ.get(
    "MINERU_WINDOWS_EXECUTABLE",
    r"C:\Users\YOUR_USER\.conda\envs\mineru\Scripts\mineru.exe",
)
DEFAULT_WSL_EXECUTABLE = os.environ.get(
    "MINERU_WSL_EXECUTABLE",
    "/home/YOUR_USER/miniconda3/envs/mineru/bin/mineru",
)

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
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
)
OFFICE_SUFFIXES = (".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx")
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
    if not Path(executable).exists():
        return EnvironmentReport(available=False, executable=executable,
                                reason="executable not found")
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
        # On Windows the current MinerU picks lmdeploy for hybrid-engine, but
        # lmdeploy/turbomind currently fails on this GPU's kernel. We surface
        # that as a reason rather than pretending it works.
        report.reason = (
            "no vllm; hybrid-engine will pick lmdeploy (known CUDA kernel "
            "failure on RTX 5060 with lmdeploy 0.11.1)"
        )
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
    report = EnvironmentReport(available=False, executable=executable, distro=distro)
    pybin = shlex.quote(str(__import__("posixpath").join(str(__import__("posixpath").dirname(executable)), "python")))
    exe_q = shlex.quote(executable)
    vllm_q = shlex.quote("import importlib.util; print('VLLM=' + ('yes' if importlib.util.find_spec('vllm') is not None else 'no'))")
    lmd_q = shlex.quote("import importlib.util; print('LMDEPLOY=' + ('yes' if importlib.util.find_spec('lmdeploy') is not None else 'no'))")
    inner = (
        f"if [ ! -x {exe_q} ]; then echo STATUS=MISSING; exit 0; fi; "
        f"{exe_q} --version 2>/dev/null | head -1 | sed 's/^/MINERU=/'; "
        f"{pybin} -V 2>/dev/null | sed 's/^/PYVER=/'; "
        f"{pybin} -c {vllm_q} 2>/dev/null; "
        f"{pybin} -c {lmd_q} 2>/dev/null"
    )
    code, out, _ = _run_text(_wsl_translate_cmd(distro, inner), timeout=60.0)
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
        # MinerU on Linux prefers vllm when both are available; fall back here
        # only if vllm is missing.
        if "hybrid-engine" not in usable:
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


def doctor(args: argparse.Namespace) -> int:
    win = _probe_windows(args.windows_executable)
    wsl = _probe_wsl(args.wsl_distro, args.wsl_executable)
    ollama = _probe_ollama()

    selected_env: Optional[str] = None
    selected_backend: Optional[str] = None
    if args.env == "windows":
        selected_env = "windows"
    elif args.env == "wsl":
        selected_env = "wsl"
    else:
        if wsl.available and "hybrid-engine" in wsl.backends_usable:
            selected_env = "wsl"
        elif wsl.available:
            selected_env = "wsl"
        elif win.available:
            selected_env = "windows"
        else:
            selected_env = None

    if selected_env == "wsl":
        if args.backend:
            selected_backend = args.backend
        else:
            selected_backend = "hybrid-engine" if "hybrid-engine" in wsl.backends_usable else "pipeline"
    elif selected_env == "windows":
        selected_backend = args.backend or "pipeline"
    else:
        selected_backend = None

    payload = {
        "environments": {
            "windows": win.to_dict(),
            "wsl": wsl.to_dict(),
        },
        "ollama": ollama,
        "selected_environment": selected_env,
        "selected_backend": selected_backend,
    }
    return _emit(payload)


# ---------------------------------------------------------------------------
# Argument construction
# ---------------------------------------------------------------------------


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


def _select_runtime(env_hint: str, backend: str) -> tuple[Optional[str], Optional[EnvironmentReport], Optional[EnvironmentReport]]:
    win = _probe_windows(DEFAULT_WINDOWS_EXECUTABLE)
    wsl = _probe_wsl(DEFAULT_WSL_DISTRO, DEFAULT_WSL_EXECUTABLE)
    if env_hint == "windows":
        return ("windows", win, wsl)
    if env_hint == "wsl":
        return ("wsl", win, wsl)
    # auto
    if wsl.available and backend in wsl.backends_usable:
        return ("wsl", win, wsl)
    if wsl.available:
        return ("wsl", win, wsl)
    if win.available:
        return ("windows", win, wsl)
    return (None, win, wsl)


def _run_argv(
    env_name: str,
    argv: list[str],
    *,
    cwd: Optional[str],
) -> tuple[int, str, str]:
    if env_name == "windows":
        return _run_text(argv, timeout=None, env=_build_env(os.environ, wsl=False))
    # wsl
    inner = " ".join(shlex.quote(a) for a in argv)
    if cwd:
        inner = f"cd {shlex.quote(cwd)} && {inner}"
    return _run_text(_wsl_translate_cmd(DEFAULT_WSL_DISTRO, inner), timeout=None,
                     env=_build_env(os.environ, wsl=True))


def _executable_for(env_name: str) -> list[str]:
    if env_name == "windows":
        return [DEFAULT_WINDOWS_EXECUTABLE]
    return [DEFAULT_WSL_EXECUTABLE]


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


def _build_output_layout(parent: Path, *, explicit: bool, batch: bool) -> dict:
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
    example = str(parent / "<doc-stem>" / "<method>" / "<doc-stem>.md")
    return {"parent": parent_str, "note": note, "example": example}


def parse(args: argparse.Namespace) -> int:
    try:
        backend = _validate_backend(args.backend) if args.backend else None
    except ValueError as exc:
        return _emit({"error": str(exc)}, code=2)
    if args.api_url:
        try:
            _check_loopback_url(args.api_url)
        except ValueError as exc:
            return _emit({"error": str(exc)}, code=2)

    input_path = Path(args.path).expanduser().resolve()
    if not input_path.exists():
        return _emit({"error": f"input not found: {input_path}"}, code=2)
    if input_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return _emit(
            {"error": f"unsupported suffix: {input_path.suffix}"},
            code=2,
        )

    output_explicit = bool(args.output)
    if output_explicit:
        output_path = Path(args.output).expanduser().resolve()
    else:
        output_path = default_output_base()
    output_path.mkdir(parents=True, exist_ok=True)

    win = _probe_windows(DEFAULT_WINDOWS_EXECUTABLE)
    wsl = _probe_wsl(DEFAULT_WSL_DISTRO, DEFAULT_WSL_EXECUTABLE)
    env_name, win2, wsl2 = _select_runtime(args.env, backend or "pipeline")
    if env_name is None:
        return _emit(
            {"error": "neither Windows nor WSL conda mineru is available; run `doctor`"},
            code=3,
        )
    report_env = wsl2 if env_name == "wsl" else win2
    if backend is None:
        if env_name == "wsl":
            backend = "hybrid-engine" if "hybrid-engine" in (report_env.backends_usable or []) else "pipeline"
        else:
            backend = "pipeline"
    if backend not in (report_env.backends_usable or []):
        return _emit(
            {
                "error": (
                    f"backend {backend!r} is not usable in the {env_name} env. "
                    f"Usable: {report_env.backends_usable}. "
                    f"Run `doctor` for details or pass --env / --backend."
                )
            },
            code=4,
        )

    method = args.method
    if method not in SUPPORTED_METHODS:
        return _emit({"error": f"unsupported method {method!r}"}, code=2)
    if input_path.suffix.lower() in OFFICE_SUFFIXES and method == "ocr":
        return _emit(
            {"error": "Office files do not accept method=ocr; use --method auto or txt"},
            code=2,
        )
    if args.lang and args.lang not in SUPPORTED_LANGS:
        return _emit({"error": f"unsupported lang {args.lang!r}"}, code=2)
    if args.effort and args.effort not in SUPPORTED_EFFORTS:
        return _emit({"error": f"unsupported effort {args.effort!r}"}, code=2)

    input_arg = _translate_input_for(env_name, input_path)
    output_arg = _translate_output_for(env_name, output_path)
    executable = _executable_for(env_name)
    argv = _build_argv(
        executable=executable,
        backend=backend,
        method=method,
        input_path=input_arg,
        output_dir=output_arg,
        lang=args.lang,
        start_page=args.start,
        end_page=args.end,
        formula=args.formula,
        table=args.table,
        image_analysis=args.image_analysis,
        effort=args.effort,
    )
    cmd_repr = " ".join(shlex.quote(a) for a in argv)
    if env_name == "wsl":
        cmd_repr = f"wsl -d {DEFAULT_WSL_DISTRO} -- {cmd_repr}"
    returncode, stdout, stderr = _run_argv(env_name, argv, cwd=None)

    artifacts = _list_artifacts(output_path)
    md_non_empty = [p for p in artifacts["markdown"] if _looks_non_empty(Path(p))]

    success = returncode == 0 and len(md_non_empty) > 0
    if not success and returncode == 0:
        returncode = 5  # empty result
    payload = {
        "local": True,
        "environment": env_name,
        "executable": executable[0],
        "mineru_version": report_env.mineru_version,
        "input": str(input_path),
        "output": str(output_path),
        "backend": backend,
        "method": method,
        "returncode": returncode,
        "wsl": env_name == "wsl",
        "wsl_distro": DEFAULT_WSL_DISTRO if env_name == "wsl" else None,
        "host_path": str(output_path),
        "artifacts": {
            "markdown": md_non_empty,
            "json": artifacts["json"],
            "images": artifacts["images"],
        },
        "output_layout": _build_output_layout(
            output_path, explicit=output_explicit, batch=False
        ),
        "command": cmd_repr,
    }
    extras = _sanitize_report_extras({
        "stdout_tail": "\n".join((stdout or "").splitlines()[-30:]),
        "stderr_tail": "\n".join((stderr or "").splitlines()[-30:]),
    })
    payload.update(extras)
    return _emit(payload, code=0 if success else returncode)


def parse_dir(args: argparse.Namespace) -> int:
    folder = Path(args.path).expanduser().resolve()
    if not folder.is_dir():
        return _emit({"error": f"not a directory: {folder}"}, code=2)
    files = sorted(p for p in folder.rglob("*") if p.is_file()
                   and p.suffix.lower() in SUPPORTED_SUFFIXES)
    if not files:
        return _emit({"error": f"no supported files under {folder}"}, code=2)
    base_out_explicit = bool(args.output)
    if base_out_explicit:
        base_out = Path(args.output).expanduser().resolve()
    else:
        base_out = default_output_base()
    base_out.mkdir(parents=True, exist_ok=True)

    overall: list[dict[str, Any]] = []
    failures = 0
    for f in files:
        per_out = base_out / f.stem
        per_out.mkdir(parents=True, exist_ok=True)
        sub_args = argparse.Namespace(**vars(args))
        sub_args.path = str(f)
        sub_args.output = str(per_out)
        rc = parse(sub_args)
        if rc != 0:
            failures += 1
        # collect last printed JSON
    summary = {
        "local": True,
        "command": "parse-dir",
        "input": str(folder),
        "output": str(base_out),
        "output_layout": _build_output_layout(
            base_out, explicit=base_out_explicit, batch=True
        ),
        "files": [str(p) for p in files],
        "failures": failures,
    }
    return _emit(summary, code=0 if failures == 0 else 1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mineru_local")
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--windows-executable", default=DEFAULT_WINDOWS_EXECUTABLE)
    common.add_argument("--wsl-executable", default=DEFAULT_WSL_EXECUTABLE)
    common.add_argument("--wsl-distro", default=DEFAULT_WSL_DISTRO)

    p_doc = sub.add_parser("doctor", parents=[common])
    p_doc.add_argument("--env", choices=("auto", "windows", "wsl"), default="auto")
    p_doc.add_argument("--backend", choices=SUPPORTED_BACKENDS + ("",), default="")
    p_doc.set_defaults(func=doctor)

    p_parse = sub.add_parser("parse", parents=[common])
    p_parse.add_argument("--path", required=True)
    p_parse.add_argument("--output")
    p_parse.add_argument("--env", choices=("auto", "windows", "wsl"), default="auto")
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
    p_dir.add_argument("--env", choices=("auto", "windows", "wsl"), default="auto")
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

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
