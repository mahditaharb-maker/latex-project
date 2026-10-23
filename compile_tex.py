"""
compile_tex.py - Compile all .tex files in document/ to .pdf locally.

Usage:
    python compile_tex.py            # Skips PDFs that are already up to date
    python compile_tex.py --force    # Force compile everything
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# ============ CONFIGURATION ============
LATEX_EXE = r"E:\archives\Processings\Doctorate\texlive\2024\bin\windows\pdflatex.exe"
DOCUMENT_DIR = Path(__file__).resolve().parent / "document"
# =======================================


def safe_print(msg):
    try:
        print(msg)
    except Exception:
        print(msg.encode("ascii", "replace").decode("ascii"))


def clean_aux_files(tex_path):
    """Remove temporary LaTeX files after compilation."""
    extensions = [".aux", ".log", ".out", ".toc", ".synctex.gz", ".fls", ".fdb_latexmk"]
    for ext in extensions:
        aux_file = tex_path.with_suffix(ext)
        if aux_file.exists():
            try:
                aux_file.unlink()
            except Exception:
                pass


def compile_tex(tex_path):
    """Compile a single .tex file. Returns True on success."""
    
    for run in range(1, 3):
        safe_print(f"    pass {run}/2...")
        cmd = [
            LATEX_EXE,
            "-interaction=nonstopmode",
            "-halt-on-error",
            tex_path.name
        ]
        
        try:
            result = subprocess.run(
                cmd,
                cwd=str(tex_path.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace"
            )
            
            if result.returncode != 0:
                safe_print(f"    [ERROR] Compilation failed on pass {run}.")
                lines = result.stdout.splitlines()
                for line in lines[-10:]:
                    if line.strip():
                        safe_print(f"      {line}")
                clean_aux_files(tex_path)
                return False
                
        except Exception as e:
            safe_print(f"    [ERROR] Could not run {LATEX_EXE}: {e}")
            return False

    safe_print(f"    -> OK: {tex_path.with_suffix('.pdf').name}")
    clean_aux_files(tex_path)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Force compilation even if PDF exists")
    args = parser.parse_args()

    if not os.path.exists(LATEX_EXE):
        safe_print(f"[FATAL] LaTeX executable not found at:\n        {LATEX_EXE}")
        return 1

    if not DOCUMENT_DIR.exists():
        safe_print(f"[ERROR] Directory not found: {DOCUMENT_DIR}")
        return 1

    tex_files = [
        p for p in DOCUMENT_DIR.rglob("*.tex")
        if not any(part.startswith(".") for part in p.parts)
    ]

    if not tex_files:
        safe_print("[INFO] No .tex files found in document/")
        return 0

    safe_print("=" * 60)
    safe_print(f" Found {len(tex_files)} .tex file(s)")
    safe_print(f" Using TeX Live: {LATEX_EXE}")
    safe_print("=" * 60)
    safe_print("")

    success_count = 0
    fail_count = 0
    skip_count = 0

    for tex_path in tex_files:
        pdf_path = tex_path.with_suffix(".pdf")
        
        # Check if PDF exists and is newer than the .tex file
        if not args.force and pdf_path.exists():
            if pdf_path.stat().st_mtime > tex_path.stat().st_mtime:
                safe_print(f"  [skip]    {tex_path.relative_to(DOCUMENT_DIR.parent)} (PDF is up-to-date)")
                skip_count += 1
                safe_print("")
                continue
                
        safe_print(f"  [compile] {tex_path.relative_to(DOCUMENT_DIR.parent)}")
        if compile_tex(tex_path):
            success_count += 1
        else:
            fail_count += 1
        safe_print("")

    safe_print("=" * 60)
    safe_print(f" Done. Compiled: {success_count}  Skipped: {skip_count}  Failed: {fail_count}")
    safe_print("=" * 60)
    
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        safe_print("\n[ABORT] cancelled.")
        sys.exit(130)