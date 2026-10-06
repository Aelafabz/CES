import sys
import os
import json
import time
import subprocess
import urllib.request
import urllib.error

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'client', 'credit-entry-client')))

from v6_common import parse_sms_payment, TARGET_SMS_SENDERS

APP_DIR = os.path.dirname(os.path.abspath(__file__))
RELAY_CONFIG_PATH = os.path.join(APP_DIR, "relay_config.json")
RELAY_STATE_PATH = os.path.join(APP_DIR, "relay_state.json")

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
            pass
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

def read_new_messages(cfg, last_seen):
    last_seen = last_seen or 0
    query = f"content query --uri content://sms/inbox --projection _id:address:body --where \"_id > {last_seen}\" --sort \"_id ASC\""
    output = adb_shell(cfg, query)
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

def post_sms(cfg, payment):
    url = cfg["server_url"].rstrip('/') + "/api/relay/sms"
    data = json.dumps(payment).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    if cfg.get("relay_token"):
        req.add_header('X-Cred-Token', cfg["relay_token"])
    
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.getcode() == 201

def main():
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
