import os
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime

class PageScraper:
    def __init__(self, username=None, password=None, check_retro=True):
        self.username = username or os.environ.get('MRK_USERNAME')
        self.password = password or os.environ.get('MRK_PASSWORD')
        self.session = requests.Session()
        self.base_url = "http://192.168.1.24/MarakiReports2012"
        self.start_date = None
        self.end_date = None
        
        current_month = datetime.now().strftime("%B_%Y")
        self.data_path = f"C:/Client-data/mrk-data/{current_month}/"
        os.makedirs(self.data_path, exist_ok=True)
        
        self.erca_file = None
        self.sales_file = None
        
        if check_retro:
            self.check_retro_scrape()
        
    def check_retro_scrape(self):
        import threading
        def _check():
            try:
                # Check with host if DB exists
                response = requests.get("http://192.168.1.2:8001/db_status", timeout=5)
                if response.status_code == 200:
                    exists = response.json().get("exists", True)
                    if not exists:
                        import retro_scraper
                        retro_scraper.run_retro_pipeline()
            except Exception as e:
                pass
        threading.Thread(target=_check, daemon=True).start()

    def login(self):
        login_url = f"{self.base_url}/login.php"
        if self.username and self.password:
            self.session.post(login_url, data={'username': self.username, 'password': self.password})
            
    def get_report_data(self, report_url, start_date, end_date):
        payload = {
            'txt_trans_date': start_date,
            'txt2_trans_date': end_date
        }
        response = self.session.post(report_url, data=payload)
        return response.text

    def scrape_site(self, start_date, end_date):
        self.start_date = start_date
        self.end_date = end_date
        self.login()
        
        home_url = f"{self.base_url}/home.php"
        response = self.session.get(home_url)
        soup = BeautifulSoup(response.text, "html.parser")
        
        erca_html = None
        sales_html = None
        
        for link in soup.find_all("a"):
            text = link.get_text(strip=True)
            href = link.get("href")
            if not href:
                continue
                
            if "ERCA Report 1" in text:
                report_url = href if href.startswith('http') else f"{self.base_url}/{href}"
                erca_html = self.get_report_data(report_url, start_date, end_date)
            elif "Sales Report" == text:
                report_url = href if href.startswith('http') else f"{self.base_url}/{href}"
                sales_html = self.get_report_data(report_url, start_date, end_date)
                
        return erca_html, sales_html

    def store_site_data(self, start_date, end_date):
        erca_html, sales_html = self.scrape_site(start_date, end_date)
        
        if erca_html:
            self.erca_file = os.path.join(self.data_path, f"{start_date}_{end_date}_erca.html")
            with open(self.erca_file, "w", encoding="utf-8") as f:
                f.write(erca_html)
                
        if sales_html:
            self.sales_file = os.path.join(self.data_path, f"{start_date}_{end_date}_sales.html")
            with open(self.sales_file, "w", encoding="utf-8") as f:
                f.write(sales_html)

if __name__ == "__main__":
    pass
