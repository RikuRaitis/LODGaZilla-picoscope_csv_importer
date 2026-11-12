"""Import PicoScope time-domain CSV exports through the host parser contract."""

from __future__ import annotations

import csv
import io
import math
import re
from array import array
from dataclasses import replace
from pathlib import Path
from typing import Any, BinaryIO

from src.contracts.importer import DataParser, ProgressCallback, ProgressReporter, SeriesDefinition


TIME_TO_MS = {"s": 1000.0, "ms": 1.0, "us": 0.001, "ns": 0.000001, "min": 60000.0}
HEADER_LINE_LIMIT = 65536
# PicoScope writes over-range samples as an infinity sign.
INFINITY_CELLS = {"\u221e": "inf", "+\u221e": "inf", "-\u221e": "-inf"}


def _read_header(file: BinaryIO) -> tuple[str, str, list[str], list[str], float]:
    lines = [file.readline(HEADER_LINE_LIMIT + 1) for _ in range(2)]
    if any(not line or len(line) > HEADER_LINE_LIMIT for line in lines):
        raise ValueError("PicoScope CSV requires a channel header and a units row.")
    raw = b"".join(lines)
    try:
        header = raw.decode("utf-8-sig")
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        # Windows exports can encode the micro sign as a single byte.
        header = raw.decode("cp1252")
        encoding = "cp1252"
    for delimiter in (";", ",", "\t"):
        try:
            rows = list(csv.reader(io.StringIO(header), delimiter=delimiter, strict=True))
        except csv.Error:
            continue
        if len(rows) != 2:
            continue
        names, units = ([cell.strip() for cell in row] for row in rows)
        if len(names) < 2 or names[0].casefold() != "time" or len(units) != len(names):
            continue
        if not all(re.fullmatch(r"Channel [A-Z]", name, re.IGNORECASE) for name in names[1:]):
            continue
        channels = [f"Channel {name[-1].upper()}" for name in names[1:]]
        if len(set(channels)) != len(channels):
            raise ValueError("PicoScope CSV contains duplicate channel names.")
        if not all(unit.startswith("(") and unit.endswith(")") for unit in units):
            continue
        units = [unit[1:-1].strip() for unit in units]
        time_unit = units[0].replace("\u00b5", "u").replace("\u03bc", "u").lower()
        if time_unit not in TIME_TO_MS:
            raise ValueError(f"Unsupported PicoScope time unit: {units[0]!r}")
        return encoding, delimiter, channels, units, TIME_TO_MS[time_unit]
    raise ValueError("Not a PicoScope time-domain CSV (expected Time, Channel A, ... and units).")


class Parser(DataParser):
    name = "PicoScope CSV"
    priority = 150
    extensions = frozenset({".csv", ".txt"})
    format_name = "picoscope.csv"

    def can_parse(self, input_path: str | Path) -> bool:
        try:
            with Path(input_path).open("rb") as file:
                _read_header(file)
        except (OSError, ValueError, csv.Error):
            return False
        return True

    def parse(
        self,
        input_path: str | Path,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        path = Path(input_path)
        file_size = max(1, path.stat().st_size)
        progress = ProgressReporter(progress_callback)
        times = array("d")
        with path.open("rb") as file:
            encoding, delimiter, channels, units, time_scale = _read_header(file)
            values = [array("d") for _ in channels]
            missing_counts = dict.fromkeys(channels, 0)
            # Stream rows into compact numeric buffers, without retaining CSV text.
            with io.TextIOWrapper(file, encoding=encoding, newline="") as text:
                reader = csv.reader(text, delimiter=delimiter, strict=True)
                progress.report(0.0)
                try:
                    for row in reader:
                        line_number = reader.line_num + 2
                        if not row or all(not cell.strip() for cell in row):
                            continue
                        if len(row) != len(channels) + 1:
                            raise ValueError(f"PicoScope CSV line {line_number}: wrong number of columns.")
                        cells = [cell.strip().replace(",", ".") if delimiter != "," else cell.strip() for cell in row]
                        try:
                            timestamp = float(cells[0]) * time_scale
                            samples = [float(INFINITY_CELLS.get(cell, cell)) if cell else math.nan for cell in cells[1:]]
                        except ValueError as error:
                            raise ValueError(f"PicoScope CSV line {line_number}: invalid numeric value.") from error
                        if not math.isfinite(timestamp):
                            raise ValueError(f"PicoScope CSV line {line_number}: non-finite timestamp.")
                        if times and timestamp < times[-1]:
                            raise ValueError(
                                f"PicoScope CSV line {line_number}: time runs backwards; "
                                "export each capture separately."
                            )
                        times.append(timestamp)
                        for channel, buffer, value in zip(channels, values, samples):
                            if not math.isfinite(value):
                                missing_counts[channel] += 1
                                value = math.nan
                            buffer.append(value)
                        if len(times) % 8192 == 0:
                            progress.report(min(0.99, file.tell() / file_size))
                except csv.Error as error:
                    raise ValueError(f"PicoScope CSV line {reader.line_num + 2}: {error}") from error

        if not times:
            raise ValueError(f"PicoScope CSV contains no samples: {path.name}")
        if all(missing_counts[channel] == len(times) for channel in channels):
            raise ValueError(f"PicoScope CSV contains no finite channel values: {path.name}")
        output: dict[str, dict[str, Any]] = {}
        for channel, unit, samples in zip(channels, units[1:], values):
            definition = self.find_series_definition(channel, "PicoScope")
            if definition is None:
                definition = SeriesDefinition(channel, "PicoScope", unit)
            # A blank presentation unit means use the unit recorded in this CSV.
            definition = replace(definition, unit=definition.unit or unit, original_unit=unit)
            key, payload = self.make_series_from_definition(
                definition, times, samples,
                time_unit="ms", time_reference="device_relative", source=f"PicoScope {channel}",
            )
            output[key] = payload
        progress.finish()
        return {
            "format": self.format_name,
            "series": output,
            "metadata": {
                "sample_count": len(times),
                "source_time_unit": units[0],
                "channel_units": dict(zip(channels, units[1:])),
                "missing_sample_counts": missing_counts,
                "delimiter": delimiter,
            },
        }
