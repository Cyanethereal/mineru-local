# -*- coding: utf-8 -*-
"""Unit tests for mineru_local.py. Standard library only.

Run:
    python -m unittest tests.test_mineru_local -v
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve()
SCRIPT_DIR = HERE.parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import mineru_local as ml  # noqa: E402


# ---------------------------------------------------------------------------
# Existing tests (from v1.0)
# ---------------------------------------------------------------------------

class PathConversionTests(unittest.TestCase):
    def test_windows_to_wsl_basic(self):
        self.assertEqual(ml._windows_to_wsl_path(r"D:\foo\bar.pdf"),
                         "/mnt/d/foo/bar.pdf")

    def test_windows_to_wsl_lowercase_drive(self):
        self.assertEqual(ml._windows_to_wsl_path(r"c:\Users\me\doc.pdf"),
                         "/mnt/c/Users/me/doc.pdf")

    def test_windows_to_wsl_mixed_separators(self):
        self.assertEqual(ml._windows_to_wsl_path(r"E:/a/b/c.docx"),
                         "/mnt/e/a/b/c.docx")

    def test_windows_to_wsl_passthrough(self):
        self.assertEqual(ml._windows_to_wsl_path("/already/posix"),
                         "/already/posix")

    def test_windows_to_wsl_unc_unchanged(self):
        self.assertEqual(ml._windows_to_wsl_path(r"\\server\share\file"),
                         "//server/share/file")


class ApiUrlGuardTests(unittest.TestCase):
    def test_loopback_ok(self):
        self.assertEqual(ml._check_loopback_url("http://127.0.0.1:30000/v1"),
                         "http://127.0.0.1:30000/v1")
        self.assertEqual(ml._check_loopback_url("http://localhost:8000"),
                         "http://localhost:8000")
        self.assertEqual(ml._check_loopback_url("http://[::1]:9000"),
                         "http://[::1]:9000")

    def test_remote_rejected(self):
        with self.assertRaises(ValueError):
            ml._check_loopback_url("https://api.mineru.net/v4/extract")
        with self.assertRaises(ValueError):
            ml._check_loopback_url("http://192.168.1.10:8000")
        with self.assertRaises(ValueError):
            ml._check_loopback_url("http://10.0.0.5/health")

    def test_dns_resolution_remote(self):
        with self.assertRaises(ValueError):
            ml._check_loopback_url("https://www.example.com/anything")

    def test_invalid_scheme(self):
        with self.assertRaises(ValueError):
            ml._check_loopback_url("file:///etc/passwd")


class BackendValidationTests(unittest.TestCase):
    def test_supported_backends(self):
        for b in ml.SUPPORTED_BACKENDS:
            self.assertEqual(ml._validate_backend(b), b)

    def test_forbidden_backends(self):
        for b in ml.FORBIDDEN_BACKENDS:
            with self.assertRaises(ValueError):
                ml._validate_backend(b)

    def test_unknown_backend(self):
        with self.assertRaises(ValueError):
            ml._validate_backend("not-a-real-backend")


class ArgvBuilderTests(unittest.TestCase):
    def test_minimal_argv(self):
        argv = ml._build_argv(
            executable=[r"C:\mineru.exe"],
            backend="pipeline",
            method="auto",
            input_path="C:\\path\\in.pdf",
            output_dir="C:\\path\\out",
            lang=None,
            start_page=None,
            end_page=None,
            formula=None,
            table=None,
            image_analysis=None,
            effort=None,
        )
        self.assertEqual(argv[:2], [r"C:\mineru.exe", "-p"])
        self.assertNotIn("--api-url", argv)
        self.assertNotIn("-u", argv)
        self.assertFalse(any(b in ml.FORBIDDEN_BACKENDS for b in argv))

    def test_hybrid_argv_includes_effort(self):
        argv = ml._build_argv(
            executable=["mineru"],
            backend="hybrid-engine",
            method="auto",
            input_path="in.pdf",
            output_dir="out",
            lang=None,
            start_page=None,
            end_page=None,
            formula=None,
            table=None,
            image_analysis=None,
            effort="medium",
        )
        self.assertIn("--effort", argv)
        self.assertIn("medium", argv)


class EnvScrubbingTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "PATH": "C:\\Windows",
            "MINERU_MODEL_SOURCE": "modelscope",
            "MINERU_TOKEN": "secret-token",
            "MINERU_API_URL": "https://api.mineru.net",
            "MINERU_API_TOKEN": "other-secret",
            "CUDA_VISIBLE_DEVICES": "0",
        }

    def test_remote_vars_stripped(self):
        env = ml._build_env(self.base, wsl=False)
        for v in ml.REMOTE_TOKEN_VARS:
            self.assertNotIn(v, env)
        self.assertEqual(env.get("MINERU_MODEL_SOURCE"), "modelscope")
        self.assertEqual(env.get("CUDA_VISIBLE_DEVICES"), "0")

    def test_omp_default_set(self):
        env = ml._build_env({"PATH": "x"}, wsl=False)
        self.assertEqual(env["OMP_NUM_THREADS"], "1")

    def test_omp_preserved_if_user_set(self):
        base = dict(self.base, OMP_NUM_THREADS="4")
        env = ml._build_env(base, wsl=False)
        self.assertEqual(env["OMP_NUM_THREADS"], "4")


class ReportScrubbingTests(unittest.TestCase):
    def test_remote_urls_redacted(self):
        out = ml._sanitize_report_extras({
            "stderr": "https://api.mineru.net/api/v4/extract/task http://127.0.0.1:30000/v1"
        })
        self.assertIn("<remote-url-redacted>", out["stderr"])
        self.assertIn("http://127.0.0.1:30000/v1", out["stderr"])

    def test_token_redacted(self):
        out = ml._sanitize_report_extras({
            "log": "Authorization header: MINERU_TOKEN=eyJhbGciOiJIUzI1NiJ9"
        })
        self.assertIn("MINERU_TOKEN=<redacted>", out["log"])
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", out["log"])


class ArtifactListingTests(unittest.TestCase):
    def test_finds_markdown_json_and_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.md").write_text("hello", encoding="utf-8")
            (root / "b.JSON").write_text("{}", encoding="utf-8")
            (root / "img.png").write_bytes(b"\x89PNG")
            (root / "img.jpg").write_bytes(b"\xff\xd8")
            (root / "ignore.txt").write_text("no", encoding="utf-8")
            arts = ml._list_artifacts(root)
            names = {Path(p).name for p in arts["markdown"]}
            self.assertIn("a.md", names)
            self.assertIn("b.JSON", {Path(p).name for p in arts["json"]})
            self.assertEqual(len(arts["images"]), 2)

    def test_empty_markdown_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md = root / "x.md"
            md.write_text("", encoding="utf-8")
            self.assertFalse(ml._looks_non_empty(md))


class DoctorSelectionTests(unittest.TestCase):
    def test_auto_prefers_wsl(self):
        win = ml.EnvironmentReport(available=True, executable="win",
                                   backends_usable=["pipeline"])
        wsl = ml.EnvironmentReport(available=True, executable="wsl",
                                   backends_usable=["pipeline", "vlm-engine", "hybrid-engine"],
                                   distro="Ubuntu-24.04")

        def fake_probe_win(_): return win
        def fake_probe_wsl(_, __): return wsl
        ml._probe_windows = fake_probe_win  # type: ignore
        ml._probe_wsl = fake_probe_wsl      # type: ignore

        captured = {}

        def fake_emit(payload, *, code=0):
            captured.update(payload)
            return 0
        ml._emit = fake_emit  # type: ignore

        rc = ml.doctor(ml.build_parser().parse_args(["doctor"]))
        self.assertEqual(rc, 0)
        self.assertEqual(captured["selected_environment"], "wsl")
        self.assertEqual(captured["selected_backend"], "hybrid-engine")

    def test_force_windows(self):
        win = ml.EnvironmentReport(available=True, executable="win",
                                   backends_usable=["pipeline"])
        wsl = ml.EnvironmentReport(available=True, executable="wsl",
                                   backends_usable=["pipeline", "hybrid-engine"],
                                   distro="Ubuntu-24.04")
        ml._probe_windows = lambda _: win  # type: ignore
        ml._probe_wsl = lambda _, __: wsl  # type: ignore
        captured = {}
        ml._emit = lambda p, code=0: (captured.update(p) or 0)  # type: ignore

        ml.doctor(ml.build_parser().parse_args(["doctor", "--env", "windows"]))
        self.assertEqual(captured["selected_environment"], "windows")
        self.assertEqual(captured["selected_backend"], "pipeline")

    def test_wsl_unavailable_falls_back(self):
        win = ml.EnvironmentReport(available=True, executable="win",
                                   backends_usable=["pipeline"])
        wsl = ml.EnvironmentReport(available=False, executable="wsl",
                                   reason="missing")
        settings = ml.ml_config.RuntimeSettings(
            windows_executable=None,
            wsl_executable=None,
            wsl_distro=None,
            default_env="auto",
            io_policy="auto",
            config_path=Path("config.json"),
            sources={},
        )
        ml._probe_windows = lambda _: win  # type: ignore
        ml._probe_wsl = lambda _, __: wsl  # type: ignore
        captured = {}
        ml._emit = lambda p, code=0: (captured.update(p) or 0)  # type: ignore
        with mock.patch.object(ml.ml_config, "resolve_settings", return_value=settings):
            ml.doctor(ml.build_parser().parse_args(["doctor"]))
        self.assertEqual(captured["selected_environment"], "windows")
        self.assertEqual(captured["selected_backend"], "pipeline")


# ---------------------------------------------------------------------------
# v1.2: output directory normalisation
# ---------------------------------------------------------------------------

class DefaultOutputBaseTests(unittest.TestCase):
    """default_output_base precedence and collision-avoidance behaviour."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="mineru-local-test-")
        self._cwd = os.getcwd()
        os.chdir(self._tmp)
        self._saved_env = {}
        for v in ("MINERU_LOCAL_OUTPUT_DIR",):
            self._saved_env[v] = os.environ.pop(v, None)

    def tearDown(self):
        os.chdir(self._cwd)
        for v, val in self._saved_env.items():
            if val is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = val
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_default_under_cwd(self):
        out = ml.default_output_base()
        self.assertTrue(str(out).startswith(self._tmp))
        self.assertTrue(out.name.endswith(" minerUToMd"))
        import re
        self.assertRegex(out.name, r"^\d{4}-\d{2}-\d{2} minerUToMd$")

    def test_env_absolute_path(self):
        target = os.path.join(self._tmp, "explicit-root")
        os.makedirs(target)
        os.environ["MINERU_LOCAL_OUTPUT_DIR"] = target
        out = ml.default_output_base()
        self.assertEqual(out.parent, Path(target))

    def test_env_relative_path_joined_with_cwd(self):
        rel = "rel-sub"
        target = Path(self._tmp) / rel
        target.mkdir()
        os.environ["MINERU_LOCAL_OUTPUT_DIR"] = rel
        out = ml.default_output_base()
        self.assertEqual(out.parent, target)

    def test_env_absolute_missing_raises(self):
        os.environ["MINERU_LOCAL_OUTPUT_DIR"] = os.path.join(self._tmp, "no-such-dir")
        with self.assertRaises(FileNotFoundError):
            ml.default_output_base()

    def test_collision_creates_dash_two(self):
        first = ml.default_output_base()
        first.mkdir()
        second = ml.default_output_base()
        self.assertNotEqual(first, second)
        self.assertEqual(second.name, first.name + "-2")

    def test_three_runs_yield_dash_three(self):
        first = ml.default_output_base(); first.mkdir()
        second = ml.default_output_base(); second.mkdir()
        third = ml.default_output_base()
        self.assertEqual(third.name, first.name + "-3")


class DefaultOutputLayoutTests(unittest.TestCase):
    """_build_output_layout shape for explicit / single / batch cases."""

    def test_explicit_layout(self):
        layout = ml._build_output_layout(Path("/tmp/x"), explicit=True, batch=False)
        self.assertEqual(layout["parent"], os.fspath(Path("/tmp/x")))
        # explicit note differs from the auto single-file note (different length, or different content)
        auto_single = ml._build_output_layout(Path("/tmp/x"), explicit=False, batch=False)
        self.assertNotEqual(layout["note"], auto_single["note"])
        # and the example always shows minerU internal layout
        self.assertIn("<doc-stem>", layout["example"])

    def test_single_auto_layout(self):
        layout = ml._build_output_layout(Path("/tmp/x"), explicit=False, batch=False)
        self.assertEqual(layout["parent"], os.fspath(Path("/tmp/x")))
        # the auto single-file note references minerU nesting
        self.assertIn("minerU", layout["note"])
        self.assertIn("<doc-stem>", layout["example"])

    def test_batch_auto_layout(self):
        layout = ml._build_output_layout(Path("/tmp/x"), explicit=False, batch=True)
        self.assertEqual(layout["parent"], os.fspath(Path("/tmp/x")))
        # the auto batch note mentions -2 collision suffix
        self.assertIn("-2", layout["note"])
        self.assertIn("<doc-stem>", layout["example"])




class SelectRuntimeFallbackTests(unittest.TestCase):
    """_select_runtime return contract: (env, win, wsl, fallback_info)."""

    def setUp(self):
        from unittest import mock
        self._mocks = []
        self.win_ok = ml.EnvironmentReport(available=True, executable="ignored",
                                           backends_usable=["pipeline"])
        self.win_limited = ml.EnvironmentReport(available=True, executable="ignored",
                                                backends_usable=["pipeline"])
        self.wsl_full = ml.EnvironmentReport(available=True, executable="ignored",
                                             backends_usable=["pipeline", "vlm-engine", "hybrid-engine"],
                                             distro="Ubuntu-24.04")
        self.wsl_no_vllm = ml.EnvironmentReport(available=True, executable="ignored",
                                                backends_usable=["pipeline"],
                                                distro="Ubuntu-24.04")
        self.wsl_unavail = ml.EnvironmentReport(available=False, executable="ignored",
                                               reason="no wsl")
        self.win_unavail = ml.EnvironmentReport(available=False, executable="ignored",
                                               reason="no windows mineru")

    def _patch(self, win, wsl, smoke_results):
        from unittest import mock
        idx = [0]
        def fake_smoke(env, backend, **kwargs):
            if idx[0] >= len(smoke_results):
                return (True, "")
            r = smoke_results[idx[0]]
            idx[0] += 1
            return r
        p1 = mock.patch.object(ml, "_probe_windows", return_value=win)
        p2 = mock.patch.object(ml, "_probe_wsl", return_value=wsl)
        p3 = mock.patch.object(ml, "_smoke_test", side_effect=fake_smoke)
        p1.start(); p2.start(); p3.start()
        self._mocks.extend([p1, p2, p3])

    def tearDown(self):
        from unittest import mock
        for p in self._mocks: p.stop()
        self._mocks.clear()

    def test_auto_prefers_wsl_smoke_passes(self):
        self._patch(self.win_ok, self.wsl_full, [(True, "")])
        env, _w, _s, fb = ml._select_runtime("auto", "hybrid-engine")
        self.assertEqual(env, "wsl")
        self.assertIsNone(fb)

    def test_auto_falls_back_when_wsl_smoke_fails(self):
        self._patch(self.win_limited, self.wsl_full, [(False, "no kernel image"), (True, "")])
        env, _w, _s, fb = ml._select_runtime("auto", "hybrid-engine")
        self.assertEqual(env, "windows")
        self.assertIsNotNone(fb)
        self.assertTrue(fb["used"])
        self.assertEqual(fb["actual_env"], "windows")
        self.assertEqual(fb["actual_backend"], "pipeline")
        self.assertEqual(fb["requested_env"], "auto")
        self.assertIn("WSL smoke failed", fb["reason"])
        self.assertEqual(len(fb["smoke_attempts"]), 2)
        self.assertEqual(fb["smoke_attempts"][0]["env"], "wsl")
        self.assertFalse(fb["smoke_attempts"][0]["ok"])
        self.assertEqual(fb["smoke_attempts"][1]["env"], "windows")
        self.assertTrue(fb["smoke_attempts"][1]["ok"])

    def test_auto_falls_back_when_wsl_lacks_backend(self):
        self._patch(self.win_limited, self.wsl_no_vllm, [(True, "")])
        env, _w, _s, fb = ml._select_runtime("auto", "hybrid-engine")
        self.assertEqual(env, "windows")
        self.assertIsNotNone(fb)
        self.assertTrue(fb["used"])
        self.assertIn("cannot run", fb["reason"])

    def test_auto_hard_errors_when_both_smoke_fail(self):
        self._patch(self.win_limited, self.wsl_full, [(False, "wsl err"), (False, "win err")])
        env, _w, _s, fb = ml._select_runtime("auto", "hybrid-engine")
        # _select_runtime does NOT raise; it returns (None, ..., fallback_info)
        # so the caller (parse()) can produce a clean exit-6 report.
        self.assertIsNone(env)
        self.assertIsNotNone(fb)
        self.assertTrue(fb["used"])
        self.assertEqual(fb["actual_env"], "none")
        self.assertEqual(len(fb["smoke_attempts"]), 2)
        self.assertFalse(fb["smoke_attempts"][0]["ok"])
        self.assertFalse(fb["smoke_attempts"][1]["ok"])

    def test_explicit_windows_hard_errors_when_unavailable(self):
        self._patch(self.win_unavail, self.wsl_full, [])
        with self.assertRaises(RuntimeError) as cm:
            ml._select_runtime("windows", "pipeline")
        self.assertIn("Windows mineru is not available", str(cm.exception))

    def test_explicit_wsl_hard_errors_when_backend_missing(self):
        self._patch(self.win_ok, self.wsl_no_vllm, [])
        with self.assertRaises(RuntimeError) as cm:
            ml._select_runtime("wsl", "hybrid-engine")
        self.assertIn("WSL cannot run", str(cm.exception))
        self.assertIn("WSL cannot run", str(cm.exception))

    def test_explicit_wsl_hard_errors_when_unavailable(self):
        self._patch(self.win_ok, self.wsl_unavail, [])
        with self.assertRaises(RuntimeError) as cm:
            ml._select_runtime("wsl", "pipeline")
        self.assertIn("WSL mineru is not available", str(cm.exception))


class OutputPreparationTests(unittest.TestCase):
    def test_empty_output_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "new-output"
            ok, error = ml._prepare_output_root(out)
            self.assertTrue(ok)
            self.assertIsNone(error)
            self.assertTrue(out.is_dir())

    def test_non_empty_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "stale-output"
            out.mkdir()
            (out / "old.md").write_text("old", encoding="utf-8")
            ok, error = ml._prepare_output_root(out)
            self.assertFalse(ok)
            self.assertIn("not empty", error)

class SmokeTestInfrastructureTests(unittest.TestCase):
    """Verify the smoke test artifact is shipped with the repo."""

    def test_smoke_pdf_present(self):
        smoke = Path(ml.__file__).resolve().parent / "tests" / "smoke.pdf"
        self.assertTrue(smoke.is_file(), f"smoke.pdf missing at {smoke}")
        self.assertGreater(smoke.stat().st_size, 0)


class ManifestCommandTests(unittest.TestCase):
    def test_io_mode_accepts_only_resolved_values(self):
        parser = ml.build_parser()
        for value in ("native", "staged"):
            args = parser.parse_args([
                "manifest", "--path", "input.pdf", "--output", "raw", "--io-mode", value
            ])
            self.assertEqual(args.io_mode, value)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args([
                    "manifest", "--path", "input.pdf", "--output", "raw", "--io-mode", "auto"
                ])

    def test_manifest_reads_stdout_and_stderr_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "lecture.pdf"
            input_path.write_bytes(b"pdf")
            raw = root / "raw"
            markdown = raw / "lecture" / "auto" / "lecture.md"
            markdown.parent.mkdir(parents=True)
            markdown.write_text("# Lecture", encoding="utf-8")
            stdout_file = root / "stdout.log"
            stderr_file = root / "stderr.log"
            stdout_file.write_text("ERROR: stdout transient\n", encoding="utf-8")
            stderr_file.write_text("WARNING: stderr warning\n", encoding="utf-8")
            args = ml.build_parser().parse_args([
                "manifest",
                "--path", str(input_path),
                "--output", str(raw),
                "--stdout-file", str(stdout_file),
                "--stderr-file", str(stderr_file),
            ])
            with mock.patch.object(ml, "_emit", return_value=0) as emit:
                rc = ml.manifest_from_existing(args)
            self.assertEqual(rc, 0)
            self.assertEqual(emit.call_args.kwargs["code"], 0)
            manifest = json.loads(
                (raw / ml.ml_manifest.MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
            warning_codes = {item["code"] for item in manifest["warnings"]}
            self.assertIn("stdout_error_sample", warning_codes)
            self.assertIn("stderr_warning_sample", warning_codes)

    def test_manifest_function_rejects_unresolved_io_mode(self):
        with mock.patch.object(ml, "_emit", return_value=2) as emit:
            rc = ml.manifest_from_existing(type("Args", (), {"io_mode": "auto"})())
        self.assertEqual(rc, 2)
        payload = emit.call_args.args[0]
        self.assertEqual(payload["error_code"], "invalid_io_mode")
        self.assertEqual(emit.call_args.kwargs["code"], 2)


class OfficeBackendPolicyTests(unittest.TestCase):
    def test_office_input_forces_pipeline_and_records_override(self):
        backend, override = ml._effective_backend_for_input(Path("slides.pptx"), "hybrid-engine")
        self.assertEqual(backend, "pipeline")
        self.assertIsNotNone(override)
        self.assertEqual(override["requested_backend"], "hybrid-engine")
        self.assertEqual(override["actual_backend"], "pipeline")

    def test_office_default_backend_is_pipeline(self):
        backend, override = ml._effective_backend_for_input(Path("notes.docx"), None)
        self.assertEqual(backend, "pipeline")
        self.assertIsNotNone(override)

    def test_non_office_requested_backend_is_preserved(self):
        backend, override = ml._effective_backend_for_input(Path("lecture.pdf"), "hybrid-engine")
        self.assertEqual(backend, "hybrid-engine")
        self.assertIsNone(override)


class StagedIntegrityTests(unittest.TestCase):
    def _settings(self) -> ml.ml_config.RuntimeSettings:
        return ml.ml_config.RuntimeSettings(
            windows_executable="C:/mineru.exe",
            wsl_executable="/opt/mineru/bin/mineru",
            wsl_distro="Ubuntu-24.04",
            default_env="wsl",
            io_policy="staged",
            config_path=Path("config.json"),
            sources={},
        )

    def test_verify_wsl_hash_accepts_match_and_rejects_mismatch(self):
        expected = "b" * 64
        with mock.patch.object(
            ml, "_run_text", return_value=(0, f"{expected}  /tmp/mineru-local.ok/input.pdf\n", "")
        ):
            ml._verify_wsl_file_sha256(
                "Ubuntu-24.04", "/tmp/mineru-local.ok/input.pdf", expected
            )
        with mock.patch.object(
            ml, "_run_text", return_value=(0, f"{'c' * 64}  /tmp/mineru-local.ok/input.pdf\n", "")
        ):
            with self.assertRaises(ml.RuntimeConfigError) as raised:
                ml._verify_wsl_file_sha256(
                    "Ubuntu-24.04", "/tmp/mineru-local.ok/input.pdf", expected
                )
            self.assertIn("SHA256 mismatch", str(raised.exception))

    def test_staged_run_hashes_before_copy_verifies_and_keeps_pre_run_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "lecture.pdf"
            input_path.write_bytes(b"original handout")
            original_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
            output_path = root / "output"
            events = []
            original_fingerprint = ml._fingerprint_input

            def fingerprint(path):
                events.append("fingerprint")
                return original_fingerprint(path)

            def make_temp(_distro):
                events.append("make-temp")
                return "/tmp/mineru-local.Abc123"

            def copy_to_wsl(_distro, _source, _destination):
                events.append("copy-to-wsl")

            def verify_hash(_distro, staged_path, expected):
                events.append("verify-hash")
                self.assertEqual(staged_path, "/tmp/mineru-local.Abc123/lecture.pdf")
                self.assertEqual(expected, original_hash)

            def run_text(cmd, **_kwargs):
                if "mkdir -p" in cmd[-1]:
                    events.append("mkdir-output")
                    return 0, "", ""
                raise AssertionError(f"unexpected command: {cmd}")

            def run_mineru(*_args, **_kwargs):
                events.append("run-mineru")
                input_path.write_bytes(b"changed during run")
                return 0, "ok", ""

            def copy_from_wsl(_distro, _source, _destination):
                events.append("copy-from-wsl")

            def cleanup(_distro, _temp):
                events.append("cleanup")

            with mock.patch.object(ml, "_fingerprint_input", side_effect=fingerprint), \
                 mock.patch.object(ml, "_make_wsl_temp", side_effect=make_temp), \
                 mock.patch.object(ml, "_copy_to_wsl", side_effect=copy_to_wsl), \
                 mock.patch.object(ml, "_verify_wsl_file_sha256", side_effect=verify_hash), \
                 mock.patch.object(ml, "_run_text", side_effect=run_text), \
                 mock.patch.object(ml, "_run_argv", side_effect=run_mineru), \
                 mock.patch.object(ml, "_copy_from_wsl", side_effect=copy_from_wsl), \
                 mock.patch.object(ml, "_cleanup_wsl_temp", side_effect=cleanup):
                result = ml._execute_parse(
                    env_name="wsl",
                    settings=self._settings(),
                    backend="pipeline",
                    method="auto",
                    input_path=input_path,
                    output_path=output_path,
                    lang=None,
                    start_page=None,
                    end_page=None,
                    formula=None,
                    table=None,
                    image_analysis=None,
                    effort=None,
                )
            self.assertEqual(events, [
                "fingerprint",
                "make-temp",
                "copy-to-wsl",
                "verify-hash",
                "mkdir-output",
                "run-mineru",
                "copy-from-wsl",
                "cleanup",
            ])
            self.assertEqual(result.input_sha256, original_hash)
            self.assertEqual(result.input_used, "/tmp/mineru-local.Abc123/lecture.pdf")

    def test_cleanup_failure_is_explicit(self):
        with mock.patch.object(ml, "_run_text", return_value=(1, "", "cleanup denied")):
            with self.assertRaises(ml.RuntimeConfigError) as raised:
                ml._cleanup_wsl_temp("Ubuntu-24.04", "/tmp/mineru-local.Abc123")
        self.assertIn("cannot clean up staged WSL temp directory", str(raised.exception))
        self.assertIn("cleanup denied", str(raised.exception))


class BatchInterfaceTests(unittest.TestCase):
    def test_batch_output_directories_are_collision_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "out"
            base.mkdir()
            used = set()
            first = ml._allocate_batch_output_dir(base, Path("a/lecture.pdf"), used)
            second = ml._allocate_batch_output_dir(base, Path("b/lecture.pdf"), used)
            repeated = ml._allocate_batch_output_dir(base, Path("a/lecture.pdf"), used)
            self.assertEqual(len({first, second, repeated}), 3)
            self.assertEqual(repeated.name, "lecture.pdf-2")

    def test_parse_dir_emits_one_json_with_embedded_same_stem_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / "inputs"
            (inputs / "a").mkdir(parents=True)
            (inputs / "b").mkdir(parents=True)
            (inputs / "a" / "lecture.pdf").write_bytes(b"a")
            (inputs / "b" / "lecture.pdf").write_bytes(b"b")
            output = root / "out"
            args = ml.build_parser().parse_args([
                "parse-dir", "--path", str(inputs), "--output", str(output)
            ])

            def fake_parse(sub_args):
                Path(sub_args.output).mkdir(parents=True, exist_ok=True)
                return ml._emit({
                    "command": "parse",
                    "input": sub_args.path,
                    "output": sub_args.output,
                    "returncode": 0,
                })

            with mock.patch.object(ml, "parse", side_effect=fake_parse), \
                 contextlib.redirect_stdout(io.StringIO()) as stdout:
                rc = ml.parse_dir(args)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["command"], "parse-dir")
            self.assertEqual(payload["failures"], 0)
            self.assertEqual(len(payload["results"]), 2)
            outputs = {item["output"] for item in payload["results"]}
            self.assertEqual(len(outputs), 2)
            self.assertTrue(all(item["report"]["command"] == "parse" for item in payload["results"]))


if __name__ == "__main__":
    unittest.main()
