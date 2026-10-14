"""
zenodo_dedupe.py - Find and delete duplicate Zenodo depositions.

For each paper tracked in .zenodo_dois.json, this script looks for OTHER
Zenodo depositions with the SAME TITLE but a DIFFERENT concept record.
Those are the duplicates (from past runs when the state file was lost or
reset). Only those are reported - unrelated depositions on the account
are left alone.

Usage:
    python zenodo_dedupe.py                  # list duplicates (safe)
    python zenodo_dedupe.py --list           # same, explicit
    python zenodo_dedupe.py --delete         # delete with confirmation
    python zenodo_dedupe.py --delete --yes   # delete without prompting
    python zenodo_dedupe.py --sandbox        # against sandbox
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ZENODO_TOKEN = os.environ.get("ZENODO_TOKEN", "").strip()

REPO_DIR     = Path(__file__).resolve().parent
DOI_FILE     = REPO_DIR / ".zenodo_dois.json"

ZENODO_API   = "https://zenodo.org/api"
SANDBOX_API  = "https://sandbox.zenodo.org/api"


def safe_print(msg):
    try:
        print(msg)
    except Exception:
        print(msg.encode("ascii", "replace").decode("ascii"))


def api_request(url, method="GET", payload=None, token=None, allow_404=False):
    data = None
    headers = {"Accept": "application/json", "User-Agent": "zenodo-dedupe"}
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
        raise RuntimeError("HTTP %d %s\n    URL: %s\n    Body:\n%s"
                           % (e.code, e.reason, url, err_body))


def load_state():
    if DOI_FILE.exists():
        try:
            return json.loads(DOI_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def trailing_int(s):
    if s is None:
        return None
    m = re.search(r"(\d+)\s*$", str(s))
    return int(m.group(1)) if m else None


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


def get_all_depositions(api_base, token):
    depositions = []
    page = 1
    while True:
        url = (f"{api_base}/deposit/depositions"
               f"?size=25&page={page}&sort=mostrecent")
        try:
            data = api_request(url, token=token)
        except Exception as e:
            safe_print(f"[ERROR] listing depositions page {page}: {e}")
            break
        if not isinstance(data, list) or not data:
            break
        depositions.extend(data)
        if len(data) < 25:
            break
        page += 1
    return depositions


def delete_deposition(api_base, dep_id, token):
    url = f"{api_base}/deposit/depositions/{dep_id}"
    try:
        api_request(url, method="DELETE", token=token, allow_404=True)
        return True, ""
    except Exception as e:
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", action="store_true",
                        help="Use sandbox instead of production")
    parser.add_argument("--delete", action="store_true",
                        help="Actually delete (default is list-only)")
    parser.add_argument("--yes", action="store_true",
                        help="Skip confirmation prompt")
    parser.add_argument("--list", action="store_true",
                        help="List only (default; explicit no-op flag)")
    args = parser.parse_args()

    api_base = SANDBOX_API if args.sandbox else ZENODO_API
    env_name = "sandbox" if args.sandbox else "production"

    if not ZENODO_TOKEN:
        safe_print("[FATAL] ZENODO_TOKEN is not set.")
        return 1

    safe_print("=" * 60)
    safe_print(" Zenodo Duplicate Cleanup")
    safe_print("=" * 60)
    safe_print(f"Environment:  {env_name}")
    safe_print(f"API:          {api_base}")
    safe_print(f"State file:   {DOI_FILE}")
    safe_print(f"Mode:         {'DELETE' if args.delete else 'LIST-ONLY'}")
    safe_print("=" * 60)
    safe_print("")

    state = load_state()
    if not state:
        safe_print("[ERROR] state file is empty or missing.")
        return 1

    canonical_concepts = {}
    managed_titles = {}

    for name, entry in state.items():
        concept_doi = entry.get("concept_doi") or ""
        cid = trailing_int(concept_doi)
        if cid is None:
            safe_print(f"[WARN] '{name}' has no parseable concept_doi; skipping.")
            continue
        canonical_concepts[cid] = {
            "paper": name,
            "current_id": entry.get("id"),
            "doi": entry.get("doi"),
        }
        managed_titles[normalize_title(title_from_folder(name))] = name

    if not canonical_concepts:
        safe_print("[FATAL] no usable concept_dois in state file.")
        return 1

    safe_print("Managed papers (from state file):")
    for cid, info in canonical_concepts.items():
        safe_print(f"  concept {cid}  [{info['paper']}]")
        safe_print(f"    current deposition: {info['current_id']}")
        safe_print(f"    current doi:        {info['doi']}")
    safe_print("")

    safe_print("Fetching all depositions from Zenodo...")
    try:
        depositions = get_all_depositions(api_base, ZENODO_TOKEN)
    except Exception as e:
        safe_print(f"[FATAL] {e}")
        return 1
    safe_print(f"Found {len(depositions)} deposition(s) on {env_name}.")
    safe_print("")

    if not depositions:
        safe_print("Nothing to inspect. Done.")
        return 0

    keep = []
    orphans = []
    unrelated = 0

    for d in depositions:
        did = d.get("id")
        cid = trailing_int(d.get("conceptrecid"))
        title = (d.get("metadata") or {}).get("title") or "?"
        norm_title = normalize_title(title)
        doi = d.get("doi") or "(none)"
        st = d.get("state", "?")
        rec = {
            "id": did, "concept_id": cid, "title": title,
            "norm_title": norm_title, "doi": doi, "state": st,
        }
        if cid in canonical_concepts:
            keep.append(rec)
        elif norm_title in managed_titles:
            orphans.append(rec)
        else:
            unrelated += 1

    safe_print("=" * 60)
    safe_print(f" KEEP (canonical):          {len(keep)}")
    safe_print(f" ORPHAN (dup of managed):   {len(orphans)}")
    safe_print(f" UNRELATED (ignored):       {unrelated}")
    safe_print("=" * 60)
    safe_print("")

    if keep:
        safe_print("Kept (canonical concepts):")
        for r in keep:
            safe_print(f"  id={r['id']:<10} concept={r['concept_id']:<10} "
                       f"state={r['state']:<12} {r['doi']}")
            safe_print(f"      title: {r['title']}")
        safe_print("")

    if not keep:
        safe_print("[FATAL] KEEP list is empty. Refusing to delete anything.")
        return 1

    if not orphans:
        safe_print("No duplicates found. Nothing to delete.")
        return 0

    safe_print("Duplicates (would be deleted):")
    for r in orphans:
        safe_print(f"  id={r['id']:<10} concept={r['concept_id']:<10} "
                   f"state={r['state']:<12} {r['doi']}")
        safe_print(f"      title: {r['title']}")
        safe_print(f"      edit:  {api_base.replace('/api','')}/deposit/{r['id']}")
    safe_print("")

    if not args.delete:
        safe_print("(list-only mode. Add --delete to actually delete them.)")
        return 0

    safe_print("")
    safe_print("WARNING: deleting a published Zenodo record is permanent.")
    safe_print("")

    if not args.yes:
        try:
            resp = input(f"Delete {len(orphans)} duplicate deposition(s)? [y/N] ")
        except EOFError:
            resp = ""
        if resp.strip().lower() != "y":
            safe_print("Cancelled.")
            return 0

    safe_print("")
    deleted = 0
    failed = 0
    for r in orphans:
        safe_print(f"Deleting id={r['id']} ({r['title']}) ...")
        ok, err = delete_deposition(api_base, r["id"], ZENODO_TOKEN)
        if ok:
            safe_print("  deleted")
            deleted += 1
        else:
            safe_print(f"  [ERROR] {err}")
            failed += 1

    safe_print("")
    safe_print("=" * 60)
    safe_print(f" Done. Deleted: {deleted}  Failed: {failed}")
    if failed:
        safe_print(" Failed items can be removed via the web UI:")
        safe_print(f"   {api_base.replace('/api','')}/me/uploads")
    safe_print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        safe_print("\n[ABORT] cancelled.")
        sys.exit(130)