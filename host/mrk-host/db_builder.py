import os
import sqlite3
import csv

def create_table_from_headers(cursor, table_name, headers):
    # Sanitize headers for SQL columns
    sanitized = [h.replace(' ', '_').replace('?', '').replace('/', '_').replace('-', '_').replace('#', 'Num') for h in headers]
    columns = ", ".join([f'"{col}" TEXT' for col in sanitized])
    cursor.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({columns})")
    return sanitized

def import_csv_to_db(db_path, csv_path, table_name):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            return
            
        sanitized_headers = create_table_from_headers(cursor, table_name, headers)
        
        placeholders = ", ".join(["?" for _ in sanitized_headers])
        insert_sql = f"INSERT INTO {table_name} VALUES ({placeholders})"
        
        for row in reader:
            # Pad row if missing columns
            if len(row) < len(sanitized_headers):
                row.extend([''] * (len(sanitized_headers) - len(row)))
            # Truncate row if too many columns
            row = row[:len(sanitized_headers)]
            cursor.execute(insert_sql, row)
            
    conn.commit()
    conn.close()

def build_database_from_folder(folder_path, db_path=None):
    if db_path is None:
        db_path = os.path.join(os.path.dirname(__file__), '..', '..', 'database', 'credit-entry.db')
    
    for filename in os.listdir(folder_path):
        if filename.endswith(".csv"):
            table_name = "sales" if "sales" in filename.lower() else "erca" if "erca" in filename.lower() else "data"
            csv_path = os.path.join(folder_path, filename)
            import_csv_to_db(db_path, csv_path, table_name)
            print(f"Imported {filename} into table {table_name}")

if __name__ == "__main__":
    pass
