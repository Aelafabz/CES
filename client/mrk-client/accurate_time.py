import os
import fnmatch
import glob
import re
from bs4 import BeautifulSoup

class AccurateTimeManager:
    def __init__(self, sales_file_path, xml_dir_path, ref_format=None, xml_pattern=None):
        self.sales_file = sales_file_path
        self.xml_path = xml_dir_path
        # {id} is the variable part; {ref_note} is the complete report reference.
        self.ref_format = ref_format if ref_format is not None else os.environ.get("MRK_REF_NOTE_FORMAT", "PAY-{id}")
        self.xml_pattern = xml_pattern if xml_pattern is not None else os.environ.get("MRK_XML_FILENAME_PATTERN", "P{ref_note}-*-*.xml")
        if self.ref_format.count("{id}") != 1:
            raise ValueError("MRK_REF_NOTE_FORMAT must contain exactly one {id}, e.g. PAY-{id}")
        ref_parts = self.ref_format.split("{id}")
        if any("{" in part or "}" in part for part in ref_parts):
            raise ValueError("MRK_REF_NOTE_FORMAT supports only the {id} placeholder")
        self.ref_regex = re.compile(re.escape(ref_parts[0]) + r"(?P<id>[^/\\]+?)" + re.escape(ref_parts[1]))
        remainder = self.xml_pattern.replace("{ref_note}", "").replace("{id}", "")
        if (not self.xml_pattern or any(char in remainder for char in "{}/\\:")
                or not any(token in self.xml_pattern for token in ("{ref_note}", "{id}"))):
            raise ValueError("MRK_XML_FILENAME_PATTERN must be a filename pattern containing {ref_note} or {id}")
        self.xml_names = None
        
    def time_from_xml(self, ref_note):
        match = self.ref_regex.fullmatch(ref_note)
        if not match:
            return None
        if self.xml_names is None:
            if not os.path.isdir(self.xml_path):
                print(f"XML timestamp correction skipped: directory does not exist: {self.xml_path}")
                self.xml_names = []
            else:
                with os.scandir(self.xml_path) as entries:
                    self.xml_names = [entry.name for entry in entries if entry.is_file()]
        # Keep literal reference characters from becoming filename wildcards.
        pattern = self.xml_pattern.replace("{ref_note}", glob.escape(ref_note)).replace("{id}", glob.escape(match["id"]))
        matches = [name for name in self.xml_names if fnmatch.fnmatchcase(name, pattern)]
        if len(matches) > 1:
            print(f"XML timestamp correction skipped for {ref_note}: {len(matches)} files match {pattern}")
            return None
        if matches:
            xml_file = os.path.join(self.xml_path, matches[0])
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
            if len(cols) >= 3:
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
