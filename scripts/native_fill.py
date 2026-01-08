#!/usr/bin/env python3
"""
native_fill.py

1. Loads the Map CSV (for geometry/coordinates).
2. Loads the Fill JSON (for values).
3. JOINS them by 'row' ID.
4. Fills the PDF widgets natively.
"""

import argparse
import csv
import json
from typing import Any, Dict

import fitz


def rects_overlap(r1: fitz.Rect, r2: fitz.Rect) -> bool:
    """Check if widget rect matches CSV rect."""
    intersect = r1 & r2
    if intersect.is_empty:
        return False
    return (intersect.get_area() / r2.get_area()) > 0.8


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, help="Original PDF form")
    parser.add_argument("--csv", required=True, help="Map CSV (for coordinates)")
    parser.add_argument("--plan", required=True, help="Fill Plan JSON (values)")
    parser.add_argument("--out", required=True, help="Output PDF")
    args = parser.parse_args()

    # 1. Load CSV Map (The "Schema")
    # We need this to know where Row 10 is located on the page.
    csv_map: Dict[int, Dict[str, Any]] = {}
    with open(args.csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            # Store rect data keyed by Row ID
            csv_map[int(r["row"])] = {
                "page": int(r["page"]),
                "rect": fitz.Rect(
                    float(r["x1"]),
                    float(r["y1"]),
                    float(r["x2"]),
                    float(r["y2"]),
                ),
            }

    # 2. Load JSON Plan (The "Data")
    with open(args.plan, "r") as f:
        fill_data = json.load(f)

    # 3. Open PDF
    doc = fitz.open(args.pdf)
    count = 0

    # 4. Iterate over filled items
    for item in fill_data:
        row_id = item["row"]
        val = item["value"]

        # Retrieve geometry from CSV map
        geo = csv_map.get(row_id)
        if not geo:
            print(f"Warning: JSON has Row {row_id}, but it's not in the CSV map.")
            continue

        # Go to specific page
        # PDF pages are 0-indexed, our map is 1-indexed
        page = doc[geo["page"] - 1]
        target_rect = geo["rect"]

        # Find the matching widget
        for widget in page.widgets():
            if rects_overlap(widget.rect, target_rect):
                # Checkbox Logic
                if widget.field_type in (
                    fitz.PDF_WIDGET_TYPE_CHECKBOX,
                    fitz.PDF_WIDGET_TYPE_RADIO,
                ):
                    check = str(val).lower() in ["true", "yes", "x", "on", "1"]
                    widget.field_value = check
                else:
                    # Text Logic
                    widget.field_value = str(val)

                widget.update()
                count += 1
                break

    doc.save(args.out)
    print(f"✓ Filled {count} fields. Saved to {args.out}")


if __name__ == "__main__":
    main()
