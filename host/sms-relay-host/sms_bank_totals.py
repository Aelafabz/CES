"""Historical incoming-payment totals from a phone connected through ADB."""
import argparse
import csv
import json
import re
import subprocess
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'client', 'credit-entry-client')))

from v6_common import TARGET_SMS_SENDERS, parse_datetime_text, parse_sms_payment

ETHIOPIA = timezone(timedelta(hours=3))
APP_DIR = Path(__file__).resolve().parent
SUPPORTED_BANKS = ("Awash", "Bank of Abyssinia", "CBE", "Telebirr")


def run_adb(cfg, args, timeout=120):
    """Read through ADB without depending on the installed relay version."""
    command = [cfg.get("adb_path") or "adb"] + list(args)
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError("ADB was not found. Set adb_path in relay_config.json "
                           "or supply --adb-path with the full path to adb.exe.") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("ADB timed out while reading the phone. Check its connection and authorization.") from None
    except OSError as exc:
        raise RuntimeError("Cannot start ADB: %s" % exc) from exc
    output = result.stdout.decode("utf-8", errors="replace")
    detail = result.stderr.decode("utf-8", errors="replace").strip()
    if result.returncode:
        raise RuntimeError("ADB failed: " + (detail or output.strip() or str(result.returncode)))
    return output


def read_phone(cfg):
    # Keep body last: commas and field-like text inside an SMS are not metadata.
    output = run_adb(cfg, ["shell", "content query --uri content://sms/inbox "
                           "--projection _id:address:date:body"], timeout=120)
    if "No result found." in output and not output.strip().startswith("Row:"):
        return []
    pattern = re.compile(r"^Row:\s*\d+\s+_id=(\d+), address=(.*?), date=(\d+), body=", re.M)
    matches = list(pattern.finditer(output))
    if not matches or output[:matches[0].start()].strip():
        raise RuntimeError("Could not read SMS inbox. Check ADB authorization and SMS permissions. "
                           + output[:500])
    rows = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(output)
        rows.append({"_id": match[1], "address": match[2], "date": match[3],
                     "body": output[match.end():end].rstrip("\r\n")})
    return rows


def bounds(start, end):
    values = []
    for value in (start, end):
        value = str(value).strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?", value):
            raise ValueError("Use YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS (Ethiopia time).")
        parsed = parse_datetime_text(value)
        if parsed is None:
            raise ValueError("Invalid date or time: " + value)
        values.append(parsed)
    if values[0] > values[1]:
        raise ValueError("Start must be before or equal to end.")
    return values


def build_report(rows, start, end, senders=TARGET_SMS_SENDERS):
    start, end = bounds(start, end)
    totals = {bank: {"count": 0, "total": Decimal("0.00")} for bank in SUPPORTED_BANKS}
    included, review = [], []
    counts = defaultdict(int)
    seen = set()
    for row in rows:
        counts["scanned"] += 1
        msg_id = str(row.get("_id", ""))
        if not msg_id or msg_id in seen:
            if not msg_id:
                raise ValueError("SMS record is missing its ID.")
            counts["duplicate_ids"] += 1
            continue
        seen.add(msg_id)
        sender = str(row.get("address", ""))
        if sender not in senders:
            counts["other_senders"] += 1
            continue
        body = str(row.get("body", ""))
        try:
            # Reuse transaction-time rules, but convert fallback timestamps in
            # Ethiopia time regardless of the PC's timezone.
            payment = parse_sms_payment(sender, body)
            sms_dt = datetime.fromtimestamp(int(row["date"]) / 1000, ETHIOPIA).replace(tzinfo=None)
            if payment and payment["time_source"] != "Exact":
                payment["received_at"] = sms_dt.strftime("%Y-%m-%d %H:%M:%S")
                payment["time_source"] = "SMS received time (Ethiopia)"
        except (ValueError, TypeError, OverflowError, OSError) as exc:
            review.append({"sms_id": msg_id, "sender": sender, "reason": str(exc), "body": body})
            continue
        if not payment:
            # These may be outgoing notices or a new incoming template. Do not silently total them.
            if start <= sms_dt <= end:
                review.append({"sms_id": msg_id, "sender": sender,
                               "reason": "Not recognized as incoming; check template/direction", "body": body})
            counts["unrecognized"] += 1
            continue
        dt = parse_datetime_text(payment["received_at"])
        if not start <= dt <= end:
            counts["outside_interval"] += 1
            continue
        if sender == "BOA" and not re.search(r"\bcredited\b", body, re.I):
            review.append({"sms_id": msg_id, "sender": sender,
                           "reason": "BOA amount matched but incoming credit wording absent", "body": body})
            continue
        if re.search(r"\b(failed|unsuccessful|reversed|reversal|pending)\b", body, re.I):
            review.append({"sms_id": msg_id, "sender": sender,
                           "reason": "Transaction status requires review", "body": body})
            continue
        amount = Decimal(str(payment["amount"]))
        if not amount.is_finite() or amount <= 0:
            review.append({"sms_id": msg_id, "sender": sender, "reason": "Invalid amount", "body": body})
            continue
        total = totals.setdefault(payment["channel"], {"count": 0, "total": Decimal("0.00")})
        total["count"] += 1
        total["total"] += amount
        included.append({"sms_id": msg_id, "bank": payment["channel"], "amount_etb": str(amount),
                         "transaction_time": payment["received_at"], "time_source": payment["time_source"],
                         "sender": sender, "payer": payment["payer"], "body": body})
    return {"totals": totals, "payments": included, "review": review, "counts": dict(counts)}


def export_report(report, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    tables = {
        "totals": (["bank", "transactions", "total_etb"],
                   [{"bank": b, "transactions": v["count"], "total_etb": format(v["total"], ".2f")}
                    for b, v in sorted(report["totals"].items())]),
        "payments": (["sms_id", "bank", "amount_etb", "transaction_time", "time_source", "sender", "payer", "body"], report["payments"]),
        "review": (["sms_id", "sender", "reason", "body"], report["review"]),
    }
    for name, (fields, records) in tables.items():
        with (folder / (name + ".csv")).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)


def load_rows(args):
    if args.input:
        with open(args.input, encoding="utf-8") as handle:
            return json.load(handle)
    cfg = {}
    config = APP_DIR / "relay_config.json"
    if config.exists():
        cfg = json.loads(config.read_text(encoding="utf-8"))
    if args.adb_path:
        cfg["adb_path"] = args.adb_path
    return read_phone(cfg)


def summary(report):
    lines = ["Bank                         Payments          Total ETB"]
    for bank, value in sorted(report["totals"].items()):
        lines.append(f"{bank:28} {value['count']:8} {value['total']:18,.2f}")
    grand = sum((v["total"] for v in report["totals"].values()), Decimal("0.00"))
    lines.append(f"{'TOTAL':28} {len(report['payments']):8} {grand:18,.2f}")
    fallback = sum(p["time_source"] != "Exact" for p in report["payments"])
    lines.append(f"\nScanned {report['counts'].get('scanned', 0)} SMS. Review: {len(report['review'])}. "
                 f"Included using SMS received time: {fallback}.")
    return "\n".join(lines)


def gui(args):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    root.title("Incoming SMS totals by bank â€” ETB")
    root.geometry("790x430")
    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)
    today = datetime.now(ETHIOPIA).strftime("%Y-%m-%d")
    start = tk.StringVar(value=today + " 00:00:00")
    end = tk.StringVar(value=today + " 23:59:59")
    for label, variable in (("Start (Ethiopia time)", start), ("End (inclusive)", end)):
        ttk.Label(frame, text=label).pack(anchor="w")
        ttk.Entry(frame, textvariable=variable, width=30).pack(anchor="w", pady=(0, 8))
    ttk.Label(frame, text="YYYY-MM-DD HH:MM:SS. Reads the full stored inbox; uses transaction time when recognized.").pack(anchor="w")
    output = tk.Text(frame, height=12, font=("Consolas", 10))
    output.pack(fill="both", expand=True, pady=10)
    state = {}
    def finish(report=None, error=None):
        button.config(state="normal")
        if error:
            messagebox.showerror("SMS report failed", error)
            return
        state["report"] = report
        output.delete("1.0", "end")
        output.insert("end", summary(report))
        export.config(state="normal")
    def calculate():
        try:
            first, last = start.get(), end.get()
            bounds(first, last)
        except ValueError as exc:
            messagebox.showerror("Invalid interval", str(exc))
            return
        button.config(state="disabled")
        export.config(state="disabled")
        output.delete("1.0", "end")
        output.insert("end", "Reading all SMS from phoneâ€¦")
        def worker():
            try:
                report = build_report(load_rows(args), first, last)
                root.after(0, lambda: finish(report=report))
            except Exception as exc:
                detail = str(exc)
                root.after(0, lambda: finish(error=detail))
        threading.Thread(target=worker, daemon=True).start()
    def save():
        folder = filedialog.askdirectory(title="Choose folder for report CSV files")
        if folder:
            try:
                export_report(state["report"], folder)
                messagebox.showinfo("Saved", "Saved totals.csv, payments.csv, and review.csv.")
            except OSError as exc:
                messagebox.showerror("Export failed", str(exc))
    button = ttk.Button(frame, text="Read phone and calculate", command=calculate)
    button.pack(side="left")
    export = ttk.Button(frame, text="Export CSV", command=save, state="disabled")
    export.pack(side="left", padx=8)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="Inclusive start, YYYY-MM-DD HH:MM:SS, Ethiopia time")
    parser.add_argument("--end", help="Inclusive end, YYYY-MM-DD HH:MM:SS, Ethiopia time")
    parser.add_argument("--adb-path", help="Override adb executable from relay_config.json")
    parser.add_argument("--input", help="Offline JSON list of {_id, address, date, body} SMS records")
    parser.add_argument("--export", help="Folder for totals.csv, payments.csv, review.csv")
    args = parser.parse_args()
    if not args.start and not args.end:
        gui(args)
        return
    if not args.start or not args.end:
        parser.error("Supply both --start and --end.")
    try:
        bounds(args.start, args.end)
        report = build_report(load_rows(args), args.start, args.end)
        print(summary(report))
        if args.export:
            export_report(report, args.export)
    except Exception as exc:
        parser.exit(1, "Report failed: %s\n" % exc)


if __name__ == "__main__":
    main()