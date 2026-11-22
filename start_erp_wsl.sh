#!/usr/bin/env bash
# Start the packaged ERP Backbone development build on Ubuntu/Debian under WSL.
set -Eeuo pipefail
umask 077

die() { printf '\nError: %s\n' "$*" >&2; exit 1; }
info() { printf '\n[ERP] %s\n' "$*"; }

usage() {
    printf '%s\n' \
        'Usage: bash start_erp_wsl.sh [--upgrade] [installed-project-directory]' \
        'Extract the v2.8.2 ZIP and run with --upgrade for an existing installation.' \
        'Optional: ERP_WSL_PORT=8001 bash start_erp_wsl.sh' \
        'First run may need Internet and sudo to install missing system packages.'
}

if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then usage; exit 0; fi
upgrade=0
if [[ "${1:-}" == --upgrade ]]; then upgrade=1; shift; fi
(( $# <= 1 )) || { usage >&2; exit 2; }
[[ "$(uname -s)" == Linux ]] || die "Run this file in your WSL terminal."
(( EUID != 0 )) || die "Run as your normal WSL user, without sudo."

state_home="$HOME/.local/share/erp-backbone-wsl"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
payload="$script_dir/erp-backbone"
default_project="$state_home/releases/v0_1/erp-backbone"
if [[ $# == 1 ]]; then
    [[ -f "$1/manage.py" ]] || die "Installed project not found: $1"
    project="$(realpath -- "$1")"
else
    project="$(realpath -m -- "$default_project")"
fi
action="${ERP_WSL_ACTION:-serve}"
case "$action" in serve|backup|restore-check|reset-mfa|subscription-gl-preview|subscription-gl-backfill) ;; *) die "Unknown ERP_WSL_ACTION: $action" ;; esac
if [[ "$action" == restore-check ]]; then
    [[ -f "${ERP_WSL_RESTORE_ARCHIVE:-}" ]] || die "Set ERP_WSL_RESTORE_ARCHIVE to an existing backup ZIP."
    ERP_WSL_RESTORE_ARCHIVE="$(realpath -- "$ERP_WSL_RESTORE_ARCHIVE")"
fi

port="${ERP_WSL_PORT:-8001}"
[[ "$port" =~ ^[0-9]{1,5}$ ]] && (( 10#$port >= 1024 && 10#$port <= 65535 )) \
    || die "ERP_WSL_PORT must be between 1024 and 65535."
port="$((10#$port))"

pick_python() {
    local executable
    for executable in python3.12 python3 python3.11 python3.13 python3.14; do
        command -v "$executable" >/dev/null 2>&1 || continue
        if "$executable" -c 'import sys; raise SystemExit(not ((3, 11) <= sys.version_info[:2] <= (3, 14)))'; then
            command -v "$executable"
            return
        fi
    done
    return 1
}

state_key="$(printf '%s' "$project" | sha256sum)"
state_key="${state_key%% *}"
state="$state_home/instances/$state_key"
mkdir -p -m 700 "$state"
pgdata="$state/postgresql"
pg_version=
[[ ! -f "$pgdata/PG_VERSION" ]] || pg_version="$(<"$pgdata/PG_VERSION")"

pick_postgres() {
    local candidate version
    local -a candidates=()
    [[ -z "${ERP_WSL_PG_BIN:-}" ]] || candidates+=("$ERP_WSL_PG_BIN")
    if [[ -n "$pg_version" ]]; then candidates+=("/usr/lib/postgresql/$pg_version/bin"); fi
    if command -v pg_config >/dev/null 2>&1; then candidates+=("$(pg_config --bindir)"); fi
    candidates+=(/usr/lib/postgresql/*/bin)
    for candidate in "${candidates[@]}"; do
        [[ -x "$candidate/postgres" && -x "$candidate/initdb" && -x "$candidate/pg_ctl" ]] || continue
        version="$("$candidate/postgres" --version)"
        if [[ "$version" =~ PostgreSQL\)\ ([0-9]+) ]]; then
            (( BASH_REMATCH[1] >= 14 )) || continue
            if [[ -z "$pg_version" || "$pg_version" == "${BASH_REMATCH[1]}" ]]; then
                realpath -- "$candidate"
                return
            fi
        fi
    done
    return 1
}

declare -a packages=() as_root=()
python_bin="$(pick_python || true)"
pg_bin="$(pick_postgres || true)"
if [[ -n "${ERP_WSL_PG_BIN:-}" && -z "$pg_bin" ]]; then
    die "ERP_WSL_PG_BIN did not provide a usable PostgreSQL server."
fi
if [[ -z "$pg_bin" ]]; then
    packages+=("postgresql${pg_version:+-$pg_version}")
elif [[ ! -x "$pg_bin/pg_dump" || ! -x "$pg_bin/pg_restore" ]]; then
    [[ -z "${ERP_WSL_PG_BIN:-}" ]] || die "ERP_WSL_PG_BIN must include pg_dump and pg_restore."
    installed_version="$("$pg_bin/postgres" --version)"
    [[ "$installed_version" =~ PostgreSQL\)\ ([0-9]+) ]] || die "Could not read PostgreSQL version."
    packages+=("postgresql-client-${BASH_REMATCH[1]}")
fi
command -v flock >/dev/null 2>&1 || packages+=(util-linux)
if [[ -z "$python_bin" ]] || ! "$python_bin" -c 'import ensurepip, venv' >/dev/null 2>&1; then
    packages+=(python-venv)
fi
if (( ${#packages[@]} )); then
    command -v apt-get >/dev/null 2>&1 || die "Automatic setup is for Ubuntu/Debian. Install Python 3.11–3.14 with venv, PostgreSQL 14+, and util-linux first."
    command -v sudo >/dev/null 2>&1 || die "sudo is required to install missing Ubuntu/Debian packages."
    as_root=(sudo)
    info "Installing missing WSL packages. You may be asked for your WSL password."
    "${as_root[@]}" apt-get update
    for index in "${!packages[@]}"; do
        [[ "${packages[index]}" == python-venv ]] || continue
        if [[ -n "$python_bin" ]]; then
            py_series="$("$python_bin" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
            packages[index]="python$py_series-venv"
        elif python3 -c 'import sys; raise SystemExit(not ((3, 11) <= sys.version_info[:2] <= (3, 14)))' 2>/dev/null; then
            packages[index]=python3-venv
        else
            unset 'packages[index]'
            for series in 3.12 3.11 3.13 3.14; do
                if apt-cache show "python$series-venv" >/dev/null 2>&1; then
                    packages+=("python$series" "python$series-venv")
                    break
                fi
            done
            if [[ -z "$(pick_python || true)" && " ${packages[*]} " != *-venv* ]]; then
                die "This ERP needs Python 3.11–3.14. Ubuntu 24.04 WSL has Python 3.12, which matches the bundled wheels."
            fi
        fi
    done
    "${as_root[@]}" apt-get install -y -- "${packages[@]}"
fi
python_bin="$(pick_python)" || die "Python 3.11–3.14 with venv is required."
"$python_bin" -c 'import ensurepip, venv' >/dev/null 2>&1 || die "The venv module is missing for $python_bin."
pg_bin="$(pick_postgres)" || die "PostgreSQL 14+ is required. An existing cluster must use its original major version."

[[ -x "$pg_bin/pg_dump" && -x "$pg_bin/pg_restore" ]] || die "Install the client tools for this PostgreSQL version."
export ERP_PG_BIN="$pg_bin"
export ERP_MEDIA_ROOT="${ERP_MEDIA_ROOT:-$state/media}"
backup_dir="$state/backups"
interval="${ERP_WSL_BACKUP_INTERVAL:-86400}"
retain="${ERP_WSL_BACKUP_RETAIN:-14}"
[[ "$interval" =~ ^[1-9][0-9]{1,8}$ ]] && (( interval >= 60 )) || die "Backup interval must be at least 60 seconds."
[[ "$retain" =~ ^[1-9][0-9]{0,5}$ ]] || die "Backup retention must be a positive number."
if [[ -n "${ERP_WSL_BACKUP_DIR:-}" ]]; then
    [[ "$ERP_WSL_BACKUP_DIR" == /* ]] || die "ERP_WSL_BACKUP_DIR must be an absolute path."
    [[ -d "$ERP_WSL_BACKUP_DIR" ]] || die "Mount or create the backup destination first: $ERP_WSL_BACKUP_DIR"
    backup_dir="$ERP_WSL_BACKUP_DIR/$state_key"
fi

export ERP_HEALTH_BACKUP_DIR="$backup_dir" ERP_HEALTH_PREUPGRADE_DIR="$state/pre-upgrade" ERP_HEALTH_RETAIN="$retain"
exec 9>"$state/run.lock"
flock -n 9 || die "This ERP instance is already being started or run in another terminal."

deploy=0
existing=0
[[ ! -f "$project/manage.py" ]] || existing=1
app="$project"
if (( upgrade || ! existing )); then
    [[ -f "$payload/.erp-release" && -f "$payload/manage.py" ]] \
        || die "Extract the v2.8.2 package beside this launcher."
    if (( ! existing )) && [[ -e "$project" ]]; then
        die "The installation directory is already in use: $project"
    fi
    if ! cmp -s "$payload/.erp-release" "$project/.erp-release" ||
       ! cmp -s "$payload/frontend/dist/index.html" "$project/frontend/dist/index.html"; then
        app="$payload"
        deploy=1
    fi
elif [[ ! -f "$project/.erp-release" ]]; then
    die "Existing v0.1 installation found. Run this launcher with --upgrade."
fi

cd -- "$app"
[[ -f frontend/dist/index.html ]] || die "The built Vue interface is missing. Use the development ZIP that includes frontend/dist."
python_series="$("$python_bin" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
venv="$state/venv-$python_series"
if [[ ! -x "$venv/bin/python" ]] || ! "$venv/bin/python" -m pip --version >/dev/null 2>&1; then
    info "Preparing Python $python_series in WSL"
    "$python_bin" -m venv "$venv"
fi
python="$venv/bin/python"
requirements_hash="$(sha256sum requirements.txt)"
requirements_hash="${requirements_hash%% *}"
stamp="$venv/requirements.sha256"
if [[ ! -f "$stamp" || "$(<"$stamp")" != "$requirements_hash" ]] ||
   ! "$python" -c 'import django, psycopg, cryptography, pyotp, PIL, celery, redis, packaging, rest_framework, rest_framework_simplejwt, storages' >/dev/null 2>&1 ||
   ! "$python" -m pip check >/dev/null 2>&1; then
    info "Installing pinned Python dependencies"
    if [[ ! -d wheelhouse ]] || ! "$python" -m pip install --disable-pip-version-check \
        --no-index --find-links wheelhouse --require-hashes -r requirements.txt; then
        info "The bundled wheels do not match this Python or are missing. Fetching the pinned wheels."
        "$python" -m pip install --disable-pip-version-check --only-binary=:all: \
            --require-hashes -r requirements.txt
    fi
    "$python" -m pip check
    "$python" -c 'import django, psycopg, cryptography, pyotp, PIL, celery, redis, packaging, rest_framework, rest_framework_simplejwt, storages'
    printf '%s\n' "$requirements_hash" >"$stamp"
fi

port_available() {
    "$python" - "$1" <<'PYTHON'
import socket
import sys
with socket.socket() as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", int(sys.argv[1])))
    except OSError:
        raise SystemExit(1) from None
    print(sock.getsockname()[1])
PYTHON
}
[[ "$action" != serve ]] || port_available "$port" >/dev/null || die "Web port $port is busy. Try ERP_WSL_PORT=8001 bash start_erp_wsl.sh."

for secret_file in django.key postgres.key database.key connector.key mfa.key; do
    if [[ ! -s "$state/$secret_file" ]]; then
        "$python" -c 'from secrets import token_urlsafe; print(token_urlsafe(48))' >"$state/$secret_file"
    fi
done
export ERP_SECRET_KEY="$(<"$state/django.key")"
export ERP_MFA_KEY="${ERP_MFA_KEY:-$(<"$state/mfa.key")}"
export ERP_CONNECTOR_KEY="${ERP_CONNECTOR_KEY:-$(<"$state/connector.key")}"
export ERP_PUBLIC_BASE_URL="${ERP_PUBLIC_BASE_URL:-http://127.0.0.1:$port}"
export ERP_APP_MODE=development ERP_DEBUG=1 ERP_ALLOWED_HOSTS=localhost,127.0.0.1
export ERP_MARKETING_PUBLIC_URL="${ERP_MARKETING_PUBLIC_URL:-http://127.0.0.1:$port}"
export ERP_DB_NAME=erp_backbone ERP_DB_USER=erp_backbone ERP_DB_HOST=127.0.0.1
export ERP_DB_PASSWORD="$(<"$state/database.key")"
if [[ ! -f "$pgdata/PG_VERSION" ]]; then
    info "Creating an isolated ERP PostgreSQL cluster"
    "$pg_bin/initdb" -D "$pgdata" --username=postgres --pwfile="$state/postgres.key" \
        --auth-host=scram-sha-256 --auth-local=scram-sha-256 --encoding=UTF8 --no-locale >/dev/null
fi

pg_started=0
backup_pid=
worker_pid=
dispatch_pid=
redis_container=
redis_pid=
staging=
notification_run=
stop_postgres() {
    if [[ -n "$redis_container" ]]; then docker stop "$redis_container" >/dev/null 2>&1 || true; fi
    for pid in "$dispatch_pid" "$worker_pid" "$redis_pid"; do
        if [[ -n "$pid" ]]; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
    if [[ -n "$notification_run" ]]; then
        rm -rf -- "$notification_run"
    fi
    if [[ -n "$backup_pid" ]]; then
        kill "$backup_pid" 2>/dev/null || true
        wait "$backup_pid" 2>/dev/null || true
    fi
    if [[ -n "$staging" ]]; then
        rm -rf -- "$staging"
    fi
    if (( pg_started )); then
        "$pg_bin/pg_ctl" -D "$pgdata" -m fast -w stop >/dev/null 2>&1 || true
    fi
}
trap stop_postgres EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if "$pg_bin/pg_ctl" -D "$pgdata" status >/dev/null 2>&1; then
    die "The private database is already running. Close the earlier ERP terminal and try again."
fi

info "Starting the private PostgreSQL server"
export ERP_DB_PORT="$(port_available 0)"
"$pg_bin/pg_ctl" -D "$pgdata" -l "$state/postgresql.log" -w \
    -o "-h 127.0.0.1 -p $ERP_DB_PORT -c unix_socket_directories=''" start 9>&- >/dev/null \
    || die "PostgreSQL did not start. See $state/postgresql.log"
pg_started=1

restore_db=
if [[ "$action" == restore-check ]]; then
    restore_db="$("$python" -c 'from secrets import token_hex; print("erp_restore_" + token_hex(8))')"
fi
"$python" - "$state/postgres.key" "$restore_db" <<'PYTHON'
import os
from pathlib import Path
import sys

import psycopg
from psycopg import sql

name = os.environ["ERP_DB_NAME"]
user = os.environ["ERP_DB_USER"]
with psycopg.connect(
    host=os.environ["ERP_DB_HOST"], port=os.environ["ERP_DB_PORT"], dbname="postgres",
    user="postgres", password=Path(sys.argv[1]).read_text().strip(), autocommit=True,
) as connection:
    if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [user]).fetchone():
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
            sql.Identifier(user), sql.Literal(os.environ["ERP_DB_PASSWORD"]),
        ))
    if not connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", [name]).fetchone():
        connection.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(name), sql.Identifier(user),
        ))
    if sys.argv[2]:
        connection.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(sys.argv[2]), sql.Identifier(user),
        ))
PYTHON

if (( deploy && existing )); then
    info "Saving a pre-upgrade backup"
    "$python" manage.py erp_backup "$state/pre-upgrade" --retain 14
fi

info "Checking the ERP and applying database migrations"
"$python" manage.py check
"$python" manage.py erp_preflight
"$python" manage.py migrate --noinput
"$python" manage.py audit_modules
"$python" manage.py migrate_wsl_mfa

if (( deploy )); then
    mkdir -p -- "$(dirname -- "$project")"
    staging="$(mktemp -d "$(dirname -- "$project")/.erp-deploy-XXXXXX")"
    cp -a -- "$payload/." "$staging/"
    "$python" - "$staging" "$project" <<'PYTHON'
from datetime import UTC, datetime
from pathlib import Path
import sys

source, target = map(Path, sys.argv[1:])
previous = target.with_name(target.name + ".before-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
if target.exists():
    target.rename(previous)
try:
    source.rename(target)
except OSError:
    if previous.exists():
        previous.rename(target)
    raise
if previous.exists():
    print(f"Previous application files: {previous}")
PYTHON
    staging=
    cd -- "$project"
fi

case "$action" in
    backup)
        "$python" manage.py erp_backup "$backup_dir" --retain "$retain"
        exit
        ;;
    restore-check)
        "$python" manage.py erp_restore "$ERP_WSL_RESTORE_ARCHIVE" \
            --target-db "$restore_db" --target-media "$state/restore-tests/$restore_db"
        exit
        ;;
    subscription-gl-preview|subscription-gl-backfill)
        [[ -n "${ERP_WSL_SITE:-}" ]] || die "Set ERP_WSL_SITE to the site name."
        extra=()
        [[ "$action" != subscription-gl-preview ]] || extra+=(--dry-run)
        "$python" manage.py backfill_subscription_gl --site "$ERP_WSL_SITE" "${extra[@]}"
        exit
        ;;
    reset-mfa)
        [[ -n "${ERP_WSL_ACCOUNT:-}" ]] || die "Set ERP_WSL_ACCOUNT to the account to reset."
        "$python" manage.py reset_mfa "$ERP_WSL_ACCOUNT" --confirm
        exit
        ;;
esac

admin_exists="$("$python" manage.py shell -v 0 -c \
    'from django.contrib.auth import get_user_model; print(int(get_user_model().objects.filter(is_superuser=True).exists()))')"
if [[ "$admin_exists" == 0 ]]; then
    [[ -t 0 ]] || die "Run the first setup in an interactive WSL terminal to create the admin password."
    info "Create the admin password (at least 12 characters)."
    "$python" manage.py setup_backbone
elif [[ "$admin_exists" != 1 ]]; then
    die "Could not check whether the administrator exists."
fi

info "Scheduled backup destination: $backup_dir"
PYTHONUNBUFFERED=1 "$python" manage.py erp_backup "$backup_dir" --schedule \
    --interval "$interval" --retain "$retain" 9>&- &
backup_pid=$!
if [[ "${ERP_WSL_OUTBOX:-1}" == 1 ]]; then
    "$python" - <<'PYTHON'
import os
from time import sleep

import psycopg

for attempt in range(10):
    try:
        with psycopg.connect(host=os.environ["ERP_DB_HOST"], port=os.environ["ERP_DB_PORT"],
                             dbname=os.environ["ERP_DB_NAME"], user=os.environ["ERP_DB_USER"],
                             password=os.environ["ERP_DB_PASSWORD"], connect_timeout=2) as connection:
            connection.execute("SELECT 1")
        break
    except psycopg.OperationalError:
        if attempt == 9:
            raise SystemExit("The ERP database is unavailable after startup checks.") from None
        sleep(1)
PYTHON
    info "ERP database ready on port $ERP_DB_PORT. Starting background notifications."
    notification_run="$(mktemp -d "$state/notifications.XXXXXX")"
    if [[ -z "${ERP_REDIS_URL:-}" ]]; then
        redis_native_version="$(redis-server --version 2>/dev/null || true)"
        if [[ ! "$redis_native_version" =~ v=8\. ]]; then
            if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
                redis_container="$(docker run -d --rm -p 127.0.0.1::6379 redis:8.2.11-alpine redis-server --appendonly no)" || die "Could not start Redis 8. Check Docker."
                redis_binding="$(docker port "$redis_container" 6379/tcp)"
                export ERP_REDIS_URL="redis://127.0.0.1:${redis_binding##*:}/0"
                info "Private Redis 8 container started. Pending work remains durable in PostgreSQL."
            else
                die "Redis 8 is required. Start Docker Desktop (WSL integration), install Redis 8, or set ERP_REDIS_URL to your Redis 8 server."
            fi
        fi
    fi
    if [[ -z "${ERP_REDIS_URL:-}" ]] && command -v redis-server >/dev/null 2>&1; then
        mkdir -p "$state/redis"
        redis_port="$(port_available "${ERP_WSL_REDIS_PORT:-0}")" || die "Private Redis port unavailable."
        export ERP_REDIS_URL="redis://127.0.0.1:$redis_port/0"
        redis-server --bind 127.0.0.1 --port "$redis_port" --dir "$state/redis" \
            --appendonly yes >"$state/redis.log" 2>&1 9>&- &
        redis_pid=$!
    fi
    export ERP_REDIS_URL="${ERP_REDIS_URL:-redis://127.0.0.1:6379/0}"

    if ! "$python" - <<'REDIS_CHECK'
import os,time,redis
broker=redis.Redis.from_url(os.environ["ERP_REDIS_URL"],socket_connect_timeout=2,socket_timeout=2)
for attempt in range(30):
    try:
        version=broker.info("server").get("redis_version","")
        if not version.startswith("8."):
            raise SystemExit("ERP needs Redis 8; the configured broker is a different major version.")
        print("[ERP] Redis 8 is ready.")
        break
    except (redis.RedisError,OSError):
        if attempt==29: raise SystemExit("Redis is not ready. Check Docker/ERP_REDIS_URL.")
        time.sleep(.2)
broker.close()
REDIS_CHECK
    then
        die "Redis 8 readiness check failed. Worker and Dispatcher were not started."
    fi

    if [[ -z "$redis_pid" ]]; then
        info "The notification worker uses ERP_REDIS_URL. Install redis-server locally if no broker is available."
    fi
    ERP_WORKER_READY_FILE="$notification_run/worker.ready" \
        "$python" -m celery -A backbone.celeryapp:app worker -Q notifications,marketing --concurrency=2 --loglevel=INFO >"$state/worker.log" 2>&1 9>&- &
    worker_pid=$!
    "$python" -u manage.py erp_dispatch_outbox --ready-file "$notification_run/dispatcher.ready" >"$state/outbox.log" 2>&1 9>&- &
    dispatch_pid=$!

    for ((attempt=0; attempt<25; attempt++)); do
        if { [[ -f "$notification_run/worker.ready" ]] || ! kill -0 "$worker_pid" 2>/dev/null; } &&
           { [[ -f "$notification_run/dispatcher.ready" ]] || ! kill -0 "$dispatch_pid" 2>/dev/null; }; then
            break
        fi
        sleep 1
    done
    if ! kill -0 "$worker_pid" 2>/dev/null; then
        info "Celery Worker exited. See $state/worker.log"
    elif [[ -f "$notification_run/worker.ready" ]]; then
        info "Celery Worker ready (queues: notifications, marketing). Log: $state/worker.log"
    else
        info "Celery Worker readiness was not confirmed. See $state/worker.log"
    fi
    if ! kill -0 "$dispatch_pid" 2>/dev/null; then
        info "Outbox Dispatcher exited. See $state/outbox.log"
    elif [[ -f "$notification_run/dispatcher.ready" ]]; then
        info "Outbox Dispatcher ready (PostgreSQL and Redis connected). Log: $state/outbox.log"
    else
        info "Outbox Dispatcher readiness was not confirmed. See $state/outbox.log"
    fi
else
    info "Background notifications are disabled by ERP_WSL_OUTBOX."
fi
info "Open http://127.0.0.1:$port in your Windows browser. Press Ctrl+C here to stop."
"$python" manage.py runserver "127.0.0.1:$port" --noreload
