#!/usr/bin/env bash
set -Eeuo pipefail
if (( $# != 2 )); then
    echo "Usage: backup.sh /absolute/.env.staging /absolute/.env.staging.backup" >&2
    exit 2
fi
source "$(dirname -- "${BASH_SOURCE[0]}")/deploy/backup-common.sh" "$@"
runtime_dir="${XDG_RUNTIME_DIR:-$HOME/.cache}/erp-backup"
mkdir -p -m 0700 "$runtime_dir"
exec 9>"$runtime_dir/$tag.lock"
flock -n 9 || { echo "Backup already running for $project" >&2; exit 1; }
coupled_backup=$(mktemp -d "${TMPDIR:-/tmp}/erp-backup.XXXXXX")
trap 'rm -rf -- "$coupled_backup"' EXIT
"${compose[@]}" run --rm --no-deps -T app sh -ec '
    work=$(mktemp -d)
    trap '\''rm -rf -- "$work"'\'' EXIT
    python manage.py erp_backup "$work" --retain 1 >&2
    set -- "$work"/erp-backup-*.zip
    [ "$#" -eq 1 ] && [ -f "$1" ]
    cat "$1"
' >"$coupled_backup/erp.zip"
# Do not publish a partial stdin stream when the database dump or attachment copy fails.
[[ -s "$coupled_backup/erp.zip" ]]
restic backup --stdin --stdin-filename erp.zip --host "$project" --tag "$tag" <"$coupled_backup/erp.zip"
restic forget --host "$project" --tag "$tag" --group-by host,tags --keep-within 30d --keep-last 1
