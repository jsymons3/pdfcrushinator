#!/usr/bin/env python
"""
label_from_vision.py
------------------------------------
Usage:
    python label_from_vision.py marked_form.pdf widget_rows.csv

Output:
    widget_rows_rich.csv   (same as input CSV + rich_description column)
"""

from pathlib import Path
import os, sys, json, base64
import fitz
import pandas as pd
from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI
client = OpenAI()

from PIL import Image  # still imported if you want to use it later

# ------------------- CONFIG -------------------------------------------
MODEL       = "gpt-5.1"
DPI         = 300       # rasterise resolution
BATCH_SIZE  = 50        # <= HOW MANY FIELDS PER API CALL
# ----------------------------------------------------------------------


def pdf_to_png_data_urls(pdf_path: str, dpi=DPI):
    """Convert each PDF page to a base-64 PNG data-URL."""
    doc = fitz.open(pdf_path)
    urls = []

    for page in doc:
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        png_bytes = pix.tobytes("png")
        b64 = base64.b64encode(png_bytes).decode("ascii")
        urls.append(f"data:image/png;base64,{b64}")

    return urls


def build_history_text(history_examples):
    """
    Optional: previous (id -> description) examples so the model
    can keep style/ontology consistent across batches.
    history_examples is a list of dicts {row_id, description}.
    """
    if not history_examples:
        return ""

    recent = history_examples[-40:]  # or whatever cap you like
    lines = [
        "Here are some previously processed IDs and their final descriptions.",
        "Use these as examples of the desired style and level of detail:"
    ]
    for ex in recent:
        lines.append(f"- ID {ex['row_id']}: {ex['description']}")
    return "\n".join(lines)


def call_openai_vision(image_urls, batch_rows, history_examples=None):
    """
    Ask the model to map a *batch* of rows to concise descriptions,
    using heading / subheading / form_entry_description as hints.

    image_urls: list of data-URL PNGs for all pages
    batch_rows: list of dicts from df.to_dict('records') for this batch
    history_examples: list of {row_id, description} from previous batches
    """
    allowed_keys = [str(r["row"]) for r in batch_rows]
    history_text = build_history_text(history_examples or [])

    # Describe CSV metadata for this batch
    meta_lines = ["For this batch, here are the IDs and their CSV metadata:"]
    for r in batch_rows:
        rid = str(r["row"])
        heading = str(r.get("heading", "") or "").strip()
        subheading = str(r.get("subheading", "") or "").strip()
        prelim = str(r.get("form_entry_description", "") or "").strip()
        page = r.get("page", "")

        meta_lines.append(
            f"- ID {rid} (page {page}): "
            f"heading={heading!r}, subheading={subheading!r}, "
            f"preliminary_description={prelim!r}"
        )
    batch_meta_text = "\n".join(meta_lines)

    system_prompt = (
        "You are a highly reliable forms analyst. "
        "You see one or more pages of a PDF form with red numbers stamped in the centre "
        "of each entry box. These red numbers may appear in any order on the page; "
        "they do NOT necessarily increase left-to-right or top-to-bottom. "
        "Never assume numeric order corresponds to reading order.\n\n"
        "For each specific ID in this batch, you must:\n"
        "1. Look at the region of the form where that red number appears.\n"
        "2. Understand the actual field label or purpose near that red number.\n"
        "3. Combine that with the preliminary CSV description (if provided) to produce "
        "   a final, concise, human-readable field description.\n\n"
        "Rules:\n"
        "- Only produce entries for the IDs explicitly listed in this batch.\n"
        "- Do NOT invent additional IDs.\n"
        "- If the preliminary CSV description already looks correct and specific, reuse it "
        "  or lightly polish it; do NOT radically change it unless it is clearly wrong.\n"
        "- If an ID truly has no meaningful caption, you may leave its description empty.\n"
        "- Keep descriptions short but clear (a short phrase is usually enough).\n\n"
        "Output format is VERY strict:\n"
        "- One line per ID.\n"
        "- No header or commentary.\n"
        "- Each line must be: <ID>: <description>\n"
        "- Use each ID exactly once.\n"
        "- Do NOT use any additional ':' characters in the description; use commas or dashes instead.\n"
    )

    user_text = (
        "You will see:\n"
        "1) Some previous examples of IDs and descriptions (for style consistency), if any.\n"
        "2) The CSV metadata (heading, subheading, preliminary description) for this batch.\n"
        "3) The form images.\n\n"
        "Important:\n"
        "- Red numbers may be out of order or scattered around the page.\n"
        "- Do not infer ordering from position; treat each ID independently.\n"
        "- Use the preliminary CSV description as a baseline; correct or refine it only "
        "when the image clearly shows it is wrong or incomplete.\n\n"
        f"{history_text}\n\n"
        f"{batch_meta_text}\n\n"
        "Use exactly these IDs, no extras and no omissions:\n"
        f"{json.dumps(allowed_keys)}\n\n"
        "Return your answer as plain text, with one line for each ID, in the same order as the list above.\n"
        "Each line MUST be of the form:\n"
        "<ID>: <description>\n"
        "Do NOT add any explanation before or after the list. Just the lines."
    )

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": (
                [{"type": "text", "text": user_text}] +
                [
                    {"type": "image_url", "image_url": {"url": u, "detail": "low"}}
                    for u in image_urls
                ]
            ),
        },
    ]

    completion = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        reasoning_effort="high",
    )

    raw = completion.choices[0].message.content or ""
    lines = raw.splitlines()

    result = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            continue
        key_part, desc_part = line.split(":", 1)
        key = key_part.strip()
        desc = desc_part.strip()
        if key in allowed_keys:
            result[key] = desc

    # Ensure every ID has at least an empty string
    for k in allowed_keys:
        result.setdefault(k, "")

    return result


def chunk_list(lst, size):
    """Yield successive chunks of list `lst` with max length `size`."""
    for i in range(0, len(lst), size):
        yield lst[i:i+size]


def main():
    if len(sys.argv) != 3:
        sys.exit("Usage: python label_from_vision.py annotated.pdf widget_rows.csv")

    pdf_path, csv_path = Path(sys.argv[1]), Path(sys.argv[2])

    # --- Rasterise PDF --------------------------------------------------
    print("Rasterising PDF…")
    imgs = pdf_to_png_data_urls(pdf_path)

    # --- Load CSV + rows -----------------------------------------------
    df = pd.read_csv(csv_path)
    if "row" not in df.columns:
        sys.exit("CSV must contain a 'row' column with the numeric IDs.")

    # Turn each row into a dict so we can pass heading / subheading / form_entry_description
    records = df.to_dict(orient="records")

    print(f"Found {len(records)} fields. Processing in batches of {BATCH_SIZE}…")

    # Global map (row_id -> description) + running history for context
    descr_map = {}
    history_examples = []  # list of {"row_id": ..., "description": ...}

    batch_index = 0
    for batch_rows in chunk_list(records, BATCH_SIZE):
        batch_index += 1
        batch_ids = [str(r["row"]) for r in batch_rows]
        print(
            f"Calling OpenAI Vision for batch {batch_index}: "
            f"IDs {batch_ids[0]} … {batch_ids[-1]} "
            f"({len(batch_ids)} fields)"
        )

        # Call model with full row dicts + history
        batch_map = call_openai_vision(imgs, batch_rows, history_examples)

        # Merge into global map + update history
        for r in batch_rows:
            rid = str(r["row"])
            desc = batch_map.get(rid, "")
            descr_map[rid] = desc
            history_examples.append({"row_id": rid, "description": desc})

    # --- Append descriptions -------------------------------------------
    df["rich_description"] = df["row"].astype(str).map(descr_map).fillna("")

    out_path = csv_path.with_name(csv_path.stem + "_rich.csv")
    df.to_csv(out_path, index=False)

    print(f"✓ Wrote enriched CSV → {out_path}")



if __name__ == "__main__":
    main()
