# Deploying the CES client

Copy this entire `client` folder to a writable location on each Windows client
machine. The project root, `host` folder, root requirements, and root Python
environment are not needed. Python 3.10 or newer with Tcl/Tk support must be
installed on the machine. Installing dependencies requires internet access or
access to your configured Python package mirror.

1. Run `setup.cmd`. It creates a Python environment in `.venv`, installs only
   client dependencies, and creates `.env` from `.env.example` when missing.
2. Edit `.env`. Set `CRED_V6_SERVER_URL` to the actual host machine address,
   and supply its client token if authentication is enabled. Configure Maraki
   credentials and receiver addresses if using reports. Existing `.env` values
   are preserved by setup; review them on every machine.
3. Run `start-client.cmd` to open Credit Entry. A shortcut to this file works
   even when its working directory is elsewhere.

Run setup again after an update. Create `.venv` on each destination machine;
do not copy the development environment or another machine's `.venv`.
Exclude caches, crash logs, and any machine-specific credentials when distributing
a clean folder. Keep `env_config.py`, the launcher, and both component folders.

## Other commands

From a terminal in this folder:

```bat
start-client.cmd --check
start-client.cmd mrk --start-date 2026-10-01 --end-date 2026-10-07
start-client.cmd retro
```

`mrk` scrapes, converts, and sends both reports for the requested dates (today
if omitted). Set `MRK_XML_DIR` to correct sales timestamps using local XML files.
`retro` runs the existing historical scrape pipeline. Report archives stay in
the configured report data directory, regardless of the working directory.
`--check` validates local dependencies, configuration syntax, and source files
without connecting to any server or opening the GUI.

## Sales timestamp configuration

Configure these entries in `.env` separately on each client PC:

```dotenv
MRK_XML_DIR=
MRK_REF_NOTE_FORMAT=PAY-{id}
MRK_XML_FILENAME_PATTERN=P{ref_note}-*-*.xml
```

Set `MRK_XML_DIR` to the XML directory; leave it empty to disable correction.
Relative paths resolve from this client folder. Only files directly inside the
directory are searched.

The reference format must contain exactly one `{id}` placeholder. Other text is
literal, and the whole reference must match. For `PAY-12345`, `{id}` is `12345`
and `{ref_note}` is `PAY-12345`. The filename pattern can use either placeholder.
`*` matches any number of characters; `?` matches one. Matching is case-sensitive.

The example matches `PPAY-12345-xxxxxx-xxxx.xml`. To require exactly six and four
suffix characters, use `P{ref_note}-??????-????.xml`. For reference `INV-12345`
and filename `receipt-12345.xml`, use `INV-{id}` and `receipt-{id}.xml`.
For the old exact-filename behavior, use `{id}` and `{ref_note}.xml`.

Exactly one file must match. Missing files, mismatched references, and ambiguous
matches preserve the report timestamp. Ambiguous matches print a diagnostic.
The timestamp source remains file creation time in the client PC's local timezone,
not XML contents. It replaces the entire Sales date cell before CSV conversion.
ERCA and the retro pipeline do not receive correction.

Default ledger and report data directories remain `C:/client-data/credit-entry-data`
and `C:/Client-data/mrk-data` so existing data continues to be used. Relative
paths in `.env` now resolve from this `client` folder. If an old configuration
uses a relative path from the project root, change it to the corresponding
absolute path before launching to keep using the same data. Operating-system
environment variables override `.env` values.

Copying the client folder does not include data stored in those external
directories. Back up and transfer existing ledgers separately when replacing
a client machine. The remote CES host and Maraki service must still be reachable
for synchronization and report extraction; Credit Entry retains its local offline
ledger behavior.
