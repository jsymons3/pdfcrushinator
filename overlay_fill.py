"""Overlay mapping values onto a PDF using PyMuPDF.

This module consumes the JSON mapping emitted by the LLM prompt and draws the
values onto the target PDF. The coordinates in the JSON are expected to come
from the form CSV (x1, y1, x2, y2, page) and match the pixel space used by
PyMuPDF.
"""

import argparse
import json
from typing import Any, Dict, Iterable, List

import fitz  # PyMuPDF


def draw_text(page: fitz.Page, x: float, y: float, text: Any, fontsize: int = 9) -> None:
    """Draw text at the provided coordinates.

    The y coordinate is intended to be the vertical center of the target box, so
    we place the text directly at (x, y) and let insert_text handle baseline
    placement.
    """

    page.insert_text((x, y), str(text), fontsize=fontsize)


def overlay_pdf(pdf_in: str, mapping: Iterable[Dict[str, Any]], pdf_out: str) -> None:
    """Apply the overlay defined by ``mapping`` to ``pdf_in`` and save ``pdf_out``."""

    doc = fitz.open(pdf_in)

    for entry in mapping:
        page_idx = int(entry["page"]) - 1  # CSV is 1-based, PyMuPDF is 0-based
        if page_idx < 0 or page_idx >= len(doc):
            continue

        page = doc[page_idx]
        # Position text near the center of the target box
        x1 = float(entry["x1"]) + 10.0
        y_center = (float(entry["y1"]) + float(entry["y2"])) / 2.0
        value = entry.get("value")
        if value is None or value == "":
            continue
        draw_text(page, x1, y_center, value, fontsize=9)

    doc.save(pdf_out)


def load_mapping(json_map_path: str) -> List[Dict[str, Any]]:
    with open(json_map_path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="Overlay automapped values onto a PDF")
    parser.add_argument("--pdf-in", required=True, help="Path to the original, unfilled PDF")
    parser.add_argument("--json-map", required=True, help="Path to JSON mapping (row, page, x1,y1, value)")
    parser.add_argument("--pdf-out", required=True, help="Path to write the overlay PDF")
    args = parser.parse_args()

    mapping = load_mapping(args.json_map)
    overlay_pdf(args.pdf_in, mapping, args.pdf_out)
    print(f"Saved overlay to: {args.pdf_out}")


if __name__ == "__main__":
    main()
