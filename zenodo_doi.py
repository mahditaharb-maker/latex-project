#!/usr/bin/env python3
"""
zenodo_doi.py - Mint a Zenodo DOI for each paper subdirectory.

Scans document/ for subdirectories. For each subdirectory without a
recorded DOI, creates a Zenodo deposition, uploads the paper's files,
publishes it, and records the resulting DOI.

Change detection:
    Each published paper records a content hash of the files that were
    uploaded. On subsequent runs, the hash of the current files is
    compared to the recorded one. If they differ, the paper is reported
    as [stale] - it still has a DOI, but the published version is out of
    date. To publish a new version, run:

        zenodo_doi.bat --new-version --only <paper-name>

    or, more conveniently:

        zenodo_release.bat <paper-name>

    A new version is never created automatically. That's deliberate -
    you decide when a revision is ready to be published.

Usage:
    python zenodo_doi.py                  # process all new subdirs
    python zenodo_doi.py --dry-run        # show what would be done
    python zenodo_doi.py --sandbox        # use Zenodo sandbox
    python zenodo_doi.py --only PDE       # process one subdirectory
    python zenodo_doi.py --new-version    # create new versions for existing DOIs

    python zenodo_doi.py --status         # report DOI status (no token/API)
    python zenodo_doi.py --verify-stale X # exit 0 if X changed (no token/API)

    python zenodo_doi.py --reset                    # wipe state file first
    python zenodo_doi.py --reset-on-success         # wipe state file if all OK
    python zenodo_doi.py --sandbox --reset-on-success   # typical sandbox use

IMPORTANT: Zenodo sandbox and production use SEPARATE tokens.
    Sandbox token:    https://sandbox.zenodo.org/account/settings/applications/tokens/new/
    Production token: https://zenodo.org/account/settings/applications/tokens/new/

WARNING about --reset-on-success:
    In production, deleting the state file means the NEXT run recreates
    every DOI -> duplicate Zenodo records. Only use it with --sandbox,
    or when you genuinely intend to start over.
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ============ CONFIGURATION ============
ZENODO_TOKEN = os.environ.get("ZENODO_TOKEN", "").strip()

REPO_DIR       = Path(__file__).resolve().parent
DOCUMENT_DIR   = REPO_DIR / "document"
DOI_FILE       = REPO_DIR / ".zenodo_dois.json"

ZENODO_API     = "https://zenodo.org/api"
SANDBOX_API    = "https://sandbox.zenodo.org/api"

GITHUB_USER    = "mahditaharb-maker"
GITHUB_REPO    = "latex-project"
GITHUB_URL     = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}"

UPLOAD_EXTS    = {".pdf", ".tex", ".zip", ".txt"}

CREATORS = [
    {
        "name": "Tahar, Mahdi",
        "affiliation": "University Mohamed Boudiaf of M'Sila",
        "orcid": "0009-0007-2507-7548",
    }
]
LICENSE     = "MIT"
KEYWORDS    = ["LaTeX", "Mathlib", "formalization", "mathematics"]

# =======================================


def safe_print(msg):
    try:
        print(msg)
    except Exception:
        print(msg.encode("ascii", "replace").decode("ascii"))


# ---------- Zenodo API helpers ----------

def api_request(url, method="GET", payload=None, token=None):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    headers = {
        "Accept": "application/json",
        "User-Agent": "zenodo-doi-script",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        detail = err_body
        try:
            detail = json.dumps(json.loads(err_body), indent=2)
        except Exception:
            pass
        raise RuntimeError(
            "HTTP %d %s\n    URL: %s\n    Response body:\n%s"
            % (e.code, e.reason, url, detail)
        )


def upload_file(bucket_url, filepath, token):
    filename = filepath.name
    url = f"{bucket_url}/{filename}"
    with open(filepath, "rb") as fh:
        data = fh.read()
    req = urllib.request.Request(url, data=data, method="PUT")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            return response.status in (200, 201)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        safe_print(f"    [ERROR] upload {filename}: HTTP {e.code} {e.reason}")
        if body:
            safe_print(f"            {body[:400]}")
        return False


# ---------- State ----------

def load_doi_state():
    if DOI_FILE.exists():
        try:
            return json.loads(DOI_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_doi_state(state):
    DOI_FILE.write_text(
        json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
    )


def delete_doi_state(reason):
    if DOI_FILE.exists():
        try:
            DOI_FILE.unlink()
            safe_print(f"[{reason}] deleted {DOI_FILE.name}")
        except Exception as e:
            safe_print(f"[{reason}] could not delete {DOI_FILE.name}: {e}")
    else:
        safe_print(f"[{reason}] {DOI_FILE.name} not present, nothing to delete")


# ---------- Paper discovery ----------

def find_papers(document_dir):
    papers = []
    if not document_dir.exists():
        return papers
    for entry in sorted(document_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        tex_files = list(entry.glob("*.tex"))
        if not tex_files:
            continue
        papers.append((entry.name, entry))
    return papers


def collect_upload_files(paper_dir):
    files = []
    for p in paper_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        if p.suffix.lower() in UPLOAD_EXTS:
            files.append(p)
    return files


def compute_paper_hash(paper_dir):
    h = hashlib.sha256()
    files = sorted(
        collect_upload_files(paper_dir),
        key=lambda p: str(p.relative_to(paper_dir)).replace("\\", "/").lower(),
    )
    for f in files:
        rel = str(f.relative_to(paper_dir)).replace("\\", "/")
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        try:
            with open(f, "rb") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()


# ---------- Metadata ----------

def build_metadata(name, paper_dir):
    title = name.replace("-", " ").replace("_", " ").strip()

    desc = f"Preprint from the {GITHUB_REPO} repository."
    for candidate in ("README.md", "README.txt", "abstract.txt"):
        p = paper_dir / candidate
        if p.exists():
            try:
                desc = p.read_text(encoding="utf-8")[:1000]
                break
            except Exception:
                pass

    return {
        "metadata": {
            "title": title,
            "upload_type": "publication",
            "publication_type": "preprint",
            "description": desc,
            "creators": CREATORS,
            "license": LICENSE,
            "keywords": KEYWORDS,
            "related_identifiers": [
                {
                    "identifier": GITHUB_URL,
                    "relation": "isSupplementTo",
                    "resource_type": "software",
                }
            ],
            "access_right": "open",
        }
    }


# ---------- Status / verify (no token, no API) ----------

def status_report():
    """Print a one-line-per-paper status. Returns exit code 0 always."""
    if not DOCUMENT_DIR.exists():
        safe_print("  (no document/ folder)")
        return 0

    papers = find_papers(DOCUMENT_DIR)
    if not papers:
        safe_print("  (no paper subdirectories found)")
        return 0

    state = load_doi_state()

    ok = 0
    stale = 0
    new = 0
    rebase = 0

    for name, paper_dir in papers:
        entry = state.get(name)
        if entry is None:
            safe_print(f"  [new]    {name}")
            new += 1
            continue

        stored = entry.get("files_hash")
        if stored is None:
            safe_print(f"  [rebase] {name}  (no baseline recorded)")
            rebase += 1
            continue

        current = compute_paper_hash(paper_dir)
        doi = entry.get("doi", "?")

        if current == stored:
            safe_print(f"  [ok]     {name}  {doi}")
            ok += 1
        else:
            safe_print(f"  [stale]  {name}  {doi}")
            stale += 1

    if stale or new or rebase:
        safe_print("")
        if new:
            safe_print(f"  {new} new paper(s)      -> zenodo_doi.bat")
        if stale:
            safe_print(f"  {stale} stale paper(s)  -> zenodo_release.bat <name>")
        if rebase:
            safe_print(f"  {rebase} paper(s) need  -> zenodo_doi.bat (records baseline)")

    return 0


def verify_stale(name):
    """Check whether a paper has changed since its recorded DOI.

    Exit codes:
        0 = stale (changed, ready to release)
        1 = unchanged (nothing to do)
        2 = not tracked / no baseline
        3 = paper folder not found
    """
    state = load_doi_state()
    entry = state.get(name)
    if entry is None:
        safe_print(f"[verify] '{name}' is not in {DOI_FILE.name}")
        safe_print(f"         (not yet published - run zenodo_doi.bat to create it)")
        return 2

    stored = entry.get("files_hash")
    if stored is None:
        safe_print(f"[verify] '{name}' has no recorded baseline hash yet")
        safe_print(f"         Run zenodo_doi.bat once to record it.")
        return 2

    paper_dir = DOCUMENT_DIR / name
    if not paper_dir.exists() or not paper_dir.is_dir():
        safe_print(f"[verify] paper folder not found: {paper_dir}")
        return 3

    current = compute_paper_hash(paper_dir)
    if current == stored:
        safe_print(f"[verify] '{name}' is unchanged since publish")
        safe_print(f"         DOI: {entry.get('doi')}")
        safe_print(f"         Nothing to release.")
        return 1

    safe_print(f"[verify] '{name}' has changed since publish")
    safe_print(f"         Current DOI: {entry.get('doi')}")
    safe_print(f"         Ready to release a new version.")
    return 0


# ---------- Core workflow ----------

def create_deposition(api_base, token):
    url = f"{api_base}/deposit/depositions"
    return api_request(url, method="POST", payload={}, token=token)


def publish_deposition(api_base, deposition_id, token):
    url = f"{api_base}/deposit/depositions/{deposition_id}/actions/publish"
    return api_request(url, method="POST", token=token)


def new_version(api_base, deposition_id, token):
    url = f"{api_base}/deposit/depositions/{deposition_id}/actions/newversion"
    return api_request(url, method="POST", token=token)


def process_paper(name, paper_dir, api_base, token, state, dry_run=False,
                  force_new_version=False):
    existing = state.get(name)

    if dry_run:
        action = "would create" if not existing else "already has DOI"
        safe_print(f"  [dry-run] {name}: {action}")
        if existing:
            safe_print(f"            DOI: {existing.get('doi')}")
            current_hash = compute_paper_hash(paper_dir)
            stored = existing.get("files_hash")
            if stored is None:
                safe_print(f"            baseline not recorded yet")
            elif current_hash == stored:
                safe_print(f"            files unchanged since publish")
            else:
                safe_print(f"            files CHANGED since publish "
                           f"(would be [stale])")
        else:
            files = collect_upload_files(paper_dir)
            safe_print(f"            would upload {len(files)} file(s):")
            for f in files[:5]:
                safe_print(f"              {f.relative_to(paper_dir)}")
            if len(files) > 5:
                safe_print(f"              ... and {len(files) - 5} more")
        return None, "dry-run"

    if existing and not force_new_version:
        current_hash = compute_paper_hash(paper_dir)
        stored = existing.get("files_hash")

        if stored is None:
            existing["files_hash"] = current_hash
            save_doi_state(state)
            safe_print(f"  [skip]   {name} - already has DOI "
                       f"{existing.get('doi')} (baseline recorded)")
            return existing.get("doi"), "skipped"

        if current_hash == stored:
            safe_print(f"  [skip]   {name} - already has DOI "
                       f"{existing.get('doi')}")
            return existing.get("doi"), "skipped"

        safe_print(f"  [stale]  {name} - files changed since DOI was minted")
        safe_print(f"           DOI:       {existing.get('doi')}")
        safe_print(f"           published: {existing.get('published', '?')}")
        safe_print(f"           To publish a new version, run:")
        safe_print(f"             zenodo_release.bat {name}")
        return existing.get("doi"), "stale"

    if existing and force_new_version:
        safe_print(f"  [update] {name} - creating new version")
        try:
            draft = new_version(api_base, existing["id"], token)
        except Exception as e:
            safe_print(f"    [ERROR] new version failed: {e}")
            return None, "failed"
        draft_url = draft.get("links", {}).get("latest_draft", "")
        draft_id = draft_url.rstrip("/").split("/")[-1] if draft_url else None
        if not draft_id:
            safe_print("    [ERROR] could not determine new draft ID")
            return None, "failed"
        deposition_id = draft_id
        try:
            draft = api_request(
                f"{api_base}/deposit/depositions/{draft_id}", token=token
            )
        except Exception as e:
            safe_print(f"    [ERROR] could not fetch draft: {e}")
            return None, "failed"
    else:
        safe_print(f"  [create] {name} - new deposition")
        try:
            draft = create_deposition(api_base, token)
        except Exception as e:
            safe_print(f"    [ERROR] create failed:")
            for line in str(e).splitlines():
                safe_print(f"      {line}")
            return None, "failed"
        deposition_id = draft["id"]

    bucket_url = draft.get("links", {}).get("bucket")
    if not bucket_url:
        safe_print("    [ERROR] no bucket URL in deposition response")
        return None, "failed"

    files = collect_upload_files(paper_dir)
    safe_print(f"    uploading {len(files)} file(s)...")
    uploaded = 0
    for f in files:
        if upload_file(bucket_url, f, token):
            uploaded += 1
            safe_print(f"      OK  {f.relative_to(paper_dir)}")
        else:
            safe_print(f"      ERR {f.relative_to(paper_dir)}")
    safe_print(f"    uploaded {uploaded}/{len(files)} file(s)")

    metadata = build_metadata(name, paper_dir)
    try:
        api_request(
            f"{api_base}/deposit/depositions/{deposition_id}",
            method="PUT",
            payload=metadata,
            token=token,
        )
    except Exception as e:
        safe_print(f"    [ERROR] metadata update failed:")
        for line in str(e).splitlines():
            safe_print(f"      {line}")
        return None, "failed"

    try:
        published = publish_deposition(api_base, deposition_id, token)
    except Exception as e:
        safe_print(f"    [ERROR] publish failed:")
        for line in str(e).splitlines():
            safe_print(f"      {line}")
        return None, "failed"

    doi = published.get("doi")
    concept_doi = published.get("conceptdoi")
    safe_print(f"    published: {doi}")
    if concept_doi:
        safe_print(f"      concept DOI (cite this): {concept_doi}")

    state[name] = {
        "id": deposition_id,
        "doi": doi,
        "concept_doi": concept_doi,
        "published": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files_hash": compute_paper_hash(paper_dir),
    }
    save_doi_state(state)

    write_citation_cff(paper_dir, name, doi, concept_doi)
    return doi, "created"


def write_citation_cff(paper_dir, name, doi, concept_doi):
    title = name.replace("-", " ").replace("_", " ").strip()
    cff = f"""cff-version: 1.2.0
title: "{title}"
message: "If you use this work, please cite it as below."
type: software
authors:
  - family-names: Tahar
    given-names: Mahdi
    affiliation: "University Mohamed Boudiaf of M'Sila"
    orcid: "https://orcid.org/0009-0007-2507-7548"
license: MIT
identifiers:
  - type: doi
    value: {concept_doi or doi}
    description: "Zenodo DOI for this work"
"""
    (paper_dir / "CITATION.cff").write_text(cff, encoding="utf-8")


# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done, take no action")
    parser.add_argument("--sandbox", action="store_true",
                        help="Use Zenodo sandbox instead of production")
    parser.add_argument("--only", metavar="NAME",
                        help="Process only this subdirectory")
    parser.add_argument("--new-version", action="store_true",
                        help="Create new versions for papers that already have DOIs")
    parser.add_argument("--reset", action="store_true",
                        help="Delete .zenodo_dois.json BEFORE running")
    parser.add_argument("--reset-on-success", action="store_true",
                        help="Delete .zenodo_dois.json AFTER a fully successful run")
    parser.add_argument("--status", action="store_true",
                        help="Report DOI status of all papers (no token, no API)")
    parser.add_argument("--verify-stale", metavar="NAME",
                        help="Check if a paper's files changed since publishing "
                             "(exit 0 if changed, 1 if unchanged, 2 if untracked)")
    args = parser.parse_args()

    # Short-circuit modes: no token, no API, no banner
    if args.status:
        return status_report()
    if args.verify_stale:
        return verify_stale(args.verify_stale)

    if not ZENODO_TOKEN and not args.dry_run:
        safe_print("[FATAL] ZENODO_TOKEN environment variable is not set.")
        safe_print("        set ZENODO_TOKEN=your_token_here")
        return 1

    api_base = SANDBOX_API if args.sandbox else ZENODO_API
    env_name = "sandbox" if args.sandbox else "production"

    safe_print("=" * 60)
    safe_print(" Zenodo DOI Automation")
    safe_print("=" * 60)
    safe_print(f"Environment:  {env_name}")
    safe_print(f"API:          {api_base}")
    safe_print(f"Papers dir:   {DOCUMENT_DIR}")
    safe_print(f"DOI state:    {DOI_FILE}")
    safe_print(f"Mode:         {'DRY-RUN' if args.dry_run else 'LIVE'}")
    if args.new_version:
        safe_print("             new versions enabled")
    if args.reset:
        safe_print("             --reset: state will be wiped before run")
    if args.reset_on_success:
        safe_print("             --reset-on-success: state will be wiped if all OK")
        if not args.sandbox:
            safe_print("             WARNING: production + reset-on-success will")
            safe_print("                      cause duplicate DOIs on next run.")
    safe_print("=" * 60)
    safe_print("")

    if args.reset and not args.dry_run:
        delete_doi_state("reset")

    papers = find_papers(DOCUMENT_DIR)
    if not papers:
        safe_print("[ERROR] No paper subdirectories found under document/")
        return 1

    safe_print(f"Found {len(papers)} paper(s):")
    for name, _ in papers:
        safe_print(f"  - {name}")
    safe_print("")

    state = load_doi_state()

    created = 0
    skipped = 0
    stale   = 0
    failed  = 0

    for name, paper_dir in papers:
        if args.only and name != args.only:
            continue

        result, outcome = process_paper(
            name, paper_dir, api_base, ZENODO_TOKEN, state,
            dry_run=args.dry_run,
            force_new_version=args.new_version,
        )

        if args.dry_run:
            continue
        if outcome == "created":
            created += 1
        elif outcome == "skipped":
            skipped += 1
        elif outcome == "stale":
            stale += 1
        else:
            failed += 1

    safe_print("")
    safe_print("=" * 60)
    if args.dry_run:
        safe_print(" Dry run complete. No changes were made.")
    else:
        line = f" Done. Created: {created}  Skipped: {skipped}  Failed: {failed}"
        if stale:
            line += f"  Stale: {stale}"
        safe_print(line)
        if stale:
            safe_print("")
            safe_print(" Stale papers have edits since their DOI was minted.")
            safe_print(" To publish an update, run:")
            safe_print("   zenodo_release.bat <paper-name>")
        safe_print(f" DOI state saved to: {DOI_FILE}")
    safe_print("=" * 60)

    if args.reset_on_success and not args.dry_run and failed == 0:
        safe_print("")
        delete_doi_state("reset-on-success")
    elif args.reset_on_success and not args.dry_run and failed > 0:
        safe_print("")
        safe_print("[reset-on-success] skipped: %d failure(s) occurred, "
                   "state file kept." % failed)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        safe_print("\n[ABORT] cancelled.")
        sys.exit(130)