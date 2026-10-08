"""Durable client heartbeats and scrape commands, separate from report data."""
from contextlib import closing
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

PHASES = {"idle", "starting", "scraping", "timestamps", "organizing", "packaging", "uploading", "awaiting_import", "completed", "failed"}
BUSY = PHASES - {"idle", "completed", "failed"}


def client_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value):
        raise ValueError("Invalid client ID")
    return value


class ControlStore:
    def __init__(self, path, offline_seconds=45):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.offline_seconds = offline_seconds
        with closing(self.connect()) as conn, conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS clients (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, ip TEXT NOT NULL,
                    seen REAL NOT NULL, phase TEXT NOT NULL, signal TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY, client_id TEXT NOT NULL,
                    start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                    created REAL NOT NULL, status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS command_client ON commands(client_id, status);
            ''')

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def heartbeat(self, data, ip, is_host=False):
        identifier = client_id(data.get("client_id"))
        phase = data.get("phase", "idle")
        if phase not in PHASES:
            raise ValueError("Invalid scrape phase")
        signal = {key: str(data.get(key, ""))[:240] for key in ("message", "run_id", "command_id", "start_date", "end_date", "package", "updated_at", "hostname", "report_url")}
        signal["is_host"] = bool(is_host)
        name = str(data.get("name") or identifier)[:100]
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT INTO clients VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, ip=excluded.ip, seen=excluded.seen, phase=excluded.phase, signal=excluded.signal",
                         (identifier, name, ip, time.time(), phase, json.dumps(signal)))
            if signal["command_id"]:
                status = "completed" if phase == "completed" else "failed" if phase == "failed" else "running"
                terminal = "('completed')" if phase == "completed" else "('completed','failed')"
                conn.execute(f"UPDATE commands SET status=? WHERE id=? AND client_id=? AND status NOT IN {terminal}",
                             (status, signal["command_id"], identifier))
        return identifier

    def request_scrape(self, identifier, start, end):
        identifier = client_id(identifier)
        if date.fromisoformat(end) < date.fromisoformat(start):
            raise ValueError("End date must be on or after start date")
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            client = conn.execute("SELECT * FROM clients WHERE id=?", (identifier,)).fetchone()
            if not client or time.time() - client["seen"] > self.offline_seconds:
                raise ValueError("Client is offline; start its scraping agent first")
            if client["phase"] in BUSY or conn.execute("SELECT 1 FROM commands WHERE client_id=? AND status IN ('queued','dispatched','running')", (identifier,)).fetchone():
                raise ValueError("Client already has a scrape running or queued")
            command = {"id": uuid.uuid4().hex, "client_id": identifier, "start_date": start, "end_date": end}
            conn.execute("INSERT INTO commands VALUES (?,?,?,?,?,?)", (command["id"], identifier, start, end, time.time(), "queued"))
            return command

    def next_command(self, identifier):
        identifier = client_id(identifier)
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM commands WHERE client_id=? AND status IN ('queued','dispatched') ORDER BY created LIMIT 1", (identifier,)).fetchone()
            if not row:
                return None
            # Re-deliver until a heartbeat acknowledges start; client also keeps a durable run ID.
            conn.execute("UPDATE commands SET status='dispatched' WHERE id=?", (row["id"],))
            return dict(row)

    def clients(self, package_state=None):
        packages = (package_state or {}).get("packages", {})
        with closing(self.connect()) as conn:
            result = []
            for row in conn.execute("SELECT * FROM clients ORDER BY name,id"):
                client = dict(row)
                client.update(json.loads(client.pop("signal")))
                client["online"] = time.time() - client["seen"] <= self.offline_seconds
                client["last_seen"] = datetime.fromtimestamp(client["seen"], timezone.utc).isoformat()
                package = packages.get(client.get("package", ""), {})
                client["import_status"] = package.get("status", "pending" if client.get("package") else "")
                client["import_error"] = package.get("error", "")
                queued = conn.execute("SELECT status FROM commands WHERE client_id=? AND status IN ('queued','dispatched','running') ORDER BY created LIMIT 1", (client["id"],)).fetchone()
                client["command_status"] = queued[0] if queued else ""
                result.append(client)
            return result
