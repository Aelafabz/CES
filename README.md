# CES (Centralized Enterprise System)

CES is a multi-component system designed for data extraction, transaction reporting, and credit entry management. It follows a client-host architecture to collect, organize, and centralize business data.

## Project Structure

The project is divided into `client` and `host` directories:

### 1. Maraki Reports Data Extractor (`mrk-client` & `mrk-host`)
A toolset for scraping, processing, and aggregating report data from the MarakiReports2012 system.
- **Client (`mrk-client`)**: 
  - Scrapes ERCA and Sales reports (`page_scraper.py`).
  - Correlates transactions with local XML files to record accurate timestamps (`accurate_time.py`).
  - Converts HTML reports into clean CSV formats (`data_organizer.py`).
  - Packages and transmits the data to the host server (`mrk_sender.py`).
- **Host (`mrk-host`)**: 
  - Receives and extracts the packaged data (`mrk_receiver.py`).
  - Automatically builds and populates a SQLite database (`db_builder.py`) for centralized querying.

### 2. Credit Entry System (`credit-entry-client` & `credit-entry-host`)
A robust desktop application for logging and managing credit payments.
- **Client (`credit-entry-client`)**: 
  - A Tkinter-based GUI for cashiers (`credit-entry.py`).
  - Features a durable local SQLite ledger to ensure no data is lost during network outages.
  - Automatically exports sessions to Excel workbooks for local backups and easy auditing.
  - Syncs securely with the remote server when connected.
  - Data is saved in the `C:\client-data\credit-entry-data` directory.
- **Host (`credit-entry-host`)**: 
  - Contains server-side administration tools (currently in development).

### 3. SMS Relay (`sms-relay-client` & `sms-relay-host`)
- The host relay polls an Android phone's inbox through ADB and forwards recognized mobile payments to the credit-entry server.
- To exercise SMS parsing and posting without a connected phone or writing to the live server, run `python host/sms-relay-host/sms_adb_relay.py --test-fake`. The test sends generated messages to a temporary local receiver.
- To continuously insert clearly labeled fake payments into the configured credit-entry server database, run `python host/sms-relay-host/sms_adb_relay.py --fake-stream`. It sends one fake payment every 5-10 seconds until stopped with Ctrl+C. These entries are test data and will appear in the credit-entry client.
- The host switchboard also has a separate **Faux SMS Generator** ON/OFF control for starting and stopping that stream.

## Setup & Requirements

For temporary scraping tests and database inspection, run
`tools\scraping-dashboard\start-dashboard.cmd` and open `http://127.0.0.1:8790`.
See [dashboard instructions](tools/scraping-dashboard/README.md).

The `client` folder can be deployed independently to client machines. See
[client deployment instructions](client/README.md) for local setup, launchers,
configuration, and data migration. No project-root files or host code are needed.

- Python 3.x
- Dependencies: `requests`, `beautifulsoup4`, `Flask`, `openpyxl`, `werkzeug`

## Host and Client Environment

The Maraki receiver automatically checks `MRK_UPLOAD_DIR` (default
`host/mrk-host/received_packages`) every 10 seconds and runs the database builder
for new or changed ZIP packages. This also handles packages copied into the folder
manually. Upload responses acknowledge receipt; database import runs asynchronously.
Packages must be unchanged across two scans before importing, so detection and
import normally take up to 20 seconds. Invalid packages and failed database imports
are retried each scan, with the error recorded in the session state.

Tracking is saved atomically to `host/mrk-host/package_session_state.json`, including
the last scan, file size/modification time, import status, attempts, SHA-256, and
import timestamps. Successful unchanged packages are skipped across restarts.
New sessions also process ZIPs already in the folder. Imports use `CES_DATABASE_PATH`
and deduplicate existing rows. Existing extracted folders are ignored; each ZIP is
extracted privately for import. All tables in a package must import successfully
before the package is marked complete.

The watcher starts with the MRK Receiver in the switchboard. To watch independently
of the HTTP receiver, run `python host/mrk-host/db_builder.py` (or
`python host/mrk-host/package_watcher.py`). A file lock prevents the standalone
watcher and receiver from scanning concurrently. Change `MRK_PACKAGE_STATE_FILE`
or `MRK_PACKAGE_SCAN_INTERVAL_SECONDS` in `host/.env` if needed. Restart the receiver
after updating its code to enable its built-in watcher.

Runtime paths, service addresses/ports, relay settings, and credentials are read from separate `host/.env` and `client/.env` files. Create them from `host/.env.example` and `client/.env.example`; both actual `.env` files are ignored by Git. The loader uses only Python's standard library. Values provided by the operating system override `.env` values.

On Windows, create the files with `Copy-Item host\.env.example host\.env` and `Copy-Item client\.env.example client\.env`, then edit the values for the local network and directories.

The credit-entry server creates missing API tokens in `host/.env` on first startup and no longer prints them to the console. If API authentication is enabled, copy `CRED_V6_CLIENT_TOKEN` from `host/.env` to `client/.env`; the relay reads `CRED_V6_RELAY_TOKEN` directly from `host/.env`. Restart the relevant application after editing its `.env`. Keep credentials private and do not commit actual `.env` files.
