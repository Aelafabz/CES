# Temporary scraping dashboard

Run from any working directory:

```powershell
C:\CES\venv\Scripts\python.exe C:\CES\tools\scraping-dashboard\dashboard.py --client-dir C:\client
```

Open http://127.0.0.1:8790. The dashboard binds only to localhost.
Stop it with Ctrl+C. No new packages are required beyond the project requirements.

Select **Offline sample reports** to exercise the actual client HTML storage,
CSV conversion, ZIP packaging, extraction, and host database import without
contacting Maraki. Select **Live Maraki service** to test the real scraper using
the deployed client's `.env`. Network calls have connection/read timeouts.
Historical auto-scraping is disabled for dashboard runs.

All runs store artifacts in `runtime/runs` and import into the separate
`runtime/maraki_db.sqlite`. Each run has its own database schema, and the latest
successful local import replaces the test database shown in the viewer. This
allows switching between sample and live reports with different columns.
Downloads include HTML, CSV, and the packaged ZIP. The sample contains three
rows in each report; identical rows within an import are deduplicated by the
existing host importer. Sample rows are labeled TEST.

The existing host database defaults to `MRK_DATABASE_PATH` from `host/.env`,
currently `database/marak.db`. Maraki's `sales`, `erca`, and historical
tables live there; credit-entry data uses `database/credit_entry.db`. To inspect a different
`maraki_db` file, pass `--database C:\path\to\maraki_db.sqlite` at startup.
The viewer opens both databases in SQLite read-only mode and supports table
counts, text search, and pages of 50 rows.

**Also upload live reports** is optional and sends the package to the configured
client receiver address, updating the host database through its normal receiver.
Sample uploads are blocked. Without that checkbox, the host database is only
read and all imports stay in the test database. Client files are never edited.

To remove this temporary tool, stop the process and remove this dashboard folder.
Its runtime directory contains all test output; the host database and deployed
client remain separate.
