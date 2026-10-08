"""Poll received Maraki ZIP packages and durably track successful imports."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import tempfile
import threading
import zipfile

from db_builder import PROJECT_ROOT, build_database_from_folder
from env_config import get_path

LOG = logging.getLogger("maraki.packages")


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(path):
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


@contextmanager
def single_watcher(path):
    """An OS file lock prevents receiver and standalone watchers racing."""
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
            raise RuntimeError("Another Maraki package watcher is already running") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class PackageWatcher:
    def __init__(self, folder, db_path, state_file, interval=10):
        self.folder = Path(folder).resolve()
        self.db_path = Path(db_path).resolve()
        self.state_file = Path(state_file).resolve()
        self.interval = float(interval)
        if self.interval <= 0:
            raise ValueError("Scan interval must be positive")
        self.state = {"version": 1, "packages": {}}
        if self.state_file.exists():
            # Do not overwrite damaged tracking; stop and report it for repair.
            self.state = json.loads(self.state_file.read_text(encoding="utf-8"))
            if self.state.get("version") != 1 or not isinstance(self.state.get("packages"), dict):
                raise ValueError("Invalid Maraki package session state")
        if self.state.get("database") != str(self.db_path) or self.state.get("folder") != str(self.folder) or not self.db_path.exists():
            self.state["packages"] = {}
        self.state.update(database=str(self.db_path), folder=str(self.folder), interval_seconds=self.interval)
        self.stop = threading.Event()

    def save(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=self.state_file.name + ".", suffix=".tmp", dir=self.state_file.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.state, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.state_file)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def import_package(self, path, expected):
        # Import from a private snapshot, never from stale extracted folders.
        with tempfile.TemporaryDirectory(prefix="maraki-import-") as temporary:
            directory = Path(temporary)
            snapshot = directory / "package.zip"
            digest = hashlib.sha256()
            with path.open("rb") as source, snapshot.open("wb") as target:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                    target.write(chunk)
            if fingerprint(path) != expected:
                raise ValueError("Package changed while being copied; will retry")
            extracted = directory / "extracted"
            extracted.mkdir()
            with zipfile.ZipFile(snapshot) as archive:
                for member in archive.infolist():
                    parts = PurePosixPath(member.filename.replace("\\", "/"))
                    destination = (extracted / str(parts)).resolve()
                    if parts.is_absolute() or ".." in parts.parts or not destination.is_relative_to(extracted):
                        raise ValueError("Package contains an unsafe extraction path")
                    if member.is_dir():
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, destination.open("wb") as target:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            target.write(chunk)
            csv_files = sorted(p for p in extracted.rglob("*") if p.is_file() and p.suffix.lower() == ".csv")
            if not csv_files or any(p.stat().st_size == 0 for p in csv_files):
                raise ValueError("Package contains no CSV reports or an empty CSV report")
            for parent in sorted({p.parent for p in csv_files}):
                build_database_from_folder(str(parent), str(self.db_path))
            return digest.hexdigest(), len(csv_files)

    def scan_once(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        self.state["last_scan_at"] = now()
        if not self.db_path.exists():
            for record in self.state["packages"].values():
                if record.get("status") == "imported":
                    record["status"] = "pending"
        for path in sorted(self.folder.iterdir()):
            if not path.is_file() or path.suffix.lower() != ".zip":
                continue
            try:
                current = fingerprint(path)
            except OSError:
                continue
            record = self.state["packages"].get(path.name)
            if not record or record.get("fingerprint") != current:
                self.state["packages"][path.name] = {"fingerprint": current, "status": "pending", "detected_at": now(), "attempts": 0}
                LOG.info("Detected package %s; waiting for a stable scan", path.name)
                continue
            if record.get("status") == "imported":
                continue
            record["attempts"] += 1
            record["last_attempt_at"] = now()
            try:
                digest, count = self.import_package(path, current)
                record.update(status="imported", sha256=digest, csv_files=count, imported_at=now())
                record.pop("error", None)
                LOG.info("Imported package %s (%s CSV reports)", path.name, count)
            except Exception as exc:
                record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                LOG.warning("Failed package %s; will retry: %s", path.name, exc)
        self.save()

    def run_forever(self):
        try:
            with single_watcher(self.state_file.with_suffix(self.state_file.suffix + ".lock")):
                LOG.info("Watching %s every %s seconds; database %s", self.folder, self.interval, self.db_path)
                while not self.stop.is_set():
                    try:
                        self.scan_once()
                    except Exception:
                        LOG.exception("Package scan failed; retrying next interval")
                    self.stop.wait(self.interval)
        except RuntimeError as exc:
            LOG.warning("%s", exc)


def configured_watcher():
    folder = get_path("MRK_UPLOAD_DIR", "host/mrk-host/received_packages", PROJECT_ROOT)
    db_path = get_path("MRK_DATABASE_PATH", "database/marak.db", PROJECT_ROOT)
    state_file = get_path("MRK_PACKAGE_STATE_FILE", "host/mrk-host/package_session_state.json", PROJECT_ROOT)
    return PackageWatcher(folder, db_path, state_file, os.environ.get("MRK_PACKAGE_SCAN_INTERVAL_SECONDS", "10"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, help="Seconds between scans (default 10)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    watcher = configured_watcher()
    if args.interval is not None:
        if args.interval <= 0:
            parser.error("Interval must be positive")
        watcher.interval = args.interval
        watcher.state["interval_seconds"] = args.interval
    try:
        watcher.run_forever()
    except KeyboardInterrupt:
        watcher.stop.set()


if __name__ == "__main__":
    main()
