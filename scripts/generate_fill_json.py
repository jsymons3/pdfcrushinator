#!/usr/bin/env python3
"""
generate_fill_json.py (Token Optimized)

Outputs a minimal JSON containing ONLY row IDs and values.
We do NOT return coordinates or headings to save tokens.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()


# --- Minimal Output Schema (Saves Tokens) ---
class FillItem(BaseModel):
    row: int
    value: str = Field(description="The exact string or boolean to enter.")
    note: str = Field(description="Brief reason (e.g. 'Inferred from context').")


class FillPlan(BaseModel):
    items: list[FillItem]


# --- Helpers ---
def load_rich_map_summary(csv_path: Path) -> str:
    """Load CSV but formatting as a text block for the prompt."""
    lines = []
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            # We explicitly format the context for the AI
            # We do NOT need to load coords here, just the semantic data
            lines.append(
                f"ID {r['row']}: {r.get('heading','')} | {r.get('subheading','')} | {r.get('rich_description','')}"
            )
    return "\n".join(lines)


def load_pdf_part(pdf_path: Path) -> types.Part:
    with open(pdf_path, "rb") as f:
        return types.Part.from_bytes(data=f.read(), mime_type="application/pdf")


def chunk_text(text_list: list[str], chunk_size: int = 100):
    """Simple chunking for text lines"""
    for i in range(0, len(text_list), chunk_size):
        yield text_list[i:i + chunk_size]


# --- Main Logic ---
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", default="gemini-3-pro-preview")
    args = parser.parse_args()

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    # 1. Load context
    print("Loading map and PDF...")
    full_map_text = load_rich_map_summary(args.csv).split("\n")
    pdf_part = load_pdf_part(args.pdf)

    # 2. Iterate in chunks (to keep input context manageable)
    # Gemini 3 has huge context, but chunking ensures we don't hit output limits
    all_items = []

    # Process 150 fields at a time
    for i, chunk in enumerate(chunk_text(full_map_text, 150)):
        print(f"Processing chunk {i + 1}...")
        field_block = "\n".join(chunk)

        prompt = (
            "You are a form-filling engine. \n"
            "1. Look at the PDF (Red IDs match the IDs below).\n"
            "2. Read the User Instruction.\n"
            "3. Return a JSON list of **ONLY** the fields that must be filled.\n"
            "4. If a field should be left empty, do not include it in the JSON.\n\n"
            f"USER INSTRUCTION: \"{args.instruction}\"\n\n"
            f"CANDIDATE FIELDS:\n{field_block}"
        )

        try:
            response = client.models.generate_content(
                model=args.model,
                contents=[pdf_part, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=FillPlan,
                    temperature=0.0,
                ),
            )
            if response.parsed:
                all_items.extend(response.parsed.items)
        except Exception as e:
            print(f"Error in chunk {i}: {e}")

    # 3. Save Minimal JSON
    output_data = [item.model_dump() for item in all_items]

    with open(args.out, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"✓ Saved {len(output_data)} instructions to {args.out}")


if __name__ == "__main__":
    main()
