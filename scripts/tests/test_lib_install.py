"""Unit tests for lib_install.py. Standard library only.

Run:
    python -m unittest tests.test_lib_install -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve()
SCRIPT_DIR = HERE.parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import lib_install as li  # noqa: E402


class PathDiscoveryTests(unittest.TestCase):
    def test_find_python_uses_env_first(self):
        with mock.patch.dict(os.environ, {"PYTHON": r"C:\custom\python.exe"}):
            with mock.patch.object(li.Path, "is_file", return_value=True):
                self.assertEqual(li.find_python(), r"C:\custom\python.exe")

    def test_find_python_falls_back_to_path(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PYTHON", None)
            with mock.patch.object(li.shutil, "which", return_value=r"C:\Windows\py.exe"):
                self.assertEqual(li.find_python(), r"C:\Windows\py.exe")

    def test_find_quick_validator_when_present(self):
        with mock.patch.object(li, "discover_codex_home",
                               return_value=Path(r"C:\Users\test\.codex")):
            fake = (Path(r"C:\Users\test\.codex") / "skills" / ".system"
                    / "skill-creator" / "scripts" / "quick_validate.py")
            with mock.patch.object(li.Path, "is_file", return_value=True):
                self.assertEqual(li.find_quick_validator(), str(fake))

    def test_find_quick_validator_when_absent(self):
        with mock.patch.object(li, "discover_codex_home", return_value=Path(r"C:\none")):
            with mock.patch.object(li.Path, "is_file", return_value=False):
                self.assertIsNone(li.find_quick_validator())

    def test_find_skill_root_walks_up(self):
        # Locate the real skill root from this test file
        root = li.find_skill_root(HERE)
        self.assertIsNotNone(root)
        self.assertTrue((root / "SKILL.md").is_file())


class DiagnoseTests(unittest.TestCase):
    def test_libgl_pattern_matches(self):
        text = "ImportError: libGL.so.1: cannot open shared object file: No such file"
        hits = li.diagnose(text, scope=li.SCOPE_STDERR)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["title"], "WSL2 Ubuntu 22.04 缺 libgl")
        self.assertIn("wsl2-ubuntu2204-libgl", hits[0]["remediation_doc"])

    def test_blackwell_kernel_pattern_matches(self):
        text = "[TM][FATAL] kernels/gemm/tuner/measurer.cu(83): no kernel image is available for execution on the device"
        hits = li.diagnose(text, scope=li.SCOPE_STDERR)
        self.assertTrue(any("Blackwell" in h["title"] for h in hits))

    def test_unrelated_text_returns_empty(self):
        self.assertEqual(li.diagnose("hello world", li.SCOPE_STDERR), [])
        self.assertEqual(li.diagnose("", li.SCOPE_STDERR), [])

    def test_scope_filter(self):
        # The libGL pattern is scope=stderr; should not match if passed to scope=stdout
        text = "ImportError: libGL.so.1: cannot open shared object file"
        self.assertEqual(li.diagnose(text, li.SCOPE_STDOUT), [])
        self.assertEqual(len(li.diagnose(text, li.SCOPE_STDERR)), 1)

    def test_no_prescriptive_language(self):
        """Diagnostic messages must reference official docs, not prescribe backends."""
        for pat in li.ERROR_PATTERNS:
            self.assertIn("http", pat.remediation_doc,
                          f"{pat.title!r} has no doc link")
            for forbidden in ("切换到", "请用", "use backend", "switch to"):
                self.assertNotIn(forbidden.lower(), pat.cause.lower(),
                                 f"{pat.title!r} cause contains prescriptive phrase {forbidden!r}")

    def test_all_patterns_have_unique_titles(self):
        titles = [p.title for p in li.ERROR_PATTERNS]
        self.assertEqual(len(titles), len(set(titles)))

    def test_diagnose_all_combines_scopes(self):
        text = "ImportError: libGL.so.1: cannot open shared object file"
        parts = [(li.SCOPE_DOCTOR, "no match"),
                 (li.SCOPE_STDERR, text)]
        out = li.diagnose_all(parts)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["scope"], li.SCOPE_STDERR)


class InstallOrchestratorTests(unittest.TestCase):
    def test_install_no_skill_root_returns_error(self):
        with mock.patch.object(li, "find_skill_root", return_value=None):
            r = li.install()
            self.assertIsNone(r["skill_root"])
            self.assertIn("error", r)
            self.assertFalse(r.get("ok", False))

    def test_install_no_python_or_validator_diagnostics(self):
        with mock.patch.object(li, "find_skill_root", return_value=Path(r"C:\fake\skill")):
            with mock.patch.object(li, "find_python", return_value=None):
                with mock.patch.object(li, "find_quick_validator", return_value=None):
                    r = li.install()
        self.assertFalse(r.get("ok", False))
        titles = [d["title"] for d in r["diagnostics"]]
        self.assertIn("找不到 Python", titles)
        self.assertIn("找不到 quick_validate.py", titles)


if __name__ == "__main__":
    unittest.main()