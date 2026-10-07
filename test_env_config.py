import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from env_config import get_path, load_env_file


class EnvConfigTest(unittest.TestCase):
    def test_load_env_file_preserves_os_overrides_and_parses_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            env_path = Path(temporary) / ".env"
            env_path.write_text(
                '# comment\nFROM_FILE="hello world"\nFROM_OS=from-file\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"FROM_OS": "operating-system"}, clear=True):
                load_env_file(env_path)
                self.assertEqual(os.environ["FROM_FILE"], "hello world")
                self.assertEqual(os.environ["FROM_OS"], "operating-system")

    def test_get_path_resolves_relative_values_from_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"TEST_CONFIG_PATH": "data/store.sqlite"}, clear=True):
                path = get_path("TEST_CONFIG_PATH", "unused", temporary)
                self.assertEqual(path, Path(temporary).resolve() / "data" / "store.sqlite")


if __name__ == "__main__":
    unittest.main()
