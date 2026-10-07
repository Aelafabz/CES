from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from split_database import split_database, tables


class SplitDatabaseTest(unittest.TestCase):
    def test_preserves_wal_rows_ids_schema_and_sequences(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "shared.db"
            with closing(sqlite3.connect(source)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("CREATE TABLE sales (ref TEXT, amount TEXT)")
                conn.execute("CREATE TABLE credit_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, amount TEXT)")
                conn.execute("CREATE INDEX credit_amount ON credit_entries(amount)")
                conn.execute("INSERT INTO sales VALUES ('S1','10')")
                conn.execute("INSERT INTO credit_entries(id,amount) VALUES (42,'20')")
                conn.commit()
                result = split_database(source, root / "marak.db", root / "credit_entry.db", root / "backup")
            with closing(sqlite3.connect(root / "marak.db")) as conn:
                self.assertEqual(tables(conn), {"sales"})
                self.assertEqual(conn.execute("SELECT * FROM sales").fetchall(), [("S1", "10")])
            with closing(sqlite3.connect(root / "credit_entry.db")) as conn:
                self.assertEqual(tables(conn), {"credit_entries"})
                self.assertEqual(conn.execute("SELECT * FROM credit_entries").fetchall(), [(42, "20")])
                self.assertEqual(conn.execute("INSERT INTO credit_entries(amount) VALUES ('30')").lastrowid, 43)
                self.assertIn("credit_amount", {r[1] for r in conn.execute("PRAGMA index_list(credit_entries)")})
            with closing(sqlite3.connect(result["backup"])) as conn:
                self.assertEqual(tables(conn), {"sales", "credit_entries"})

    def test_existing_destinations_and_unknown_tables_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.db"
            with closing(sqlite3.connect(source)) as conn:
                conn.execute("CREATE TABLE unknown (id INTEGER)")
                conn.commit()
            target = root / "marak.db"
            target.write_bytes(b"keep existing data")
            with self.assertRaises(ValueError):
                split_database(source, target, root / "credit.db", root / "backup1")
            self.assertEqual(target.read_bytes(), b"keep existing data")
            with self.assertRaises(ValueError):
                split_database(source, root / "new.db", root / "credit.db", root / "backup2")
            self.assertFalse((root / "new.db").exists())
            self.assertFalse((root / "credit.db").exists())
