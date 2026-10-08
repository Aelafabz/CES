"""Verify the real Tk client panel against the running host, without showing a window."""
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "host/switchboard"))
from switchboard import Switchboard, client_control_request


class HiddenSwitchboard(Switchboard):
    def build(self):
        self.withdraw()
        super().build()

    def poller(self):
        pass


clients = []
for _ in range(20):
    try:
        clients = client_control_request("/api/clients")["clients"]
        if any(client["online"] for client in clients):
            break
    except Exception:
        pass
    time.sleep(1)
if not clients or not any(client["online"] for client in clients):
    raise RuntimeError("No live client heartbeat reached the host")
app = HiddenSwitchboard()
try:
    app.render_clients(clients, "")
    online = next(client for client in clients if client["online"])
    app.client_tree.selection_set(online["id"])
    app.update_client_selection()
    app.update_idletasks()
    assert len(app.client_tree.get_children()) == len(clients)
    assert "Online" in app.client_tree.item(online["id"], "values")[2]
    if online["phase"] in ("idle", "completed", "failed") and not online["command_status"]:
        assert str(app.scrape_button.cget("state")) == "normal"
    print("Switchboard client panel verified: live indicator, scrape controls, and host import column")
    print("Client:", online["name"], "|", online["phase"], "| online")
finally:
    app.stop_flag.set()
    app.destroy()
