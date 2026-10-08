from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from package_watcher import PackageWatcher, single_watcher


class PackageWatcherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "received"
        self.folder.mkdir()
        self.db = self.root / "database" / "maraki.db"
        self.state = self.root / "session_state.json"
        self.watcher = PackageWatcher(self.folder, self.db, self.state)

    def package(self, name="report.zip", rows="ID,Amount\n1,10\n"):
        path = self.folder / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("nested/report_sales.csv", rows)
            archive.writestr("nested/report_erca.csv", rows)
        return path

    def count(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]

    def test_stable_detection_import_and_restart_skips_completed(self):
        self.package()
        self.watcher.scan_once()
        self.assertFalse(self.db.exists())
        state = json.loads(self.state.read_text())
        self.assertEqual(state["packages"]["report.zip"]["status"], "pending")
        self.assertEqual(state["interval_seconds"], 10)
        self.watcher.scan_once()
        self.assertEqual(self.count(), 1)
        state = json.loads(self.state.read_text())
        self.assertEqual(state["packages"]["report.zip"]["status"], "imported")
        self.assertEqual(len(state["packages"]["report.zip"]["sha256"]), 64)
        restarted = PackageWatcher(self.folder, self.db, self.state)
        with patch.object(restarted, "import_package", side_effect=AssertionError("reimport")):
            restarted.scan_once()
        self.assertEqual(self.count(), 1)

    def test_changed_packages_and_new_packages_import_without_duplicate_rows(self):
        self.package()
        self.watcher.scan_once()
        self.watcher.scan_once()
        self.package(rows="ID,Amount\n1,10\n2,20\n")
        self.package("next.zip", "ID,Amount\n2,20\n3,30\n")
        self.watcher.scan_once()
        self.assertEqual(self.count(), 1)
        self.watcher.scan_once()
        self.assertEqual(self.count(), 3)

    def test_partial_bad_and_unsafe_packages_are_retried(self):
        (self.folder / "upload.part").write_bytes(b"unfinished")
        path = self.folder / "report.zip"
        path.write_bytes(b"partial zip")
        self.watcher.scan_once()
        self.watcher.scan_once()
        self.assertEqual(self.watcher.state["packages"]["report.zip"]["status"], "failed")
        self.watcher.scan_once()
        self.assertEqual(self.watcher.state["packages"]["report.zip"]["attempts"], 2)
        self.package()
        self.watcher.scan_once()
        self.watcher.scan_once()
        self.assertEqual(self.count(), 1)
        self.assertNotIn("upload.part", self.watcher.state["packages"])
        with zipfile.ZipFile(self.folder / "unsafe.zip", "w") as archive:
            archive.writestr("../escaped_sales.csv", "ID\n1\n")
        self.watcher.scan_once()
        self.watcher.scan_once()
        self.assertEqual(self.watcher.state["packages"]["unsafe.zip"]["status"], "failed")
        self.assertFalse((self.root / "escaped_sales.csv").exists())

    def test_database_failure_is_retried_without_open_connection_leaks(self):
        self.package()
        self.watcher.scan_once()
        with patch("package_watcher.build_database_from_folder", side_effect=sqlite3.OperationalError("database is locked")):
            self.watcher.scan_once()
        self.assertEqual(self.watcher.state["packages"]["report.zip"]["status"], "failed")
        self.watcher.scan_once()
        self.assertEqual(self.count(), 1)

    def test_lock_and_corrupt_state(self):
        lock = self.root / "watcher.lock"
        with single_watcher(lock):
            with self.assertRaises(RuntimeError):
                with single_watcher(lock):
                    pass
        self.state.write_text("{broken json")
        with self.assertRaises(ValueError):
            PackageWatcher(self.folder, self.db, self.state)
        self.assertEqual(self.state.read_text(), "{broken json")

    def test_receiver_stages_upload_for_watcher(self):
        import mrk_receiver
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("report_sales.csv", "ID,Amount\n1,10\n")
        data.seek(0)
        with patch.object(mrk_receiver, "UPLOAD_FOLDER", str(self.folder)), mrk_receiver.app.test_client() as client:
            response = client.post("/upload", data={"file": (data, "report.zip")})
            self.assertEqual(response.status_code, 200)
            self.assertIn("queued", response.text)
            self.assertEqual(list(self.folder.glob("*.part")), [])
            response = client.post("/upload", data={"file": (io.BytesIO(b"bad"), "invalid.zip")})
            self.assertEqual(response.status_code, 400)
            self.assertFalse((self.folder / "invalid.zip").exists())
        self.watcher.scan_once()
        self.watcher.scan_once()
        self.assertEqual(self.count(), 1)


if __name__ == "__main__":
    unittest.main()
