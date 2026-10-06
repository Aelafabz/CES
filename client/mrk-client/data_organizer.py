import os
import csv
from bs4 import BeautifulSoup
import accurate_time as at
import page_scraper as ps

class DataOrganizer:
    def __init__(self, sales_file, erca_file):
        self.sales_file = sales_file
        self.erca_file = erca_file
        self.sales_csv = self.sales_file.replace('.html', '.csv') if self.sales_file else None
        self.erca_csv = self.erca_file.replace('.html', '.csv') if self.erca_file else None

    def html_table_to_csv(self, html_file, csv_file):
        if not html_file or not os.path.exists(html_file):
            print(f"Error: HTML file not found: {html_file}")
            return
            
        with open(html_file, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
            
        header = soup.find("tr", class_="StyleReportDataHeaderTr")
        rows = soup.find_all("tr", class_="StyleReportDataTr")
        
        if not header and not rows:
            print(f"No valid data tables found in {html_file}")
            return
            
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if header:
                headers = [th.get_text(strip=True) for th in header.find_all("td")]
                writer.writerow(headers)
            
            for row in rows:
                cols = [td.get_text(strip=True) for td in row.find_all("td")]
                writer.writerow(cols)

    def parse_sales_file(self):
        if self.sales_file and self.sales_csv:
            self.html_table_to_csv(self.sales_file, self.sales_csv)

    def parse_erca_file(self):
        if self.erca_file and self.erca_csv:
            self.html_table_to_csv(self.erca_file, self.erca_csv)

    def process_all(self):
        self.parse_sales_file()
        self.parse_erca_file()
        return self.sales_csv, self.erca_csv

if __name__ == "__main__":
    pass
