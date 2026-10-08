"""CES Host Switchboard.

One window for the admin to see which host processes are running, whether
they answer, how data is flowing through both databases, and to switch
each process on or off at any time.

Run:  python host/switchboard/switchboard.py
"""
import json
import os
import queue
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import date, datetime
from tkinter import messagebox, ttk
from urllib.request import Request, urlopen
from urllib.error import HTTPError

HERE = os.path.dirname(os.path.abspath(__file__))
HOST_DIR = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(HOST_DIR, ".."))
sys.path.insert(0, ROOT)
from env_config import get_path, load_env_file

HOST_ENV_PATH = os.path.join(HOST_DIR, ".env")
load_env_file(HOST_ENV_PATH)
CREDIT_DB_PATH = str(get_path("CRED_V6_DATABASE_PATH", "database/credit_entry.db", ROOT))
MARAKI_DB_PATH = str(get_path("MRK_DATABASE_PATH", "database/marak.db", ROOT))
LOG_DIR = str(get_path("CES_SWITCHBOARD_LOG_DIR", os.path.join("host", "switchboard", "logs"), ROOT))
os.makedirs(LOG_DIR, exist_ok=True)

POLL_SECONDS = 3
STARTUP_GRACE = 10  # seconds a freshly started process may take to answer
CLIENT_CONTROL_URL = "http://127.0.0.1:%s" % os.environ.get("MRK_RECEIVER_PORT", "8000")
SCRAPE_BUSY = {"starting", "scraping", "timestamps", "organizing", "packaging", "uploading", "awaiting_import"}


def client_control_request(path, data=None):
    token = os.environ.get("MRK_CONTROL_ADMIN_TOKEN", "")
    if not token:
        raise RuntimeError("Start the updated MRK Receiver, then reopen the switchboard to load its admin control token.")
    payload = json.dumps(data).encode("utf-8") if data is not None else None
    req = Request(CLIENT_CONTROL_URL + path, data=payload,
                  headers={"X-MRK-Token": token, "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=3) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            message = json.loads(exc.read()).get("error", "Receiver rejected request")
        except ValueError:
            message = "Update/restart the MRK Receiver to enable client status"
        raise RuntimeError(message) from None

# ---------------------------------------------------------------------------
# Managed processes. Edit here to add/remove/re-port anything.
#   port   -> TCP health probe (None = no port, "alive" means process is up)
#   url    -> optional HTTP probe (must return 200) instead of a bare TCP probe
# ---------------------------------------------------------------------------
sms_relay_script = os.path.join(HOST_DIR, "sms-relay-host", "sms_adb_relay.py")
PROCESSES = [
    {"key": "credit", "name": "Credit Entry Server",
     "script": os.path.join(HOST_DIR, "credit-entry-host", "v6_server.py"),
     "port": int(os.environ.get("CRED_V6_PORT", "8765")),
     "url": "http://127.0.0.1:%s/api/config" % os.environ.get("CRED_V6_PORT", "8765"),
     "note": "Cashier clients, SMS ingest, XML uploads"},
    {"key": "relay", "name": "SMS ADB Relay",
     "script": sms_relay_script,
     "port": None, "url": None,
     "note": "Phone inbox -> credit server"},
    {"key": "fake_sms", "name": "Faux SMS Generator",
     "script": sms_relay_script, "args": ["--fake-stream"],
     "port": None, "url": None,
     "note": "Generated test payments -> credit server (every 5-10 sec)"},
    {"key": "mrk", "name": "MRK Receiver",
     "script": os.path.join(HOST_DIR, "mrk-host", "mrk_receiver.py"),
     "port": int(os.environ.get("MRK_RECEIVER_PORT", "8000")), "url": None,
     "note": "Daily Maraki sales/ERCA packages"},
    {"key": "retro", "name": "MRK Retro Receiver",
     "script": os.path.join(HOST_DIR, "mrk-host", "retro_receiver.py"),
     "port": int(os.environ.get("MRK_RETRO_RECEIVER_PORT", "8001")),
     "url": "http://127.0.0.1:%s/db_status" % os.environ.get("MRK_RETRO_RECEIVER_PORT", "8001"),
     "note": "5-year historical Maraki import"},
]

# Tables watched on the Data Flow panel: (label, table, timestamp column)
WATCHED = [
    ("SMS payments", "sms_payments", "created_at"),
    ("Credit entries", "credit_entries", "created_at"),
    ("XML invoices", "xml_documents", "received_at"),
    ("Audit events", "audit_log", "created_at"),
    ("MRK sales", "sales", None),
    ("MRK ERCA", "erca", None),
    ("Retro sales", "retro_sales", None),
    ("Retro ERCA", "retro_erca", None),
]


def tcp_open(port, timeout=1.0):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_ok(url, timeout=1.5):
    try:
        with urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def probe(spec):
    """True/False if the process answers; None if it has no probe."""
    if spec["url"]:
        return http_ok(spec["url"])
    if spec["port"]:
        return tcp_open(spec["port"])
    return None


def read_db_stats():
    """Read-only snapshot of table counts, newest timestamps and recent audit rows."""
    stats = {"db_exists": any(os.path.exists(p) for p in (CREDIT_DB_PATH, MARAKI_DB_PATH)), "tables": {}, "audit": [], "unlogged_sms": None, "error": ""}
    if not stats["db_exists"]:
        return stats
    for db_path in (CREDIT_DB_PATH, MARAKI_DB_PATH):
        if not os.path.exists(db_path):
            continue
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True, timeout=5)
            try:
                existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for label, table, ts_col in WATCHED:
                    if table not in existing:
                        continue
                    count = conn.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
                    last = None
                    if ts_col:
                        last = conn.execute('SELECT MAX("%s") FROM "%s"' % (ts_col, table)).fetchone()[0]
                    stats["tables"][table] = (count, last)
                if "sms_payments" in existing:
                    stats["unlogged_sms"] = conn.execute(
                        "SELECT COUNT(*) FROM sms_payments WHERE status='new'").fetchone()[0]
                if "audit_log" in existing:
                    stats["audit"] = conn.execute(
                        "SELECT id, created_at, event_type, actor, source_ip, details "
                        "FROM audit_log ORDER BY id DESC LIMIT 60").fetchall()
            finally:
                conn.close()
        except Exception as exc:
            stats["error"] += str(exc) + " "
    return stats


def fmt_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return "%dh %02dm" % (h, m) if h else "%dm %02ds" % (m, s)


class Managed:
    """One child process owned by the switchboard."""

    def __init__(self, spec):
        self.spec = spec
        self.proc = None
        self.started = None
        self.exit_code = None
        self.log_path = os.path.join(LOG_DIR, spec["key"] + ".log")
        self.log_handle = None
        self.healthy = None          # latest probe result
        self.external = False        # answering, but not started by us
        self.wanted = False          # admin wants it on (for crash detection)

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.alive:
            return
        if not os.path.exists(self.spec["script"]):
            raise FileNotFoundError(self.spec["script"])
        port = self.spec["port"]
        if port and tcp_open(port):
            raise RuntimeError("Port %d is already in use (a copy may be running outside the switchboard)." % port)
        self.log_handle = open(self.log_path, "ab", buffering=0)
        self.log_handle.write(("\n=== Started %s ===\n" % datetime.now().isoformat(timespec="seconds")).encode())
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            [sys.executable, "-u", self.spec["script"]] + self.spec.get("args", []),
            cwd=os.path.dirname(self.spec["script"]), env=env,
            stdout=self.log_handle, stderr=subprocess.STDOUT, creationflags=flags)
        self.started = time.time()
        self.exit_code = None
        self.wanted = True

    def stop(self):
        self.wanted = False
        if self.alive:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        if self.proc is not None:
            self.exit_code = self.proc.returncode
        self.started = None
        self.healthy = None
        if self.log_handle:
            try:
                self.log_handle.write(("=== Stopped %s ===\n" % datetime.now().isoformat(timespec="seconds")).encode())
                self.log_handle.close()
            except OSError:
                pass
            self.log_handle = None

    def status(self):
        """(text, colour) for the status lamp."""
        if self.alive:
            up = time.time() - self.started
            if self.healthy is False:
                if up < STARTUP_GRACE:
                    return "STARTING", "#d97706"
                return "NOT RESPONDING", "#dc2626"
            return "RUNNING", "#16a34a"
        if self.proc is not None and self.wanted and self.proc.poll() not in (None, 0):
            return "CRASHED (exit %s)" % self.proc.poll(), "#dc2626"
        if self.external:
            return "RUNNING (external)", "#2563eb"
        return "STOPPED", "#6b7280"


class Switchboard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CES Host Switchboard")
        self.geometry("1180x900")
        self.managed = [Managed(spec) for spec in PROCESSES]
        self.results = queue.Queue()
        self.stop_flag = threading.Event()
        self.baseline = {}       # table -> count when the switchboard opened
        self.last_stats = None
        self.rows = {}
        self.clients = {}
        self.client_actions = queue.Queue()
        self.client_request_pending = False
        self.client_control_error = ""
        self.build()
        threading.Thread(target=self.poller, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(500, self.tick)

    # ----- UI ------------------------------------------------------------
    def build(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text="Process Switchboard", font=("Segoe UI", 15, "bold")).pack(side="left")
        ttk.Button(head, text="Stop All", command=self.stop_all).pack(side="right")
        ttk.Button(head, text="Start All", command=self.start_all).pack(side="right", padx=6)

        board = ttk.LabelFrame(outer, text="Processes", padding=8)
        board.pack(fill="x", pady=(8, 8))
        board.columnconfigure(1, weight=1)
        for i, m in enumerate(self.managed):
            lamp = tk.Canvas(board, width=18, height=18, highlightthickness=0)
            lamp.grid(row=i, column=0, padx=(0, 8), pady=4)
            dot = lamp.create_oval(2, 2, 16, 16, fill="#6b7280", outline="")
            name = ttk.Label(board, text="%s\n%s" % (m.spec["name"], m.spec["note"]), justify="left")
            name.grid(row=i, column=1, sticky="w")
            status = ttk.Label(board, text="", width=24, font=("Segoe UI", 9, "bold"))
            status.grid(row=i, column=2, padx=8)
            info = ttk.Label(board, text="", width=34)
            info.grid(row=i, column=3, padx=8)
            btns = ttk.Frame(board)
            btns.grid(row=i, column=4)
            ttk.Button(btns, text="ON", width=6, command=lambda x=m: self.start(x)).pack(side="left", padx=2)
            ttk.Button(btns, text="OFF", width=6, command=lambda x=m: self.stop(x)).pack(side="left", padx=2)
            ttk.Button(btns, text="Restart", width=8, command=lambda x=m: self.restart(x)).pack(side="left", padx=2)
            self.rows[m.spec["key"]] = {"lamp": lamp, "dot": dot, "status": status, "info": info}

        flow = ttk.LabelFrame(outer, text="Data flow (Maraki + credit-entry databases, read-only view)", padding=8)
        flow.pack(fill="x")
        self.flow_tree = ttk.Treeview(flow, columns=("table", "rows", "new", "last"), show="headings", height=len(WATCHED))
        for col, label, width, anchor in (("table", "Table", 190, "w"), ("rows", "Rows", 100, "e"),
                                          ("new", "New since opened", 140, "e"), ("last", "Latest record", 220, "w")):
            self.flow_tree.heading(col, text=label)
            self.flow_tree.column(col, width=width, anchor=anchor)
        self.flow_tree.pack(fill="x")
        self.flow_note = ttk.Label(flow, text="")
        self.flow_note.pack(anchor="w", pady=(4, 0))

        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True, pady=(8, 0))

        clients = ttk.Frame(tabs, padding=8)
        tabs.add(clients, text="Client scraping")
        self.client_note = ttk.Label(clients, text="Waiting for client heartbeats…")
        self.client_note.pack(anchor="w", pady=(0, 6))
        controls = ttk.Frame(clients)
        controls.pack(fill="x", pady=(0, 8))
        today = datetime.now().date().isoformat()
        self.scrape_start = tk.StringVar(value=today)
        self.scrape_end = tk.StringVar(value=today)
        ttk.Label(controls, text="Start date:").pack(side="left")
        ttk.Entry(controls, textvariable=self.scrape_start, width=12).pack(side="left", padx=(4, 12))
        ttk.Label(controls, text="End date:").pack(side="left")
        ttk.Entry(controls, textvariable=self.scrape_end, width=12).pack(side="left", padx=(4, 12))
        self.scrape_button = ttk.Button(controls, text="Scrape selected client", command=self.request_client_scrape, state="disabled")
        self.scrape_button.pack(side="left")
        ttk.Label(controls, text="YYYY-MM-DD  ·  heartbeat every 10s; offline after 45s").pack(side="left", padx=12)
        grid = ttk.Frame(clients)
        grid.pack(fill="both", expand=True)
        self.client_tree = ttk.Treeview(grid, columns=("name", "ip", "signal", "stage", "import", "range", "seen"), show="headings", selectmode="browse")
        for key, label, width in (("name", "Client", 190), ("ip", "IP", 115), ("signal", "Signal", 85),
                                  ("stage", "Scrape status", 140), ("import", "Host import", 100),
                                  ("range", "Report dates", 215), ("seen", "Last heartbeat", 130)):
            self.client_tree.heading(key, text=label)
            self.client_tree.column(key, width=width)
        for tag, color in (("offline", "#6b7280"), ("ready", "#16753b"), ("active", "#ac6b00"), ("failed", "#b42318")):
            self.client_tree.tag_configure(tag, foreground=color)
        self.client_tree.pack(side="left", fill="both", expand=True)
        client_scroll = ttk.Scrollbar(grid, orient="vertical", command=self.client_tree.yview)
        client_scroll.pack(side="right", fill="y")
        self.client_tree.configure(yscrollcommand=client_scroll.set)
        self.client_tree.bind("<<TreeviewSelect>>", lambda event: self.update_client_selection())
        self.client_detail = ttk.Label(clients, text="Select a client to see its latest signal.", wraplength=1100)
        self.client_detail.pack(anchor="w", pady=(8, 0))

        act = ttk.Frame(tabs)
        tabs.add(act, text="Activity (audit log)")
        self.audit_tree = ttk.Treeview(act, columns=("time", "event", "actor", "ip", "details"), show="headings")
        for col, label, width in (("time", "Time", 150), ("event", "Event", 130), ("actor", "Actor", 90),
                                  ("ip", "Source IP", 110), ("details", "Details", 500)):
            self.audit_tree.heading(col, text=label)
            self.audit_tree.column(col, width=width)
        sy = ttk.Scrollbar(act, orient="vertical", command=self.audit_tree.yview)
        self.audit_tree.configure(yscrollcommand=sy.set)
        self.audit_tree.pack(side="left", fill="both", expand=True)
        sy.pack(side="right", fill="y")

        logs = ttk.Frame(tabs)
        tabs.add(logs, text="Process output")
        bar = ttk.Frame(logs)
        bar.pack(fill="x", pady=4)
        ttk.Label(bar, text="Process:").pack(side="left")
        self.log_choice = tk.StringVar(value=self.managed[0].spec["name"])
        box = ttk.Combobox(bar, textvariable=self.log_choice, state="readonly", width=28,
                           values=[m.spec["name"] for m in self.managed])
        box.pack(side="left", padx=6)
        self.log_text = tk.Text(logs, height=10, font=("Consolas", 9), state="disabled", wrap="none")
        self.log_text.pack(fill="both", expand=True)

        self.footer = ttk.Label(outer, text="")
        self.footer.pack(anchor="w", pady=(6, 0))

    # ----- controls ------------------------------------------------------
    def update_client_selection(self):
        selected = self.client_tree.selection()
        client = self.clients.get(selected[0]) if selected else None
        enabled = bool(client and client.get("online") and client["phase"] not in SCRAPE_BUSY
                       and not client.get("is_host")
                       and not client.get("command_status") and not self.client_request_pending
                       and not self.client_control_error)
        self.scrape_button.config(state="normal" if enabled else "disabled")
        if client:
            self.client_detail.config(text="%s  ·  %s  ·  %s%s%s" % (
                client["name"], client["id"], client.get("message", ""),
                "  ·  Import error: " + client["import_error"] if client.get("import_error") else "",
                "  ·  Agent is on the host PC; start it on the intended client PC." if client.get("is_host") else
                "  ·  Report source: " + client.get("report_url", "Not reported")))

    def request_client_scrape(self):
        selected = self.client_tree.selection()
        if not selected or self.client_request_pending:
            return
        identifier = selected[0]
        start, end = self.scrape_start.get().strip(), self.scrape_end.get().strip()
        try:
            if date.fromisoformat(end) < date.fromisoformat(start):
                raise ValueError("End date must be on or after start date")
        except ValueError as exc:
            messagebox.showerror("Invalid report dates", str(exc))
            return
        self.client_request_pending = True
        self.update_client_selection()
        self.client_note.config(text="Sending scrape request…")
        def request_scrape():
            try:
                result = client_control_request("/api/clients/%s/scrape" % identifier,
                                                {"start_date": start, "end_date": end})
                self.client_actions.put((identifier, result, None))
            except Exception as exc:
                self.client_actions.put((identifier, None, str(exc)))
        threading.Thread(target=request_scrape, daemon=True).start()

    def render_clients(self, clients, error):
        self.client_control_error = error
        if not error:
            self.clients = {client["id"]: client for client in clients}
        else:
            for client in self.clients.values():
                client["online"] = False
        for identifier in self.client_tree.get_children():
            if identifier not in self.clients:
                self.client_tree.delete(identifier)
        for identifier, client in self.clients.items():
            online = client.get("online", False)
            phase = client["phase"]
            stage = "Queued" if client.get("command_status") in ("queued", "dispatched") else phase.replace("_", " ").title()
            tag = "offline" if not online else "failed" if phase == "failed" or client.get("import_status") == "failed" else "active" if phase in SCRAPE_BUSY or client.get("command_status") else "ready"
            dates = "%s → %s" % (client.get("start_date", ""), client.get("end_date", "")) if client.get("start_date") else "—"
            seen = datetime.fromisoformat(client["last_seen"]).astimezone().strftime("%H:%M:%S")
            values = (client["name"], client["ip"], "Host PC" if client.get("is_host") else "● Online" if online else "● Offline", stage,
                      client.get("import_status", "").title() or "—", dates, seen)
            if self.client_tree.exists(identifier):
                self.client_tree.item(identifier, values=values, tags=(tag,))
            else:
                self.client_tree.insert("", "end", iid=identifier, values=values, tags=(tag,))
        remote = [client for client in self.clients.values() if not client.get("is_host")]
        count = sum(client.get("online", False) for client in remote)
        note = "%s of %s clients online. Select an idle client to request a scrape." % (count, len(remote)) if remote else "No remote clients registered. Run start-scraping-agent.cmd on each client PC with the host address and client token in its .env."
        self.client_note.config(text=error or note,
                                foreground="#b42318" if error else "#374151")
        self.update_client_selection()

    def start(self, m):
        try:
            m.start()
        except Exception as exc:
            messagebox.showerror("Cannot start " + m.spec["name"], str(exc))
        self.render()

    def stop(self, m):
        if m.external and not m.alive:
            messagebox.showinfo("External process",
                                "%s was not started by the switchboard, so it cannot be switched off from here.\n"
                                "Close it where it was started, then use ON." % m.spec["name"])
            return
        if m.spec["key"] == "credit" and m.alive and not messagebox.askyesno(
                "Switch off", "Cashier clients will go offline and queue entries locally. Switch off the Credit Entry Server?"):
            return
        m.stop()
        self.render()

    def restart(self, m):
        m.stop()
        time.sleep(0.5)
        self.start(m)

    def start_all(self):
        for m in self.managed:
            if not m.alive and not m.external:
                self.start(m)

    def stop_all(self):
        if messagebox.askyesno("Stop all", "Switch off every process started by this switchboard?"):
            for m in self.managed:
                m.stop()
            self.render()

    def on_close(self):
        running = [m for m in self.managed if m.alive]
        if running:
            answer = messagebox.askyesnocancel(
                "Close switchboard",
                "%d process(es) are still running.\n\nYes = switch them off and close\nNo = leave them running and close\n"
                "Cancel = stay open" % len(running))
            if answer is None:
                return
            if answer:
                for m in running:
                    m.stop()
        self.stop_flag.set()
        self.destroy()

    # ----- polling -------------------------------------------------------
    def poller(self):
        while not self.stop_flag.is_set():
            health = {m.spec["key"]: probe(m.spec) for m in self.managed}
            try:
                clients = client_control_request("/api/clients")["clients"]
                client_error = ""
            except Exception as exc:
                clients, client_error = [], "Client status unavailable: " + str(exc)
            self.results.put((health, read_db_stats(), clients, client_error))
            self.stop_flag.wait(POLL_SECONDS)

    def tick(self):
        try:
            while True:
                health, stats, clients, client_error = self.results.get_nowait()
                for m in self.managed:
                    result = health.get(m.spec["key"])
                    if m.alive:
                        m.healthy = True if result is None else result
                        m.external = False
                    else:
                        m.healthy = None
                        m.external = bool(result) and m.spec["port"] is not None
                self.last_stats = stats
                self.render_flow(stats)
                self.render_clients(clients, client_error)
        except queue.Empty:
            pass
        try:
            while True:
                identifier, result, error = self.client_actions.get_nowait()
                self.client_request_pending = False
                if error:
                    messagebox.showerror("Cannot request scrape", error)
                else:
                    if identifier in self.clients:
                        self.clients[identifier]["command_status"] = "queued"
                    self.client_note.config(text="Scrape queued; the client will pick it up on its next poll.")
                self.update_client_selection()
        except queue.Empty:
            pass
        self.render()
        self.after(1000, self.tick)

    # ----- rendering -----------------------------------------------------
    def render(self):
        for m in self.managed:
            row = self.rows[m.spec["key"]]
            text, colour = m.status()
            row["lamp"].itemconfig(row["dot"], fill=colour)
            row["status"].config(text=text, foreground=colour)
            parts = []
            if m.alive:
                parts.append("PID %d" % m.proc.pid)
                parts.append("up " + fmt_duration(time.time() - m.started))
            if m.spec["port"]:
                parts.append("port %d" % m.spec["port"])
            if m.spec["key"] == "relay":
                try:
                    with open(os.path.join(HOST_DIR, "sms-relay-host", "relay_state.json"), encoding="utf-8") as f:
                        parts.append("last SMS _id %s" % json.load(f).get("last_seen_id"))
                except (OSError, ValueError):
                    pass
            row["info"].config(text="  |  ".join(parts))
        self.render_log()
        up = sum(1 for m in self.managed if m.alive or m.external)
        self.footer.config(text="%d of %d processes up  |  databases: %s, %s  |  refreshed %s" % (
            up, len(self.managed), CREDIT_DB_PATH, MARAKI_DB_PATH, datetime.now().strftime("%H:%M:%S")))

    def render_flow(self, stats):
        for item in self.flow_tree.get_children():
            self.flow_tree.delete(item)
        if not stats["db_exists"]:
            self.flow_note.config(text="Database file not created yet. Start the Credit Entry Server to create it.", foreground="#b45309")
            return
        for label, table, _ in WATCHED:
            if table not in stats["tables"]:
                continue
            count, last = stats["tables"][table]
            self.baseline.setdefault(table, count)
            self.flow_tree.insert("", "end", values=(label, "{:,}".format(count),
                                                     "+{:,}".format(count - self.baseline[table]), last or "-"))
        note = ""
        if stats["unlogged_sms"] is not None:
            note = "SMS payments waiting for a cashier: %d" % stats["unlogged_sms"]
        if stats["error"]:
            note = "Database read problem: " + stats["error"]
        self.flow_note.config(text=note, foreground="#dc2626" if stats["error"] else "black")
        self.audit_tree.delete(*self.audit_tree.get_children())
        for rid, created, event, actor, ip, details in stats["audit"]:
            self.audit_tree.insert("", "end", iid=str(rid), values=(created, event, actor or "", ip or "", details or ""))

    def render_log(self):
        m = next(x for x in self.managed if x.spec["name"] == self.log_choice.get())
        text = ""
        try:
            with open(m.log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 12000))
                text = f.read().decode("utf-8", errors="replace")
        except OSError:
            text = "(no output yet)"
        current = self.log_text.get("1.0", "end-1c")
        if text != current:
            self.log_text.config(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("end", text)
            self.log_text.see("end")
            self.log_text.config(state="disabled")


if __name__ == "__main__":
    Switchboard().mainloop()
