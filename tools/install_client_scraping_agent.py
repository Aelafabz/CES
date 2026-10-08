"""Install the reviewed client update, backing up overwritten files first."""
from datetime import datetime
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "host/mrk-host")]
from env_config import load_env_file, save_env_values
from mrk_receiver import ensure_control_tokens
import os


def main():
    target = Path("C:/client").resolve()
    if target != Path("C:/client").absolute() or not (target / "mrk-client/page_scraper.py").is_file():
        raise ValueError("The deployed client folder was not found")
    source = ROOT / "client-agent-update"
    files = ["mrk_signal.py", "mrk_agent.py", "run_client.py", "start-scraping-agent.cmd", ".env.example", "SCRAPING_AGENT.md", "mrk-client/page_scraper.py"]
    for name in files:
        if not (source / name).is_file():
            raise ValueError("Missing update file: " + name)
        if name.endswith(".py"):
            compile((source / name).read_text(encoding="utf-8"), name, "exec")
    backup = ROOT / "client-agent-backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
    backup.mkdir(parents=True, exist_ok=False)
    for name in files + [".env"]:
        if (target / name).is_file():
            (backup / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target / name, backup / name)
    load_env_file(ROOT / "host/.env")
    ensure_control_tokens()
    for name in files:
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target / name)
    save_env_values(target / ".env", {"MRK_CONTROL_CLIENT_TOKEN": os.environ["MRK_CONTROL_CLIENT_TOKEN"]})
    print("Installed client scraping agent in", target)
    print("Previous client files saved in", backup)


if __name__ == "__main__":
    main()
