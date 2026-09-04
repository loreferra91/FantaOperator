#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"

on_error() {
    printf '\nAvvio non riuscito. Controlla il messaggio sopra.\n'
    if [ -t 0 ]; then read -r -p 'Premi Invio per chiudere… ' _reply; fi
}
trap on_error ERR

runtime=""
for candidate in "$PWD/.venv/bin/python3" "$(command -v python3 || true)" /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [ -x "$candidate" ] && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        runtime="$candidate"
        break
    fi
done
if [ -z "$runtime" ]; then
    printf 'Serve Python 3.10 o successivo. Installa Python e riapri questo file.\n'
    false
fi

if ! "$runtime" -c 'import streamlit, pandas, openpyxl, defusedxml, certifi' >/dev/null 2>&1; then
    printf 'Preparazione delle dipendenze: serve Internet solo per questa installazione.\n'
    if [ ! -x .venv/bin/python3 ]; then "$runtime" -m venv .venv; fi
    runtime="$PWD/.venv/bin/python3"
    "$runtime" -m pip install -r requirements.txt
fi
"$runtime" launcher.py "$@"
