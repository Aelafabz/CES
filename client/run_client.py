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
    parser.add_argument("component", nargs="?", default="credit", choices=("credit", "mrk", "retro"))
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
                       "mrk-client/accurate_time.py", "mrk-client/mrk_sender.py", "mrk-client/retro_scraper.py"):
            path = CLIENT_ROOT / module
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        print("Client setup OK. Host connectivity and credentials were not tested.")
        return
    if args.component == "credit":
        sys.path.insert(0, str(CLIENT_ROOT / "credit-entry-client"))
        runpy.run_path(str(CLIENT_ROOT / "credit-entry-client" / "credit-entry.py"), run_name="__main__")
        return
    sys.path.insert(0, str(CLIENT_ROOT / "mrk-client"))
    if args.component == "retro":
        from retro_scraper import run_retro_pipeline
        run_retro_pipeline()
        return
    start = args.start_date or date.today()
    end = args.end_date or start
    if end < start:
        parser.error("--end-date must be on or after --start-date")
    from page_scraper import PageScraper
    from accurate_time import AccurateTimeManager
    from data_organizer import DataOrganizer
    from mrk_sender import MRKSender
    scraper = PageScraper(check_retro=False)
    scraper.store_site_data(start.isoformat(), end.isoformat())
    if not scraper.sales_file or not scraper.erca_file:
        raise RuntimeError("Maraki did not return both reports. Check the login and report dates.")
    if os.environ.get("MRK_XML_DIR"):
        xml_dir = get_path("MRK_XML_DIR", "", CLIENT_ROOT)
        AccurateTimeManager(scraper.sales_file, str(xml_dir)).process_sales_file()
    sales, erca = DataOrganizer(scraper.sales_file, scraper.erca_file).process_all()
    MRKSender(sales, erca, start.isoformat(), end.isoformat()).sender()


if __name__ == "__main__":
    main()
