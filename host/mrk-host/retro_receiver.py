import os
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import zipfile
import retro_db_builder

app = Flask(__name__)
UPLOAD_FOLDER = 'retro_received_packages'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
DB_PATH = 'maraki-db.db'

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

def start_receiver(port=8001):
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    start_receiver()
