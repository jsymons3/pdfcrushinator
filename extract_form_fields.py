#!/usr/bin/env python
"""
extract_form_fields.py  –  v5  (Sandwich Method + Manual Centering)
Usage:  python extract_form.py path/to/form.pdf
"""

import sys, os, csv, fitz  # PyMuPDF

# ----------------------------------------------------------------------
def get_widget_label(doc, page, w, max_dist=200, vert_pad=2):
    """
    Return the most human‑readable label for `w`.
    """
    # -- 1) Tooltip
    tooltip = ""
    try:
        raw = doc.xref_object(w.xref, compressed=False)
        if b"/TU" in raw:
            start = raw.find(b"/TU") + 3
            start = raw.find(b"(", start) + 1
            end   = raw.find(b")", start)
            tooltip = raw[start:end].decode("utf‑8", errors="ignore").strip()
    except Exception:
        pass
    if tooltip:
        return " ".join(tooltip.split())

    # -- 2) Neighbouring printed text
    words = page.get_text("words")
    left_words = []
    x0_box = w.rect.x0
    y0_box, y1_box = w.rect.y0 - vert_pad, w.rect.y1 + vert_pad

    for (x0, y0, x1, y1, text, *_rest) in words:
        if (x1 < x0_box - 5) and (x1 > x0_box - max_dist):
            if (y1 > y0_box) and (y0 < y1_box):
                left_words.append((x0, text))

    if left_words:
        left_words.sort(key=lambda t: t[0])
        label = " ".join(t[1] for t in left_words).strip(" :")
        if label:
            return " ".join(label.split())

    # -- 3) Fallback
    return w.field_name or ""


# ----------------------------------------------------------------------
def extract_form_fields(pdf_path: str, csv_path: str):
    """Return a list of rows with field geometry & verbose label; also writes CSV."""
    doc = fitz.open(pdf_path)

    rows, row_idx = [], 1
    for page_no in range(len(doc)):
        page = doc[page_no]
        widgets = page.widgets() or []
        for w in widgets:
            if w.rect is None:
                continue

            x1, y1, x2, y2 = w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1
            label = get_widget_label(doc, page, w)

            parts = [p.strip() for p in (w.field_name or "").split(".")]
            heading    = parts[0] if len(parts) > 0 else ""
            subheading = parts[1] if len(parts) > 1 else ""

            rows.append(
                [row_idx, heading, subheading, label, x1, y1, x2, y2, page_no + 1]
            )
            row_idx += 1

    if not rows:
        raise RuntimeError(f"No interactive form fields found in “{os.path.basename(pdf_path)}”.")

    # CSV
    header = ["row", "heading", "subheading", "form_entry_description", "x1", "y1", "x2", "y2", "page"]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([header] + rows)

    return rows


# ----------------------------------------------------------------------
def create_overlay_pdf(original_pdf_path: str, rows, output_pdf_path: str):
    """
    Creates a NEW PDF where each page is an image of the original, 
    with red numbers drawn explicitly ON TOP of that image.
    """
    src_doc = fitz.open(original_pdf_path)
    out_doc = fitz.open()

    # Group rows by page for easier processing
    rows_by_page = {}
    for r in rows:
        p_num = r[8]  # "page" is at index 8
        if p_num not in rows_by_page:
            rows_by_page[p_num] = []
        rows_by_page[p_num].append(r)

    print(f"Processing {len(src_doc)} pages...")

    for i in range(len(src_doc)):
        page_num = i + 1
        src_page = src_doc[i]
        
        # 1. Render the original page (buttons becoming pixels)
        #    annots=True ensures buttons are visible in the image
        pix = src_page.get_pixmap(dpi=150, annots=True)
        
        # 2. Create a fresh page in the new document
        new_page = out_doc.new_page(width=src_page.rect.width, height=src_page.rect.height)
        
        # 3. Paint the image of the original page as the background
        new_page.insert_image(src_page.rect, pixmap=pix)
        
        # 4. Draw Red Numbers ON TOP of the background image
        page_rows = rows_by_page.get(page_num, [])
        for row_data in page_rows:
            # row format: [row_idx, heading, sub, label, x1, y1, x2, y2, page]
            idx = row_data[0]
            x1, y1, x2, y2 = row_data[4], row_data[5], row_data[6], row_data[7]
            
            # Button Center
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            
            # --- Manual Centering (Compatible with all PyMuPDF versions) ---
            text = str(idx)
            font_size = 10
            font_name = "helv"
            
            # Calculate width of the text in points
            text_width = fitz.get_text_length(text, fontname=font_name, fontsize=font_size)
            
            # Shift x back by half width to center horizontally
            # Shift y down slightly to align baseline (approx 30% of font size)
            draw_x = cx - (text_width / 2)
            draw_y = cy + (font_size * 0.3)
            
            new_page.insert_text(
                (draw_x, draw_y),
                text,
                fontname=font_name,
                fontsize=font_size,
                color=(1, 0, 0)
                # Removed 'align' to fix TypeError
            )

    out_doc.save(output_pdf_path)


# ----------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage:  python extract_form.py path/to/form.pdf")

    in_pdf  = sys.argv[1]
    stem, _ = os.path.splitext(in_pdf)
    
    csv_out = f"{stem}_map.csv"
    pdf_out = f"{stem}_final.pdf"

    # 1. Extract Data
    rows = extract_form_fields(in_pdf, csv_out)
    print(f"✓ Extracted coordinates to      → {csv_out}")

    # 2. Create Final "Sandwich" PDF
    create_overlay_pdf(in_pdf, rows, pdf_out)
    print(f"✓ Created guaranteed overlay at → {pdf_out}")
