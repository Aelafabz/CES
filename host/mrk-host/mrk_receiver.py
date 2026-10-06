import os
from flask import Flask, request
from werkzeug.utils import secure_filename
import zipfile
import db_builder

app = Flask(__name__)
UPLOAD_FOLDER = 'received_packages'
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
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        
        # Extract the package
        extract_folder = os.path.join(UPLOAD_FOLDER, filename.replace('.zip', ''))
        os.makedirs(extract_folder, exist_ok=True)
        with zipfile.ZipFile(filepath, 'r') as zip_ref:
            zip_ref.extractall(extract_folder)
            
        print(f"Received and extracted {filename}")
        
        db_builder.build_database_from_folder(extract_folder)
        
        return "File uploaded and extracted successfully", 200

def start_receiver(port=8000):
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    start_receiver()
