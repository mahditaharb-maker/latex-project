#!/usr/bin/env python3
"""
zenodo_doi.py - Mint or update Zenodo DOIs for each paper subdirectory.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ============ CONFIGURATION ============
ZENODO_TOKEN = os.environ.get("ZENODO_TOKEN", "").strip()

REPO_DIR       = Path(__file__).resolve().parent
DOCUMENT_DIR   = REPO_DIR / "document"
DOI_FILE       = REPO_DIR / ".zenodo_dois.json"
STRAY_DOI_FILE = REPO_DIR / "zenodo_dois.json"

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

def api_request(url, method="GET", payload=None, token=None, allow_404=False):
    data = None
    headers = {"Accept": "application/json", "User-Agent": "zenodo-doi-script"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
        sep = "&" if "?" in url else "?"
        url = url + sep + "access_token=" + urllib.parse.quote(token, safe="")

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        if e.code == 404 and allow_404:
            return None
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
    # FIX: URL-encode the filename to handle spaces and special characters
    encoded_filename = urllib.parse.quote(filename)
    url = f"{bucket_url}/{encoded_filename}"
    
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

# ... [Keep all the other functions exactly as they are in your file] ...

def load_doi_state():
    if DOI_FILE.exists():
        try:
            return json.loads(DOI_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_doi_state(state):
    DOI_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

def delete_doi_state(reason):
    if DOI_FILE.exists():
        try:
            DOI_FILE.unlink()
            safe_print(f"[{reason}] deleted {DOI_FILE.name}")
        except Exception as e:
            safe_print(f"[{reason}] could not delete {DOI_FILE.name}: {e}")
    else:
        safe_print(f"[{reason}] {DOI_FILE.name} not present, nothing to delete")

def warn_stray_state_file():
    if STRAY_DOI_FILE.exists():
        safe_print(f"[WARN] found stray file '{STRAY_DOI_FILE.name}' (no leading dot).")
        safe_print("       The script uses '.zenodo_dois.json' as its state file.")
        safe_print("       Delete the stray one to avoid confusion:")
        safe_print(f"         del {STRAY_DOI_FILE.name}")
        safe_print("")

def title_from_folder(name):
    return name.replace("-", " ").replace("_", " ").strip()

def normalize_title(s):
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def find_papers(document_dir):
    papers = []
    if not document_dir.exists():
        return papers
    for entry in sorted(document_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        tex_files = list(entry.glob("*.tex"))
        if not tex_files:
            continue
        papers.append((entry.name, entry))
    return papers

def collect_upload_files(paper_dir):
    files = []
    for p in paper_dir.rglob("*"):
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.suffix.lower() in UPLOAD_EXTS:
            files.append(p)
    return files

def compute_paper_hash(paper_dir):
    h = hashlib.sha256()
    files = sorted(collect_upload_files(paper_dir), key=lambda p: str(p.relative_to(paper_dir)).replace("\\", "/").lower())
    for f in files:
        rel = str(f.relative_to(paper_dir)).replace("\\", "/")
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        try:
            with open(f, "rb") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk: break
                    h.update(chunk)
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()

def fetch_deposition_index(api_base, token):
    index = {}
    page = 1
    while True:
        url = f"{api_base}/deposit/depositions?size=25&page={page}&sort=mostrecent"
        try:
            data = api_request(url, token=token)
        except Exception as e:
            safe_print(f"[WARN] could not list depositions: {e}")
            return index
        if not isinstance(data, list) or not data:
            break
        for d in data:
            if d.get("state") != "done":
                continue
            title = (d.get("metadata") or {}).get("title") or ""
            key = normalize_title(title)
            if not key: continue
            if key not in index:
                index[key] = d
        if len(data) < 25:
            break
        page += 1
    return index

def build_metadata(name, paper_dir):
    title = title_from_folder(name)
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
            "related_identifiers": [{"identifier": GITHUB_URL, "relation": "isSupplementTo", "resource_type": "software"}],
            "access_right": "open",
        }
    }

def status_report():
    if not DOCUMENT_DIR.exists():
        safe_print("  (no document/ folder)")
        return 0
    papers = find_papers(DOCUMENT_DIR)
    if not papers:
        safe_print("  (no paper subdirectories found)")
        return 0
    state = load_doi_state()
    ok = stale = new = rebase = 0
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
        if new: safe_print(f"  {new} new paper(s)      -> zenodo_doi.bat")
        if stale: safe_print(f"  {stale} stale paper(s)  -> zenodo_doi.bat (auto-release)")
        if rebase: safe_print(f"  {rebase} paper(s) need  -> zenodo_doi.bat (records baseline)")
    return 0

def verify_stale(name):
    state = load_doi_state()
    entry = state.get(name)
    if entry is None:
        safe_print(f"[verify] '{name}' is not in {DOI_FILE.name}")
        return 2
    stored = entry.get("files_hash")
    if stored is None:
        safe_print(f"[verify] '{name}' has no recorded baseline hash yet")
        return 2
    paper_dir = DOCUMENT_DIR / name
    if not paper_dir.exists() or not paper_dir.is_dir():
        safe_print(f"[verify] paper folder not found: {paper_dir}")
        return 3
    current = compute_paper_hash(paper_dir)
    if current == stored:
        safe_print(f"[verify] '{name}' is unchanged since publish")
        return 1
    safe_print(f"[verify] '{name}' has changed since publish")
    safe_print(f"         Current DOI: {entry.get('doi')}")
    return 0

def create_deposition(api_base, token):
    url = f"{api_base}/deposit/depositions"
    return api_request(url, method="POST", payload={}, token=token)

def publish_deposition(api_base, deposition_id, token):
    url = f"{api_base}/deposit/depositions/{deposition_id}/actions/publish"
    return api_request(url, method="POST", token=token)

def new_version(api_base, deposition_id, token):
    url = f"{api_base}/deposit/depositions/{deposition_id}/actions/newversion"
    return api_request(url, method="POST", token=token)

def find_orphan_by_hash(state, current_hash, papers_present):
    matches = []
    for sname, sentry in state.items():
        if sname in papers_present:
            continue
        if sentry.get("files_hash") == current_hash:
            matches.append((sname, sentry))
    if len(matches) == 1:
        return matches[0]
    return None

def process_paper(name, paper_dir, api_base, token, state, papers_present, dry_run=False, force_new_version=False, auto_release_stale=False, existing_index=None):
    existing = state.get(name)
    current_hash = compute_paper_hash(paper_dir) if not dry_run else None

    if dry_run:
        action = "would create" if not existing else "already has DOI"
        safe_print(f"  [dry-run] {name}: {action}")
        if existing:
            safe_print(f"            DOI: {existing.get('doi')}")
            stored = existing.get("files_hash")
            if stored is None:
                safe_print(f"            baseline not recorded yet")
            elif current_hash == stored:
                safe_print(f"            files unchanged since publish")
            else:
                mode = "would be [update]" if (auto_release_stale or force_new_version) else "would be [stale]"
                safe_print(f"            files CHANGED since publish ({mode})")
        else:
            files = collect_upload_files(paper_dir)
            safe_print(f"            would upload {len(files)} file(s):")
            for f in files[:5]:
                safe_print(f"              {f.relative_to(paper_dir)}")
            if len(files) > 5:
                safe_print(f"              ... and {len(files) - 5} more")
            orphan = find_orphan_by_hash(state, current_hash, papers_present)
            if orphan:
                safe_print(f"            NOTE: content matches orphaned state entry '{orphan[0]}' - live run would ADOPT it")
            elif existing_index is not None:
                folder_title = normalize_title(title_from_folder(name))
                if folder_title in existing_index:
                    safe_print(f"            NOTE: a matching Zenodo record already exists - live run would ADOPT it")
        return None, "dry-run"

    if existing is None:
        orphan = find_orphan_by_hash(state, current_hash, papers_present)
        if orphan:
            sname, sentry = orphan
            safe_print(f"  [adopt]  {name} - content matches orphaned entry")
            safe_print(f"           old name: '{sname}'")
            safe_print(f"           DOI:      {sentry.get('doi')}")
            safe_print(f"           Reusing that DOI instead of creating a new one.")
            del state[sname]
            sentry["files_hash"] = current_hash
            state[name] = sentry
            save_doi_state(state)
            return sentry.get("doi"), "adopted"

        if existing_index is not None:
            folder_title = normalize_title(title_from_folder(name))
            match = existing_index.get(folder_title)
            if match:
                dep_id = match.get("id")
                version_doi = match.get("doi")
                concept_doi = match.get("conceptdoi")
                pub_date = (match.get("metadata") or {}).get("publication_date") or "?"
                safe_print(f"  [adopt]  {name} - found existing record on Zenodo")
                safe_print(f"           DOI:       {version_doi}")
                safe_print(f"           concept:   {concept_doi}")
                safe_print(f"           published: {pub_date}")
                safe_print(f"           Reusing it instead of creating a duplicate.")
                state[name] = {"id": dep_id, "doi": version_doi, "concept_doi": concept_doi, "published": pub_date + " (adopted)", "files_hash": current_hash}
                save_doi_state(state)
                return version_doi, "adopted"

    if not existing:
        mode = "create"
        outcome = "created"
        safe_print(f"  [create] {name} - new deposition")
    else:
        stored = existing.get("files_hash")
        if stored is None:
            existing["files_hash"] = current_hash
            save_doi_state(state)
            safe_print(f"  [skip]   {name} - already has DOI {existing.get('doi')} (baseline recorded)")
            return existing.get("doi"), "skipped"
        if current_hash == stored:
            safe_print(f"  [skip]   {name} - already has DOI {existing.get('doi')}")
            return existing.get("doi"), "skipped"
        if force_new_version or auto_release_stale:
            mode = "version"
            outcome = "updated"
            safe_print(f"  [update] {name} - files changed, publishing new version")
            safe_print(f"           previous:  {existing.get('doi')}")
            safe_print(f"           published: {existing.get('published', '?')}")
        else:
            safe_print(f"  [stale]  {name} - files changed since DOI was minted")
            safe_print(f"           DOI:       {existing.get('doi')}")
            safe_print(f"           published: {existing.get('published', '?')}")
            safe_print(f"           To publish a new version, run:")
            safe_print(f"             zenodo_release.bat {name}")
            return existing.get("doi"), "stale"

    if mode == "create":
        try:
            draft = create_deposition(api_base, token)
        except Exception as e:
            safe_print(f"    [ERROR] create failed:")
            for line in str(e).splitlines():
                safe_print(f"      {line}")
            return None, "failed"
    else:
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
        try:
            draft = api_request(f"{api_base}/deposit/depositions/{draft_id}", token=token)
        except Exception as e:
            safe_print(f"    [ERROR] could not fetch draft: {e}")
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

    if uploaded < len(files):
        safe_print("    [ERROR] Not all files uploaded successfully. Aborting publish.")
        safe_print("            You may need to delete the draft on Zenodo and try again.")
        return None, "failed"

    metadata = build_metadata(name, paper_dir)
    try:
        api_request(f"{api_base}/deposit/depositions/{deposition_id}", method="PUT", payload=metadata, token=token)
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
        "files_hash": current_hash,
    }
    save_doi_state(state)

    write_citation_cff(paper_dir, name, doi, concept_doi)
    return doi, outcome

def write_citation_cff(paper_dir, name, doi, concept_doi):
    title = title_from_folder(name)
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

def sweep_orphaned_state(state, papers_present):
    removed = []
    for sname in list(state.keys()):
        if sname in papers_present:
            continue
        removed.append(sname)
        del state[sname]
    if removed:
        save_doi_state(state)
        safe_print("")
        safe_print("[sweep] removed %d orphaned state entry(ies):" % len(removed))
        for n in removed:
            safe_print("        - %s" % n)
    return removed

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sandbox", action="store_true")
    parser.add_argument("--only", metavar="NAME")
    parser.add_argument("--new-version", action="store_true")
    parser.add_argument("--auto-release-stale", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--reset-on-success", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--verify-stale", metavar="NAME")
    args = parser.parse_args()

    if args.status:
        return status_report()
    if args.verify_stale:
        return verify_stale(args.verify_stale)

    if not ZENODO_TOKEN and not args.dry_run:
        safe_print("[FATAL] ZENODO_TOKEN environment variable is not set.")
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
    if args.new_version: safe_print("             --new-version: forcing new version for all")
    if args.auto_release_stale: safe_print("             --auto-release-stale: stale papers will be updated")
    if args.reset: safe_print("             --reset: state will be wiped before run")
    if args.reset_on_success: safe_print("             --reset-on-success: state will be wiped if all OK")
    safe_print("=" * 60)
    safe_print("")

    warn_stray_state_file()

    if args.reset and not args.dry_run:
        delete_doi_state("reset")

    papers = find_papers(DOCUMENT_DIR)
    if not papers:
        safe_print("[ERROR] No paper subdirectories found under document/")
        return 1

    papers_present = {name for name, _ in papers}
    safe_print(f"Found {len(papers)} paper(s):")
    for name, _ in papers:
        safe_print(f"  - {name}")
    safe_print("")

    state = load_doi_state()

    existing_index = None
    if not args.dry_run:
        safe_print("Fetching existing depositions for adoption check...")
        existing_index = fetch_deposition_index(api_base, ZENODO_TOKEN)
        safe_print(f"Indexed {len(existing_index)} published record(s) by title.")
        safe_print("")

    created = updated = skipped = stale = adopted = failed = 0

    for name, paper_dir in papers:
        if args.only and name != args.only:
            continue
        result, outcome = process_paper(
            name, paper_dir, api_base, ZENODO_TOKEN, state, papers_present,
            dry_run=args.dry_run,
            force_new_version=args.new_version,
            auto_release_stale=args.auto_release_stale,
            existing_index=existing_index,
        )
        if args.dry_run: continue
        if outcome == "created": created += 1
        elif outcome == "updated": updated += 1
        elif outcome == "adopted": adopted += 1
        elif outcome == "skipped": skipped += 1
        elif outcome == "stale": stale += 1
        else: failed += 1

    if not args.dry_run and not args.only:
        sweep_orphaned_state(state, papers_present)

    safe_print("")
    safe_print("=" * 60)
    if args.dry_run:
        safe_print(" Dry run complete. No changes were made.")
    else:
        line = f" Done. Created: {created}  Updated: {updated}  Adopted: {adopted}  Skipped: {skipped}  Failed: {failed}"
        if stale: line += f"  Stale: {stale}"
        safe_print(line)
        if stale:
            safe_print("")
            safe_print(" Stale papers have edits since their DOI was minted.")
            safe_print(" To publish updates, run:")
            safe_print("   zenodo_doi.bat")
        safe_print(f" DOI state saved to: {DOI_FILE}")
    safe_print("=" * 60)

    if args.reset_on_success and not args.dry_run and failed == 0:
        safe_print("")
        delete_doi_state("reset-on-success")
    elif args.reset_on_success and not args.dry_run and failed > 0:
        safe_print("")
        safe_print("[reset-on-success] skipped: %d failure(s) occurred, state file kept." % failed)

    return 0 if failed == 0 else 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        safe_print("\n[ABORT] cancelled.")
        sys.exit(130)