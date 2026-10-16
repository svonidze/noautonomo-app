#!/usr/bin/env bash
# Install only the case-reminder units; delivery acceptance and activation are separate.
set -euo pipefail
if [[ $# -ne 3 ]]; then
  echo 'Usage: install-case-reminder-units.sh RELEASE_ROOT PRIVATE_ROOT RUNTIME_ENV' >&2
  exit 2
fi
source_dir="$(cd "$(dirname "$0")" && pwd)"
units_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
python3 - "$source_dir" "$units_dir" "$1" "$2" "$3" <<'PY'
from pathlib import Path
import re
import sys
source,units,release,private,runtime=map(Path,sys.argv[1:])
for value in (release,private,runtime):
    if not value.is_absolute() or not re.fullmatch(r'/[A-Za-z0-9_./-]+',str(value)):
        raise SystemExit('Use absolute operational paths without spaces or systemd specifiers')
if private.resolve() in {Path('/'),Path('/srv'),Path('/var'),Path('/home')}:
    raise SystemExit('Refusing a broad private data root')
if not release.is_dir() or not private.is_dir() or not runtime.is_file():
    raise SystemExit('Existing release root, private root and runtime environment are required')
content=(source/'systemd/autonomo-case-reminders.service.template').read_text()
for key,value in {'RELEASE_ROOT':release,'PRIVATE_ROOT':private,'RUNTIME_ENV':runtime}.items():
    content=content.replace('@'+key+'@',str(value))
if re.search(r'@[A-Z_]+@',content):
    raise SystemExit('Unresolved systemd template token')
units.mkdir(parents=True,exist_ok=True)
(units/'autonomo-case-reminders.service').write_text(content)
(units/'autonomo-case-reminders.timer').write_bytes((source/'systemd/autonomo-case-reminders.timer').read_bytes())
PY
systemctl --user daemon-reload
printf '%s\n' 'Case reminder units installed; timer was not enabled or started.'
