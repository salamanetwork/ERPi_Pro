"""Create verified ERP backup archives and restore into an empty PostgreSQL database."""
from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
from hashlib import file_digest
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.db import connection, transaction
import psycopg

from .mfa import cipher

PREFIX = "erp-backup-"
FORMAT = 1


def digest(path):
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


def pg_config():
    database = settings.DATABASES["default"]
    return {
        "dbname": database["NAME"], "user": database["USER"], "password": database["PASSWORD"],
        "host": database["HOST"], "port": database["PORT"],
    }


def run_pg(command, config, *options):
    bin_dir = os.getenv("ERP_PG_BIN")
    executable = str(Path(bin_dir) / command) if bin_dir else command
    args = [executable, "--no-password", "--dbname", config["dbname"], "--username", config["user"]]
    if config["host"]:
        args.extend(["--host", config["host"]])
    if config["port"]:
        args.extend(["--port", config["port"]])
    subprocess.run(args + list(options), env={**os.environ, "PGPASSWORD": config["password"]}, check=True)


@contextmanager
def backup_lock(directory):
    directory = directory.resolve()
    media = settings.MEDIA_ROOT.resolve()
    if directory.is_relative_to(media) or media.is_relative_to(directory):
        raise ValueError("Keep the backup and live attachment directories separate.")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / ".erp-backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def archives(directory):
    return sorted(
        (path for path in directory.glob(PREFIX + "*.zip")
         if re.fullmatch(r"erp-backup-[0-9]{8}T[0-9]{12}Z\.zip", path.name)),
        reverse=True,
    )


def dump_database(destination):
    # Attachment keys and the database dump use one PostgreSQL snapshot.
    from .models import Attachment

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor.execute("SELECT pg_export_snapshot()")
            snapshot = cursor.fetchone()[0]
        attachments = {f"media/documents/{key.hex}": size
                       for key, size in Attachment.objects.values_list("storage_key", "size")}
        # Foundation media shares MEDIA_ROOT and the same PostgreSQL snapshot.
        if "media_service_mediaasset" in connection.introspection.table_names():
            from media_service.models import MediaAsset
            attachments.update({"media/"+key:size for key,size in MediaAsset.objects.filter(storage_alias="public_media").values_list("object_key","size")})
        run_pg("pg_dump", pg_config(), "--format=custom", "--snapshot", snapshot, "--file", str(destination))
    return attachments


def create_backup(directory):
    now = datetime.now(UTC)
    destination = directory / (PREFIX + now.strftime("%Y%m%dT%H%M%S%fZ") + ".zip")
    with tempfile.TemporaryDirectory(prefix=".erp-backup-", dir=directory) as working:
        staging = Path(working)
        dump = staging / "database.dump"
        attachments = dump_database(dump)
        files = {"database.dump": digest(dump)}
        archive = staging / "backup.zip"
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
            bundle.write(dump, "database.dump")
            if settings.MEDIA_ROOT.is_symlink():
                raise ValueError("The attachment root must not be a symbolic link.")
            if settings.MEDIA_ROOT.exists():
                for source in sorted(settings.MEDIA_ROOT.rglob("*")):
                    if source.is_symlink() or not (source.is_file() or source.is_dir()):
                        raise ValueError(f"Unsupported attachment path: {source}")
                    if source.is_file():
                        name = "media/" + source.relative_to(settings.MEDIA_ROOT).as_posix()
                        if name not in attachments and name.startswith("media/documents/"):
                            continue
                        copied = staging / "attachment"
                        shutil.copyfile(source, copied)
                        if name in attachments and copied.stat().st_size != attachments.pop(name):
                            raise ValueError(f"Attachment size mismatch: {name}")
                        files[name] = digest(copied)
                        bundle.write(copied, name)
            if attachments:
                raise ValueError("The database references attachments missing from the backup.")
            bundle.writestr("manifest.json", json.dumps({
                "format": FORMAT, "created_utc": now.isoformat(),
                "database": pg_config()["dbname"], "files": files,
                "postgresql_version": connection.pg_version,
                "settings": {
                    "mfa_all": settings.ERP_MFA_ALL, "mfa_roles": sorted(settings.ERP_MFA_ROLES),
                    "mfa_key_check": cipher().encrypt(b"erp-backup-key-check").decode(),
                },
            }, indent=2))
        archive.chmod(0o600)
        with archive.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(archive, destination)
    return destination


def unpack(archive, staging):
    with ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        names = bundle.namelist()
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise ValueError("Unsupported ERP backup format.")
        files = manifest["files"]
        if (not isinstance(files, dict) or len(names) != len(set(names))
                or set(names) != {"manifest.json", *files} or "database.dump" not in files):
            raise ValueError("The archive and backup manifest disagree.")
        for name, checksum in files.items():
            entry = PurePosixPath(name)
            if (entry.is_absolute() or ".." in entry.parts or "\\" in name or entry.as_posix() != name
                    or (name != "database.dump" and (len(entry.parts) < 2 or entry.parts[0] != "media"))):
                raise ValueError("Unsafe path in the backup archive.")
            target = staging / entry
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(name) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            if digest(target) != checksum:
                raise ValueError(f"Backup checksum mismatch: {name}")
    return manifest


def restore_backup(archive, target_db, target_media):
    config = pg_config()
    if target_db == config["dbname"] or target_db in {"postgres", "template0", "template1"}:
        raise ValueError("Choose a separate empty database for the restore.")
    target_media = target_media.resolve()
    live_media = settings.MEDIA_ROOT.resolve()
    if target_media.exists() or target_media.is_relative_to(live_media) or live_media.is_relative_to(target_media):
        raise ValueError("Choose a new attachment directory outside the live attachment directory.")
    target_media.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    config["dbname"] = target_db

    with tempfile.TemporaryDirectory(prefix=".erp-restore-", dir=target_media.parent) as working:
        staging = Path(working)
        manifest = unpack(archive, staging)
        if target_db == manifest["database"]:
            raise ValueError("Use a database name different from the original.")
        if cipher().decrypt(manifest["settings"]["mfa_key_check"].encode()) != b"erp-backup-key-check":
            raise ValueError("The backup requires the original MFA key.")
        with psycopg.connect(**config, autocommit=True) as target:
            if target.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname NOT IN ('public', 'information_schema')"
                " AND nspname NOT LIKE 'pg_%' UNION ALL"
                " SELECT 1 FROM pg_class WHERE relnamespace = 'public'::regnamespace LIMIT 1"
            ).fetchone():
                raise ValueError("The restore database is not empty.")
            run_pg("pg_restore", config, "--single-transaction", "--exit-on-error", "--no-owner",
                   "--no-privileges", "--", str(staging / "database.dump"))
            target.execute("DELETE FROM django_session")
            work_orders = target.execute("SELECT count(*) FROM workorders_workorder").fetchone()[0]
        media = staging / "media"
        media.mkdir(exist_ok=True)
        media.rename(target_media)
    return work_orders
