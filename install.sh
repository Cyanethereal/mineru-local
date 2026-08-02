#!/usr/bin/env bash
# install.sh - bootstrap entry point for mineru-local skill on POSIX systems.
# Forces UTF-8, then delegates to scripts/lib_install.py install.
set -euo pipefail
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/scripts/lib_install.py" install