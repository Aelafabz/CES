from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from switchboard import Switchboard


class Widget:
    def __init__(self):
        self.settings = {}
    def config(self, **settings):
        self.settings.update(settings)


class Tree:
    def __init__(self):
        self.rows = {}
        self.selected = ()
    def get_children(self): return tuple(self.rows)
    def selection(self): return self.selected
    def exists(self, key): return key in self.rows
    def insert(self, parent, position, iid, **settings): self.rows[iid] = settings
    def item(self, key, **settings): self.rows[key].update(settings)
    def delete(self, key): del self.rows[key]


class ClientPanelTest(unittest.TestCase):
    def panel(self):
        panel = SimpleNamespace(clients={}, client_control_error="", client_request_pending=False,
                                client_tree=Tree(), client_note=Widget(), client_detail=Widget(), scrape_button=Widget())
        panel.update_client_selection = lambda: Switchboard.update_client_selection(panel)
        return panel

    def client(self, **changes):
        client = dict(id="client-a", name="Cashier A", ip="192.168.1.22", online=True, phase="idle", import_status="",
                      message="Ready", last_seen=datetime.now(timezone.utc).isoformat(), command_status="", import_error="")
        client.update(changes)
        return client

    def test_online_idle_allows_manual_scrape_and_running_disables_it(self):
        panel = self.panel()
        Switchboard.render_clients(panel, [self.client()], "")
        panel.client_tree.selected = ("client-a",)
        panel.update_client_selection()
        self.assertEqual(panel.scrape_button.settings["state"], "normal")
        Switchboard.render_clients(panel, [self.client(phase="scraping", message="Fetching reports")], "")
        self.assertEqual(panel.scrape_button.settings["state"], "disabled")
        self.assertEqual(panel.client_tree.rows["client-a"]["tags"], ("active",))
        self.assertIn("Fetching reports", panel.client_detail.settings["text"])
        self.assertEqual(panel.client_tree.selected, ("client-a",))

    def test_queued_failed_offline_and_receiver_unavailable_indicators(self):
        panel = self.panel()
        for client, tag in ((self.client(command_status="queued"), "active"), (self.client(phase="failed"), "failed"), (self.client(online=False), "offline")):
            Switchboard.render_clients(panel, [client], "")
            panel.client_tree.selected = ("client-a",)
            panel.update_client_selection()
            self.assertEqual(panel.client_tree.rows["client-a"]["tags"], (tag,))
        Switchboard.render_clients(panel, [self.client()], "")
        Switchboard.render_clients(panel, [], "Receiver unavailable")
        self.assertEqual(panel.scrape_button.settings["state"], "disabled")
        self.assertEqual(panel.client_tree.rows["client-a"]["tags"], ("offline",))
        self.assertEqual(panel.client_note.settings["text"], "Receiver unavailable")

    def test_host_agent_is_labeled_and_manual_scraping_disabled(self):
        panel = self.panel()
        Switchboard.render_clients(panel, [self.client(is_host=True)], "")
        panel.client_tree.selected = ("client-a",)
        panel.update_client_selection()
        self.assertEqual(panel.client_tree.rows["client-a"]["values"][2], "Host PC")
        self.assertEqual(panel.scrape_button.settings["state"], "disabled")
        self.assertIn("No remote clients registered", panel.client_note.settings["text"])
        self.assertIn("host PC", panel.client_detail.settings["text"])
