#!/usr/bin/env sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
    echo 'Install Python 3.11 or newer, then run this script again.' >&2
    exit 1
fi
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt --disable-pip-version-check
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
fi
exec .venv/bin/python app.py
