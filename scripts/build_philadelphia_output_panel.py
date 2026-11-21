"""Extract paired quarterly levels from the archived Philadelphia Fed ROUTPUT workbook."""

import argparse
import csv
import hashlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from finreplay.engines.release_timing import AGES, quarter_index

WORKBOOK_SHA256 = "6a7971b31b7336551705465de8a0855826f3652abe112406f52b2c2ed0994c0c"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def extract(workbook: Path, output: Path) -> None:
    if hashlib.sha256(workbook.read_bytes()).hexdigest() != WORKBOOK_SHA256:
        raise ValueError("Workbook differs from the dated input recorded in the source receipt")
    with zipfile.ZipFile(workbook) as archive:
        strings = [
            s.findtext(f"{NS}t", "") for s in ET.fromstring(archive.read("xl/sharedStrings.xml"))
        ]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    rows = []
    for sheet_row in sheet.iter(f"{NS}row"):
        cells = {}
        for cell in sheet_row.findall(f"{NS}c"):
            value = cell.findtext(f"{NS}v")
            if value is not None:
                cells[cell.attrib["r"].rstrip("0123456789")] = (
                    strings[int(value)] if cell.get("t") == "s" else value
                )
        if cells:
            rows.append(cells)
    header, *observations = rows
    columns = {}
    for col, title in header.items():
        if col == "A":
            continue
        code = title.removeprefix("ROUTPUT")
        year = int(code[:2])
        year += 1900 if year >= 65 else 2000
        columns[quarter_index(f"{year}{code[2:]}")] = col
    levels = {quarter_index(row["A"].replace(":", "")): row for row in observations}
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["period", *(f"age{a}_{s}" for a in AGES for s in ("previous", "current"))])
        for q, row in sorted(levels.items()):
            if q + 1 not in columns:
                continue
            values = []
            for age in AGES:
                column = columns.get(q + age, "")
                for level_row in (levels.get(q - 1, {}), row):
                    value = level_row.get(column, "")
                    values.append("" if value == "#N/A" else value)
            writer.writerow([row["A"].replace(":", ""), *values])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    extract(args.workbook, args.output)
