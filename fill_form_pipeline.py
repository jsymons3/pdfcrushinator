"""End-to-end pipeline to convert a natural-language request into a filled PDF.

Steps
-----
1. Read the CSV mapping for a form.
2. Build a natural-language prompt that instructs the LLM how to populate that
   mapping from a user request.
3. Call the OpenAI Chat Completions API to retrieve the JSON mapping.
4. Save the JSON mapping and render it onto the target PDF using ``overlay_fill``.

Usage
-----
python fill_form_pipeline.py \\
    --pdf path/to/form.pdf \\
    --csv path/to/form_map_rich.csv \\
    --request "Fill for Sip With Zoe ..." \\
    --json-out mapped.json \\
    --pdf-out filled.pdf

You must export OPENAI_API_KEY in your environment; optionally set OPENAI_BASE
if you are targeting a compatible endpoint.
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from openai import OpenAI

from overlay_fill import overlay_pdf

PROMPT_TEMPLATE = """You are given:
1) A CSV that maps form fields to on-page coordinates: row, heading, subheading, form_entry_description, x1, y1, x2, y2, page, rich_description.
2) Light instructions about the form (e.g., tax rate is a multiplier, container size typically 750 ml, etc.).
3) A user’s free-form request describing permittee, supplier, dates, quantities, taxes, and product details.

Your task: Produce a JSON array where each object matches one CSV row that you can confidently fill. Include:
- row (integer from CSV)
- heading
- rich_description
- page
- x1, y1, x2, y2 (from CSV)
- value (string or number; omit if unknown)
- note (optional, clarifications/assumptions)

Rules:
- Only include rows you can fill confidently from the request; skip rows you cannot fill.
- Do not change coordinates or page numbers; copy them from the CSV row you’re populating.
- Keep numeric fields numeric (e.g., tax rate 1.5 should be 1.5, not “1.5%”).
- Tax rate is a multiplier per gallon, NOT a percent.
- Container size: default to “750 ml” when implied but not stated.
- Cases/BBL: if only total bottle count is given, assume 12 bottles per case unless the request states otherwise.
- Gallons: 12 × 750 ml ≈ 9 liters per case; 1 gallon ≈ 3.785 L → about 2.38 gallons per case. Multiply by cases to get total gallons.
- Tax Quan Rate: usually total_gallons × tax_rate for that line.
- If multiple product lines are present, use the repeated line items (rows 18–24, 25–31, etc.) in order.
- Preserve capitalization from the request for names/addresses unless clearly a formatting typo.
- If date is provided, place it in date fields; reuse for signature date if appropriate.

Output strictly as JSON (no prose). Example schema for each filled row:
{
  "row": <int>,
  "heading": "<string>",
  "rich_description": "<string>",
  "page": <int>,
  "x1": <float>,
  "y1": <float>,
  "x2": <float>,
  "y2": <float>,
  "value": <string or number>,
  "note": "<string>"
}

CSV mapping:
```csv
{csv_text}
```

User request:
{user_request}
"""


def build_prompt(csv_text: str, user_request: str) -> str:
    return PROMPT_TEMPLATE.format(csv_text=csv_text.strip(), user_request=user_request.strip())


def call_openai(prompt: str, model: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in the environment")

    client = OpenAI(
        api_key=api_key,
        base_url=os.environ.get("OPENAI_BASE"),
    )

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a careful form-filling assistant."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )

    return completion.choices[0].message.content or ""


def parse_json_mapping(raw_text: str) -> List[Dict[str, Any]]:
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError("Model output was not valid JSON") from exc


def save_json(mapping: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2)


def read_csv_text(csv_path: Path) -> str:
    with csv_path.open("r", encoding="utf-8") as f:
        return f.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate mapping JSON from a natural-language request and render the overlay.")
    parser.add_argument("--pdf", required=True, help="Path to the blank form PDF")
    parser.add_argument("--csv", required=True, help="Path to the CSV mapping file")
    parser.add_argument("--request", required=True, help="Natural-language request describing the form contents")
    parser.add_argument("--json-out", default="mapping.json", help="Where to write the JSON mapping")
    parser.add_argument("--pdf-out", default="overlay.pdf", help="Where to write the filled PDF overlay")
    parser.add_argument("--model", default="gpt-4o-mini", help="OpenAI model to use")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    pdf_path = Path(args.pdf)
    json_out_path = Path(args.json_out)
    pdf_out_path = Path(args.pdf_out)

    csv_text = read_csv_text(csv_path)
    prompt = build_prompt(csv_text, args.request)
    print("Calling OpenAI with prompt built from CSV mapping and user request...")
    raw_response = call_openai(prompt, args.model)

    mapping = parse_json_mapping(raw_response)
    save_json(mapping, json_out_path)
    print(f"Saved JSON mapping to {json_out_path}")

    overlay_pdf(str(pdf_path), mapping, str(pdf_out_path))
    print(f"Saved filled overlay PDF to {pdf_out_path}")


if __name__ == "__main__":
    main()
