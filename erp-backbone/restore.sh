#!/usr/bin/env bash
set -Eeuo pipefail
if (( $# < 5 || $# > 6 )); then
    echo "Usage: restore.sh COMPOSE_ENV BACKUP_ENV latest|SNAPSHOT_ID NEW_DATABASE NEW_MEDIA_SUBDIR [SOURCE_COMPOSE_ENV]" >&2
    exit 2
fi
snapshot=$3
target_db=$4
target_media=$5
[[ "$snapshot" == latest || "$snapshot" =~ ^[0-9a-f]{8,64}$ ]] || exit 2
[[ "$target_db" =~ ^restore_[a-z0-9_]{1,48}$ && "$target_media" =~ ^restore_[a-z0-9_]{1,48}$ ]] || {
    echo "Both restore destinations must start with restore_ and use lowercase ASCII letters, digits or underscore." >&2
    exit 2
}
if (( $# == 6 )); then
    source_env=$(realpath "$6")
fi
source "$(dirname -- "${BASH_SOURCE[0]}")/deploy/backup-common.sh" "$1" "$2"
if (( $# == 6 )); then
    source_project=$(docker compose --env-file "$source_env" -f docker-compose.yml config --format json |
      python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
    tag="erp-${source_project}"
fi
working=$(mktemp -d "${TMPDIR:-/tmp}/erp-restore.XXXXXX")
trap 'rm -rf -- "$working"' EXIT
# Resolve the ID through the repository's environment tag, including for explicit IDs.
snapshots=$(restic snapshots --tag "$tag" --json)
id=$(python3 -c '
import json, sys
snapshot = sys.argv[1]
rows = json.load(sys.stdin)
rows = rows if snapshot == "latest" else [r for r in rows if r["id"].startswith(snapshot)]
if not rows or snapshot != "latest" and len(rows) != 1:
    sys.exit("Snapshot is missing or ambiguous in this environment.")
print(max(rows, key=lambda r: r["time"])["id"])
' "$snapshot" <<<"$snapshots")
restic dump "$id" /erp.zip >"$working/erp.zip"
[[ -s "$working/erp.zip" ]]
"${compose[@]}" exec -T db createdb -U postgres --owner=erp_app -- "$target_db"
"${compose[@]}" run --rm --no-deps -T app sh -ec '
    work=$(mktemp -d)
    trap '\''rm -rf -- "$work"'\'' EXIT
    cat >"$work/erp.zip"
    python manage.py erp_restore "$work/erp.zip" --target-db "$1" --target-media "/srv/erp/media/$2"
' erp-restore "$target_db" "$target_media" <"$working/erp.zip"
printf 'Restored %s into %s and %s. Live environment has not been switched.\n' "$id" "$target_db" "$target_media"
