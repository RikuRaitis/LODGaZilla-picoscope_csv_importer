"""PicoScope over-range sample regression."""
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from picoscope_csv_importer.importer import Parser as PicoScopeParser


PLUGIN_DIR = Path(__file__).resolve().parents[1]


class PicoScopeInfinityTests(unittest.TestCase):
    def test_over_range_infinity_cells_count_as_missing(self):
        content = (
            "Time;Channel A;Channel B\n(ms);(mV);(V)\n\n"
            "0,0;∞;1,0\n"
            "0,1;-∞;2,0\n"
            "0,2;+∞;3,0\n"
            "0,3;4,0;5,0\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "capture.csv"
            path.write_text(content, encoding="utf-8")
            result = PicoScopeParser(PLUGIN_DIR / "importer.json").parse(path)
        a = result["series"]["PicoScope/Channel A"]
        b = result["series"]["PicoScope/Channel B"]
        self.assertTrue(all(math.isnan(v) for v in a["values"][:3]))
        self.assertEqual(float(a["values"][3]), 4.0)
        np.testing.assert_allclose(b["values"], [1.0, 2.0, 3.0, 5.0])
        self.assertEqual(result["metadata"]["missing_sample_counts"], {"Channel A": 3, "Channel B": 0})
