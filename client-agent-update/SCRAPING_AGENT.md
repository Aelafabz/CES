# Client scraping status and admin requests

These files belong in the root of the independently deployed client folder:
`mrk_signal.py`, `mrk_agent.py`, `run_client.py`, and `start-scraping-agent.cmd`.
The usual client dependencies are sufficient; no inbound port is opened on clients.
To upgrade an existing deployment, extract `client-agent-update.zip` into its
client folder, replacing the included scripts (including `mrk-client/page_scraper.py`).
Keep the existing `.env`; `.env.example` is a reference template, not a replacement.
Restart `start-scraping-agent.cmd` on that PC after updating.

Configure `MRK_CONTROL_CLIENT_TOKEN` in the client `.env` using the matching value
from the host `.env`. The updated MRK Receiver generates separate client and admin
control tokens on first startup. Keep the admin token only on the host.
Set `MRK_CLIENT_NAME` to a recognizable machine or branch name, if desired.
Receiver addresses continue to use `MRK_RECEIVER_HOST` and `MRK_RECEIVER_PORT`.
An optional `MRK_CONTROL_URL` overrides the host URL.

Install and run the agent **on each client PC**, not on the admin/host PC.
`MRK_RECEIVER_HOST` identifies the host that receives signals and packages.
`MRK_BASE_URL` identifies the Maraki report source, defaulting to
`http://127.0.0.1/MarakiReports2012` on the PC executing the agent.
The switchboard obtains client IPs from incoming heartbeats; it does not scan the
LAN or connect directly to client PCs. It queues a command for the selected
client ID, which that client's agent fetches from the host. If no remote PCs
appear, check that their agents are running, the client token matches the host,
and they can reach the host on port 8000.
Agents running on the host appear as **Host PC**, with manual scraping disabled.
For API testing only, the host may set `MRK_ALLOW_HOST_CLIENT=1`.

Opening Credit Entry through `start-client.cmd` automatically starts the scraping
agent in the background. Alternatively, run `start-scraping-agent.cmd` or
`start-client.cmd agent` and keep it running. Add the agent launcher to your normal
Windows startup procedure on machines that must stay available before Credit
Entry is opened. Closing Credit Entry leaves the agent running.

The agent emits a heartbeat every 10 seconds and checks for admin requests.
The host marks a client offline after 45 seconds without a heartbeat. Scrapes
requested through `start-client.cmd mrk` also emit progress signals. Historical
runs through `start-client.cmd retro` emit a running/result signal. Directly running
individual scraper helper files bypasses the launcher and its status integration.

Local signals, identity, and logs live in `mrk-agent-data` (configurable through
`MRK_AGENT_STATE_DIR`). `signal.json` records the current stage, run ID, dates,
package, and last progress event. `agent.log` records connectivity and failures.
Each client generates a durable unique ID. **Do not copy `mrk-agent-data` to another
client machine**; each destination needs its own identity. `MRK_CLIENT_ID` can
explicitly override the generated ID when a stable site ID is needed.
Generated identities are bound to the PC hostname and network identity. Moving
a previously initialized agent state to another PC creates a fresh generated
ID and clears the copied scrape state. Explicit `MRK_CLIENT_ID` overrides must
be unique across PCs. When upgrading older clients, exclude `mrk-agent-data`
from the copy because older identity files did not record the source machine.

In the updated switchboard, open **Client scraping**, select an online idle client,
choose the report dates, and click **Scrape selected client**. The request is picked
up on the next client poll. Status progresses through scraping, timestamps,
organizing, packaging, uploading, awaiting import, and completed/failed.
Completed means the report package was imported into the host Maraki database.
The host-import column separately exposes package-watcher errors.

The agent reconnects automatically after network interruptions. Commands and
signals survive restarts. An interrupted active scrape is reported as failed,
rather than automatically replayed; request another scrape to retry it. Locks
prevent multiple agents or overlapping scrapes on one client machine. Separate
tokens protect client signalling and admin commands; use your trusted LAN or
an HTTPS endpoint for connections outside it.
