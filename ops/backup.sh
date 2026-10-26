#!/usr/bin/env bash
# Create a verified archive using the installed operations control plane.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/lib.sh"
require_command python3
load_runtime_env
with_lock
data="$(private_root)"; sha="$(current_release_sha)"
release="$(release_path "$sha")"
tool="$script_dir/backup_private_root.py"
[[ -f "$tool" ]] || die "control-plane private-root backup tool missing: $tool"
backup_class="${AUTONOMO_BACKUP_CLASS:-daily}"
[[ "$backup_class" == "daily" || "$backup_class" == "monthly" ]] || die "AUTONOMO_BACKUP_CLASS must be daily or monthly"
if [[ "$backup_class" == "monthly" ]]; then
  backup_dir="${AUTONOMO_MONTHLY_BACKUP_DIR:-$data/backups/monthly}"
  backup_keep="${AUTONOMO_MONTHLY_BACKUP_KEEP:-13}"
  remote="${AUTONOMO_RCLONE_MONTHLY_REMOTE:-}"
else
  backup_dir="${AUTONOMO_BACKUP_DIR:-$data/backups/private-root}"
  backup_keep="${AUTONOMO_BACKUP_KEEP:-35}"
  remote="${AUTONOMO_RCLONE_REMOTE:-}"
fi
if [[ -e "$data/account-backup-settings.json" || -L "$data/account-backup-settings.json" ]]; then
  policy_tool="$script_dir/backup_settings.py"
  [[ -f "$policy_tool" ]] || die "installed backup settings reader is missing"
  backup_keep="$(python3 "$policy_tool" --private-root "$data" --backup-class "$backup_class" --fallback "$backup_keep")"
fi
rclone_args=()
if [[ -n "${AUTONOMO_RCLONE_CONFIG:-}" ]]; then
  rclone_args+=(--config "$AUTONOMO_RCLONE_CONFIG")
fi
verify_crypt_remote() {
  local remote="$1" name
  [[ "$remote" == *crypt:* ]] || { echo "error: backup remote must name a crypt remote" >&2; return 1; }
  [[ -n "${AUTONOMO_RCLONE_CONFIG:-}" && -f "${AUTONOMO_RCLONE_CONFIG}" ]] \
    || { echo "error: AUTONOMO_RCLONE_CONFIG must name the private rclone config" >&2; return 1; }
  name="${remote%%:*}"
  rclone "${rclone_args[@]}" config show "$name" | grep -Eq '^type = crypt$' \
    || { echo "error: rclone remote is not configured as crypt" >&2; return 1; }
}
backup_scope="${AUTONOMO_BACKUP_SCOPE:-full}"
[[ "$backup_scope" == full || "$backup_scope" == database ]] || die "AUTONOMO_BACKUP_SCOPE must be full or database"
reconcile_storage() {
  if [[ -n "${AUTONOMO_RECONCILE_BACKENDS:-}" ]]; then
    local backend
    IFS=',' read -r -a reconcile_backends <<< "$AUTONOMO_RECONCILE_BACKENDS"
    for backend in "${reconcile_backends[@]}"; do
      [[ -n "$backend" ]] || continue
      "$release/.venv/bin/autonomo-tax" storage reconcile --db "$data/autonomo.sqlite" --backend "$backend"
    done
  fi
}
files_args=()
if [[ "$backup_scope" == database ]]; then
  require_command rclone
  require_private_file "${AUTONOMO_RCLONE_CONFIG:-}" "rclone config"
  # Match the standalone helper's explicit-config environment.
  for variable in "${!RCLONE_@}"; do [[ -z "$variable" ]] || unset "$variable"; done
  verify_crypt_remote "$remote" || die "database scope requires a crypt archive remote"
  files_tool="$script_dir/backup_files.py"
  [[ -f "$files_tool" ]] || die "installed file backup helper is missing"
  files_args=(--rclone-config "$AUTONOMO_RCLONE_CONFIG"
    --archive-remote "$remote"
    --files-remote "${AUTONOMO_RCLONE_FILES_REMOTE:-}"
    --evidence-remote "${AUTONOMO_RCLONE_EVIDENCE_REMOTE:-}"
    --private-root "$data"
    --exclude-dir "${AUTONOMO_BACKUP_DIR:-$data/backups/private-root}"
    --exclude-dir "${AUTONOMO_MONTHLY_BACKUP_DIR:-$data/backups/monthly}")
  python3 "$files_tool" preflight "${files_args[@]}"
  reconcile_storage
fi
require_absolute_directory "$backup_dir"
output="$(python3 "$tool" --private-root "$data" --database "$data/autonomo.sqlite" --out-dir "$backup_dir" --keep "$backup_keep" --scope "$backup_scope")"
printf '%s\n' "$output"
backup_file="$(printf '%s\n' "$output" | sed -n 's/^archive=//p')"
manifest_file="$(printf '%s\n' "$output" | sed -n 's/^manifest=//p')"
[[ -f "$backup_file" ]] || die "backup tool did not return a file"
[[ -f "$manifest_file" ]] || die "backup tool did not return a manifest"
state_tool="$script_dir/backup_state.py"
[[ -f "$state_tool" ]] || die "installed backup state writer is missing"
marker="$data/backups/last-backup-$backup_class.json"
# cryptcheck encrypts the local pair with each object's nonce and compares the
# stored MD5, so it proves the remote copy without downloading the archive.
# A filter that matches nothing also exits 0, so both names must be reported
# as matching.
cryptcheck_pair() {
  local archive="$1" base matched
  base="$(basename "$archive" .tar.gz)"
  if [[ "$backup_scope" == database ]]; then
    python3 "$files_tool" verify-pair "${files_args[@]}" \
      --archive "$archive" --manifest "${archive%.tar.gz}.manifest.json"
    return $?
  fi
  matched="$(rclone "${rclone_args[@]}" cryptcheck --one-way --match - \
    "$(dirname "$archive")" "$remote" \
    --include "/$base.tar.gz" --include "/$base.manifest.json")" || return 1
  grep -qxF "$base.tar.gz" <<< "$matched" && grep -qxF "$base.manifest.json" <<< "$matched"
}
# The newest pair stays for deploy readiness; an older one leaves the disk only
# once its remote copy matches. Unmatched pairs stay under the count limit.
prune_offsite_pairs() {
  local old old_manifest
  for old in "$(dirname "$backup_file")"/private-root-*.tar.gz; do
    [[ "${old##*/}" != "${backup_file##*/}" ]] || continue
    old_manifest="${old%.tar.gz}.manifest.json"
    [[ -f "$old" && -f "$old_manifest" ]] || continue
    if cryptcheck_pair "$old"; then
      rm -f -- "$old" "$old_manifest" "${old%.tar.gz}.files.json"
    else
      echo "warning: kept $(basename "$old"): remote copy did not match" >&2
    fi
  done
}
python3 "$state_tool" local \
  --marker "$marker" --class "$backup_class" \
  --archive "$backup_file" --manifest "$manifest_file" \
  --app-sha "$sha" --database "$data/autonomo.sqlite" --keep "$backup_keep" \
  --remote-configured "$([[ -n "$remote" ]] && printf yes || printf no)"
if [[ "$backup_scope" == database ]]; then
  trap 'python3 "$state_tool" offsite --marker "$marker" --status failed' ERR
fi
if [[ "$backup_scope" == full && -n "$remote" ]]; then
  require_command rclone
  if ! verify_crypt_remote "$remote"; then
    python3 "$state_tool" offsite --marker "$marker" --status failed
    die "offsite backup configuration is invalid"
  fi
  backup_remote_file="${remote%/}/$(basename "$backup_file")"
  manifest_remote_file="${remote%/}/$(basename "$manifest_file")"
  if rclone "${rclone_args[@]}" copyto --immutable "$backup_file" "$backup_remote_file" \
    && rclone "${rclone_args[@]}" copyto --immutable "$manifest_file" "$manifest_remote_file" \
    && cryptcheck_pair "$backup_file"; then
    python3 "$state_tool" offsite --marker "$marker" --status acknowledged
    prune_offsite_pairs
  else
    python3 "$state_tool" offsite --marker "$marker" --status failed
    die "offsite backup upload failed"
  fi
fi
if [[ "$backup_scope" == database ]]; then
  python3 "$files_tool" backup "${files_args[@]}" \
    --archive "$backup_file" --manifest "$manifest_file"
  python3 "$state_tool" offsite --marker "$marker" --status acknowledged
  trap - ERR
  prune_offsite_pairs
else
  if [[ -n "${AUTONOMO_RCLONE_EVIDENCE_REMOTE:-}" && -d "$data/evidence" ]]; then
    require_command rclone
    verify_crypt_remote "$AUTONOMO_RCLONE_EVIDENCE_REMOTE"
    rclone "${rclone_args[@]}" copy --immutable "$data/evidence" "${AUTONOMO_RCLONE_EVIDENCE_REMOTE%/}"
  fi
  reconcile_storage
fi
