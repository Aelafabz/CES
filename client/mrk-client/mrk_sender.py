import os
import zipfile
import requests
import socket
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
from env_config import load_env_file

load_env_file(PROJECT_ROOT / "client" / ".env")

class MRKSender:
    def __init__(self, sales_csv, erca_csv, start_date, end_date):
        self.sales_csv = sales_csv
        self.erca_csv = erca_csv
        self.start_date = start_date
        self.end_date = end_date
        self.receiver_ip = os.environ.get("MRK_RECEIVER_HOST", "192.168.1.2")
        self.receiver_port = int(os.environ.get("MRK_RECEIVER_PORT", "8000"))
        
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            self.source_ip = s.getsockname()[0]
            s.close()
        except:
            self.source_ip = "127.0.0.1"

    def package_wrapper(self):
        folder_name = f"{self.start_date} - {self.end_date} {self.source_ip} maraki-scraped"
        zip_filename = f"{folder_name}.zip"
        
        with zipfile.ZipFile(zip_filename, 'w') as zipf:
            if self.sales_csv and os.path.exists(self.sales_csv):
                zipf.write(self.sales_csv, os.path.basename(self.sales_csv))
            if self.erca_csv and os.path.exists(self.erca_csv):
                zipf.write(self.erca_csv, os.path.basename(self.erca_csv))
                
        return zip_filename

    def sender(self):
        zip_file = self.package_wrapper()
        url = f"http://{self.receiver_ip}:{self.receiver_port}/upload"
        try:
            with open(zip_file, 'rb') as f:
                files = {'file': (zip_file, f)}
                response = requests.post(url, files=files)
                if response.status_code == 200:
                    print("Successfully sent package to receiver.")
                else:
                    print(f"Failed to send. Status: {response.status_code}")
        except Exception as e:
            print(f"Error sending package: {e}")

if __name__ == "__main__":
    pass
