"""Portable Maraki client identity, durable progress signal and scrape runner."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import csv
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import uuid

import requests
from env_config import get_path, load_env_file

LOG = logging.getLogger("maraki.client")
ACTIVE = {"starting", "scraping", "timestamps", "organizing", "packaging", "uploading"}


@contextmanager
def local_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Another scraping process is already active") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_json(path, data):
    descriptor, temporary = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Signal:
    def __init__(self, root=None):
        self.root = Path(root or Path(__file__).resolve().parent).resolve()
        load_env_file(self.root / ".env")
        self.directory = get_path("MRK_AGENT_STATE_DIR", "mrk-agent-data", self.root)
        self.directory.mkdir(parents=True, exist_ok=True)
        machine = uuid.uuid5(uuid.NAMESPACE_DNS, f"{socket.gethostname().lower()}:{uuid.getnode()}").hex
        with local_lock(self.directory / "identity.lock"):
            identity_file = self.directory / "identity.json"
            if identity_file.exists():
                identity = json.loads(identity_file.read_text(encoding="utf-8"))
            else:
                identity = {}
            moved = identity.get("machine_id") not in (None, machine)
            if moved or not identity.get("client_id"):
                identity = {"client_id": uuid.uuid4().hex}
            if identity.get("machine_id") != machine:
                identity["machine_id"] = machine
                atomic_json(identity_file, identity)
            if moved:
                # A copied deployment must not inherit another PC's active run.
                atomic_json(self.directory / "signal.json", {"phase": "idle", "message": "Ready for a scrape request"})
        self.client_id = os.environ.get("MRK_CLIENT_ID") or identity["client_id"]
        self.name = os.environ.get("MRK_CLIENT_NAME") or socket.gethostname()
        self.path = self.directory / "signal.json"
        self.url = os.environ.get("MRK_CONTROL_URL") or f'http://{os.environ.get("MRK_RECEIVER_HOST", "192.168.1.2")}:{os.environ.get("MRK_RECEIVER_PORT", "8000")}'
        self.url = self.url.rstrip("/")
        self.headers = {"X-MRK-Token": os.environ.get("MRK_CONTROL_CLIENT_TOKEN", "")}

    def read(self):
        if self.path.exists():
            state = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            state = {"phase": "idle", "message": "Ready for a scrape request"}
        state.update(client_id=self.client_id, name=self.name, hostname=socket.gethostname(),
                     report_url=os.environ.get("MRK_BASE_URL", "http://127.0.0.1/MarakiReports2012").rstrip("/"))
        return state

    def emit(self, phase, message, **values):
        state = self.read()
        state.update(phase=phase, message=message, updated_at=datetime.now(timezone.utc).isoformat(), **values)
        atomic_json(self.path, state)
        LOG.info("%s | %s", phase.upper(), message)
        return state

    def heartbeat(self):
        response = requests.post(self.url + "/api/clients/heartbeat", json=self.read(), headers=self.headers, timeout=(3, 5))
        response.raise_for_status()
        return response.json()

    def next_command(self):
        response = requests.post(self.url + f"/api/clients/{self.client_id}/commands/next", headers=self.headers, timeout=(3, 5))
        response.raise_for_status()
        return response.json().get("command")


def ensure_agent(root=None):
    signal = Signal(root)
    script = signal.root / "mrk_agent.py"
    if not script.is_file():
        return
    with (signal.directory / "agent.log").open("ab") as output:
        subprocess.Popen([sys.executable, "-u", "-B", str(script)], cwd=signal.root,
                         stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def run_pipeline(start, end, command_id="", root=None):
    if date.fromisoformat(end) < date.fromisoformat(start):
        raise ValueError("End date must be on or after start date")
    signal = Signal(root)
    with local_lock(signal.directory / "scrape.lock"):
        run_id = command_id or uuid.uuid4().hex
        signal.emit("starting", "Scrape started", run_id=run_id, command_id=command_id,
                    start_date=start, end_date=end, package="")
        session = None
        try:
            sys.path.insert(0, str(signal.root / "mrk-client"))
            from page_scraper import PageScraper
            from data_organizer import DataOrganizer
            from accurate_time import AccurateTimeManager
            from mrk_sender import MRKSender
            folder = get_path("MRK_DATA_DIR", "C:/Client-data/mrk-data", signal.root) / "agent-runs" / run_id
            folder.mkdir(parents=True, exist_ok=True)
            scraper = PageScraper.__new__(PageScraper)
            session = requests.Session()
            original_request = session.request
            def bounded_request(method, url, **kwargs):
                kwargs.setdefault("timeout", (5, 30))
                response = original_request(method, url, **kwargs)
                response.raise_for_status()
                return response
            session.request = bounded_request
            scraper.session = session
            scraper.username = os.environ.get("MRK_USERNAME")
            scraper.password = os.environ.get("MRK_PASSWORD")
            scraper.base_url = signal.read()["report_url"]
            scraper.data_path = str(folder)
            scraper.sales_file = scraper.erca_file = None
            signal.emit("scraping", "Fetching Sales and ERCA reports")
            scraper.store_site_data(start, end)
            if not scraper.sales_file or not scraper.erca_file:
                raise ValueError("Maraki did not return both reports; check login and report links")
            if os.environ.get("MRK_XML_DIR"):
                signal.emit("timestamps", "Applying timestamps from local XML files")
                AccurateTimeManager(scraper.sales_file, str(get_path("MRK_XML_DIR", "", signal.root))).process_sales_file()
            signal.emit("organizing", "Converting reports to CSV")
            sales, erca = DataOrganizer(scraper.sales_file, scraper.erca_file).process_all()
            for path in (sales, erca):
                if not path or not Path(path).is_file():
                    raise ValueError("Report response has no valid data table; check Maraki login")
                with open(path, encoding="utf-8", newline="") as handle:
                    headers = next(csv.reader(handle), [])
                if not headers or any(not h.strip() for h in headers):
                    raise ValueError("Report is missing column headers")
            signal.emit("packaging", "Creating report package")
            sender = MRKSender.__new__(MRKSender)
            sender.sales_csv, sender.erca_csv = sales, erca
            sender.start_date, sender.end_date, sender.source_ip = start, end, signal.client_id
            archive = Path(sender.package_wrapper())
            signal.emit("uploading", "Sending package to host")
            with archive.open("rb") as handle:
                response = session.post(signal.url + "/upload", files={"file": (archive.name, handle)},
                                        data={"client_id": signal.client_id, "run_id": run_id}, headers=signal.headers)
            package = response.json()["package"]
            signal.emit("awaiting_import", "Upload accepted; waiting for host database import", package=package)
            return package
        except Exception as exc:
            message = str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}: scraping or upload failed; check connection and credentials"
            signal.emit("failed", message)
            raise
        finally:
            if session:
                session.close()


def run_historical(root=None):
    signal = Signal(root)
    with local_lock(signal.directory / "scrape.lock"):
        sys.path.insert(0, str(signal.root / "mrk-client"))
        from retro_scraper import get_state, run_retro_pipeline
        before = get_state()
        signal.emit("scraping", "Historical scrape running", run_id=uuid.uuid4().hex,
                    command_id="", package="", start_date="", end_date="")
        try:
            run_retro_pipeline()
            after = get_state()
            if before["current_offset"] > 5 or before["last_run"] == date.today().isoformat():
                signal.emit("idle", "Historical reports already up to date")
            elif after["current_offset"] > before["current_offset"]:
                signal.emit("completed", "Historical reports uploaded and imported by the retro receiver")
            else:
                raise ValueError("Historical upload did not confirm success; inspect the client log")
        except Exception as exc:
            signal.emit("failed", str(exc) if isinstance(exc, ValueError) else "Historical scrape failed: " + type(exc).__name__)
            raise
