import pikepdf
import re

def clean_pdf_aggressive(input_filename, output_filename):
    # Open PDF
    pdf = pikepdf.Pdf.open(input_filename, allow_overwriting_input=True)
    print(f"Processing: {input_filename}")

    # --- 1. KILL THE "SMART" FORM (XFA) ---
    # TTB forms are often "Hybrid". If we delete the XFA key, 
    # it forces the PDF viewer to use the simple "blue box" AcroForm instead.
    if "/AcroForm" in pdf.Root:
        acroform = pdf.Root.AcroForm
        if "/XFA" in acroform:
            del acroform["/XFA"]
            print(" - Removed XFA 'Smart Form' layer (Disables complex validation)")

    # --- 2. REMOVE SCRIPTS (Pop-ups) ---
    # Remove global JavaScript and OpenActions that trigger on startup
    if "/OpenAction" in pdf.Root:
        del pdf.Root["/OpenAction"]
        print(" - Removed OpenAction (Startup scripts)")
    
    if "/Names" in pdf.Root and "/JavaScript" in pdf.Root.Names:
        del pdf.Root.Names["/JavaScript"]
        print(" - Removed Embedded JavaScripts")

    # --- 3. REMOVE BUTTONS & "REQUIRED" WARNINGS ---
    total_removed = 0
    
    for page_num, page in enumerate(pdf.pages):
        if "/Annots" not in page:
            continue
        
        new_annots = []
        for annot in page.Annots:
            should_keep = True
            
            # Get basic info
            subtype = annot.get("/Subtype")
            field_name = str(annot.get("/T", ""))
            
            # CHECK A: Is it a Print/Reset Button?
            if subtype == "/Widget" and annot.get("/FT") == "/Btn":
                # Delete if it has "Print", "Reset", or "Submit" in the name
                if any(x in field_name.lower() for x in ["print", "reset", "submit", "clear"]):
                    print(f"   [Page {page_num+1}] Removing Button: {field_name}")
                    should_keep = False

            # CHECK B: Is it a "REQUIRED" text warning?
            # We look for fields named "Required", "Warning" OR fields that display that text.
            # Many TTB warnings are just Text Fields (/Tx) named things like "Text1.0.1"
            # so we check their Default Value (/V) or Default Appearance (/DA) for red color.
            
            # Check field name for "Required"
            if "required" in field_name.lower() or "warning" in field_name.lower():
                print(f"   [Page {page_num+1}] Removing Warning Field (by Name): {field_name}")
                should_keep = False
            
            # Check field content (Value) for "REQUIRED"
            val = str(annot.get("/V", ""))
            if "REQUIRED" in val.upper():
                print(f"   [Page {page_num+1}] Removing Warning Field (by Content): {field_name}")
                should_keep = False

            # CHECK C: Is it RED text? (Common for warnings)
            # We look at the /DA (Default Appearance) string.
            # Red is usually "1 0 0 rg" (RGB) or "0 1 1 0 k" (CMYK)
            da_str = str(annot.get("/DA", ""))
            if "1 0 0 rg" in da_str or "1 0 0 RG" in da_str:
                # Double check it's not a field YOU fill in (usually you type in black 0 g)
                # Most warnings are read-only (/Ff 1)
                flags = int(annot.get("/Ff", 0))
                if flags & 1: # If ReadOnly bit is set
                    print(f"   [Page {page_num+1}] Removing Red Read-Only Field: {field_name}")
                    should_keep = False

            if should_keep:
                new_annots.append(annot)

        page.Annots = new_annots
        total_removed += (len(page.Annots) - len(new_annots)) * -1

    # Save
    pdf.save(output_filename)
    print(f"\nDone! Saved as: {output_filename}")

# --- EXECUTE ---
# Make sure to verify your filename matches exactly!
clean_pdf_aggressive('f500024sm.pdf', 'cleaned_aggressive.pdf')