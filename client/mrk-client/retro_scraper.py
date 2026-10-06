import os
import json
import socket
import requests
from datetime import datetime
from page_scraper import PageScraper
from data_organizer import DataOrganizer
import zipfile

STATE_FILE = "C:/Client-data/mrk-data/retro_state.json"
HOST_STATUS_URL = "http://192.168.1.2:8001/db_status"
HOST_UPLOAD_URL = "http://192.168.1.2:8001/upload_retro"

def get_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"current_offset": 1, "last_run": None}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def run_retro_pipeline():
    state = get_state()
    offset = state["current_offset"]
    
    if offset > 5:
        print("Retro scraping complete (5 years stored).")
        return
        
    today_str = datetime.now().strftime("%Y-%m-%d")
    if state["last_run"] == today_str:
        print("Retro scraping already ran today.")
        return
        
    print(f"Running retro scrape for year offset {offset}")
    
    current_year = datetime.now().year
    target_year = current_year - offset
    start_date = f"{target_year}-01-01"
    end_date = f"{target_year}-12-31"
    
    scraper = PageScraper(check_retro=False)
    scraper.data_path = f"C:/Client-data/mrk-data/retro_{target_year}/"
    os.makedirs(scraper.data_path, exist_ok=True)
    
    print(f"Scraping data for {target_year}...")
    scraper.store_site_data(start_date, end_date)
    
    print("Organizing data...")
    organizer = DataOrganizer(scraper.sales_file, scraper.erca_file)
    sales_csv, erca_csv = organizer.process_all()
    
    # Zip the files
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        source_ip = s.getsockname()[0]
        s.close()
    except:
        source_ip = "127.0.0.1"
        
    folder_name = f"retro_{target_year}_{source_ip}_maraki-scraped"
    zip_filename = f"{folder_name}.zip"
    
    with zipfile.ZipFile(zip_filename, 'w') as zipf:
        if sales_csv and os.path.exists(sales_csv):
            zipf.write(sales_csv, os.path.basename(sales_csv))
        if erca_csv and os.path.exists(erca_csv):
            zipf.write(erca_csv, os.path.basename(erca_csv))
            
    # Upload to host
    print(f"Uploading {zip_filename} to host...")
    try:
        with open(zip_filename, 'rb') as f:
            files = {'file': (zip_filename, f)}
            response = requests.post(HOST_UPLOAD_URL, files=files)
            if response.status_code == 200:
                print(f"Successfully sent retro package for {target_year}.")
                state["current_offset"] += 1
                state["last_run"] = today_str
                save_state(state)
            else:
                print(f"Failed to send retro package. Status: {response.status_code}")
    except Exception as e:
        print(f"Error sending retro package: {e}")
    finally:
        if os.path.exists(zip_filename):
            os.remove(zip_filename)

if __name__ == "__main__":
    run_retro_pipeline()
