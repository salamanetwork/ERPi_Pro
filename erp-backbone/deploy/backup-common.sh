# Sourced by the host backup and restore commands.
set -Eeuo pipefail
umask 077
compose_env=$(realpath "$1")
backup_env=$(realpath "$2")
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
compose=(docker compose --env-file "$compose_env" -f docker-compose.yml)
# The backup configuration is a trusted, operator-owned shell file.
set -a
source "$backup_env"
set +a
: "${RESTIC_REPOSITORY:?Configure a remote Restic repository}"
: "${RESTIC_PASSWORD_FILE:?Configure RESTIC_PASSWORD_FILE}"
[[ "$RESTIC_REPOSITORY" == s3:https://* ]] || { echo "Use an HTTPS S3 Restic repository" >&2; exit 2; }
[[ -r "$RESTIC_PASSWORD_FILE" ]] || { echo "Restic password file is unreadable" >&2; exit 2; }
project=$("${compose[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
tag="erp-${project}"
