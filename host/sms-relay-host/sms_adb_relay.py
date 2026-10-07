import sys
import os
import json
import time
import argparse
import random
import subprocess
import threading
import uuid
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'client', 'credit-entry-client')))

from v6_common import parse_sms_payment, TARGET_SMS_SENDERS

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(APP_DIR, "..", ".."))
sys.path.insert(0, PROJECT_ROOT)
from env_config import get_path, load_env_file

HOST_ENV_PATH = os.path.join(PROJECT_ROOT, "host", ".env")
load_env_file(HOST_ENV_PATH)
RELAY_CONFIG_PATH = str(get_path(
    "SMS_RELAY_CONFIG_FILE", os.path.join("host", "sms-relay-host", "relay_config.json"), PROJECT_ROOT))
RELAY_STATE_PATH = str(get_path(
    "SMS_RELAY_STATE_FILE", os.path.join("host", "sms-relay-host", "relay_state.json"), PROJECT_ROOT))

def load_relay_config():
    cfg = {
        "enabled": True,
        "adb_path": "adb",
        "poll_interval_seconds": 2,
        "server_url": "http://127.0.0.1:8765",
        "start_after_id": None,
        "target_senders": TARGET_SMS_SENDERS,
        "relay_token": ""
    }
    if os.path.exists(RELAY_CONFIG_PATH):
        try:
            with open(RELAY_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except ValueError as exc:
            raise RuntimeError(f"Invalid relay configuration in {RELAY_CONFIG_PATH}: {exc}") from exc
    cfg.update({
        "adb_path": os.environ.get("SMS_RELAY_ADB_PATH", cfg["adb_path"]),
        "poll_interval_seconds": float(os.environ.get(
            "SMS_RELAY_POLL_INTERVAL_SECONDS", cfg["poll_interval_seconds"])),
        "server_url": os.environ.get("SMS_RELAY_SERVER_URL", cfg["server_url"]),
        "relay_token": os.environ.get("CRED_V6_RELAY_TOKEN", cfg["relay_token"]),
        "target_senders": [sender.strip() for sender in os.environ.get(
            "SMS_RELAY_TARGET_SENDERS", ",".join(cfg["target_senders"])).split(",") if sender.strip()],
    })
    return cfg

def load_relay_state():
    try:
        with open(RELAY_STATE_PATH, "r", encoding="utf-8") as f:
            value = json.load(f).get("last_seen_id")
        return int(value) if value not in (None, "") else None
    except (OSError, ValueError, TypeError):
        return None

def save_relay_state(last_seen):
    temp = RELAY_STATE_PATH + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump({"last_seen_id": int(last_seen)}, f, indent=2)
    os.replace(temp, RELAY_STATE_PATH)

def adb_shell(cfg, command):
    cmd = [cfg.get("adb_path") or "adb", "shell", command]
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=20).decode("utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError(f"ADB error: {exc}")

def parse_content_rows(output):
    records = []
    current = []
    for line in output.splitlines():
        if line.startswith("Row:"):
            if current:
                records.append("\n".join(current))
            current = [line.strip()]
        elif current:
            current.append(line.rstrip())
    if current:
        records.append("\n".join(current))
    return records

def parse_message_output(cfg, output):
    if "No result found." in output:
        return []

    rows = parse_content_rows(output)
    results = []
    for row_text in rows:
        import re
        m = re.match(r'^Row:\s*\d+\s+_id=(\d+),\s+address=(.*?),\s+body=(.*)$', row_text, re.DOTALL)
        if m:
            msg_id = int(m.group(1))
            address = m.group(2)
            body = m.group(3)
            if address in cfg["target_senders"]:
                payment = parse_sms_payment(address, body)
                if payment:
                    payment["external_id"] = f"adb-{msg_id}"
                    payment["display_text"] = body.replace("\n", " ")[:60]
                    results.append((msg_id, payment))
            else:
                results.append((msg_id, None))
    return results

def read_new_messages(cfg, last_seen):
    last_seen = last_seen or 0
    query = f"content query --uri content://sms/inbox --projection _id:address:body --where \"_id > {last_seen}\" --sort \"_id ASC\""
    return parse_message_output(cfg, adb_shell(cfg, query))

def post_sms(cfg, payment):
    url = cfg["server_url"].rstrip('/') + "/api/relay/sms"
    data = json.dumps(payment).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    if cfg.get("relay_token"):
        req.add_header('X-Cred-Token', cfg["relay_token"])
    
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.getcode() == 201

def generate_fake_sms_input():
    messages = [
        (101, "127", "Received ETB 1,250.50 from Alice Example on 07/10/2026 at 08:30:00"),
        (102, "CBE", "You received ETB 300.00 from account 100000 (Bob Example)."),
        (103, "Unknown Sender", "This message should not be relayed."),
    ]
    return "\n".join(
        f"Row: {index} _id={msg_id}, address={sender}, body={body}"
        for index, (msg_id, sender, body) in enumerate(messages)
    )


def generate_fake_sms_payment(cfg, sequence):
    timestamp = datetime.now().strftime("%d/%m/%Y at %H:%M:%S")
    if sequence % 2:
        sender = "127"
        body = f"Received ETB 1,250.50 from FAKE TEST Alice on {timestamp}"
    else:
        sender = "CBE"
        body = "You received ETB 300.00 from account 100000 (FAKE TEST Bob)."
    msg_id = time.time_ns()
    output = f"Row: 0 _id={msg_id}, address={sender}, body={body}"
    messages = parse_message_output(cfg, output)
    payment = next((item for _, item in messages if item is not None), None)
    if payment is None:
        raise RuntimeError(f"Generated fake SMS from {sender} was not recognized.")
    payment["external_id"] = f"fake-stream-{uuid.uuid4().hex}"
    payment["body"] = "[FAKE TEST SMS] " + payment["body"]
    payment["display_text"] = "[FAKE TEST] " + payment["display_text"]
    return payment


class _FakeRelayHandler(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.received.append((self.path, self.headers.get("X-Cred-Token"), json.loads(body)))
        self.send_response(201)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def test_sms_adb_relay():
    """Exercise SMS parsing and posting against a temporary local receiver."""
    _FakeRelayHandler.received.clear()
    server = HTTPServer(("127.0.0.1", 0), _FakeRelayHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    cfg = {
        "target_senders": ["127", "CBE"],
        "server_url": f"http://127.0.0.1:{server.server_port}",
        "relay_token": "fake-test-token",
    }
    try:
        messages = parse_message_output(cfg, generate_fake_sms_input())
        if [msg_id for msg_id, _ in messages] != [101, 102, 103]:
            raise AssertionError("Fake SMS input was not parsed as expected.")
        for _, payment in messages:
            if payment is not None and not post_sms(cfg, payment):
                raise RuntimeError("Local fake SMS receiver did not accept the payment.")
        if len(_FakeRelayHandler.received) != 2:
            raise AssertionError("Expected exactly two supported payments at the local receiver.")
        if any(path != "/api/relay/sms" for path, _, _ in _FakeRelayHandler.received):
            raise AssertionError("Fake payment was posted to an unexpected endpoint.")
        if any(token != "fake-test-token" for _, token, _ in _FakeRelayHandler.received):
            raise AssertionError("Relay token was not sent to the local fake receiver.")
        print("Fake SMS relay passed: 2 payments parsed and posted to a temporary local receiver; no phone or live server used.")
        return _FakeRelayHandler.received
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()


def run_fake_sms_stream(cfg=None, stop_event=None, interval_range=(5.0, 10.0)):
    """Continuously post visibly marked fake payments to the configured server."""
    cfg = cfg or load_relay_config()
    stop_event = stop_event or threading.Event()
    sequence = 0
    print(f"Streaming fake SMS to {cfg['server_url']} every 5-10 seconds; press Ctrl+C to stop.")
    while not stop_event.is_set():
        sequence += 1
        payment = generate_fake_sms_payment(cfg, sequence)
        try:
            if not post_sms(cfg, payment):
                raise RuntimeError("Server did not accept the fake SMS.")
            print(f"Posted {payment['channel']} fake SMS {payment['external_id']}")
        except Exception as exc:
            print(f"Failed to post fake SMS: {exc}")
        if stop_event.wait(random.uniform(*interval_range)):
            break


def main():
    parser = argparse.ArgumentParser(description="Relay incoming SMS payments.")
    parser.add_argument(
        "--test-fake",
        action="store_true",
        help="run the parser and HTTP relay against generated SMS and a temporary local receiver",
    )
    parser.add_argument(
        "--fake-stream",
        action="store_true",
        help="continuously post visibly marked fake payments to the configured server every 5-10 seconds",
    )
    args = parser.parse_args()
    if args.test_fake:
        test_sms_adb_relay()
        return
    if args.fake_stream:
        try:
            run_fake_sms_stream()
        except KeyboardInterrupt:
            print("\nFake SMS stream stopped.")
        return

    print("Starting standalone SMS ADB Relay...")
    cfg = load_relay_config()
    last_seen = load_relay_state()
    if last_seen is None:
        last_seen = cfg.get("start_after_id") or 0
    
    while True:
        try:
            cfg = load_relay_config()
            if not cfg.get("enabled", True):
                time.sleep(5)
                continue
            
            messages = read_new_messages(cfg, last_seen)
            for msg_id, payment in messages:
                if payment:
                    try:
                        post_sms(cfg, payment)
                        print(f"Relayed {payment['channel']} SMS _id {msg_id}")
                    except Exception as e:
                        print(f"Failed to post SMS _id {msg_id}: {e}")
                        break
                last_seen = max(last_seen, msg_id)
                save_relay_state(last_seen)
                
        except Exception as e:
            print(f"Relay error: {e}")
            
        time.sleep(cfg.get("poll_interval_seconds", 2))

if __name__ == '__main__':
    main()
