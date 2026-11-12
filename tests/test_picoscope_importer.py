import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import h5py
import numpy as np

from picoscope_csv_importer.importer import Parser
from src.jobs.import_data import import_file, load_import_configurations, select_importer
from src.plugins.manager import discover_plugins


PLUGIN_DIR = Path(__file__).resolve().parents[1]
PLUGIN_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = PLUGIN_DIR / "importer.json"
SAMPLE = (
    "Time;Channel A;Channel B\n(ms);(mV);(V)\n\n"
    "-6,00043985;-0,11597033;0,00518815\n"
    "-6,00003985;-0,42115542;0,00518815\n"
    "-5,99963985;-0,08850367;0,00396741\n"
)


class PicoScopeImporterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "capture.csv"
        self.parser = Parser(CONFIG_PATH)

    def write(self, content, encoding="utf-8"):
        self.path.write_text(content, encoding=encoding)
        return self.path

    def test_sample_layout_preserves_values_units_and_negative_time(self):
        self.write(SAMPLE)
        self.assertTrue(self.parser.can_parse(self.path))
        progress = []
        result = self.parser.parse(self.path, progress.append)
        self.assertEqual(result["format"], "picoscope.csv")
        a, b = (result["series"][f"PicoScope/Channel {name}"] for name in "AB")
        np.testing.assert_allclose(a["time"], [-6.00043985, -6.00003985, -5.99963985])
        np.testing.assert_allclose(a["values"], [-0.11597033, -0.42115542, -0.08850367])
        np.testing.assert_allclose(b["values"], [0.00518815, 0.00518815, 0.00396741])
        self.assertEqual(a["unit"], "mV")
        self.assertEqual(b["unit"], "V")
        self.assertEqual(a["time_unit"], "ms")
        self.assertEqual(a["time_reference"], "device_relative")
        self.assertEqual(result["metadata"]["sample_count"], 3)
        self.assertEqual(progress[0], 0.0)
        self.assertEqual(progress[-1], 1.0)
        self.assertEqual(progress, sorted(progress))

    def test_delimiters_encodings_and_time_units(self):
        cases = [
            (",", "s", 1000.0, "utf-8-sig"),
            ("\t", "\u00b5s", 0.001, "cp1252"),
            (";", "\u03bcs", 0.001, "utf-8"),
            (",", "us", 0.001, "utf-8"),
            (";", "ns", 0.000001, "utf-8"),
            (",", "min", 60000.0, "utf-8"),
        ]
        for delimiter, unit, scale, encoding in cases:
            with self.subTest(delimiter=delimiter, unit=unit, encoding=encoding):
                self.write(
                    f'"Time"{delimiter}"Channel C"\n({unit}){delimiter}(V)\n'
                    f'-1{delimiter}1.25\n0{delimiter}2.5\n1{delimiter}3.75\n',
                    encoding,
                )
                self.assertTrue(self.parser.can_parse(self.path))
                data = self.parser.parse(self.path)["series"]["PicoScope/Channel C"]
                np.testing.assert_allclose(data["time"], [-scale, 0, scale])
                np.testing.assert_allclose(data["values"], [1.25, 2.5, 3.75])

    def test_missing_channels_keep_sample_alignment_and_are_counted(self):
        self.write("Time,Channel A,Channel B\n(ms),(V),(mV)\n0,,3\n1,NaN,4\n2,inf,5\n3,2,\n")
        result = self.parser.parse(self.path)
        a = result["series"]["PicoScope/Channel A"]
        b = result["series"]["PicoScope/Channel B"]
        np.testing.assert_array_equal(a["time"], b["time"])
        np.testing.assert_allclose(a["values"], [np.nan, np.nan, np.nan, 2], equal_nan=True)
        np.testing.assert_allclose(b["values"], [3, 4, 5, np.nan], equal_nan=True)
        self.assertEqual(result["metadata"]["missing_sample_counts"], {"Channel A": 3, "Channel B": 1})

    def test_rejects_unrelated_or_invalid_headers_without_claiming_file(self):
        for content in [
            "time,voltage\ns,V\n0,1\n",
            "Time,Channel A\n0,1\n",
            "Frequency,Channel A\n(Hz),(V)\n0,1\n",
            "Time,Channel A\n(fortnight),(V)\n0,1\n",
            "Time,Channel A,Channel A\n(ms),(V),(V)\n0,1,2\n",
            "",
        ]:
            with self.subTest(content=content):
                self.write(content)
                self.assertFalse(self.parser.can_parse(self.path))
                with self.assertRaises(ValueError):
                    self.parser.parse(self.path)
        self.assertFalse(self.parser.can_parse(self.path.with_name("missing.csv")))

    def test_invalid_data_fails_with_line_number(self):
        for row, message in [
            ("1,2,3", "wrong number of columns"),
            ("1,broken", "invalid numeric value"),
            ("NaN,2", "non-finite timestamp"),
            ("-1,2", "time runs backwards"),
            ('1,"unclosed', "unexpected end of data"),
        ]:
            with self.subTest(row=row):
                self.write("Time,Channel A\n(ms),(V)\n0,1\n" + row + "\n")
                with self.assertRaisesRegex(ValueError, f"line 4:.*{message}"):
                    self.parser.parse(self.path)

    def test_empty_and_all_missing_captures_fail(self):
        for rows, message in [("\n", "no samples"), ("0,\n1,NaN\n", "no finite channel values")]:
            self.write("Time,Channel A\n(ms),(V)\n" + rows)
            with self.assertRaisesRegex(ValueError, message):
                self.parser.parse(self.path)

    def test_presentation_overrides_and_units_do_not_leak_between_files(self):
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        config["sections"][0]["series"][0].update(group="Scope/Probe", visibility="hidden")
        parser = Parser(CONFIG_PATH, config)
        for unit in ["mV", "V"]:
            self.write(f"Time,Channel A\n(ms),({unit})\n0,1\n")
            series = parser.parse(self.path)["series"]["Scope/Probe/Channel A"]
            self.assertEqual(series["unit"], unit)
            self.assertFalse(series["visible"])
            self.assertTrue(series["navigator_metadata"])

    def test_manifest_discovery_enable_disable_and_content_detection(self):
        descriptors = discover_plugins(PLUGIN_DIR.parent)
        descriptor = next(item for item in descriptors if item.plugin_id == "picoscope_csv_importer")
        self.assertIsNone(descriptor.plugin_class)
        self.write(SAMPLE)
        renamed = self.path.with_suffix(".log")
        self.path.rename(renamed)
        for enabled in [False, True]:
            with mock.patch("src.jobs.import_data.load_settings_section", return_value={
                "enabled": {"picoscope_csv_importer": enabled},
            }):
                configurations = load_import_configurations(PLUGIN_DIR.parent)
            self.assertEqual(len(configurations), int(enabled))
            if enabled:
                selected, parser = select_importer(renamed, configurations)
                self.assertEqual(selected.name, "PicoScope CSV")
                self.assertTrue(parser.can_parse(renamed))

    def test_host_import_writes_relative_time_units_metadata_and_lod(self):
        self.write("Time,Channel A\n(us),(mV)\n" + "".join(
            f"{i - 4500},{100 if i == 1234 else -1}\n" for i in range(9000)
        ))
        with mock.patch("src.jobs.import_data.load_settings_section", return_value={
            "enabled": {"picoscope_csv_importer": True},
        }):
            output = import_file(self.path, config_dir=PLUGIN_DIR.parent)
        with h5py.File(output, "r") as cache:
            self.assertEqual(cache.attrs["importer_format"], "picoscope.csv")
            series = cache["series/PicoScope/Channel_A"]
            self.assertEqual(series.attrs["sample_count"], 9000)
            self.assertEqual(series.attrs["time_reference"], "device_relative")
            self.assertEqual(series.attrs["unit"], "mV")
            self.assertAlmostEqual(series.attrs["sample_rate_hz"], 1000000.0)
            self.assertAlmostEqual(series["time_ms"][0], -4.5)
            self.assertAlmostEqual(series["time_ms"][-1], 4.499)
            self.assertEqual(series["value"][1234], 100)
            self.assertTrue(len(series["lod"]))
            for level in series["lod"].values():
                self.assertEqual(np.max(level["value_max"][:]), 100)


if __name__ == "__main__":
    unittest.main()
