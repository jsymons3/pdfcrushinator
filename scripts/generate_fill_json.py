#!/usr/bin/env python3
"""
generate_fill_json.py

Generate a fill-plan JSON from:
- rich_map.csv (field coordinates + rich descriptions)
- The ANNOTATED PDF (with red numbers) for visual grounding
- a natural-language instruction from a realtor

Output JSON is a list[dict] where each dict includes:
row, heading, rich_description, page, x1,y1,x2,y2, value, note

Requires:
  pip install google-genai pydantic python-dotenv

Env:
  export GEMINI_API_KEY=...

Usage:
  python generate_fill_json.py \
    --csv FILE_1600_map_rich.csv \
    --pdf FILE_1600_final.pdf \   <-- USE THE FLATTENED RED-NUMBER PDF
    --instruction "Fill out this buyer-broker agreement for my client..." \
    --out fill_plan.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List
from dotenv import load_dotenv

from google import genai
from google.genai import types
from pydantic import BaseModel, Field as PydanticField

load_dotenv()

# -----------------------------
# Data Models
# -----------------------------

@dataclass
class FormField:
    row: int
    heading: str
    subheading: str
    form_entry_description: str
    rich_description: str
    page: int
    x1: float
    y1: float
    x2: float
    y2: float

    def summary(self) -> str:
        return (f"Row ID {self.row}: {self.heading} | {self.subheading} | "
                f"Desc: {self.rich_description}")

    def full_dict(self) -> Dict[str, Any]:
        return asdict(self)

class RowSelection(BaseModel):
    rows_to_fill: List[int] = PydanticField(
        description="List of Red Row IDs (integers) seen on the form that require input."
    )
    reasoning: str = PydanticField(
        description="Explanation of which fields matched the instructions."
    )

class FillItem(BaseModel):
    row: int
    value: str = PydanticField(description="The value to enter (text, date, or 'X').")
    note: str = PydanticField(description="Reasoning for this value.")

class FillPlan(BaseModel):
    items: List[FillItem]

# -----------------------------
# Helpers
# -----------------------------

def _safe_float(x: str) -> float:
    try: return float(x)
    except: return 0.0

def _safe_int(x: str) -> int:
    try: return int(float(x))
    except: return 0

def load_rich_map(csv_path: Path) -> List[FormField]:
    fields: List[FormField] = []
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [n.strip() for n in reader.fieldnames or []]
        for r in reader:
            fields.append(FormField(
                row=_safe_int(r.get("row", 0)),
                heading=(r.get("heading") or "").strip(),
                subheading=(r.get("subheading") or "").strip(),
                form_entry_description=(r.get("form_entry_description") or "").strip(),
                rich_description=(r.get("rich_description") or "").strip(),
                page=_safe_int(r.get("page", 1)),
                x1=_safe_float(r.get("x1", 0)),
                y1=_safe_float(r.get("y1", 0)),
                x2=_safe_float(r.get("x2", 0)),
                y2=_safe_float(r.get("y2", 0)),
            ))
    return fields

def load_pdf_part(pdf_path: Path) -> types.Part:
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    return types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")

def chunked(lst: List[Any], n: int) -> List[List[Any]]:
    return [lst[i:i+n] for i in range(0, len(lst), n)]

# -----------------------------
# Gemini Logic
# -----------------------------

def select_relevant_rows(
    client: genai.Client,
    model: str,
    instruction: str,
    pdf_part: types.Part,
    fields: List[FormField],
) -> List[int]:
    
    field_text = "\n".join([f.summary() for f in fields])

    # --- UPDATED PROMPT FOR VISUAL ID ---
    prompt = (
        "You are an expert real estate agent assistant.\n"
        "1. **VISUAL SCAN**: Look at the attached PDF. You will see **RED ROW NUMBERS** stamped on every field.\n"
        "2. **DATA MATCH**: These red numbers correspond exactly to the 'Row IDs' in the text list below.\n"
        "3. **INSTRUCTION**: Read the User Instruction to understand what needs to be filled.\n"
        "4. **SELECT**: Return the list of Row IDs that need to be filled. Use the visual context to ensure you are picking the correct field (e.g., ensuring you pick 'Buyer Signature' and not 'Seller Signature').\n\n"
        f"USER INSTRUCTION: \"{instruction}\"\n\n"
        f"AVAILABLE FIELDS (Match these IDs to the Red Numbers):\n{field_text}"
    )

    try:
        response = client.models.generate_content(
            model=model,
            contents=[pdf_part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RowSelection,
                temperature=0.1
            )
        )
        data = response.parsed
        return data.rows_to_fill if data else []
    except Exception as e:
        print(f"Error in selection step: {e}")
        return []

def generate_fill_values(
    client: genai.Client,
    model: str,
    instruction: str,
    pdf_part: types.Part,
    selected_fields: List[FormField],
) -> List[Dict[str, Any]]:

    if not selected_fields: return []

    field_details_str = ""
    for f in selected_fields:
        field_details_str += (
            f"- Row ID {f.row}: {f.rich_description}\n"
        )

    # --- UPDATED PROMPT FOR VISUAL ID ---
    prompt = (
        "You are a precise form-filling agent.\n"
        "You have selected specific fields to fill based on the Red Row IDs on the PDF.\n"
        "Now, generate the EXACT values for these fields.\n\n"
        "RULES:\n"
        "- **Verify Visually**: Look at the Red Number on the PDF to confirm the field type (e.g., is Row 10 a small date line or a large address box?).\n"
        "- **Checkboxes**: If the visual box under the Red Number is a checkbox/radio, return 'X'.\n"
        "- **Dates**: If 'today' is implied, use the current date.\n"
        "- **Defaults**: If the instruction is missing a detail (e.g. Zip Code), infer it if possible or leave a placeholder note.\n\n"
        f"USER INSTRUCTION: \"{instruction}\"\n\n"
        f"TARGET FIELDS (Red IDs):\n{field_details_str}"
    )

    try:
        response = client.models.generate_content(
            model=model,
            contents=[pdf_part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=FillPlan,
                temperature=0.1
            )
        )
        
        result_items = []
        if response.parsed:
            field_map = {f.row: f for f in selected_fields}
            for item in response.parsed.items:
                original_field = field_map.get(item.row)
                if original_field:
                    out_dict = original_field.full_dict()
                    out_dict["value"] = item.value
                    out_dict["note"] = item.note
                    
                    final_keys = ["row", "heading", "rich_description", "page", 
                                  "x1", "y1", "x2", "y2", "value", "note"]
                    clean_dict = {k: out_dict.get(k) for k in final_keys}
                    result_items.append(clean_dict)
                    
        return result_items

    except Exception as e:
        print(f"Error in fill step: {e}")
        return []

# -----------------------------
# Main
# -----------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, type=Path, help="Path to rich_map.csv")
    ap.add_argument("--pdf", required=True, type=Path, help="Path to the ANNOTATED PDF (with red numbers)")
    ap.add_argument("--instruction", required=True, type=str, help="Natural-language instruction")
    ap.add_argument("--out", required=True, type=Path, help="Output JSON path")
    ap.add_argument("--model", default="gemini-3-pro-preview", type=str, help="Gemini Model ID")
    args = ap.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("Error: GEMINI_API_KEY not found in environment variables.")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    print(f"Loading map: {args.csv}")
    fields = load_rich_map(args.csv)
    
    print(f"Loading Visual Context (Red Numbers): {args.pdf}")
    pdf_part = load_pdf_part(args.pdf)

    # Step 1: Selection
    print("Step 1: Selecting relevant fields via Visual Grounding...")
    selected_rows: List[int] = []
    # Gemini 3 Pro has massive context, so 150-200 fields per chunk is safe
    for part in chunked(fields, 150):
        rows = select_relevant_rows(client, args.model, args.instruction, pdf_part, part)
        selected_rows.extend(rows)
        print(f"  - chunk analyzed, found {len(rows)} targets...")
    
    selected_rows = sorted(set(selected_rows))
    print(f"Total selected rows: {len(selected_rows)}")

    if not selected_rows:
        print("No fields matched. Exiting.")
        return

    # Step 2: Value Generation
    print("Step 2: Generating fill values...")
    by_row = {f.row: f for f in fields}
    target_fields = [by_row[r] for r in selected_rows if r in by_row]
    
    all_items: List[Dict[str, Any]] = []
    for part in chunked(target_fields, 50):
        items = generate_fill_values(client, args.model, args.instruction, pdf_part, part)
        all_items.extend(items)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)

    print(f"✓ Wrote {len(all_items)} fill items to: {args.out}")

if __name__ == "__main__":
    main()
