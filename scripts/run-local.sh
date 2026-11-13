#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
    echo "Сначала настрой окружение Python 3.12 по инструкции в README.md." >&2
    exit 1
fi

export PATH="$PWD/.venv/bin:$PWD/.venv/native-tools/env/bin:$PATH"

python manage.py migrate --noinput

python manage.py worker &
worker=$!
python manage.py runserver 127.0.0.1:8000 --noreload &
web=$!
trap 'kill "$worker" "$web" 2>/dev/null || true; wait "$worker" "$web" 2>/dev/null || true' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
wait "$web"
