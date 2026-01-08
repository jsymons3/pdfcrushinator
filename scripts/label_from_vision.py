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

# Load environment variables (Ensure GEMINI_API_KEY is in .env)
load_dotenv()

# --- Google Gen AI Imports ---
# pip install google-genai
from google import genai
from google.genai import types

# ------------------- CONFIG -------------------------------------------
# Using the specific Gemini 3 Pro Preview model code you provided.
MODEL_ID    = "gemini-3-pro-preview" 
DPI         = 200       # Balance clarity and memory use for vision inference
BATCH_SIZE  = 100        # Smaller batch size to allow deep reasoning per item
# ----------------------------------------------------------------------

def pdf_pages_to_image_parts(pdf_path: Path, pages: list[int], dpi=DPI):
    """
    Convert selected PDF pages to PNG bytes.
    We render only the pages needed for the current batch to reduce memory.
    """
    doc = fitz.open(pdf_path)
    parts = []
    
    page_numbers = sorted({p for p in pages if 1 <= p <= len(doc)})
    print(f"   Converting {len(page_numbers)} pages to images ({dpi} DPI) for Gemini 3 Vision...")
    for page_number in page_numbers:
        page = doc[page_number - 1]
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        png_bytes = pix.tobytes("png")
        parts.append(types.Part.from_bytes(data=png_bytes, mime_type="image/png"))
        
    doc.close()
    return parts

def build_prompt_text(batch_rows, history_examples):
    """
    Constructs a prompt designed for Gemini 3's reasoning capabilities.
    """
    
    # 1. History Context (for consistency)
    history_text = ""
    if history_examples:
        recent = history_examples[-10:] 
        lines = ["--- REFERENCE STYLE (Previous Examples) ---"]
        for ex in recent:
            lines.append(f"ID {ex['row_id']} -> {ex['description']}")
        history_text = "\n".join(lines) + "\n\n"

    # 2. Batch Metadata
    meta_lines = ["--- IDs TO ANALYZE IN THIS BATCH ---"]
    for r in batch_rows:
        rid = str(r["row"])
        # Hints from the CSV (spatial extraction)
        heading = str(r.get("heading", "") or "").strip()
        subheading = str(r.get("subheading", "") or "").strip()
        prelim = str(r.get("form_entry_description", "") or "").strip()
        page = str(r.get("page", ""))
        
        meta_lines.append(
            f"ID: {rid} (Page {page}) | Hint: {heading} > {subheading} > {prelim}"
        )

    batch_text = "\n".join(meta_lines)
    
    # 3. Reasoning Instructions
    instructions = (
        "\n\n--- MISSION --- \n"
        "You are an expert Form Reasoning Agent. You are looking at a document where specific input fields "
        "have been stamped with Red ID Numbers.\n\n"
        "For each ID listed above, perform this reasoning:\n"
        "1. VISUAL LOCATE: Find the Red ID Number on the page images.\n"
        "2. CONTEXTUALIZE: Look at the text surrounding that box. Is the label above? To the left? "
        "   Is it part of a grid or matrix? (e.g., 'Row: Heating, Column: Yes').\n"
        "3. SYNTHESIZE: Combine the visual label with the 'Hint' provided. If the Hint implies a hierarchy "
        "   (like 'Section 2 > Buyer Info'), include that nuance.\n"
        "4. OUTPUT: Generate a `rich_description` that clearly explains what data goes in that field.\n\n"
        "--- OUTPUT FORMAT ---\n"
        "Return a JSON object where keys are the IDs and values are the rich descriptions."
    )

    return history_text + batch_text + instructions

def call_gemini_vision(client, pdf_path, batch_rows, history_examples):
    """
    Sends images + prompt to Gemini 3 Pro.
    """
    pages = [int(r.get("page") or 1) for r in batch_rows]
    image_parts = pdf_pages_to_image_parts(pdf_path, pages)
    text_prompt = build_prompt_text(batch_rows, history_examples)
    contents = image_parts + [text_prompt]

    # System instruction tailored for Gemini 3's "Agentic" persona
    sys_instruction = (
        "You are a state-of-the-art multimodal form understanding agent. "
        "You possess deep reasoning capabilities to deduce the meaning of form fields "
        "even when layouts are complex, tabular, or non-standard."
    )

    try:
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=sys_instruction,
                response_mime_type="application/json", # Native JSON output
                temperature=0.1, # Low temperature for factual precision
            )
        )
        
        # Parse JSON result
        result_json = json.loads(response.text)
        return result_json

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

    # Initialize Client
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    print(f"Initialized Google GenAI Client with model: {MODEL_ID}")

    # 1. Load CSV (Data Context)
    print(f"Loading CSV data from {csv_path}...")
    df = pd.read_csv(csv_path)
    
    # Ensure columns exist
    for col in ["row", "heading", "subheading", "form_entry_description", "page"]:
        if col not in df.columns: df[col] = ""

    records = df.to_dict(orient="records")
    print(f"Found {len(records)} fields. Processing in batches of {BATCH_SIZE}...")

    # 3. Process Batches
    descr_map = {}
    history_examples = [] 

    for i, batch_rows in enumerate(chunk_list(records, BATCH_SIZE)):
        batch_ids = [str(r["row"]) for r in batch_rows]
        print(f"Batch {i+1}: Reasoning on IDs {batch_ids[0]} to {batch_ids[-1]}...")

        batch_results = call_gemini_vision(client, image_parts, batch_rows, history_examples)
        
        for r in batch_rows:
            rid = str(r["row"])
            new_desc = batch_results.get(rid)
            
            if not new_desc:
                # Fallback if the model skipped an ID
                existing = str(r.get("form_entry_description", "") or "").strip()
                new_desc = existing if existing else "[Description Unavailable]"
            
            descr_map[rid] = new_desc
            history_examples.append({"row_id": rid, "description": new_desc})

    # 4. Save Results
    df["rich_description"] = df["row"].astype(str).map(descr_map).fillna("")
    out_path = csv_path.parent / (csv_path.stem + "_rich.csv")
    df.to_csv(out_path, index=False)
    
    print("-" * 60)
    print(f"✓ Success! Gemini 3 Pro analysis saved to:")
    print(f"  {out_path}")
    print("-" * 60)

if __name__ == "__main__":
    main()
