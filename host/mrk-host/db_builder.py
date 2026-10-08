import os
import sqlite3
import csv
import sys
from contextlib import closing
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
from env_config import get_path, load_env_file

load_env_file(PROJECT_ROOT / "host" / ".env")

def create_table_from_headers(cursor, table_name, headers):
    # Sanitize headers for SQL columns
    sanitized = [h.replace(' ', '_').replace('?', '').replace('/', '_').replace('-', '_').replace('#', 'Num') for h in headers]
    if not sanitized or any(not col.strip() for col in sanitized) or len({col.lower() for col in sanitized}) != len(sanitized):
        raise ValueError("CSV headers must be nonempty and unique after sanitizing")
    columns = ", ".join(['"%s" TEXT' % col.replace('"', '""') for col in sanitized])
    cursor.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({columns})")
    return sanitized

def import_csv_to_db(db_path, csv_path, table_name):
    with open(csv_path, 'r', encoding='utf-8-sig') as f, closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        cursor = conn.cursor()
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            return
            
        with conn:
            sanitized_headers = create_table_from_headers(cursor, table_name, headers)
            columns = ", ".join(['"%s" TEXT' % col.replace('"', '""') for col in sanitized_headers])
            cursor.execute(f"CREATE TEMP TABLE tmp_{table_name} ({columns})")
            placeholders = ", ".join(["?" for _ in sanitized_headers])
            insert_sql = f"INSERT INTO tmp_{table_name} VALUES ({placeholders})"
            for row in reader:
                if len(row) < len(sanitized_headers):
                    row.extend([''] * (len(sanitized_headers) - len(row)))
                cursor.execute(insert_sql, row[:len(sanitized_headers)])
            cursor.execute(f"INSERT INTO {table_name} SELECT * FROM tmp_{table_name} EXCEPT SELECT * FROM {table_name}")
            cursor.execute(f"DROP TABLE tmp_{table_name}")

def build_database_from_folder(folder_path, db_path=None):
    if db_path is None:
        db_path = str(get_path("MRK_DATABASE_PATH", os.path.join("database", "marak.db"), PROJECT_ROOT))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    
    for filename in os.listdir(folder_path):
        if filename.lower().endswith(".csv"):
            table_name = "sales" if "sales" in filename.lower() else "erca" if "erca" in filename.lower() else "data"
            csv_path = os.path.join(folder_path, filename)
            import_csv_to_db(db_path, csv_path, table_name)
            print(f"Imported {filename} into table {table_name}")

if __name__ == "__main__":
    from package_watcher import main
    main()
