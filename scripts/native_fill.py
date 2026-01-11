#!/usr/bin/env python3
"""
native_fill.py (v19 - Preserve Structure)

Changes from v18:
- Save with garbage=0 to preserve all objects
- Use incremental save when possible
- Don't modify NeedAppearances (let viewer handle it)
"""

import argparse
import json
import csv
import fitz

def get_parent_field_type(doc, widget):
    try:
        parent_info = doc.xref_get_key(widget.xref, "Parent")
        if parent_info[0] == "xref":
            parent_xref = int(parent_info[1].split()[0])
            ft_info = doc.xref_get_key(parent_xref, "FT")
            if ft_info[0] != "null":
                return ft_info[1]
    except:
        pass
    return None

def get_on_state_from_ap(doc, widget):
    try:
        ap_info = doc.xref_get_key(widget.xref, "AP")
        if ap_info[0] == "null":
            return None
        
        ap_xref = int(ap_info[1].split()[0])
        n_info = doc.xref_get_key(ap_xref, "N")
        if n_info[0] == "null":
            return None
        
        n_xref = int(n_info[1].split()[0])
        n_obj = doc.xref_object(n_xref)
        
        for line in n_obj.split('\n'):
            line = line.strip()
            if line.startswith('/') and not line.startswith('/Off'):
                key = line.split()[0][1:]
                return key
    except:
        pass
    return None

def is_button_field(doc, widget):
    if widget.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
        return True
    parent_ft = get_parent_field_type(doc, widget)
    return parent_ft == "/Btn"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--out-active", required=True)
    parser.add_argument("--out-flat", required=True)
    args = parser.parse_args()

    # Load Map
    csv_map = {}
    with open(args.csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                row_id = int(r['row'])
                csv_map[row_id] = {
                    "page": int(r['page']),
                    "xref": int(r['xref']),
                    "rect": fitz.Rect(float(r['x1']), float(r['y1']), float(r['x2']), float(r['y2']))
                }
            except: continue

    # Load Plan
    with open(args.plan, "r") as f:
        fill_data = json.load(f)

    # ---------------------------------------------------------
    # PART A: Generate Editable PDF
    # ---------------------------------------------------------
    doc = fitz.open(args.pdf)

    for item in fill_data:
        row_id = item['row']
        val = item['value']
        target = csv_map.get(row_id)
        if not target: continue

        page = doc[target['page'] - 1]
        target_xref = target['xref']

        for widget in page.widgets():
            if widget.xref == target_xref:
                
                if is_button_field(doc, widget):
                    should_check = str(val).lower() in ["x", "true", "yes", "on", "1", "checked"]
                    
                    if should_check:
                        on_state = get_on_state_from_ap(doc, widget)
                        if not on_state:
                            try:
                                os = widget.on_state()
                                if os and os is not True:
                                    on_state = str(os)
                            except:
                                pass
                        if not on_state:
                            on_state = "Yes"
                        
                        print(f"  Radio: xref {widget.xref} -> /{on_state}")
                        
                        # ONLY set AS - minimal change
                        doc.xref_set_key(widget.xref, "AS", f"/{on_state}")
                        
                        # Set parent V for radio groups
                        try:
                            parent_info = doc.xref_get_key(widget.xref, "Parent")
                            if parent_info[0] == "xref":
                                parent_xref = int(parent_info[1].split()[0])
                                doc.xref_set_key(parent_xref, "V", f"/{on_state}")
                        except:
                            pass
                    else:
                        doc.xref_set_key(widget.xref, "AS", "/Off")

                else:
                    widget.text_font = "Helv"
                    widget.text_fontsize = 0
                    widget.text_color = [0, 0, 0]
                    widget.field_value = str(val)
                    widget.update()
                break

    # Save with minimal changes - garbage=0 preserves all objects
    doc.save(args.out_active, garbage=0, deflate=True)
    print(f"✓ Saved Editable PDF: {args.out_active}")
    doc.close()

    # ---------------------------------------------------------
    # PART B: Generate Flattened PDF
    # ---------------------------------------------------------
    doc_visual = fitz.open(args.out_active)
    print("   Applying Visual Overrides...")
    blue_color = (0.2, 0.2, 0.4) 

    for item in fill_data:
        row_id = item['row']
        val = item['value']
        target = csv_map.get(row_id)
        if not target: continue

        page = doc_visual[target['page'] - 1]
        is_check = str(val).lower() in ["x", "true", "yes", "on", "1", "checked"]
        
        if is_check:
            target_widget = None
            for w in page.widgets():
                if w.xref == target['xref']:
                    target_widget = w
                    break
            
            if target_widget and is_button_field(doc_visual, target_widget):
                rect = target_widget.rect
                is_radio_style = target_widget.field_name and "Group" in target_widget.field_name
                
                page.delete_widget(target_widget)
                
                if is_radio_style:
                    center = fitz.Point((rect.x0 + rect.x1)/2, (rect.y0 + rect.y1)/2)
                    radius = min(rect.width, rect.height) / 4 
                    page.draw_circle(center, radius, color=blue_color, fill=blue_color)
                else:
                    p = 2
                    page.draw_line(fitz.Point(rect.x0+p, rect.y0+p), fitz.Point(rect.x1-p, rect.y1-p), color=blue_color, width=1.5)
                    page.draw_line(fitz.Point(rect.x0+p, rect.y1-p), fitz.Point(rect.x1-p, rect.y0+p), color=blue_color, width=1.5)

    print("   Rasterizing...")
    doc_flat = fitz.open()
    for page in doc_visual:
        pix = page.get_pixmap(dpi=200, alpha=False)
        new_page = doc_flat.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(page.rect, pixmap=pix)

    doc_flat.save(args.out_flat)
    print(f"✓ Saved Flattened PDF: {args.out_flat}")

if __name__ == "__main__":
    main()
