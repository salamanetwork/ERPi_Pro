from django.test import Client, TestCase


class ReadinessTests(TestCase):
    def test_ready_after_migrations(self):
        response = Client().get("/health/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_database_error_is_reported_without_details(self):
        from unittest.mock import patch

        from django.db import DatabaseError

        with patch("backbone.health.connection.cursor", side_effect=DatabaseError("connection details")):
            response = Client().get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})


class DeploymentBackupTests(TestCase):
    def test_archive_requires_every_referenced_attachment(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        from django.db import connections

        from .backups import create_backup, unpack

        with TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "live"
            documents = media / "documents"
            documents.mkdir(parents=True)
            (documents / "file-ok").write_bytes(b"receipt")
            (documents / "orphan").write_bytes(b"uncommitted")
            output = root / "backup"
            output.mkdir()

            def dump(destination):
                destination.write_bytes(b"test database")
                return {"media/documents/file-ok": 7}

            with (
                self.settings(MEDIA_ROOT=media),
                patch.object(connections["default"], "pg_version", 170012, create=True),
                patch("backbone.backups.dump_database", side_effect=dump),
            ):
                bundle = create_backup(output)
                manifest = unpack(bundle, root / "unpacked")
                self.assertEqual(set(manifest["files"]), {"database.dump", "media/documents/file-ok"})
                (documents / "file-ok").unlink()
                with self.assertRaisesMessage(ValueError, "attachments missing"):
                    create_backup(output)
                self.assertEqual(list(output.glob("*.zip")), [bundle])
