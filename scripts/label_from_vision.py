#!/usr/bin/env python
"""
label_from_vision.py
------------------------------------
Usage:
    python label_from_vision.py path/to/filename_final.pdf path/to/filename_map.csv

Output:
    path/to/filename_map_rich.csv
"""

from pathlib import Path
import os, sys, json
import fitz  # PyMuPDF
import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel

# Load environment variables
load_dotenv()

# --- Google Gen AI Imports ---
from google import genai
from google.genai import types

# ------------------- CONFIG -------------------------------------------
MODEL_ID    = "gemini-3-pro-preview" 
DPI         = 300       
BATCH_SIZE  = 50        # Gemini 3 has a huge context, 50 is safe
# ----------------------------------------------------------------------

# --- PYDANTIC SCHEMAS (Enforces Output Format) ---
class FieldLabel(BaseModel):
    row_id: int
    rich_description: str

class LabelBatch(BaseModel):
    items: list[FieldLabel]


def pdf_to_image_parts(pdf_path: Path, dpi=DPI):
    doc = fitz.open(pdf_path)
    parts = []
    print(f"   Converting {len(doc)} pages to high-res images ({dpi} DPI)...")
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        png_bytes = pix.tobytes("png")
        parts.append(types.Part.from_bytes(data=png_bytes, mime_type="image/png"))
    return parts

def build_prompt_text(batch_rows, history_examples):
    # 1. History Context
    history_text = ""
    if history_examples:
        recent = history_examples[-10:] 
        lines = ["--- REFERENCE STYLE ---"]
        for ex in recent:
            lines.append(f"ID {ex['row_id']} -> {ex['description']}")
        history_text = "\n".join(lines) + "\n\n"

    # 2. Batch Metadata
    meta_lines = ["--- IDs TO ANALYZE IN THIS BATCH ---"]
    for r in batch_rows:
        rid = str(r["row"])
        heading = str(r.get("heading", "") or "").strip()
        subheading = str(r.get("subheading", "") or "").strip()
        prelim = str(r.get("form_entry_description", "") or "").strip()
        page = str(r.get("page", ""))
        
        meta_lines.append(
            f"ID: {rid} (Page {page}) | Hint: {heading} > {subheading} > {prelim}"
        )

    batch_text = "\n".join(meta_lines)
    
    # 3. Instructions
    instructions = (
        "\n\n--- MISSION --- \n"
        "You are an expert Form Reasoning Agent. Look at the Red ID Numbers on the images.\n"
        "For each ID listed above, generate a 'rich_description' explaining what data goes in that field.\n"
        "Use the Visual Context + Text Hints to determine the true label.\n"
    )

    return history_text + batch_text + instructions

def call_gemini_vision(client, image_parts, batch_rows, history_examples):
    text_prompt = build_prompt_text(batch_rows, history_examples)
    contents = image_parts + [text_prompt]

    sys_instruction = (
        "You are a state-of-the-art multimodal form understanding agent. "
        "Extract specific meanings for form fields marked by Red IDs."
    )

    try:
        # We use response_schema=LabelBatch to FORCE a structured response
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=sys_instruction,
                response_mime_type="application/json",
                response_schema=LabelBatch, 
                temperature=0.1, 
            )
        )
        
        # Parse the structured response
        parsed_batch = response.parsed
        
        # Convert the list of objects back into a simple Dictionary: { "1": "Desc", "2": "Desc" }
        # This matches what the rest of the script expects.
        if parsed_batch and parsed_batch.items:
            return {str(item.row_id): item.rich_description for item in parsed_batch.items}
            
        return {}

    except Exception as e:
        print(f"Error calling {MODEL_ID}: {e}")
        return {}

def chunk_list(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i+size]

def main():
    if len(sys.argv) != 3:
        sys.exit("Usage: python label_from_vision.py <path_to_final_pdf> <path_to_map_csv>")

    pdf_path = Path(sys.argv[1])
    csv_path = Path(sys.argv[2])

    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("Error: GEMINI_API_KEY not found in environment variables.")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    print(f"Initialized Google GenAI Client with model: {MODEL_ID}")

    image_parts = pdf_to_image_parts(pdf_path)

    print(f"Loading CSV data from {csv_path}...")
    df = pd.read_csv(csv_path)
    
    for col in ["row", "heading", "subheading", "form_entry_description", "page"]:
        if col not in df.columns: df[col] = ""

    records = df.to_dict(orient="records")
    print(f"Found {len(records)} fields. Processing in batches of {BATCH_SIZE}...")

    descr_map = {}
    history_examples = [] 

    for i, batch_rows in enumerate(chunk_list(records, BATCH_SIZE)):
        batch_ids = [str(r["row"]) for r in batch_rows]
        print(f"Batch {i+1}: Reasoning on IDs {batch_ids[0]} to {batch_ids[-1]}...")

        # Returns a Dictionary now, guaranteed.
        batch_results = call_gemini_vision(client, image_parts, batch_rows, history_examples)
        
        for r in batch_rows:
            rid = str(r["row"])
            
            # Safe .get() because batch_results is definitely a dict
            new_desc = batch_results.get(rid)
            
            if not new_desc:
                existing = str(r.get("form_entry_description", "") or "").strip()
                new_desc = existing if existing else "[Description Unavailable]"
            
            descr_map[rid] = new_desc
            history_examples.append({"row_id": rid, "description": new_desc})

    df["rich_description"] = df["row"].astype(str).map(descr_map).fillna("")
    out_path = csv_path.parent / (csv_path.stem + "_rich.csv")
    df.to_csv(out_path, index=False)
    
    print("-" * 60)
    print(f"✓ Success! Enriched CSV saved to: {out_path}")
    print("-" * 60)

if __name__ == "__main__":
    main()
