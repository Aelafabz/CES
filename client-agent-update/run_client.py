"""Entry point for the independently deployed CES client folder."""
import argparse
import importlib
import os
from pathlib import Path
import runpy
import sys
from datetime import date

from env_config import get_path, load_env_file

CLIENT_ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", nargs="?", default="credit", choices=("credit", "mrk", "retro", "agent"))
    parser.add_argument("--check", action="store_true", help="Check local setup without starting apps or contacting servers")
    parser.add_argument("--start-date", type=date.fromisoformat, help="Maraki start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=date.fromisoformat, help="Maraki end date (YYYY-MM-DD)")
    args = parser.parse_args()
    load_env_file(CLIENT_ROOT / ".env")
    if args.check:
        for module in ("tkinter", "requests", "bs4", "openpyxl"):
            importlib.import_module(module)
        if not (CLIENT_ROOT / ".env").is_file():
            parser.error("Missing .env. Copy .env.example to .env and configure the host addresses.")
        for module in ("credit-entry-client/credit-entry.py", "credit-entry-client/v6_common.py",
                       "mrk-client/page_scraper.py", "mrk-client/data_organizer.py",
                       "mrk-client/accurate_time.py", "mrk-client/mrk_sender.py", "mrk-client/retro_scraper.py",
                       "mrk_signal.py", "mrk_agent.py"):
            path = CLIENT_ROOT / module
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        print("Client setup OK. Host connectivity and credentials were not tested.")
        return
    if args.component == "credit":
        from mrk_signal import ensure_agent
        try:
            ensure_agent(CLIENT_ROOT)
        except (OSError, ValueError) as exc:
            print("Scraping status agent could not start: %s" % type(exc).__name__)
        sys.path.insert(0, str(CLIENT_ROOT / "credit-entry-client"))
        runpy.run_path(str(CLIENT_ROOT / "credit-entry-client" / "credit-entry.py"), run_name="__main__")
        return
    if args.component == "agent":
        from mrk_agent import main as agent_main
        agent_main()
        return
    sys.path.insert(0, str(CLIENT_ROOT / "mrk-client"))
    if args.component == "retro":
        from mrk_signal import ensure_agent, run_historical
        ensure_agent(CLIENT_ROOT)
        run_historical(CLIENT_ROOT)
        return
    start = args.start_date or date.today()
    end = args.end_date or start
    if end < start:
        parser.error("--end-date must be on or after --start-date")
    from mrk_signal import ensure_agent, run_pipeline
    ensure_agent(CLIENT_ROOT)
    run_pipeline(start.isoformat(), end.isoformat(), root=CLIENT_ROOT)


if __name__ == "__main__":
    main()
