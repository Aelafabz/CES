import hashlib
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import requests
from dashboard import Pipeline, create_app, database_snapshot


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.host = self.directory / "host.sqlite"
        with closing(sqlite3.connect(self.host)) as conn:
            conn.execute('CREATE TABLE "odd""table" (name TEXT, amount TEXT)')
            conn.executemany('INSERT INTO "odd""table" VALUES (?,?)', [(f"row {i}", str(i)) for i in range(125)])
            conn.commit()
        self.pipeline = Pipeline(Path("C:/client"), self.directory / "runtime", self.host)

    def test_sample_pipeline_is_offline_deduplicated_and_preserves_host(self):
        before = hashlib.sha256(self.host.read_bytes()).digest()
        with patch.object(requests.Session, "request", side_effect=AssertionError("Sample contacted network")):
            for _ in range(2):
                thread = self.pipeline.start("sample", "2026-10-07", "2026-10-07")
                thread.join(15)
                self.assertFalse(thread.is_alive())
                self.assertEqual(self.pipeline.snapshot()["status"], "Complete", self.pipeline.snapshot())
        data = database_snapshot(self.pipeline.test_db)
        self.assertEqual({t["name"]: t["count"] for t in data["tables"]}, {"sales": 3, "erca": 3})
        from db_builder import build_database_from_folder
        build_database_from_folder(str(self.pipeline.storage / "runs" / self.pipeline.state["run_id"] / "received"), str(self.pipeline.test_db))
        self.assertEqual({t["name"]: t["count"] for t in database_snapshot(self.pipeline.test_db)["tables"]}, {"sales": 3, "erca": 3})
        self.assertEqual(len(self.pipeline.snapshot()["artifacts"]), 5)
        self.assertEqual(hashlib.sha256(self.host.read_bytes()).digest(), before)

    def test_readonly_pagination_search_and_identifier_validation(self):
        before = self.host.read_bytes()
        data = database_snapshot(self.host, 'odd"table', 2)
        self.assertEqual(len(data["rows"]), 25)
        self.assertEqual(data["total"], 125)
        filtered = database_snapshot(self.host, 'odd"table', search="row 124")
        self.assertEqual(filtered["rows"], [("row 124", "124")])
        with self.assertRaises(ValueError):
            database_snapshot(self.host, 'sales; DROP TABLE sales')
        self.assertEqual(self.host.read_bytes(), before)
        self.assertFalse((self.directory / "missing.db").exists())
        self.assertEqual(database_snapshot(self.directory / "missing.db")["tables"], [])
        self.assertFalse((self.directory / "missing.db").exists())

    def test_api_controls_and_artifact_download(self):
        app = create_app(self.pipeline)
        with app.test_client() as client:
            page = client.get("/", base_url="http://127.0.0.1")
            token = re.search(r'const token="([^"]+)"', page.text).group(1)
            self.assertEqual(client.post("/api/run", json={}).status_code, 403)
            self.assertEqual(client.get("/api/status", base_url="http://untrusted.example").status_code, 403)
            headers = {"X-Dashboard-Token": token}
            self.assertEqual(client.post("/api/run", headers=headers, json={"source": "sample", "start": "2026-10-07", "end": "2026-10-01"}).status_code, 400)
            self.assertEqual(client.post("/api/run", headers=headers, json={"source": "sample", "start": "2026-10-07", "end": "2026-10-07", "upload": True}).status_code, 400)
            self.assertEqual(client.get("/api/database?db=invalid").status_code, 400)
            self.assertEqual(client.get("/artifact/unknown/0").status_code, 404)

    def test_live_scraper_uses_login_dates_and_request_timeouts(self):
        # A preceding sample run must not impose its headers on a live report.
        initial = self.pipeline.start("sample", "2026-10-07", "2026-10-07")
        initial.join(15)
        calls = []
        report = '<table><tr class="StyleReportDataHeaderTr"><td>ID</td><td>Amount</td></tr><tr class="StyleReportDataTr"><td>LIVE-1</td><td>42</td></tr></table>'
        def fake_request(session, method, url, **kwargs):
            calls.append((method, url, kwargs))
            response = requests.Response()
            response.status_code = 200
            response._content = ('<a href="sales.php">Sales Report</a><a href="erca.php">ERCA Report 1</a>' if url.endswith("home.php") else report).encode()
            return response
        with patch.dict("os.environ", {"MRK_USERNAME": "test-user", "MRK_PASSWORD": "test-password", "MRK_BASE_URL": "http://local-fixture/reports", "MRK_XML_DIR": ""}), patch.object(requests.Session, "request", fake_request):
            thread = self.pipeline.start("live", "2026-10-01", "2026-10-07")
            thread.join(15)
        self.assertEqual(self.pipeline.snapshot()["status"], "Complete", self.pipeline.snapshot())
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(call[2]["timeout"] == (5, 25) for call in calls))
        self.assertEqual(calls[0][2]["data"]["username"], "test-user")
        self.assertEqual(calls[-1][2]["data"], {"txt_trans_date": "2026-10-01", "txt2_trans_date": "2026-10-07"})


if __name__ == "__main__":
    unittest.main()
