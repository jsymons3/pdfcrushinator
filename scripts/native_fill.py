#!/usr/bin/env python3
"""
native_fill.py

Fills a PDF's interactive form fields and produces TWO outputs:
1. An active, editable PDF.
2. A flattened, guaranteed-visible PDF (for Preview/Printing).

Usage:
  python native_fill.py \
    --pdf original.pdf \
    --csv map_rich.csv \
    --plan fill_plan.json \
    --out-active filled_editable.pdf \
    --out-flat filled_flattened.pdf
"""

import argparse
import json
import csv
import fitz  # PyMuPDF
from typing import Dict, Any

def rects_overlap(r1: fitz.Rect, r2: fitz.Rect) -> bool:
    intersect = r1 & r2
    if intersect.is_empty: return False
    return (intersect.get_area() / r2.get_area()) > 0.8

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, help="Original interactive PDF")
    parser.add_argument("--csv", required=True, help="Map CSV")
    parser.add_argument("--plan", required=True, help="Fill Plan JSON")
    parser.add_argument("--out-active", required=True, help="Path for Editable PDF")
    parser.add_argument("--out-flat", required=True, help="Path for Flattened PDF")
    args = parser.parse_args()

    # 1. Load Map
    csv_map = {}
    with open(args.csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                csv_map[int(r['row'])] = {
                    "page": int(r['page']),
                    "rect": fitz.Rect(float(r['x1']), float(r['y1']), float(r['x2']), float(r['y2']))
                }
            except: continue

    # 2. Load Data
    with open(args.plan, "r") as f:
        fill_data = json.load(f)

    # 3. Open PDF
    doc = fitz.open(args.pdf)
    
    # Enable form calculation handling
    doc.form_calc = True

    fields_filled = 0

    # 4. Fill Fields
    for item in fill_data:
        row_id = item['row']
        val = item['value']
        
        target = csv_map.get(row_id)
        if not target: continue

        page_idx = target['page'] - 1
        if page_idx >= len(doc): continue
        page = doc[page_idx]
        target_rect = target['rect']

        found = False
        for widget in page.widgets():
            if rects_overlap(widget.rect, target_rect):
                found = True
                
                # Checkbox/Radio
                if widget.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
                    is_checked = str(val).lower() in ["x", "true", "yes", "on", "1", "checked"]
                    widget.field_value = is_checked
                    widget.update() 
                
                # Text
                else:
                    # Force Helvetica to ensure visibility in Mac Preview
                    widget.text_font = "Helv"
                    widget.text_fontsize = 0
                    widget.field_value = str(val)
                    widget.update()
                
                fields_filled += 1
                break

    # 5. Save Active (Editable) Copy
    # Attempt to set NeedAppearances to true to force viewers to render text
    try:
        # Compatible access for doc.pdf_catalog() (newer) vs doc.catalog (older)
        catalog_xref = doc.pdf_catalog() if hasattr(doc, "pdf_catalog") else doc.catalog
        
        if catalog_xref:
            acroform_xref = doc.xref_get_key(catalog_xref, "AcroForm")
            if acroform_xref[0] != "null":
                xref = acroform_xref[1]
                if isinstance(xref, int) and xref > 0:
                     doc.xref_set_key(xref, "NeedAppearances", "true")
    except Exception as e:
        print(f"Note: Could not set NeedAppearances: {e}")

    doc.save(args.out_active)
    print(f"✓ Saved Editable PDF: {args.out_active}")

    # 6. Flatten and Save Static Copy
    # We attempt multiple methods because PyMuPDF API changes frequently.
    
    # Method A: Document-level form flattening (Best for modern versions)
    if hasattr(doc, "flatten_form"):
        try:
            doc.flatten_form()
        except Exception:
            pass

    # Method B: Page-level annotation flattening (Fallback)
    for page in doc:
        # flatten_annots() (plural) is the modern page method
        if hasattr(page, "flatten_annots"):
            page.flatten_annots()
        # flattenPage() is the legacy method
        elif hasattr(page, "flattenPage"):
            page.flattenPage()

    doc.save(args.out_flat)
    print(f"✓ Saved Flattened PDF: {args.out_flat}")

if __name__ == "__main__":
    main()
