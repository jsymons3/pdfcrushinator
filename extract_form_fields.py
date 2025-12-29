#!/usr/bin/env python
"""
extract_form.py  –  v2  (verbose labels)
Usage:  python extract_form.py path/to/form.pdf
"""

import sys, os, csv, fitz  # PyMuPDF ≥ 1.23.21

# ----------------------------------------------------------------------
def get_widget_label(doc, page, w, max_dist=200, vert_pad=2):
    """
    Return the most human‑readable label for `w`:
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
            tooltip = raw[start:end].decode("utf‑8", errors="ignore").strip()
    except Exception:
        pass
    if tooltip:
        return " ".join(tooltip.split())  # normalise whitespace

    # -- 2) Neighbouring printed text -----------------------------------
    words = page.get_text("words")  # (x0, y0, x1, y1, "text", block, line, word)
    left_words = []
    x0_box = w.rect.x0
    y0_box, y1_box = w.rect.y0 - vert_pad, w.rect.y1 + vert_pad

    for (x0, y0, x1, y1, text, *_rest) in words:
        if (x1 < x0_box - 5) and (x1 > x0_box - max_dist):          # left side
            if (y1 > y0_box) and (y0 < y1_box):                      # vertical overlap
                left_words.append((x0, text))

    if left_words:
        # sort by x0 so sentence order is preserved
        left_words.sort(key=lambda t: t[0])
        label = " ".join(t[1] for t in left_words).strip(" :")
        if label:
            return " ".join(label.split())

    # -- 3) Fallback -----------------------------------------------------
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

            # crude hierarchy split as before
            parts = [p.strip() for p in (w.field_name or "").split(".")]
            heading    = parts[0] if len(parts) > 0 else ""
            subheading = parts[1] if len(parts) > 1 else ""

            rows.append(
                [row_idx, heading, subheading, label, x1, y1, x2, y2, page_no + 1]
            )
            row_idx += 1

    if not rows:
        raise RuntimeError(f"No interactive form fields found in “{os.path.basename(pdf_path)}”.")

    # CSV ----------------------------------------------------------------
    header = [
        "row",
        "heading",
        "subheading",
        "form_entry_description",
        "x1",
        "y1",
        "x2",
        "y2",
        "page",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([header] + rows)

    return rows


# ----------------------------------------------------------------------
def annotate_pdf(pdf_path: str, rows, out_path: str):
    doc = fitz.open(pdf_path)
    for row, *_rest, x1, y1, x2, y2, page_no in rows:
        page = doc[page_no - 1]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        page.insert_text(
            (cx, cy),
            str(row),
            fontname="helv",
            fontsize=9,
            color=(1, 0, 0),  # red
            overlay=True,
        )
    doc.save(out_path)


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
