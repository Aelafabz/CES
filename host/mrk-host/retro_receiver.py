import os
import sys
from pathlib import Path
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import zipfile
import retro_db_builder

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
from env_config import get_path, load_env_file

load_env_file(PROJECT_ROOT / "host" / ".env")

app = Flask(__name__)
UPLOAD_FOLDER = str(get_path(
    "MRK_RETRO_UPLOAD_DIR", os.path.join("host", "mrk-host", "retro_received_packages"), PROJECT_ROOT))
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
DB_PATH = str(get_path("MRK_DATABASE_PATH", os.path.join("database", "marak.db"), PROJECT_ROOT))

@app.route('/db_status', methods=['GET'])
def db_status():
    exists = os.path.exists(DB_PATH)
    return jsonify({"exists": exists})

@app.route('/upload_retro', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return "No file part", 400
    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400
        
    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        
        extract_folder = os.path.join(UPLOAD_FOLDER, filename.replace('.zip', ''))
        os.makedirs(extract_folder, exist_ok=True)
        with zipfile.ZipFile(filepath, 'r') as zip_ref:
            zip_ref.extractall(extract_folder)
            
        print(f"Received retro package {filename}")
        
        # Save to maraki-db database without interrupting normal process
        retro_db_builder.build_database_from_folder(extract_folder, DB_PATH)
        
        return "File uploaded and extracted successfully", 200

def start_receiver(port=None):
    host = os.environ.get("MRK_RETRO_RECEIVER_HOST", "0.0.0.0")
    port = port or int(os.environ.get("MRK_RETRO_RECEIVER_PORT", "8001"))
    app.run(host=host, port=port)

if __name__ == '__main__':
    start_receiver()
