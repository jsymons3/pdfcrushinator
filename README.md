# pdfcrushinator
Identify fields in pdf files, get data from pdfs and fill in forms accurately.

## LLM-powered filling pipeline

`fill_form_pipeline.py` takes a natural-language request plus a CSV field map and
produces a JSON overlay and a filled PDF.

```
python fill_form_pipeline.py \
    --pdf path/to/form.pdf \
    --csv path/to/form_map_rich.csv \
    --request "Fill for Sip With Zoe 301 New York Ave NE unit 4A027 ..." \
    --json-out mapping.json \
    --pdf-out overlay.pdf \
    --output-name "Sip With Zoe.pdf"
```

Requirements:

- `OPENAI_API_KEY` must be exported; optionally set `OPENAI_BASE` for a
  compatible endpoint.
- Dependencies: `openai`, `PyMuPDF` (fitz), and standard library modules.
- Use `--output-name` to save the filled PDF with a human-friendly filename.

The script builds the prompt (using the embedded instructions), calls the
OpenAI Chat Completions API, saves the JSON mapping, and then renders the
overlay via `overlay_fill.py`.
