#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/lib.sh"
[[ $# -eq 1 && ( "$1" == daily || "$1" == monthly ) ]] || { echo "usage: $0 daily|monthly" >&2; exit 2; }
backup_class="$1"
require_command python3
require_command rclone
load_runtime_env
with_lock
data="$(private_root)"
if [[ "$backup_class" == monthly ]]; then
  remote="${AUTONOMO_RCLONE_MONTHLY_REMOTE:-}"
  backup_dir="${AUTONOMO_MONTHLY_BACKUP_DIR:-$data/backups/monthly}"
  max_age_hours="${AUTONOMO_MONTHLY_VERIFY_MAX_AGE_HOURS:-72}"
else
  remote="${AUTONOMO_RCLONE_REMOTE:-}"
  backup_dir="${AUTONOMO_BACKUP_DIR:-$data/backups/private-root}"
  max_age_hours=48
fi
[[ -n "$remote" ]] || die "$backup_class crypt backup remote is not configured"
[[ "$remote" == *crypt:* ]] || die "$backup_class backup remote must be crypt"
[[ -n "${AUTONOMO_RCLONE_CONFIG:-}" ]] || die "rclone config is not configured"
require_private_file "$AUTONOMO_RCLONE_CONFIG" "rclone config"
python3 "$script_dir/verify_backup.py" \
  --class "$backup_class" \
  --private-root "$data" \
  --backup-dir "$backup_dir" \
  --remote "$remote" \
  --rclone-config "$AUTONOMO_RCLONE_CONFIG" \
  --restore-script "$script_dir/restore_private_root.py" \
  --max-age-hours "$max_age_hours"
