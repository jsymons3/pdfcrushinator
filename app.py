import os
import json
import time
import uuid
import shutil
import subprocess
import threading
import hashlib
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from fastapi.responses import RedirectResponse
import sys

DATA_DIR = Path(os.getenv("DATA_DIR", "/tmp/agent_assist")).resolve()
SCRIPTS_DIR = Path(os.getenv("SCRIPTS_DIR", "scripts")).resolve()

PROFILES_DIR = DATA_DIR / "profiles"
LIBRARY_DIR = DATA_DIR / "library_pdfs"
MAPPINGS_DIR = DATA_DIR / "mappings"
JOBS_DIR = DATA_DIR / "jobs"
DONE_DIR = DATA_DIR / "completed"

for d in [PROFILES_DIR, LIBRARY_DIR, MAPPINGS_DIR, JOBS_DIR, DONE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# --- SCRIPT PATHS (UPDATED FOR NEW WORKFLOW) ---
# Ensure these filenames match exactly what you saved earlier
EXTRACT = SCRIPTS_DIR / "extract_form_fields.py"
LABEL = SCRIPTS_DIR / "label_from_vision.py"
GENFILL = SCRIPTS_DIR / "generate_fill_json.py"
NATIVE = SCRIPTS_DIR / "native_fill.py"  # Replaces overlay_fill.py

# --- app ---
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def root():
    return RedirectResponse("/t/almir")


jinja = Environment(loader=FileSystemLoader("templates"))

# Token-based profiles (MVP). Create a file: /var/data/profiles/almir.json
PROFILES_DIR = Path(os.getenv("PROFILES_DIR", str(DATA_DIR / "profiles")))
FALLBACK_PROFILES_DIR = Path("profiles")


def load_profile(token: str) -> dict:
    p = PROFILES_DIR / f"{token}.json"
    if not p.exists():
        p = FALLBACK_PROFILES_DIR / f"{token}.json"
    if not p.exists():
        raise HTTPException(404, "Unknown token/profile")
    return json.loads(p.read_text(encoding="utf-8"))


def write_status(job_dir: Path, obj: dict):
    (job_dir / "status.json").write_text(json.dumps(obj, indent=2), encoding="utf-8")


def read_status(job_dir: Path) -> dict:
    p = job_dir / "status.json"
    if not p.exists():
        return {"state": "unknown", "progress": 0, "message": "No status."}
    return json.loads(p.read_text(encoding="utf-8"))


def sha1_bytes(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def mapping_exists(pdf_id: str) -> bool:
    return (MAPPINGS_DIR / pdf_id / "map_rich.csv").exists()


def set_status(job_dir: Path, state: str, progress: int, message: str, extra: dict | None = None):
    obj = {"state": state, "progress": progress, "message": message, "updated_at": time.time()}
    if extra:
        obj.update(extra)
    write_status(job_dir, obj)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/t/{token}", response_class=HTMLResponse)
def ui(token: str):
    profile = load_profile(token)
    tpl = jinja.get_template("index.html")
    return tpl.render(token=token, profile_name=profile.get("agent_name", "(profile)"))


@app.get("/t/{token}/map/{pdf_id}", response_class=HTMLResponse)
def map_editor_ui(token: str, pdf_id: str):
    load_profile(token)
    if not (MAPPINGS_DIR / pdf_id).exists():
        raise HTTPException(404, "Mapping not found")
    tpl = jinja.get_template("mapper.html")
    return tpl.render(token=token, pdf_id=pdf_id)


@app.get("/api/profile/{token}")
def api_profile(token: str):
    return load_profile(token)


@app.get("/api/library/{token}")
def api_library(token: str):
    load_profile(token)
    out = []
    for p in sorted(LIBRARY_DIR.glob("*.pdf")):
        meta_path = p.with_suffix(".json")
        name = p.name
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                name = meta.get("name", name)
            except json.JSONDecodeError:
                pass
        out.append({"id": p.stem, "name": name})
    return out


@app.get("/api/library/{token}/pdf/{pdf_id}")
def api_library_pdf(token: str, pdf_id: str):
    load_profile(token)
    p = LIBRARY_DIR / f"{pdf_id}.pdf"
    if not p.exists():
        raise HTTPException(404, "PDF not found")
    meta_path = p.with_suffix(".json")
    filename = p.name
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            filename = meta.get("name", filename)
        except json.JSONDecodeError:
            pass
    return FileResponse(str(p), media_type="application/pdf", filename=filename)


@app.delete("/api/library/{token}/{pdf_id}")
def api_library_delete(token: str, pdf_id: str):
    load_profile(token)
    pdf_path = LIBRARY_DIR / f"{pdf_id}.pdf"
    meta_path = pdf_path.with_suffix(".json")
    if pdf_path.exists():
        pdf_path.unlink()
    if meta_path.exists():
        meta_path.unlink()
    mapping_dir = MAPPINGS_DIR / pdf_id
    if mapping_dir.exists():
        shutil.rmtree(mapping_dir)
    return {"ok": True}


@app.get("/api/completed/{token}")
def api_completed_list(token: str):
    load_profile(token)
    out = []
    for d in sorted(DONE_DIR.glob("*")):
        meta = d / "meta.json"
        if meta.exists():
            m = json.loads(meta.read_text(encoding="utf-8"))
            out.append(m)
    out.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return out


@app.delete("/api/completed/{token}/{doc_id}")
def api_completed_delete(token: str, doc_id: str):
    load_profile(token)
    d = DONE_DIR / doc_id
    if d.exists():
        shutil.rmtree(d)
    return {"ok": True}


@app.post("/api/jobs/{token}")
async def create_job(
    token: str,
    instruction: str = Form(...),
    pdf: UploadFile | None = File(None),
    pdf_id: str | None = Form(None),
):
    profile = load_profile(token)

    if (pdf is None) == (pdf_id is None):
        raise HTTPException(400, "Provide exactly one of: pdf upload OR pdf_id")

    job_id = uuid.uuid4().hex
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    input_pdf_path = job_dir / "input.pdf"
    input_name = "uploaded.pdf"
    resolved_pdf_id = None

    if pdf is not None:
        b = await pdf.read()
        input_pdf_path.write_bytes(b)
        input_name = pdf.filename or "uploaded.pdf"
        resolved_pdf_id = sha1_bytes(b)[:16]
        library_pdf_path = LIBRARY_DIR / f"{resolved_pdf_id}.pdf"
        library_pdf_path.write_bytes(b)
        (library_pdf_path.with_suffix(".json")).write_text(
            json.dumps({"name": input_name}, indent=2),
            encoding="utf-8",
        )
    else:
        p = LIBRARY_DIR / f"{pdf_id}.pdf"
        if not p.exists():
            raise HTTPException(404, "Unknown pdf_id")
        shutil.copyfile(p, input_pdf_path)
        input_name = p.name
        meta_path = p.with_suffix(".json")
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                input_name = meta.get("name", input_name)
            except json.JSONDecodeError:
                pass
        resolved_pdf_id = pdf_id

    merged = (
        f"Agent profile defaults: agent_name={profile.get('agent_name')}, "
        f"brokerage={profile.get('brokerage')}, "
        f"default_fee={profile.get('default_fee','')}, "
        f"default_retainer={profile.get('default_retainer','')}, "
        f"default_dual_agency={profile.get('default_dual_agency','')}. "
        f"User instruction: {instruction}"
    )

    set_status(job_dir, "queued", 0, "Queued")

    def run_pipeline():
        try:
            set_status(job_dir, "running", 8, "Preparing…")

            pdf_id_local = resolved_pdf_id
            map_dir = MAPPINGS_DIR / pdf_id_local
            map_dir.mkdir(parents=True, exist_ok=True)

            # Files created by extract_form_fields.py in the job dir
            input_stem = input_pdf_path.stem
            script_map_csv = job_dir / f"{input_stem}_map.csv"
            script_final_pdf = job_dir / f"{input_stem}_final.pdf"

            # Destination files in the mapping directory
            annotated = map_dir / "annotated.pdf"
            map_csv = map_dir / "map.csv"
            rich_csv = map_dir / "map_rich.csv"

            # 1) Mapping phase (if unknown)
            if not rich_csv.exists():
                set_status(job_dir, "running", 18, "Extracting fields & creating visual reference…")

                # Calls extract_form_fields.py
                r1 = subprocess.run(
                    [sys.executable, str(EXTRACT), str(input_pdf_path)],
                    capture_output=True,
                    text=True,
                    cwd=str(job_dir),
                )
                if r1.returncode != 0:
                    raise RuntimeError(f"extract_form_gem4 failed:\n{r1.stderr}\n{r1.stdout}")

                try:
                    # Move the generated files to the centralized mapping folder
                    # Note: We rename _final.pdf to annotated.pdf for consistency with the UI
                    if script_map_csv.exists():
                        shutil.copyfile(script_map_csv, map_csv)
                    if script_final_pdf.exists():
                        shutil.copyfile(script_final_pdf, annotated)
                except Exception as e:
                    raise RuntimeError(f"Failed to move mapping outputs: {e}")

                set_status(job_dir, "running", 38, "Labeling fields with Gemini 3 Vision…")

                # Calls label_from_vision.py
                # Note: Pass the *annotated* (red number) PDF
                r2 = subprocess.run(
                    [sys.executable, str(LABEL), str(annotated), str(map_csv)],
                    capture_output=True,
                    text=True,
                )
                if r2.returncode != 0:
                    raise RuntimeError(f"label_from_vision failed:\n{r2.stderr}\n{r2.stdout}")

                # Wait for user confirmation (local mapping GUI would happen here in a desktop app)
                # For web app, we skip GUI but allow viewing the annotated PDF
                set_status(
                    job_dir,
                    "needs_mapping",
                    45,
                    "Mapped. Generating fill plan next...",
                    extra={
                        "pdf_id": pdf_id_local,
                        "annotated_url": f"/api/mappings/{token}/{pdf_id_local}/annotated",
                        "rich_map_url": f"/api/mappings/{token}/{pdf_id_local}/rich",
                    },
                )
                # In this demo, we auto-proceed, but you could stop here.

            # 2) Fill phase
            set_status(job_dir, "running", 55, "Generating fill JSON (Gemini 3)…")
            fill_json = job_dir / "fill_plan.json"

            # Calls generate_fill_json.py
            # IMPORTANT: Pass 'annotated' PDF for visual context + 'rich_csv' for labels
            r3 = subprocess.run(
                [
                    sys.executable,
                    str(GENFILL),
                    "--csv",
                    str(rich_csv),
                    "--pdf",
                    str(annotated),
                    "--instruction",
                    merged,
                    "--out",
                    str(fill_json),
                ],
                capture_output=True,
                text=True,
            )
            if r3.returncode != 0:
                raise RuntimeError(f"generate_fill_json failed:\n{r3.stderr}\n{r3.stdout}")

            set_status(job_dir, "running", 78, "Native filling (AcroForms)…")
            filled_pdf = job_dir / "filled.pdf"

            # Calls native_fill.py
            # IMPORTANT: Pass 'input_pdf_path' (Original Clean PDF) for final output
            # + 'rich_csv' for coordinates + 'fill_json' for values
            r4 = subprocess.run(
                [
                    sys.executable,
                    str(NATIVE),
                    "--pdf",
                    str(input_pdf_path),
                    "--csv",
                    str(rich_csv),
                    "--plan",
                    str(fill_json),
                    "--out",
                    str(filled_pdf),
                ],
                capture_output=True,
                text=True,
            )
            if r4.returncode != 0:
                raise RuntimeError(f"native_fill failed:\n{r4.stderr}\n{r4.stdout}")

            # 3) Save to completed
            done_id = job_id
            done_dir = DONE_DIR / done_id
            done_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(filled_pdf, done_dir / "filled.pdf")
            shutil.copyfile(fill_json, done_dir / "fill_plan.json")

            meta = {
                "id": done_id,
                "title": f"{input_name} • {profile.get('agent_name','')}",
                "created_at": time.time(),
                "pdf_url": f"/api/completed/{token}/{done_id}/pdf",
                "json_url": f"/api/completed/{token}/{done_id}/json",
            }
            (done_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

            set_status(
                job_dir,
                "done",
                100,
                "Done.",
                extra={
                    "pdf_url": meta["pdf_url"],
                    "json_url": meta["json_url"],
                },
            )

        except Exception as e:
            # Print full stack trace to logs for debugging
            import traceback

            traceback.print_exc()
            set_status(job_dir, "error", 100, str(e))

    threading.Thread(target=run_pipeline, daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{token}/{job_id}/status")
def job_status(token: str, job_id: str):
    load_profile(token)
    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        raise HTTPException(404, "Unknown job")
    return read_status(job_dir)


@app.get("/api/completed/{token}/{doc_id}/pdf")
def completed_pdf(token: str, doc_id: str):
    load_profile(token)
    p = DONE_DIR / doc_id / "filled.pdf"
    if not p.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(str(p), media_type="application/pdf", filename="filled.pdf")


@app.get("/api/completed/{token}/{doc_id}/json")
def completed_json(token: str, doc_id: str):
    load_profile(token)
    p = DONE_DIR / doc_id / "fill_plan.json"
    if not p.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(str(p), media_type="application/json", filename="fill_plan.json")


@app.get("/api/mappings/{token}/{pdf_id}/annotated")
def mapping_annotated(token: str, pdf_id: str):
    load_profile(token)
    p = MAPPINGS_DIR / pdf_id / "annotated.pdf"
    if not p.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(str(p), media_type="application/pdf", filename="annotated.pdf")


@app.get("/api/mappings/{token}/{pdf_id}/rich")
def mapping_rich(token: str, pdf_id: str):
    load_profile(token)
    p = MAPPINGS_DIR / pdf_id / "map_rich.csv"
    if not p.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(str(p), media_type="text/csv", filename="map_rich.csv")


@app.post("/api/mappings/{token}/{pdf_id}/save")
async def save_mapping(token: str, pdf_id: str, request: Request):
    load_profile(token)
    body = await request.body()
    csv_text = body.decode("utf-8")

    map_dir = MAPPINGS_DIR / pdf_id
    if not map_dir.exists():
        raise HTTPException(404, "Mapping not found")

    (map_dir / "map_rich.csv").write_text(csv_text, encoding="utf-8")
    return {"ok": True}
