#!/usr/bin/env python3
"""
generate_fill_json.py

Generate a fill-plan JSON from:
- rich_map.csv (field coordinates + rich descriptions)
- a blank PDF (used only for light context text extraction)
- a natural-language instruction from a realtor

Output JSON is a list[dict] where each dict includes:
row, heading, rich_description, page, x1,y1,x2,y2, value, note

Requires:
  pip install openai pdfplumber
Env:
  export OPENAI_API_KEY=...

Usage:
  python generate_fill_json.py \
    --csv FILE_1600_map_rich.csv \
    --pdf FILE_1600_annotated.pdf \
    --instruction "Fill out this buyer-broker agreement for my client..." \
    --out fill_plan.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber
from openai import OpenAI


# -----------------------------
# Data model
# -----------------------------

@dataclass
class Field:
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

    def summary(self) -> Dict[str, Any]:
        # Small payload for "select rows" step
        return {
            "row": self.row,
            "heading": self.heading,
            "subheading": self.subheading,
            "rich_description": self.rich_description,
            "page": self.page,
        }

    def full(self) -> Dict[str, Any]:
        d = self.summary()
        d.update({"x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2})
        return d


# -----------------------------
# Helpers
# -----------------------------

def _safe_float(x: str) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0

def _safe_int(x: str) -> int:
    try:
        return int(float(x))
    except Exception:
        return 0

def load_rich_map(csv_path: Path) -> List[Field]:
    fields: List[Field] = []
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"row", "heading", "subheading", "form_entry_description", "rich_description",
                    "page", "x1", "y1", "x2", "y2"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV is missing required columns: {sorted(missing)}")

        for r in reader:
            fields.append(Field(
                row=_safe_int(r["row"]),
                heading=(r.get("heading") or "").strip(),
                subheading=(r.get("subheading") or "").strip(),
                form_entry_description=(r.get("form_entry_description") or "").strip(),
                rich_description=(r.get("rich_description") or "").strip(),
                page=_safe_int(r["page"]),
                x1=_safe_float(r["x1"]),
                y1=_safe_float(r["y1"]),
                x2=_safe_float(r["x2"]),
                y2=_safe_float(r["y2"]),
            ))
    return fields

def extract_pdf_context(pdf_path: Path, max_pages: int = 2, max_chars: int = 6000) -> str:
    """
    Light context extraction (NOT OCR). This is just to help the model
    understand the form's semantics; coordinates come from the CSV.
    """
    chunks: List[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages[:max_pages]):
            text = page.extract_text() or ""
            text = re.sub(r"[ \t]+", " ", text).strip()
            if text:
                chunks.append(f"[PAGE {i+1}]\n{text}")
            if sum(len(c) for c in chunks) >= max_chars:
                break
    joined = "\n\n".join(chunks)
    return joined[:max_chars]

def response_text(resp: Any) -> str:
    """
    Works across SDK response shapes. The Responses API provides output_text helper
    in many SDK versions.
    """
    if hasattr(resp, "output_text") and isinstance(resp.output_text, str) and resp.output_text.strip():
        return resp.output_text
    # Fallback: try to navigate typical response object
    try:
        return resp.output[0].content[0].text
    except Exception:
        return str(resp)

def chunked(lst: List[Any], n: int) -> List[List[Any]]:
    return [lst[i:i+n] for i in range(0, len(lst), n)]


# -----------------------------
# OpenAI calls (2-step)
# -----------------------------

def select_relevant_rows(
    client: OpenAI,
    model: str,
    instruction: str,
    pdf_context: str,
    field_summaries: List[Dict[str, Any]],
) -> List[int]:
    schema = {
        "type": "object",
        "properties": {
            "rows_to_fill": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Row IDs from the CSV that should receive a value based on the instruction."
            },
            "notes": {"type": "string"}
        },
        "required": ["rows_to_fill", "notes"],
        "additionalProperties": False
    }

    system = (
        "You are a real-estate form-filling assistant. "
        "You will be given (a) a natural-language instruction from a realtor, "
        "(b) a short excerpt of the PDF text, and (c) a list of candidate fields identified by row number. "
        "Your job is ONLY to pick which row numbers need values."
    )

    user = {
        "instruction": instruction,
        "pdf_context_excerpt": pdf_context,
        "candidate_fields": field_summaries,
        "selection_rules": [
            "Select rows needed to satisfy the instruction (names, dates, brokerage, agent, compensation, retainer, dual agency, address/contact info, initials/signatures if present).",
            "Do not select rows that are purely explanatory paragraphs with no blanks/checkboxes.",
            "When in doubt, include the row number; the next step will decide values."
        ]
    }

    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)}
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "row_selector",
                "strict": True,
                "schema": schema
            }
        },
    )

    data = json.loads(response_text(resp))
    return sorted(set(int(x) for x in data["rows_to_fill"]))

def build_fill_items(
    client: OpenAI,
    model: str,
    instruction: str,
    pdf_context: str,
    selected_fields: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "row": {"type": "integer"},
                        "heading": {"type": "string"},
                        "rich_description": {"type": "string"},
                        "page": {"type": "integer"},
                        "x1": {"type": "number"},
                        "y1": {"type": "number"},
                        "x2": {"type": "number"},
                        "y2": {"type": "number"},
                        "value": {"type": "string"},
                        "note": {"type": "string"}
                    },
                    "required": ["row","heading","rich_description","page","x1","y1","x2","y2","value","note"],
                    "additionalProperties": False
                }
            }
        },
        "required": ["items"],
        "additionalProperties": False
    }

    system = (
        "You are a careful form-filling assistant. "
        "Given a realtor's instruction and a list of fields (with coordinates), "
        "produce ONLY the JSON fill items. "
        "Values must be realistic and consistent with the instruction. "
        "If the instruction doesn't specify a detail, choose a reasonable default and explain in note."
    )

    user = {
        "instruction": instruction,
        "pdf_context_excerpt": pdf_context,
        "selected_fields_with_coords": selected_fields,
        "value_rules": [
            "Prefer explicit details from the instruction.",
            "For dates: if 'today' is mentioned, use today's date implied by the instruction context (or keep consistent).",
            "For checkboxes: if a field represents a checkbox, set value to 'X' (or 'Yes'/'No') as appropriate and note it.",
            "For initials/signatures: use buyer initials derived from the buyer name if not provided.",
            "Do not invent extra fields that aren't in selected_fields_with_coords."
        ],
        "output_rule": "Return JSON matching the schema exactly."
    }

    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)}
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "fill_items",
                "strict": True,
                "schema": schema
            }
        },
    )

    data = json.loads(response_text(resp))
    return data["items"]


# -----------------------------
# Main
# -----------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, type=Path, help="Path to rich_map.csv")
    ap.add_argument("--pdf", required=True, type=Path, help="Path to blank/annotated PDF")
    ap.add_argument("--instruction", required=True, type=str, help="Natural-language instruction")
    ap.add_argument("--out", required=True, type=Path, help="Output JSON path")
    ap.add_argument("--model", default="gpt-4o-mini", type=str, help="Model for Responses API")
    ap.add_argument("--max-context-pages", default=2, type=int)
    args = ap.parse_args()

    fields = load_rich_map(args.csv)
    pdf_context = extract_pdf_context(args.pdf, max_pages=args.max_context_pages)

    client = OpenAI()

    # Step 1: select relevant rows (use only small summaries)
    summaries = [f.summary() for f in fields]
    # If there are tons of fields, send in chunks and union results
    selected_rows: List[int] = []
    for part in chunked(summaries, 250):
        selected_rows.extend(select_relevant_rows(
            client=client,
            model=args.model,
            instruction=args.instruction,
            pdf_context=pdf_context,
            field_summaries=part,
        ))
    selected_rows = sorted(set(selected_rows))

    # Step 2: generate fill items only for selected rows (include coords)
    by_row = {f.row: f for f in fields}
    selected_fields = [by_row[r].full() for r in selected_rows if r in by_row]

    items: List[Dict[str, Any]] = []
    for part in chunked(selected_fields, 120):
        items.extend(build_fill_items(
            client=client,
            model=args.model,
            instruction=args.instruction,
            pdf_context=pdf_context,
            selected_fields=part,
        ))

    # Save
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(items)} fill items to: {args.out}")

if __name__ == "__main__":
    main()
