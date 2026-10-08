from contextlib import closing
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import requests
from client_control import ControlStore
import mrk_receiver
from package_watcher import PackageWatcher

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "client-agent-update"), str(ROOT), "C:/client"]
from mrk_signal import Signal, run_pipeline, local_lock
from mrk_agent import tick, recover


class ClientControlTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.client_root = self.root / "client"
        self.client_root.mkdir()
        shutil.copytree(ROOT / "client/mrk-client", self.client_root / "mrk-client", ignore=shutil.ignore_patterns("__pycache__"))
        self.uploads = self.root / "received"
        self.uploads.mkdir()
        self.package_state = self.root / "packages.json"
        self.store = ControlStore(self.root / "control.sqlite")
        self.env = patch.dict(os.environ, {
            "MRK_AGENT_STATE_DIR": str(self.root / "agent"), "MRK_CLIENT_ID": "client-a", "MRK_CLIENT_NAME": "Cashier A",
            "MRK_CONTROL_CLIENT_TOKEN": "client-secret", "MRK_CONTROL_URL": "http://host.test",
            "MRK_DATA_DIR": str(self.root / "reports"), "MRK_BASE_URL": "http://maraki.test/reports",
            "MRK_USERNAME": "user", "MRK_PASSWORD": "secret", "MRK_XML_DIR": "",
            "MRK_PACKAGE_STATE_FILE": str(self.package_state),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = patch.dict(mrk_receiver.app.config, {"CONTROL_STORE": self.store, "MRK_CONTROL_CLIENT_TOKEN": "client-secret", "MRK_CONTROL_ADMIN_TOKEN": "admin-secret", "ALLOW_HOST_CLIENT": True})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.folder_patch = patch.object(mrk_receiver, "UPLOAD_FOLDER", str(self.uploads))
        self.folder_patch.start()
        self.addCleanup(self.folder_patch.stop)
        self.signal = Signal(self.client_root)
        self.admin = {"X-MRK-Token": "admin-secret"}
        self.client_headers = {"X-MRK-Token": "client-secret"}
        self.calls = []

    def adapter(self, session, method, url, **kwargs):
        self.calls.append((method, url))
        response = requests.Response()
        if url.startswith("http://host.test"):
            data = (kwargs.get("data") or {}).copy()
            if "files" in kwargs:
                filename, handle = kwargs["files"]["file"]
                data["file"] = (io.BytesIO(handle.read()), filename)
            with mrk_receiver.app.test_client() as client:
                result = client.open(urlsplit(url).path, method=method, json=kwargs.get("json"),
                                     data=data or None, headers=kwargs.get("headers", {}))
            response.status_code = result.status_code
            response._content = result.data
        else:
            response.status_code = 200
            if url.endswith("home.php"):
                text = '<a href="sales.php">Sales Report</a><a href="erca.php">ERCA Report 1</a>'
            else:
                text = '<table><tr class="StyleReportDataHeaderTr"><td>ID</td><td>Amount</td></tr><tr class="StyleReportDataTr"><td>1</td><td>10</td></tr></table>'
            response._content = text.encode()
        return response

    def test_admin_command_to_client_to_host_import_complete(self):
        with patch.object(requests.Session, "request", lambda session, method, url, **kwargs: self.adapter(session, method, url, **kwargs)):
            self.signal.heartbeat()
            with mrk_receiver.app.test_client() as client:
                response = client.post("/api/clients/client-a/scrape", headers=self.admin,
                                       json={"start_date": "2026-10-01", "end_date": "2026-10-07"})
            self.assertEqual(response.status_code, 202)
            worker = tick(self.signal)
            worker.join(10)
            self.assertFalse(worker.is_alive())
            state = self.signal.read()
            self.assertEqual(state["phase"], "awaiting_import", state)
            self.assertEqual(state["command_id"], response.json["command"]["id"])
            self.assertTrue(state["package"].startswith("client-a_"))
            watcher = PackageWatcher(self.uploads, self.root / "marak.db", self.package_state)
            watcher.scan_once()
            watcher.scan_once()
            tick(self.signal, worker)
            self.assertEqual(self.signal.read()["phase"], "completed")
            self.assertIsNone(self.signal.next_command())
            with mrk_receiver.app.test_client() as client:
                status = client.get("/api/clients", headers=self.admin).json["clients"][0]
            self.assertTrue(status["online"])
            self.assertEqual(status["import_status"], "imported")
            self.assertEqual(status["phase"], "completed")
            self.assertEqual(sum(url.endswith("/upload") for _, url in self.calls), 1)
            report_calls = [url for _, url in self.calls if url.endswith(("home.php", "sales.php", "erca.php"))]
            self.assertTrue(report_calls)
            self.assertTrue(all(url.startswith("http://maraki.test/reports/") for url in report_calls))

    def test_authentication_offline_and_duplicate_command_rejection(self):
        with mrk_receiver.app.test_client() as client:
            self.assertEqual(client.get("/api/clients").status_code, 401)
            self.assertEqual(client.get("/api/clients", headers=self.client_headers).status_code, 401)
            self.assertEqual(client.post("/api/clients/heartbeat", headers=self.admin, json={}).status_code, 401)
            client.post("/api/clients/heartbeat", headers=self.client_headers, json=self.signal.read())
            payload = {"start_date": "2026-10-01", "end_date": "2026-10-07"}
            first = client.post("/api/clients/client-a/scrape", headers=self.admin, json=payload)
            self.assertEqual(first.status_code, 202)
            self.assertEqual(client.post("/api/clients/client-a/scrape", headers=self.admin, json=payload).status_code, 409)
            self.assertEqual(client.post("/api/clients/client-a/commands/next", headers=self.admin).status_code, 401)
            with closing(self.store.connect()) as conn, conn:
                conn.execute("UPDATE clients SET seen=?", (time.time() - 100,))
            self.assertFalse(client.get("/api/clients", headers=self.admin).json["clients"][0]["online"])
            self.assertEqual(client.post("/api/clients/client-a/scrape", headers=self.admin, json=payload).status_code, 409)

    def test_restarts_keep_identity_and_commands_and_do_not_replay_interrupted_run(self):
        self.store.heartbeat(self.signal.read(), "127.0.0.1")
        command = self.store.request_scrape("client-a", "2026-10-01", "2026-10-07")
        restarted = ControlStore(self.store.path)
        self.assertEqual(restarted.next_command("client-a")["id"], command["id"])
        self.assertEqual(restarted.next_command("client-a")["id"], command["id"])
        self.signal.emit("scraping", "in progress", command_id=command["id"])
        recover(self.signal)
        self.assertEqual(self.signal.read()["phase"], "failed")
        self.store.heartbeat(self.signal.read(), "127.0.0.1")
        self.assertIsNone(restarted.next_command("client-a"))
        self.assertEqual(Signal(self.client_root).client_id, self.signal.client_id)

    def test_scrape_lock_and_failure_signal(self):
        with local_lock(self.signal.directory / "scrape.lock"):
            with self.assertRaises(RuntimeError):
                run_pipeline("2026-10-01", "2026-10-07", root=self.client_root)
        with patch.object(requests.Session, "request", side_effect=requests.ConnectionError("connection failed")):
            with self.assertRaises(requests.ConnectionError):
                run_pipeline("2026-10-01", "2026-10-07", root=self.client_root)
        self.assertEqual(self.signal.read()["phase"], "failed")
        self.assertIn("ConnectionError", self.signal.read()["message"])

    def test_copied_machine_identity_is_reset_and_same_machine_is_stable(self):
        with patch.dict(os.environ, {"MRK_CLIENT_ID": ""}):
            original = Signal(self.client_root)
            original.emit("scraping", "Copied active run", command_id="old-command")
            self.assertEqual(Signal(self.client_root).client_id, original.client_id)
            with patch("mrk_signal.socket.gethostname", return_value="another-client-pc"):
                moved = Signal(self.client_root)
                self.assertNotEqual(moved.client_id, original.client_id)
                self.assertEqual(moved.read()["phase"], "idle")
                self.assertNotIn("command_id", moved.read())
                self.assertEqual(Signal(self.client_root).client_id, moved.client_id)

    def test_remote_addresses_and_commands_stay_on_selected_client(self):
        with mrk_receiver.app.test_client() as client:
            for identifier, address in (("client-a", "192.0.2.21"), ("client-b", "192.0.2.22")):
                state = dict(self.signal.read(), client_id=identifier, name=identifier)
                self.assertEqual(client.post("/api/clients/heartbeat", json=state, headers=self.client_headers,
                                             environ_base={"REMOTE_ADDR": address}).status_code, 200)
            rows = {row["id"]: row for row in client.get("/api/clients", headers=self.admin).json["clients"]}
            self.assertEqual(rows["client-a"]["ip"], "192.0.2.21")
            self.assertEqual(rows["client-b"]["ip"], "192.0.2.22")
            self.assertFalse(rows["client-a"]["is_host"])
            result = client.post("/api/clients/client-b/scrape", headers=self.admin,
                                 json={"start_date": "2026-10-01", "end_date": "2026-10-07"})
            self.assertEqual(result.status_code, 202)
            self.assertIsNone(self.store.next_command("client-a"))
            self.assertEqual(self.store.next_command("client-b")["id"], result.json["command"]["id"])

    def test_host_agent_cannot_be_scraped_as_remote_client(self):
        with patch.dict(mrk_receiver.app.config, {"ALLOW_HOST_CLIENT": False}), mrk_receiver.app.test_client() as client:
            client.post("/api/clients/heartbeat", json=self.signal.read(), headers=self.client_headers)
            row = client.get("/api/clients", headers=self.admin).json["clients"][0]
            self.assertTrue(row["is_host"])
            result = client.post("/api/clients/client-a/scrape", headers=self.admin,
                                 json={"start_date": "2026-10-01", "end_date": "2026-10-07"})
            self.assertEqual(result.status_code, 409)
            self.assertIn("host PC", result.json["error"])

    def test_local_reports_default_is_independent_of_receiver_address(self):
        with patch.dict(os.environ):
            os.environ.pop("MRK_BASE_URL", None)
            os.environ["MRK_RECEIVER_HOST"] = "192.0.2.10"
            os.environ.pop("MRK_CONTROL_URL", None)
            signal = Signal(self.client_root)
            self.assertEqual(signal.url, "http://192.0.2.10:8000")
            self.assertEqual(signal.read()["report_url"], "http://127.0.0.1/MarakiReports2012")


if __name__ == "__main__":
    unittest.main()
