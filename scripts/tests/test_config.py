"""Tests for portable mineru-local configuration."""
from __future__ import annotations

import argparse
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

import mineru_config as mc  # noqa: E402


class ConfigPathTests(unittest.TestCase):
    def test_windows_path_uses_appdata(self):
        env = {"APPDATA": r"C:\Users\tester\AppData\Roaming"}
        path = mc.default_config_path(environ=env, platform="win32")
        self.assertEqual(path, Path(r"C:\Users\tester\AppData\Roaming") / "mineru-local" / "config.json")

    def test_linux_path_uses_xdg(self):
        env = {"XDG_CONFIG_HOME": "/tmp/xdg"}
        path = mc.default_config_path(environ=env, platform="linux")
        self.assertEqual(path, Path("/tmp/xdg") / "mineru-local" / "config.json")

    def test_explicit_config_env_wins(self):
        env = {mc.CONFIG_PATH_ENV: "relative/config.json"}
        path = mc.default_config_path(environ=env, platform="linux")
        self.assertEqual(path, (Path.cwd() / "relative" / "config.json").resolve())


class ConfigPersistenceTests(unittest.TestCase):
    def test_save_load_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            mc.save_config({"wsl_executable": "/opt/mineru/bin/mineru"}, path=path)
            mc.save_config({"wsl_distro": "Ubuntu-24.04"}, path=path)
            data = mc.load_config(path)
            self.assertEqual(data["schema_version"], 1)
            self.assertEqual(data["wsl_executable"], "/opt/mineru/bin/mineru")
            self.assertEqual(data["wsl_distro"], "Ubuntu-24.04")

    def test_empty_string_clears_a_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            mc.save_config({"windows_executable": r"C:\mineru.exe"}, path=path)
            mc.save_config({"windows_executable": ""}, path=path)
            self.assertEqual(mc.load_config(path)["windows_executable"], "")

    def test_invalid_schema_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
            with self.assertRaises(mc.ConfigError):
                mc.load_config(path)

    def test_invalid_type_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"wsl_executable": 3}), encoding="utf-8")
            with self.assertRaises(mc.ConfigError):
                mc.load_config(path)


class PrecedenceTests(unittest.TestCase):
    def test_cli_beats_env_beats_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            mc.save_config({
                "windows_executable": "config-win",
                "wsl_executable": "config-wsl",
                "wsl_distro": "ConfigDistro",
                "default_env": "wsl",
                "io_policy": "native",
            }, path=path)
            args = argparse.Namespace(
                config=str(path),
                windows_executable="cli-win",
                wsl_executable=None,
                wsl_distro=None,
                default_env=None,
                io_policy=None,
            )
            env = {
                "MINERU_WINDOWS_EXECUTABLE": "env-win",
                "MINERU_WSL_EXECUTABLE": "env-wsl",
                "MINERU_WSL_DISTRO": "EnvDistro",
            }
            settings = mc.resolve_settings(args, environ=env)
            self.assertEqual(settings.windows_executable, "cli-win")
            self.assertEqual(settings.wsl_executable, "env-wsl")
            self.assertEqual(settings.wsl_distro, "EnvDistro")
            self.assertEqual(settings.default_env, "wsl")
            self.assertEqual(settings.io_policy, "native")
            self.assertTrue(settings.sources["windows_executable"].startswith("cli"))
            self.assertTrue(settings.sources["wsl_executable"].startswith("env:"))

    def test_missing_executables_remain_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            settings = mc.resolve_settings(environ={}, config_path=path)
            self.assertIsNone(settings.windows_executable)
            self.assertIsNone(settings.wsl_executable)
            self.assertEqual(settings.sources["windows_executable"], "missing")
            self.assertEqual(settings.sources["wsl_executable"], "missing")

    def test_explicit_env_overrides_invalid_default_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            args = argparse.Namespace(
                config=str(path),
                env="wsl",
                windows_executable=None,
                wsl_executable=None,
                wsl_distro=None,
                default_env=None,
                io_policy=None,
            )
            settings = mc.resolve_settings(
                args,
                environ={
                    "MINERU_DEFAULT_ENV": "cloud",
                    "MINERU_WSL_EXECUTABLE": "/opt/mineru/bin/mineru",
                    "MINERU_WSL_DISTRO": "Ubuntu-24.04",
                },
            )
            self.assertEqual(settings.default_env, "wsl")
            self.assertEqual(settings.sources["default_env"], "cli:--env")
            self.assertEqual(settings.wsl_executable, "/opt/mineru/bin/mineru")
            self.assertEqual(settings.sources["wsl_executable"], "env:MINERU_WSL_EXECUTABLE")

    def test_invalid_environment_value_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            with self.assertRaises(mc.ConfigError):
                mc.resolve_settings(
                    argparse.Namespace(config=str(path), default_env=None),
                    environ={"MINERU_DEFAULT_ENV": "cloud"},
                )


class ConfigUpdatesTests(unittest.TestCase):
    def test_only_explicit_fields_are_saved(self):
        args = argparse.Namespace(
            windows_executable=None,
            wsl_executable="/opt/mineru",
            wsl_distro="",
            default_env="wsl",
            io_policy=None,
        )
        updates = mc.config_updates_from_args(args)
        self.assertEqual(updates, {"wsl_executable": "/opt/mineru", "wsl_distro": "", "default_env": "wsl"})


if __name__ == "__main__":
    unittest.main()
