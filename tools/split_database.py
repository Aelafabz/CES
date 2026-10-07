"""Split the legacy CES database, preserving schema, IDs, sequences and WAL data."""
import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from env_config import save_env_values

CREDIT = {'sms_payments', 'credit_entries', 'xml_documents', 'audit_log'}
MARAKI = {'sales', 'erca', 'data', 'retro_sales', 'retro_erca', 'retro_data'}


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def split_database(source, maraki, credit, backup_dir):
    source, maraki, credit, backup_dir = [Path(p).resolve() for p in (source, maraki, credit, backup_dir)]
    if not source.is_file() or len({source, maraki, credit}) != 3 or maraki.exists() or credit.exists():
        raise ValueError('Source must exist and destination databases must be distinct new files')
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / 'shared.db'
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(backup)) as dst:
        src.backup(dst)
    temporary_files = []
    try:
        with closing(sqlite3.connect(backup)) as snapshot:
            names = tables(snapshot)
            unknown = names - CREDIT - MARAKI
            if unknown:
                raise ValueError('Unclassified tables: ' + ', '.join(sorted(unknown)))
            if snapshot.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0]:
                raise ValueError('Classify legacy view dependencies before splitting')
            counts = {name: snapshot.execute(f'SELECT COUNT(*) FROM {quote(name)}').fetchone()[0] for name in sorted(names)}
            prepared = []
            for destination, keep in ((maraki, MARAKI), (credit, CREDIT)):
                destination.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix=destination.name + '.', suffix='.tmp', dir=destination.parent)
                os.close(fd)
                temporary_files.append(temporary)
                with closing(sqlite3.connect(temporary)) as output:
                    snapshot.backup(output)
                    output.execute('PRAGMA journal_mode=DELETE')
                    with output:
                        for name in names - keep:
                            output.execute(f'DROP TABLE {quote(name)}')
                    output.execute('VACUUM')
                    if output.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or output.execute('PRAGMA foreign_key_check').fetchall():
                        raise ValueError('Split database failed integrity or foreign key checks')
                    if tables(output) != names & keep:
                        raise ValueError('Split database has unexpected tables')
                    for name in names & keep:
                        if output.execute(f'SELECT COUNT(*) FROM {quote(name)}').fetchone()[0] != counts[name]:
                            raise ValueError('Row count changed for ' + name)
                prepared.append((temporary, destination))
            for temporary, destination in prepared:
                os.replace(temporary, destination)
        result = {'source': str(source), 'backup': str(backup), 'maraki': str(maraki), 'credit': str(credit), 'row_counts': counts}
        (backup_dir / 'migration.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        return result
    finally:
        for temporary in temporary_files:
            if os.path.exists(temporary):
                os.unlink(temporary)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'database/credit-entry.db')
    parser.add_argument('--apply-config', action='store_true')
    args = parser.parse_args()
    result = split_database(args.source, ROOT / 'database/marak.db', ROOT / 'database/credit_entry.db',
                            ROOT / 'database/backups' / ('split-' + datetime.now().strftime('%Y%m%d-%H%M%S')))
    if args.apply_config:
        save_env_values(ROOT / 'host/.env', {'MRK_DATABASE_PATH': 'database/marak.db', 'CRED_V6_DATABASE_PATH': 'database/credit_entry.db'})
    print(json.dumps(result, indent=2))
