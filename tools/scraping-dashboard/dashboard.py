"""Temporary local Maraki pipeline tester. Run with --client-dir C:/client."""
import argparse
import csv
from contextlib import closing
from datetime import date, datetime
import importlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import threading
import zipfile

from flask import Flask, abort, jsonify, render_template, request, send_file

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from env_config import get_path, load_env_file


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def database_snapshot(path, table=None, page=0, search=""):
    path = Path(path).resolve()
    if not path.is_file():
        return {"path": str(path), "tables": [], "columns": [], "rows": [], "total": 0, "page": 0}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)) as connection:
        names = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tables = [{"name": name, "count": connection.execute(f"SELECT COUNT(*) FROM {quote(name)}").fetchone()[0]} for name in names]
        selected = table or ("sales" if "sales" in names else names[0] if names else None)
        if selected and selected not in names:
            raise ValueError("Unknown table")
        result = {"path": str(path), "tables": tables, "table": selected, "columns": [], "rows": [], "total": 0, "page": page}
        if not selected:
            return result
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({quote(selected)})")]
        where, params = "", []
        if search:
            where = " WHERE " + " OR ".join(f"instr(lower(CAST({quote(col)} AS TEXT)), lower(?)) > 0" for col in columns)
            params = [search] * len(columns)
        total = connection.execute(f"SELECT COUNT(*) FROM {quote(selected)}{where}", params).fetchone()[0]
        page = min(max(page, 0), max((total - 1) // 50, 0))
        rows = connection.execute(f"SELECT * FROM {quote(selected)}{where} LIMIT 50 OFFSET ?", params + [page * 50]).fetchall()
        result.update(columns=columns, rows=rows, total=total, page=page)
        return result


class Pipeline:
    def __init__(self, client_dir, storage, host_db):
        self.client_dir = Path(client_dir).resolve()
        self.storage = Path(storage).resolve()
        self.test_db = self.storage / "maraki_db.sqlite"
        self.host_db = Path(host_db).resolve()
        self.lock = threading.Lock()
        self.state = {"running": False, "status": "Ready", "stage": "idle", "logs": [], "artifacts": [], "run_id": None}
        self.files = {}

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def event(self, stage, message):
        with self.lock:
            self.state["stage"] = stage
            self.state["logs"].append({"time": datetime.now().strftime("%H:%M:%S"), "stage": stage, "message": message})
            self.state["logs"] = self.state["logs"][-200:]

    def start(self, source, start, end, upload=False):
        if source not in ("sample", "live"):
            raise ValueError("Choose sample or live")
        if date.fromisoformat(end) < date.fromisoformat(start):
            raise ValueError("End date must be on or after start date")
        if source == "sample" and upload:
            raise ValueError("Sample reports cannot be uploaded to the host")
        with self.lock:
            if self.state["running"]:
                raise RuntimeError("A pipeline is already running")
            self.state.update(running=True, status="Running", stage="starting", logs=[], artifacts=[], run_id=secrets.token_hex(6))
            self.files = {}
        thread = threading.Thread(target=self.run, args=(source, start, end, upload), daemon=True)
        thread.start()
        return thread

    def run(self, source, start, end, upload):
        session = None
        try:
            run_dir = self.storage / "runs" / self.state["run_id"]
            run_dir.mkdir(parents=True)
            if not (self.client_dir / "mrk-client" / "page_scraper.py").is_file():
                raise ValueError(f"Scraper not found in {self.client_dir}. Restart with --client-dir pointing to the client folder.")
            sys.path.insert(0, str(self.client_dir / "mrk-client"))
            load_env_file(self.client_dir / ".env")
            scraper_module = importlib.import_module("page_scraper")
            organizer_module = importlib.import_module("data_organizer")
            self.event("scrape", "Using offline sample reports" if source == "sample" else "Connecting to Maraki; historical auto-scraping disabled")
            # Avoid the constructor's writes to the deployed client's normal report directory.
            scraper = scraper_module.PageScraper.__new__(scraper_module.PageScraper)
            import requests
            session = requests.Session()
            original_request = session.request
            def bounded_request(method, url, **kwargs):
                kwargs.setdefault("timeout", (5, 25))
                response = original_request(method, url, **kwargs)
                response.raise_for_status()
                return response
            session.request = bounded_request
            scraper.session = session
            scraper.username = os.environ.get("MRK_USERNAME")
            scraper.password = os.environ.get("MRK_PASSWORD")
            scraper.base_url = os.environ.get("MRK_BASE_URL", "http://127.0.0.1/MarakiReports2012").rstrip("/")
            scraper.data_path = str(run_dir)
            scraper.sales_file = scraper.erca_file = None
            if source == "sample":
                sample = '<table><tr class="StyleReportDataHeaderTr"><td>Ref Note</td><td>Amount</td><td>Transaction Date</td><td>Cashier</td></tr>'
                sample += ''.join(f'<tr class="StyleReportDataTr"><td>TEST-{i}</td><td>{i * 100}.00</td><td>{start} 09:00:00</td><td>Sample cashier</td></tr>' for i in range(1, 4)) + '</table>'
                scraper.scrape_site = lambda *_: (sample, sample)
            scraper.store_site_data(start, end)
            if not scraper.sales_file or not scraper.erca_file:
                raise ValueError("Both Sales and ERCA reports are required. Check credentials, report links, and selected dates.")
            xml = os.environ.get("MRK_XML_DIR")
            if xml and source == "live":
                self.event("timestamps", "Applying local XML timestamps")
                from accurate_time import AccurateTimeManager
                AccurateTimeManager(scraper.sales_file, str(get_path("MRK_XML_DIR", xml, self.client_dir))).process_sales_file()
            self.event("convert", "Converting HTML reports with the client DataOrganizer")
            sales, erca = organizer_module.DataOrganizer(scraper.sales_file, scraper.erca_file).process_all()
            for filename in (sales, erca):
                if not filename or not Path(filename).is_file():
                    raise ValueError("No report table found in the returned HTML. The response may be a login page.")
                with open(filename, encoding="utf-8", newline="") as handle:
                    rows = list(csv.reader(handle))
                if not rows or not rows[0] or len(set(rows[0])) != len(rows[0]) or any(not h.strip() for h in rows[0]):
                    raise ValueError("Report has missing or duplicate column headers")
                self.event("convert", f"{Path(filename).name}: {len(rows) - 1} rows")
            self.event("package", "Packaging both CSV reports with the client MRKSender")
            from mrk_sender import MRKSender
            sender = MRKSender.__new__(MRKSender)
            sender.sales_csv, sender.erca_csv = sales, erca
            sender.start_date, sender.end_date, sender.source_ip = start, end, "127.0.0.1"
            archive = Path(sender.package_wrapper())
            self.event("extract", "Extracting package into the local test receiver folder")
            extract = run_dir / "received"
            with zipfile.ZipFile(archive) as package:
                package.extractall(extract)
            self.event("import", "Importing into the test maraki_db using the host database builder")
            sys.path.insert(0, str(ROOT / "host" / "mrk-host"))
            from db_builder import build_database_from_folder
            run_database = run_dir / "maraki_db.sqlite"
            build_database_from_folder(str(extract), str(run_database))
            # Every run gets a fresh schema: sample columns need not match live reports.
            # SQLite backup publishes the latest successful run while readers remain safe.
            with closing(sqlite3.connect(run_database)) as source_db, closing(sqlite3.connect(self.test_db, timeout=5)) as target_db:
                source_db.backup(target_db)
            if upload:
                self.event("upload", "Uploading live reports to the configured host receiver")
                url = f'http://{os.environ.get("MRK_RECEIVER_HOST", "192.168.1.2")}:{os.environ.get("MRK_RECEIVER_PORT", "8000")}/upload'
                with archive.open("rb") as handle:
                    response = session.post(url, files={"file": (archive.name, handle)})
                self.event("upload", f"Host accepted package (HTTP {response.status_code})")
            with self.lock:
                self.files = {str(index): p for index, p in enumerate([Path(scraper.sales_file), Path(scraper.erca_file), Path(sales), Path(erca), archive])}
                self.state["artifacts"] = [{"id": key, "name": p.name, "bytes": p.stat().st_size} for key, p in self.files.items()]
                self.state["status"] = "Complete"
            self.event("complete", "Pipeline complete. Test database is ready to inspect.")
        except Exception as exc:
            # Do not echo arbitrary HTTP responses, URLs, or credentials into the UI.
            message = str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}: pipeline failed at {self.state['stage']}. Check service availability and report format."
            self.event("error", message)
            with self.lock:
                self.state["status"] = "Failed"
        finally:
            if session:
                session.close()
            with self.lock:
                self.state["running"] = False


def create_app(pipeline):
    app = Flask(__name__)
    token = secrets.token_urlsafe(32)
    @app.before_request
    def local_only():
        if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
            abort(403)
        if request.method == "POST" and not secrets.compare_digest(request.headers.get("X-Dashboard-Token", ""), token):
            abort(403)

    @app.get("/")
    def index():
        return render_template("dashboard.html", token=token, today=date.today().isoformat(), client=str(pipeline.client_dir))

    @app.get("/api/status")
    def status():
        return jsonify(pipeline.snapshot())

    @app.post("/api/run")
    def run():
        data = request.get_json() or {}
        try:
            pipeline.start(data.get("source", "sample"), data.get("start", ""), data.get("end", ""), data.get("upload") is True)
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 409
        return jsonify(ok=True), 202

    @app.get("/api/database")
    def database():
        choice = request.args.get("db", "test")
        path = pipeline.test_db if choice == "test" else pipeline.host_db if choice == "host" else None
        if path is None:
            return jsonify(error="Unknown database"), 400
        try:
            return jsonify(database_snapshot(path, request.args.get("table"), int(request.args.get("page", "0")), request.args.get("search", "")[:200]))
        except (ValueError, sqlite3.Error) as exc:
            return jsonify(error=str(exc)), 400

    @app.get("/artifact/<run_id>/<key>")
    def artifact(run_id, key):
        with pipeline.lock:
            path = pipeline.files.get(key) if run_id == pipeline.state["run_id"] else None
        if path is None:
            abort(404)
        return send_file(path, as_attachment=True)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-dir", type=Path, default=ROOT / "client" if (ROOT / "client").exists() else Path("C:/client"))
    parser.add_argument("--database", type=Path, help="Existing database to inspect (read-only)")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    # Client settings take precedence for scraping and uploads; host settings supply the DB path.
    load_env_file(args.client_dir / ".env")
    load_env_file(ROOT / "host" / ".env")
    host_db = args.database or get_path("MRK_DATABASE_PATH", "database/marak.db", ROOT)
    pipeline = Pipeline(args.client_dir, HERE / "runtime", host_db)
    create_app(pipeline).run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
