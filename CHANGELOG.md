# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Changed
- **Credit Entry Client**: Updated the application to save session state files, configuration, and Excel backups to C:\client-data\credit-entry-data instead of the local app directory. This improves data persistence and separates user data from application code.
- **Credit Entry Client**: Updated local Ledger database path to point to the new centralized data directory (C:\client-data\credit-entry-data).

## [Initial Version]

### Added
- **Maraki Reports Extractor**: Implemented client-side HTML scraping for ERCA and Sales reports.
- **Maraki Reports Extractor**: Added accurate time parsing from XML files to correct transaction timestamps.
- **Maraki Reports Extractor**: Added automated packaging (ZIP) and HTTP transmission to the host.
- **Maraki Reports Extractor**: Implemented host-side Flask server for receiving files and building a SQLite database (mrk_database.db).
- **Credit Entry Client**: Released Tkinter-based GUI for cashiers.
- **Credit Entry Client**: Implemented local SQLite ledger for offline resilience.
- **Credit Entry Client**: Added Excel exporting for session backups.
- **Credit Entry Client**: Integrated resilient server synchronization logic with background worker threads.
