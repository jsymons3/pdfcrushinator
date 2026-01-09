#!/usr/bin/env python3
"""
native_fill.py

Fills a PDF's interactive form fields and produces TWO outputs:
1. An active, editable PDF (Vector text).
2. A "Baked" PDF (Rasterized images) -> 100% visible in Mac Preview.

Fixes:
- "Ghost Text" in Preview (via widget.update for text)
- "Disappearing Radio Buttons" (via SKIPPING widget.update for radios)

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

    # 3. Open PDF for Filling
    doc = fitz.open(args.pdf)
    doc.form_calc = True # Let PyMuPDF calculate auto-sized text

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
                
                # --- CHECKBOX / RADIO BUTTONS ---
                if widget.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
                    # Robust boolean parsing
                    is_checked = str(val).lower() in ["x", "true", "yes", "on", "1", "checked"]
                    
                    # Set the state, but DO NOT call update()
                    # Calling update() destroys the existing circle/check drawing.
                    widget.field_value = is_checked
                    
                    # We rely on the PDF viewer to switch the appearance state (AS)
                    # based on the value change.
                
                # --- TEXT FIELDS ---
                else:
                    # Force Helvetica for max compatibility
                    widget.text_font = "Helv"
                    widget.text_fontsize = 0 # Auto-size
                    widget.field_value = str(val)
                    
                    # MUST call update() here to generate the text appearance
                    # otherwise Mac Preview shows it as invisible.
                    widget.update() 
                
                fields_filled += 1
                break

    # 5. Save Editable Version
    # Force viewers to re-render the form appearances (Crucial for the buttons we didn't update)
    try:
        catalog = doc.pdf_catalog() if hasattr(doc, "pdf_catalog") else doc.catalog
        if catalog:
            acroform = doc.xref_get_key(catalog, "AcroForm")
            if acroform[0] != "null":
                xref = acroform[1]
                if isinstance(xref, int) and xref > 0:
                     doc.xref_set_key(xref, "NeedAppearances", "true")
    except: pass

    doc.save(args.out_active)
    print(f"✓ Saved Editable PDF: {args.out_active}")

    # 6. Create "Baked" (Rasterized) Version
    # Renders the page (with current field states) to an image.
    
    print("   Rasterizing to ensure visibility in Mac Preview...")
    doc_flat = fitz.open()
    
    for page in doc:
        # DPI 200 is a good balance between print quality and file size
        pix = page.get_pixmap(dpi=200, alpha=False)
        
        # Create new blank page matching dimensions
        new_page = doc_flat.new_page(width=page.rect.width, height=page.rect.height)
        
        # Draw the image of the filled form onto the new page
        new_page.insert_image(page.rect, pixmap=pix)

    doc_flat.save(args.out_flat)
    print(f"✓ Saved Flattened (Baked) PDF: {args.out_flat}")

if __name__ == "__main__":
    main()
