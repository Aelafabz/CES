import os
from bs4 import BeautifulSoup

class AccurateTimeManager:
    def __init__(self, sales_file_path, xml_dir_path):
        self.sales_file = sales_file_path
        self.xml_path = xml_dir_path
        
    def time_from_xml(self, ref_note):
        xml_file = os.path.join(self.xml_path, f"{ref_note}.xml")
        if os.path.exists(xml_file):
            # Return creation time of the xml file
            ctime = os.path.getctime(xml_file)
            from datetime import datetime
            return datetime.fromtimestamp(ctime).strftime('%Y-%m-%d %H:%M:%S')
        return None

    def process_sales_file(self):
        if not self.sales_file or not os.path.exists(self.sales_file):
            print("Error: No specified sales file or file does not exist")
            return
            
        with open(self.sales_file, "r", encoding="utf-8") as f:
            content = f.read()
            
        soup = BeautifulSoup(content, "html.parser")
        
        # Ref Note is usually in the first column, Transaction Date is in the third column
        for row in soup.find_all("tr", class_="StyleReportDataTr"):
            cols = row.find_all("td")
            if len(cols) > 3:
                ref_note = cols[0].get_text(strip=True)
                accurate_time = self.time_from_xml(ref_note)
                if accurate_time:
                    # Update transaction date text
                    cols[2].string = accurate_time
                    
        # Save the modified HTML
        with open(self.sales_file, "w", encoding="utf-8") as f:
            f.write(str(soup))
            
if __name__ == "__main__":
    pass
