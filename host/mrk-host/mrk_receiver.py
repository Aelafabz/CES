import os
import sys
from pathlib import Path
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import zipfile
import tempfile
import threading
import logging
import hmac
import secrets
import socket
import ipaddress
from client_control import ControlStore, client_id
from package_watcher import configured_watcher

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
from env_config import get_path, load_env_file, save_env_values

load_env_file(PROJECT_ROOT / "host" / ".env")

app = Flask(__name__)
UPLOAD_FOLDER = str(get_path(
    "MRK_UPLOAD_DIR", os.path.join("host", "mrk-host", "received_packages"), PROJECT_ROOT))
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def ensure_control_tokens():
    updates = {}
    for key in ("MRK_CONTROL_CLIENT_TOKEN", "MRK_CONTROL_ADMIN_TOKEN"):
        if not os.environ.get(key):
            updates[key] = secrets.token_hex(32)
    if updates:
        save_env_values(PROJECT_ROOT / "host" / ".env", updates)


def authorized(role):
    key = "MRK_CONTROL_ADMIN_TOKEN" if role == "admin" else "MRK_CONTROL_CLIENT_TOKEN"
    token = app.config.get(key) or os.environ.get(key)
    return bool(token) and hmac.compare_digest(request.headers.get("X-MRK-Token", ""), token)


def store():
    if "CONTROL_STORE" not in app.config:
        app.config["CONTROL_STORE"] = ControlStore(get_path("MRK_CLIENT_CONTROL_DB", "host/mrk-host/client_control.sqlite", PROJECT_ROOT))
    return app.config["CONTROL_STORE"]


def tracked_clients():
    path = get_path("MRK_PACKAGE_STATE_FILE", "host/mrk-host/package_session_state.json", PROJECT_ROOT)
    try:
        import json
        packages = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        packages = {}
    rows = store().clients(packages)
    for row in rows:
        row["is_host"] = is_host_client(row, row["ip"])
    return rows


def is_host_client(data, peer):
    try:
        address = ipaddress.ip_address(peer)
        if address.is_loopback:
            return True
    except ValueError:
        return False
    try:
        local = {record[4][0] for record in socket.getaddrinfo(socket.gethostname(), None)}
    except OSError:
        local = set()
    return peer in local


@app.post('/api/clients/heartbeat')
def heartbeat():
    if not authorized("client"):
        return jsonify(error="Client authentication required"), 401
    try:
        data = request.get_json() or {}
        identifier = store().heartbeat(data, request.remote_addr, is_host_client(data, request.remote_addr))
        status = next(row for row in tracked_clients() if row["id"] == identifier)
        return jsonify(import_status=status["import_status"])
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400


@app.get('/api/clients')
def clients():
    if not authorized("admin"):
        return jsonify(error="Admin authentication required"), 401
    return jsonify(clients=tracked_clients())


@app.post('/api/clients/<identifier>/scrape')
def request_scrape(identifier):
    if not authorized("admin"):
        return jsonify(error="Admin authentication required"), 401
    data = request.get_json() or {}
    try:
        target = next((row for row in tracked_clients() if row["id"] == identifier), None)
        if target and target["is_host"] and not app.config.get("ALLOW_HOST_CLIENT", os.environ.get("MRK_ALLOW_HOST_CLIENT") == "1"):
            raise ValueError("This agent is running on the host PC. Start the scraping agent on the intended client PC instead.")
        return jsonify(command=store().request_scrape(identifier, data.get("start_date", ""), data.get("end_date", ""))), 202
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 409


@app.post('/api/clients/<identifier>/commands/next')
def next_command(identifier):
    if not authorized("client"):
        return jsonify(error="Client authentication required"), 401
    try:
        return jsonify(command=store().next_command(identifier))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return "No file part", 400
    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400
        
    if file:
        filename = secure_filename(file.filename)
        identifier = request.form.get("client_id")
        run_id = request.form.get("run_id")
        if identifier or run_id:
            if not authorized("client"):
                return "Client authentication required", 401
            try:
                client_id(identifier)
                client_id(run_id)
            except ValueError:
                return "Invalid client or run ID", 400
            filename = secure_filename(f"{identifier}_{run_id}_{filename}")
        if not filename or not filename.lower().endswith('.zip'):
            return "A ZIP package is required", 400
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        descriptor, temporary = tempfile.mkstemp(prefix="upload-", suffix=".part", dir=UPLOAD_FOLDER)
        os.close(descriptor)
        try:
            file.save(temporary)
            if not zipfile.is_zipfile(temporary):
                return "Invalid ZIP package", 400
            # The watcher sees only completed uploads, never the in-progress file.
            os.replace(temporary, filepath)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        print(f"Received {filename}; queued for automatic database import")
        if identifier:
            return jsonify(package=filename, status="awaiting_import"), 200
        return "File uploaded successfully; queued for database import", 200

def start_receiver(port=None):
    host = os.environ.get("MRK_RECEIVER_HOST", "0.0.0.0")
    port = port or int(os.environ.get("MRK_RECEIVER_PORT", "8000"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ensure_control_tokens()
    watcher = configured_watcher()
    worker = threading.Thread(target=watcher.run_forever, name="maraki-package-watcher", daemon=True)
    worker.start()
    try:
        app.run(host=host, port=port, use_reloader=False)
    finally:
        watcher.stop.set()
        worker.join(timeout=5)

if __name__ == '__main__':
    start_receiver()
