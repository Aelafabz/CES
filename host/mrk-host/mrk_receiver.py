import os
import sys
from pathlib import Path
from flask import Flask, request
from werkzeug.utils import secure_filename
import zipfile
import tempfile
import threading
import logging
from package_watcher import configured_watcher

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
from env_config import get_path, load_env_file

load_env_file(PROJECT_ROOT / "host" / ".env")

app = Flask(__name__)
UPLOAD_FOLDER = str(get_path(
    "MRK_UPLOAD_DIR", os.path.join("host", "mrk-host", "received_packages"), PROJECT_ROOT))
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return "No file part", 400
    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400
        
    if file:
        filename = secure_filename(file.filename)
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
        return "File uploaded successfully; queued for database import", 200

def start_receiver(port=None):
    host = os.environ.get("MRK_RECEIVER_HOST", "0.0.0.0")
    port = port or int(os.environ.get("MRK_RECEIVER_PORT", "8000"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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
