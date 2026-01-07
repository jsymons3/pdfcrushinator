#!/usr/bin/env python
"""
extract_form.py  –  v2  (verbose labels)
Usage:  python extract_form.py path/to/form.pdf
"""

import sys, os, csv, fitz  # PyMuPDF ≥ 1.23.21
from collections import defaultdict

# ----------------------------------------------------------------------
def get_widget_label(doc, page, w, max_dist=200, vert_pad=2):
    """
    Return the most human-readable label for `w`:
      1) /TU (tooltip / alternate text) if present
      2) Static text immediately left of the widget
      3) widget.field_name  (/T) as a last resort
    """
    # -- 1) Tooltip ------------------------------------------------------
    tooltip = ""
    try:
        raw = doc.xref_object(w.xref, compressed=False)  # bytes
        # rudimentary parse; TU string is delimited by (...)
        if b"/TU" in raw:
            start = raw.find(b"/TU") + 3
            # find first '(' after /TU
            start = raw.find(b"(", start) + 1
            end   = raw.find(b")", start)
            tooltip = raw[start:end].decode("utf-8", errors="ignore").strip()
    except Exception:
        pass
    if tooltip:
        return " ".join(tooltip.split())  # normalise whitespace

    # -- 2) Nearby static text ------------------------------------------
    try:
        r = fitz.Rect(w.rect)  # widget rect
        # search a rectangle left of widget (max_dist points), same vertical span
        search = fitz.Rect(r.x0 - max_dist, r.y0 - vert_pad, r.x0, r.y1 + vert_pad)
        words = page.get_text("words")  # [x0,y0,x1,y1,"word",block,line,word]
        candidates = []
        for x0, y0, x1, y1, txt, *_ in words:
            wx = (x0 + x1) / 2
            wy = (y0 + y1) / 2
            if search.contains(fitz.Point(wx, wy)):
                candidates.append((x1, y0, txt))
        if candidates:
            # closest to widget (highest x1, i.e. nearest on the left)
            candidates.sort(key=lambda t: t[0], reverse=True)
            return candidates[0][2].strip()
    except Exception:
        pass

    # -- 3) Fallback to field name --------------------------------------
    try:
        if getattr(w, "field_name", None):
            return str(w.field_name).strip()
    except Exception:
        pass

    return ""


# ----------------------------------------------------------------------
def extract_form_fields(pdf_path: str, csv_out: str):
    doc = fitz.open(pdf_path)
    rows = []
    row_id = 0

    for pno in range(doc.page_count):
        page = doc[pno]
        widgets = page.widgets()
        if not widgets:
            continue

        for w in widgets:
            row_id += 1
            label = get_widget_label(doc, page, w) or "UNLABELED"
            field_name = getattr(w, "field_name", "") or ""
            field_type = getattr(w, "field_type", "") or ""
            rect = fitz.Rect(w.rect)

            # Basic columns: row, heading, subheading, form_entry_description, x1,y1,x2,y2,page
            # You can adapt "heading/subheading/description" to your needs.
            heading = label
            subheading = ""
            form_entry_description = f"{field_type} {field_name}".strip()

            rows.append([
                row_id,
                heading,
                subheading,
                form_entry_description,
                rect.x0, rect.y0, rect.x1, rect.y1,
                pno + 1
            ])

    # Write CSV
    with open(csv_out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["row", "heading", "subheading", "form_entry_description", "x1", "y1", "x2", "y2", "page"])
        w.writerows(rows)

    doc.close()
    return rows


# ----------------------------------------------------------------------
def annotate_pdf(pdf_path: str, rows, out_path: str, render_scale: float = 2.0):
    """Create an *annotated helper PDF* that cannot be obscured by widgets.

    Many PDFs (checkbox/radio widgets) are drawn as annotations that can appear
    above regular page content, which can hide your red row numbers.
    To make landmarks reliably visible, we:
      1) rasterize (flatten) each page to an image
      2) create a new PDF page with that image as the background
      3) draw the row-number landmarks on top

    This output is intended for mapping/visualization only; keep the original PDF
    for actual form-filling.
    """
    src = fitz.open(pdf_path)
    out = fitz.open()

    # Group landmarks per page (0-indexed)
    by_page = defaultdict(list)
    for row, *_rest, x1, y1, x2, y2, page_no in rows:
        by_page[int(page_no) - 1].append((int(row), float(x1), float(y1), float(x2), float(y2)))

    for i in range(src.page_count):
        sp = src[i]
        rect = sp.rect

        # 1) Flatten page by rendering it
        pix = sp.get_pixmap(matrix=fitz.Matrix(render_scale, render_scale), alpha=False)

        # 2) New page with identical dimensions; paint the rendered image onto it
        np = out.new_page(width=rect.width, height=rect.height)
        np.insert_image(rect, stream=pix.tobytes("png"))

        # 3) Draw landmarks (guaranteed on top now)
        for (row_id, x1, y1, x2, y2) in by_page.get(i, []):
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            label = str(row_id)

            # Small badge centered exactly at the widget center (no re-positioning)
            w = 8 + 6 * len(label)   # width scales with digits
            h = 12
            badge = fitz.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

            np.draw_rect(badge, color=(1, 0, 0), fill=(1, 1, 1), width=0.8, overlay=True)
            np.insert_textbox(
                badge,
                label,
                fontname="helv",
                fontsize=9,
                color=(1, 0, 0),
                align=1,   # centered
                overlay=True,
            )

    out.save(out_path)
    src.close()
    out.close()


# ----------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage:  python extract_form.py path/to/form.pdf")

    in_pdf  = sys.argv[1]
    stem, _ = os.path.splitext(in_pdf)
    csv_out = f"{stem}_map.csv"
    pdf_out = f"{stem}_annotated.pdf"

    rows = extract_form_fields(in_pdf, csv_out)
    annotate_pdf(in_pdf, rows, pdf_out)

    print(f"✓ Wrote CSV with {len(rows)} rows → {csv_out}")
    print(f"✓ Annotated PDF saved as          → {pdf_out}")
