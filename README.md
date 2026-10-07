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

## Setup & Requirements

- Python 3.x
- Dependencies: `requests`, `beautifulsoup4`, `Flask`, `openpyxl`, `werkzeug`
