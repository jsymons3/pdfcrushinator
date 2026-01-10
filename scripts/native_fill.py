#!/usr/bin/env python3
"""
native_fill.py (XREF Edition)

Fills PDF forms using XREF matching (100% precision) and robust boolean logic.
"""

import argparse
import json
import csv
import fitz  # PyMuPDF

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--out-active", required=True)
    parser.add_argument("--out-flat", required=True)
    args = parser.parse_args()

    # 1. Load Map (Now includes xref)
    csv_map = {}
    with open(args.csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                row_id = int(r['row'])
                csv_map[row_id] = {
                    "page": int(r['page']),
                    "xref": int(r['xref']), # THE GOLDEN KEY
                }
            except: continue

    # 2. Load Plan
    with open(args.plan, "r") as f:
        fill_data = json.load(f)

    # 3. Open PDF
    doc = fitz.open(args.pdf)
    doc.form_calc = True

    # 4. Fill Fields
    for item in fill_data:
        row_id = item['row']
        val = item['value']
        
        target = csv_map.get(row_id)
        if not target: continue

        # Load page
        page_idx = target['page'] - 1
        if page_idx >= len(doc): continue
        page = doc[page_idx]
        
        target_xref = target['xref']

        found = False
        
        # Optimize: If PyMuPDF allows, we could grab by XREF, 
        # but iterating page widgets matches our map logic safely.
        for widget in page.widgets():
            
            # --- THE FIX: Match by XREF (Unique ID) ---
            if widget.xref == target_xref:
                found = True
                
                # --- LOGIC: CHECKBOX / RADIO ---
                if widget.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
                    
                    # 1. Determine if AI wants it checked
                    should_check = str(val).strip().lower() in ["x", "true", "yes", "on", "1", "checked"]
                    
                    # 2. Get the specific string that turns this button ON
                    on_state = "Yes" # default
                    try:
                        os = widget.on_state()
                        if os and os is not True: on_state = str(os)
                    except: pass
                    
                    # 3. Apply Logic
                    if widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                        # Checkboxes can be toggled Off
                        widget.field_value = on_state if should_check else "Off"
                        widget.update()
                        
                    elif widget.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
                        # Radios should ONLY be touched if we are selecting THIS option.
                        # We never explicitly turn a radio "Off" (that happens automatically 
                        # when another sibling is turned On).
                        if should_check:
                            widget.field_value = on_state
                            widget.update()
                
                # --- LOGIC: TEXT ---
                else:
                    widget.text_font = "Helv"
                    widget.text_fontsize = 0
                    widget.field_value = str(val)
                    widget.update()
                    
                break
        
        if not found:
            print(f"Warning: Widget XREF {target_xref} (Row {row_id}) not found on page {target['page']}")

    # 5. Fix NeedAppearances (Correctly this time)
    try:
        # Proper way to find AcroForm dict in PyMuPDF
        catalog = doc.pdf_catalog()
        if catalog > 0:
            acroform_key = doc.xref_get_key(catalog, "AcroForm")
            if acroform_key[0] == "xref":
                acro_xref = int(acroform_key[1].split()[0]) # Extract '123' from '123 0 R'
                doc.xref_set_key(acro_xref, "NeedAppearances", "true")
    except Exception as e:
        print(f"Note: NeedAppearances set failed (minor): {e}")

    doc.save(args.out_active)
    print(f"✓ Saved Editable PDF: {args.out_active}")

    # 6. Rasterize
    print("   Rasterizing...")
    doc_flat = fitz.open()
    for page in doc:
        pix = page.get_pixmap(dpi=200, alpha=False)
        new_page = doc_flat.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(page.rect, pixmap=pix)

    doc_flat.save(args.out_flat)
    print(f"✓ Saved Flattened PDF: {args.out_flat}")

if __name__ == "__main__":
    main()
