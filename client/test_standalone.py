"""Regression checks run only against an isolated copy of the client folder."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class StandaloneClientTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ces standalone ")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.client = self.base / "deployment" / "cashier"
        shutil.copytree(Path(__file__).resolve().parent, self.client,
                        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".env", "*.log"))
        (self.client / ".env").write_text(
            "CRED_V6_SERVER_URL=http://configured-host:8765\n"
            "CRED_V6_CLIENT_TOKEN=test-token\n"
            "CRED_V6_CLIENT_DATA_DIR=data/credit\n"
            "MRK_DATA_DIR=data/reports\n"
            "MRK_RETRO_DATA_DIR=data/retro\n"
            "MRK_RETRO_STATE_FILE=data/retro/state.json\n"
            "MRK_RECEIVER_HOST=configured-receiver\n",
            encoding="utf-8")
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith(("CRED_", "MRK_", "PYTHONPATH"))}

    def run_isolated(self, code):
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(self.client)],
                                cwd=self.base, env=self.environment,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_detached_imports_configuration_and_data_paths(self):
        self.run_isolated('''
import os, runpy, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path[:0] = [str(root), str(root / "credit-entry-client"), str(root / "mrk-client")]
import env_config
assert Path(env_config.__file__).parent == root
credit = runpy.run_path(str(root / "credit-entry-client" / "credit-entry.py"))
assert credit["DATA_DIR"] == root / "data/credit"
assert credit["load_config"]()["server_url"] == "http://configured-host:8765"
assert credit["load_config"]()["client_token"] == "test-token"
scratch = runpy.run_path(str(root / "credit-entry-client" / "scratch_cred.py"))
assert scratch["load_config"]()["server_url"] == "http://configured-host:8765"
assert Path(scratch["SESSION_STATE_FILE"]).parent == root / "credit-entry-client"
import page_scraper, data_organizer, accurate_time, mrk_sender, retro_scraper
scraper = page_scraper.PageScraper(check_retro=False)
assert Path(scraper.data_path).parent == root / "data/reports"
assert retro_scraper.STATE_FILE == root / "data/retro/state.json"
assert retro_scraper.RETRO_DATA_DIR == root / "data/retro"
# Packaging must write beside report data, even with an unrelated working directory.
report = Path(scraper.data_path) / "sales.csv"
report.write_text("id,amount\\n1,10\\n")
sender = mrk_sender.MRKSender.__new__(mrk_sender.MRKSender)
sender.sales_csv, sender.erca_csv = str(report), None
sender.start_date = sender.end_date = "2026-10-07"
sender.source_ip = "127.0.0.1"
archive = Path(sender.package_wrapper())
assert archive.parent == report.parent
import zipfile
with zipfile.ZipFile(archive) as package:
    assert package.namelist() == ["sales.csv"]
''')

    def test_launcher_check_and_os_override(self):
        self.environment["CRED_V6_SERVER_URL"] = "http://os-override:8765"
        self.run_isolated('''
import os, runpy, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
sys.argv = [str(root / "run_client.py"), "--check"]
runpy.run_path(sys.argv[0], run_name="__main__")
assert os.environ["CRED_V6_SERVER_URL"] == "http://os-override:8765"
assert not (root / "data").exists()
''')


if __name__ == "__main__":
    unittest.main()
