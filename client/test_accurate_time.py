"""Timestamp matching checks using temporary reports and XML filenames."""
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent / "mrk-client"))
from accurate_time import AccurateTimeManager


class AccurateTimeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {
            "MRK_REF_NOTE_FORMAT": "PAY-{id}",
            "MRK_XML_FILENAME_PATTERN": "P{ref_note}-*-*.xml",
        })
        environment.start()
        self.addCleanup(environment.stop)

    def file(self, name):
        path = self.root / name
        path.write_text("<xml/>", encoding="utf-8")
        return path

    def manager(self, **kwargs):
        return AccurateTimeManager(None, self.root, **kwargs)

    def test_example_and_reference_boundaries(self):
        path = self.file("PPAY-12345-xxxxxx-xxxx.xml")
        expected = datetime.fromtimestamp(path.stat().st_ctime).strftime("%Y-%m-%d %H:%M:%S")
        manager = self.manager()
        self.assertEqual(manager.time_from_xml("PAY-12345"), expected)
        self.assertIsNone(manager.time_from_xml("PAY-1234"))
        self.assertIsNone(manager.time_from_xml("INV-12345"))

    def test_custom_id_pattern_and_exact_suffix_lengths(self):
        self.file("receipt-ABC.xml")
        self.assertIsNotNone(self.manager(ref_format="INV-{id}", xml_pattern="receipt-{id}.xml").time_from_xml("INV-ABC"))
        self.file("PPAY-1-short-xxxx.xml")
        self.assertIsNone(self.manager(xml_pattern="P{ref_note}-??????-????.xml").time_from_xml("PAY-1"))

    def test_literal_reference_wildcards(self):
        self.file("PPAY-[12]-abcdef-abcd.xml")
        self.file("PPAY-1-abcdef-abcd.xml")
        self.assertIsNotNone(self.manager().time_from_xml("PAY-[12]"))

    def test_ambiguous_and_missing_matches_preserve_html_date(self):
        self.file("PPAY-1-aaaaaa-aaaa.xml")
        self.file("PPAY-1-bbbbbb-bbbb.xml")
        self.file("PPAY-2-cccccc-cccc.xml")
        report = self.root / "sales.html"
        report.write_text("".join(
            f'<tr class="StyleReportDataTr"><td>PAY-{i}</td><td>10</td><td>original</td></tr>'
            for i in (1, 2, 3)), encoding="utf-8")
        AccurateTimeManager(report, self.root).process_sales_file()
        rows = BeautifulSoup(report.read_text(encoding="utf-8"), "html.parser").find_all("tr")
        dates = [row.find_all("td")[2].get_text() for row in rows]
        self.assertEqual(dates[0], "original")
        self.assertNotEqual(dates[1], "original")
        self.assertEqual(dates[2], "original")

    def test_invalid_configuration(self):
        for settings in ({"ref_format": "PAY"}, {"xml_pattern": "../{id}.xml"},
                         {"xml_pattern": "{unknown}.xml"}):
            with self.assertRaises(ValueError):
                self.manager(**settings)


if __name__ == "__main__":
    unittest.main()
