"""lib_install.py

Install / validate / diagnose helpers for the mineru-local skill.

Pure standard library. No third-party dependencies. The single source of truth
for path discovery, skill validation, unit-test execution, mineru doctor
delegation, and documented-error-pattern matching.

Error pattern hints are based exclusively on the official MinerU README and
docs/{en,zh}/{faq,quick_start}/. The matcher NEVER prescribes a backend or
environment switch; it only reports the documented cause and points to the
official documentation anchor.

CLI:
    python lib_install.py install [--skill-root PATH]
    python lib_install.py validate
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# Path discovery
# ---------------------------------------------------------------------------


def find_python() -> Optional[str]:
    """Locate a Python interpreter.

    Search order:
      1. $env:PYTHON if set and executable
      2. `python` / `py -3` on PATH
      3. Common conda / anaconda install paths
    """
    env_py = os.environ.get("PYTHON")
    if env_py and Path(env_py).is_file():
        return env_py
    for cmd in ("python", "py"):
        hit = shutil.which(cmd)
        if hit:
            return hit
    home = os.environ.get("USERPROFILE") or str(Path.home())
    for cand in (
        Path(home) / "miniconda3" / "python.exe",
        Path(home) / "anaconda3" / "python.exe",
        Path("C:/ProgramData") / "anaconda3" / "python.exe",
        Path("C:/ProgramData") / "miniconda3" / "python.exe",
        Path(home) / ".conda" / "envs" / "mineru" / "python.exe",
    ):
        if cand.is_file():
            return str(cand)
    return None


def discover_codex_home() -> Optional[Path]:
    """Find the Codex skills home directory."""
    env = os.environ.get("CODEX_HOME")
    if env:
        p = Path(env)
        if p.is_dir():
            return p
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    for cand in (home / ".codex",):
        if cand.is_dir():
            return cand
    return None


def find_quick_validator(codex_home: Optional[Path] = None) -> Optional[str]:
    """Locate the system quick_validate.py inside the Codex skill-creator folder."""
    home = codex_home or discover_codex_home()
    if home is None:
        return None
    cand = home / "skills" / ".system" / "skill-creator" / "scripts" / "quick_validate.py"
    if cand.is_file():
        return str(cand)
    return None


def find_skill_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk upward from `start` (default: this file) looking for SKILL.md."""
    base = (start or Path(__file__).resolve()).parent
    for p in (base, *base.parents):
        if (p / "SKILL.md").is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# Documented error patterns
# ---------------------------------------------------------------------------

SCOPE_DOCTOR = "doctor"
SCOPE_STDOUT = "stdout"
SCOPE_STDERR = "stderr"
SCOPE_ENV = "env"


@dataclasses.dataclass(frozen=True)
class ErrorPattern:
    pattern: re.Pattern
    title: str
    cause: str
    remediation_doc: str
    scope: str


# All patterns below are tied to a specific section in MinerU's official docs.
# Adding a new pattern requires citing the source; otherwise it does not go in.
ERROR_PATTERNS: list[ErrorPattern] = [
    ErrorPattern(
        pattern=re.compile(
            r"ImportError:\s*libGL\.so\.1:\s*cannot open shared object file",
            re.IGNORECASE,
        ),
        title="WSL2 Ubuntu 22.04 缺 libgl",
        cause="WSL2 的 Ubuntu 22.04 镜像缺少 libgl 共享库。",
        remediation_doc=(
            "https://github.com/opendatalab/MinerU/blob/master/docs/zh/faq/index.md"
            "#wsl2-ubuntu2204-libgl"
        ),
        scope=SCOPE_STDERR,
    ),
    ErrorPattern(
        pattern=re.compile(
            r"no kernel image is available for execution on the device",
            re.IGNORECASE,
        ),
        title="GPU kernel 不可用（Blackwell + lmdeploy 已知问题）",
        cause=(
            "RTX 50 系（Blackwell）上 lmdeploy 0.11.1 + turbomind 可能没有匹配 "
            "当前驱动/CUDA 的预编译 kernel；官方 FAQ 未单独列出此错误，但同属 "
            "Blackwell + lmdeploy 生态。"
        ),
        remediation_doc=(
            "https://github.com/opendatalab/MinerU/blob/master/docs/zh/faq/index.md"
            "#windows-cuda-acceleration"
        ),
        scope=SCOPE_STDERR,
    ),
    ErrorPattern(
        pattern=re.compile(r"inference.*slow|slow.*inference|cuda.*not.*enabled", re.IGNORECASE),
        title="Windows 直接安装后推理慢",
        cause="Windows 上 CUDA 加速依赖未正确安装。",
        remediation_doc=(
            "https://github.com/opendatalab/MinerU/blob/master/docs/zh/faq/index.md"
            "#windows-cuda-acceleration"
        ),
        scope=SCOPE_STDERR,
    ),
    ErrorPattern(
        pattern=re.compile(r"missing\s+text|missing\s+char|fonts?-noto|fontconfig", re.IGNORECASE),
        title="Linux 缺 CJK 字体",
        cause="Linux 发行版缺 CJK 字体，PDF 渲染为图片时部分文字丢失。",
        remediation_doc=(
            "https://github.com/opendatalab/MinerU/blob/master/docs/zh/faq/index.md"
            "#linux-missing-text-cjk-fonts"
        ),
        scope=SCOPE_STDERR,
    ),
    ErrorPattern(
        pattern=re.compile(
            r"Neither vLLM nor LMDeploy is installed",
            re.IGNORECASE,
        ),
        title="vllm 与 lmdeploy 都未安装",
        cause=(
            "hybrid-engine / vlm-engine 后端需要至少一个 vllm 或 lmdeploy。"
        ),
        remediation_doc=(
            "https://github.com/opendatalab/MinerU/blob/master/docs/zh/quick_start/extension_modules.md"
        ),
        scope=SCOPE_STDERR,
    ),
    ErrorPattern(
        pattern=re.compile(
            r"Docker.*unsupported|docker.*not supported|cannot use docker",
            re.IGNORECASE,
        ),
        title="macOS 不支持 Docker 部署",
        cause="MinerU Docker 部署仅支持 Linux 与带 WSL2 的 Windows；macOS 上 Docker 无法访问 MPS/MLX。",
        remediation_doc=(
            "https://github.com/opendatalab/MinerU/blob/master/docs/en/quick_start/docker_deployment.md"
        ),
        scope=SCOPE_STDERR,
    ),
    ErrorPattern(
        pattern=re.compile(
            r"ModuleNotFoundError:\s*No module named 'vllm'",
            re.IGNORECASE,
        ),
        title="vllm 未安装（仅适用于 Windows conda）",
        cause="当前 conda 环境未安装 vllm 包，无法在 Windows 端启用 vlm-engine / hybrid-engine。",
        remediation_doc=(
            "https://github.com/opendatalab/MinerU/blob/master/docs/zh/quick_start/extension_modules.md"
        ),
        scope=SCOPE_ENV,
    ),
]


def diagnose(text: str, scope: str) -> list[dict[str, str]]:
    """Return hints for any documented error pattern matched in `text`.

    `scope` selects which patterns apply (stdout / stderr / doctor / env).
    The function NEVER invents causes; each entry references an official doc URL.
    """
    if not text:
        return []
    out: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    for pat in ERROR_PATTERNS:
        if pat.scope != scope:
            continue
        m = pat.pattern.search(text)
        if not m:
            continue
        if pat.title in seen_titles:
            continue
        seen_titles.add(pat.title)
        out.append({
            "title": pat.title,
            "cause": pat.cause,
            "remediation_doc": pat.remediation_doc,
            "matched_substring": m.group(0),
            "scope": scope,
        })
    return out


def diagnose_all(parts: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    """Run diagnose across multiple (scope, text) pairs."""
    out: list[dict[str, str]] = []
    for scope, text in parts:
        out.extend(diagnose(text, scope))
    return out


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, timeout: float = 60.0,
         env: Optional[dict[str, str]] = None) -> tuple[int, str, str]:
    try:
        cp = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, env=env,
        )
        return cp.returncode, cp.stdout or "", cp.stderr or ""
    except FileNotFoundError as exc:
        return 127, "", f"command not found: {exc}"
    except subprocess.TimeoutExpired as exc:
        so = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        se = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return 124, so, se + "\n[timeout]"


def _with_utf8(env: Optional[dict[str, str]] = None) -> dict[str, str]:
    base = dict(env or os.environ)
    base.setdefault("PYTHONUTF8", "1")
    base.setdefault("PYTHONIOENCODING", "utf-8")
    return base


# ---------------------------------------------------------------------------
# Step runners
# ---------------------------------------------------------------------------


def run_validate(skill_root: Path, validator: str, python: str) -> dict[str, Any]:
    code, out, err = _run([python, validator, str(skill_root)], timeout=60.0,
                          env=_with_utf8())
    ok = code == 0
    return {
        "ok": ok,
        "message": (out + err).strip() or ("Skill is valid!" if ok else f"exit={code}"),
        "returncode": code,
    }


def run_unit_tests(skill_root: Path, python: str) -> dict[str, Any]:
    tests_dir = skill_root / "scripts" / "tests"
    if not tests_dir.is_dir():
        return {"ok": False, "message": f"tests dir missing: {tests_dir}", "returncode": 5}
    code, out, err = _run(
        [python, "-m", "unittest", "discover",
         "-s", str(tests_dir), "-p", "test_mineru_local.py"],
        timeout=120.0, env=_with_utf8(),
    )
    text = (out + err).strip()
    m = re.search(r"Ran\s+(\d+)\s+tests?", text)
    n = m.group(1) if m else "?"
    return {
        "ok": code == 0,
        "message": text or f"exit={code}",
        "returncode": code,
        "tests_run": n,
    }


def run_mineru_doctor(skill_root: Path, python: str) -> dict[str, Any]:
    """Run the wrapper's `doctor` subcommand and capture any documented error patterns."""
    wrapper = skill_root / "scripts" / "mineru_local.py"
    if not wrapper.is_file():
        return {"ok": False, "message": f"wrapper missing: {wrapper}", "selected_environment": None}
    code, out, err = _run(
        [python, str(wrapper), "doctor"], timeout=120.0, env=_with_utf8(),
    )
    text = out + err
    diagnostics = diagnose_all([
        (SCOPE_DOCTOR, text),
        (SCOPE_STDOUT, out),
        (SCOPE_STDERR, err),
    ])
    # Parse the JSON body if present
    selected_env = None
    selected_backend = None
    try:
        m = re.search(r"\{.*\}", out, re.DOTALL)
        if m:
            payload = json.loads(m.group(0))
            selected_env = payload.get("selected_environment")
            selected_backend = payload.get("selected_backend")
    except (json.JSONDecodeError, ValueError):
        pass
    recommendation = None
    if code == 0 and selected_env == "windows" and wsl2 is not None and not wsl2.available:
        recommendation = (
            "Only Windows mineru is available; consider installing WSL Ubuntu + vLLM "
            "for hybrid-engine quality. See README '系统要求'."
        )
    return {
        "ok": code == 0,
        "returncode": code,
        "selected_environment": selected_env,
        "selected_backend": selected_backend,
        "diagnostics": diagnostics,
        "recommendation": recommendation,
    }


# ---------------------------------------------------------------------------
# Top-level install orchestrator
# ---------------------------------------------------------------------------


def install(skill_root: Optional[Path] = None) -> dict[str, Any]:
    root = skill_root or find_skill_root()
    if root is None:
        return {
            "skill_root": None,
            "python": None,
            "validator": None,
            "steps": {},
            "diagnostics": [],
            "error": "skill root not found (no SKILL.md above this script)",
        }
    py = find_python()
    validator = find_quick_validator()
    diagnostics: list[dict[str, str]] = []

    if py is None:
        diagnostics.append({
            "title": "找不到 Python",
            "cause": "$env:PYTHON 与 PATH 中都没有 python；常见安装路径也未命中。",
            "remediation_doc": "https://www.python.org/downloads/",
            "scope": SCOPE_ENV,
        })
    if validator is None:
        diagnostics.append({
            "title": "找不到 quick_validate.py",
            "cause": "未在 $env:CODEX_HOME 或 ~/.codex/skills/.system/skill-creator/scripts/ 下找到。",
            "remediation_doc": "https://github.com/openai/codex",
            "scope": SCOPE_ENV,
        })

    steps: dict[str, Any] = {}
    if py and validator:
        steps["validate_skill"] = run_validate(root, validator, py)
    if py:
        steps["unit_tests"] = run_unit_tests(root, py)
        steps["mineru_doctor"] = run_mineru_doctor(root, py)
        # surface doctor diagnostics at top level too
        for d in steps["mineru_doctor"].get("diagnostics", []):
            diagnostics.append(d)

    overall_ok = (
        steps.get("validate_skill", {}).get("ok", False) is True
        and steps.get("unit_tests", {}).get("ok", False) is True
    )
    return {
        "skill_root": str(root),
        "python": py,
        "validator": validator,
        "steps": steps,
        "diagnostics": diagnostics,
        "ok": overall_ok,
    }


def validate(skill_root: Optional[Path] = None) -> dict[str, Any]:
    """Strict validate-only entry: just runs quick_validate.py."""
    root = skill_root or find_skill_root()
    if root is None:
        return {"ok": False, "error": "skill root not found"}
    py = find_python()
    validator = find_quick_validator()
    if not py or not validator:
        return {"ok": False, "error": "python or validator not found",
                "python": py, "validator": validator}
    return run_validate(root, validator, py)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lib_install")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_install = sub.add_parser("install")
    p_install.add_argument("--skill-root")
    p_install.set_defaults(func=lambda a: _emit(install(Path(a.skill_root) if a.skill_root else None)))
    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--skill-root")
    p_validate.set_defaults(func=lambda a: _emit(validate(Path(a.skill_root) if a.skill_root else None)))
    return p


def _emit(payload: dict[str, Any]) -> int:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if payload.get("ok") is not False else 1


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())