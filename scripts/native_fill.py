#!/usr/bin/env python3
"""
native_fill.py

Fills a PDF's interactive form fields (AcroForms) by joining a minimal JSON plan
with a CSV coordinate map.

Includes fixes for "Invisible Text" bugs in macOS Preview.

Usage:
  python native_fill.py \
    --pdf original_form.pdf \
    --csv map_rich.csv \
    --plan fill_plan.json \
    --out filled_signed.pdf
"""

import argparse
import json
import csv
import fitz  # PyMuPDF
from typing import Dict, Any

def rects_overlap(r1: fitz.Rect, r2: fitz.Rect) -> bool:
    """Check if widget rect matches CSV rect with >80% overlap."""
    intersect = r1 & r2
    if intersect.is_empty:
        return False
    return (intersect.get_area() / r2.get_area()) > 0.8

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, help="Path to ORIGINAL interactive PDF")
    parser.add_argument("--csv", required=True, help="Path to Map CSV (for coordinates)")
    parser.add_argument("--plan", required=True, help="Path to Fill Plan JSON (values)")
    parser.add_argument("--out", required=True, help="Path to save filled PDF")
    args = parser.parse_args()

    # 1. Load CSV Map
    csv_map = {}
    with open(args.csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                csv_map[int(r['row'])] = {
                    "page": int(r['page']),
                    "rect": fitz.Rect(float(r['x1']), float(r['y1']), float(r['x2']), float(r['y2']))
                }
            except (ValueError, KeyError):
                continue

    # 2. Load JSON Plan
    with open(args.plan, "r") as f:
        fill_data = json.load(f)

    # 3. Open PDF
    doc = fitz.open(args.pdf)

    # --- FIX 1: FORCE RE-CALCULATION FLAG ---
    # This tells viewers "Please re-calculate scripts and appearances"
    doc.form_calc = True

    fields_filled = 0

    # 4. Execute Fill
    for item in fill_data:
        row_id = item['row']
        val = item['value']

        target = csv_map.get(row_id)
        if not target:
            continue

        page_idx = target['page'] - 1
        if page_idx >= len(doc):
            continue

        page = doc[page_idx]
        target_rect = target['rect']

        found = False
        for widget in page.widgets():
            if rects_overlap(widget.rect, target_rect):
                found = True

                # Checkbox / Radio
                if widget.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
                    is_checked = str(val).lower() in ["x", "true", "yes", "on", "1", "checked"]
                    widget.field_value = is_checked
                    widget.update()

                # Text Fields
                else:
                    # --- FIX 2: FORCE FONT & SIZE ---
                    # Setting the font to Helvetica ensures macOS Preview can render it.
                    # Setting fontsize to 0 means "Auto-size" to fit the box.
                    widget.text_font = "Helv"
                    widget.text_fontsize = 0

                    # Set the value
                    widget.field_value = str(val)

                    # Update (Bake appearance)
                    widget.update()

                fields_filled += 1
                break

        if not found:
            print(f"Warning: Could not find widget for Row {row_id}")

    # --- FIX 3: SET GLOBAL NEED_APPEARANCES FLAG ---
    # This edits the PDF Catalog to force the viewer to generate appearances
    # if they are missing or corrupt.
    try:
        if doc.catalog:
            # Get the AcroForm dictionary
            acroform_xref = doc.xref_get_key(doc.catalog, "AcroForm")
            if acroform_xref[0] != "null":
                # Set NeedAppearances = true
                xref = acroform_xref[1]
                # If AcroForm is an indirect object (xref > 0)
                if isinstance(xref, int) and xref > 0:
                    doc.xref_set_key(xref, "NeedAppearances", "true")
    except Exception as e:
        print(f"Note: Could not set NeedAppearances flag: {e}")

    # Save
    doc.save(args.out)
    print(f"✓ Native fill complete. {fields_filled} fields updated.")
    print(f"✓ Saved to: {args.out}")

if __name__ == "__main__":
    main()
