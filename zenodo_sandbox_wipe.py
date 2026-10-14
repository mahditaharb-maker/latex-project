"""
zenodo_sandbox_wipe.py - Delete ALL depositions on Zenodo Sandbox.

Use this to clean up test records. It ONLY runs against the sandbox
(https://sandbox.zenodo.org) - it refuses to touch production.

Usage:
    python zenodo_sandbox_wipe.py            # list only, safe
    python zenodo_sandbox_wipe.py --delete   # delete all
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SANDBOX_API = "https://sandbox.zenodo.org/api"
ZENODO_TOKEN = os.environ.get("ZENODO_TOKEN", "").strip()


def safe_print(msg):
    try:
        print(msg)
    except Exception:
        print(msg.encode("ascii", "replace").decode("ascii"))


def api(url, method="GET", token=None):
    """Make an API request, passing the token both as header and query param."""
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
        # also append as query parameter - older Zenodo deployments need this
        sep = "&" if "?" in url else "?"
        url = url + sep + "access_token=" + urllib.parse.quote(token, safe="")

    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError("HTTP %d %s: %s" % (e.code, e.reason, body[:400]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", action="store_true",
                    help="Actually delete (default: list only)")
    ap.add_argument("--yes", action="store_true",
                    help="Skip confirmation")
    args = ap.parse_args()

    if not ZENODO_TOKEN:
        safe_print("[FATAL] ZENODO_TOKEN not set.")
        return 1

    safe_print("=" * 60)
    safe_print(" Zenodo SANDBOX wipe")
    safe_print("=" * 60)
    safe_print(f"API:   {SANDBOX_API}")
    safe_print(f"Mode:  {'DELETE' if args.delete else 'LIST-ONLY'}")
    safe_print("=" * 60)
    safe_print("")

    # Safety: this script must never hit production
    if "sandbox" not in SANDBOX_API:
        safe_print("[FATAL] refusing to run - API is not sandbox.")
        return 1

    # ---- 1. Sanity-check the token against /deposit/depositions ----
    safe_print("Checking token against sandbox...")
    try:
        api(f"{SANDBOX_API}/deposit/depositions?size=1", token=ZENODO_TOKEN)
        safe_print("Token accepted.")
    except Exception as e:
        safe_print(f"[ERROR] token check failed: {e}")
        safe_print("")
        safe_print("Possible causes:")
        safe_print("  - token is a PRODUCTION token, not a sandbox token")
        safe_print("  - token is expired or revoked")
        safe_print("  - token has no 'deposit:write' scope")
        safe_print("")
        safe_print("Get a fresh sandbox token at:")
        safe_print("  https://sandbox.zenodo.org/account/settings/applications/tokens/new/")
        return 1
    safe_print("")

    # ---- 2. List all depositions ----
    safe_print("Fetching all sandbox depositions...")
    all_deps = []
    page = 1
    while True:
        url = f"{SANDBOX_API}/deposit/depositions?size=25&page={page}"
        try:
            data = api(url, token=ZENODO_TOKEN)
        except Exception as e:
            safe_print(f"[ERROR] listing page {page}: {e}")
            break
        if not isinstance(data, list) or not data:
            break
        all_deps.extend(data)
        if len(data) < 25:
            break
        page += 1

    safe_print(f"Found {len(all_deps)} deposition(s) on sandbox.")
    safe_print("")

    if not all_deps:
        safe_print("Nothing to clean. Done.")
        return 0

    for d in all_deps:
        did = d.get("id")
        title = (d.get("metadata") or {}).get("title") or "?"
        st = d.get("state", "?")
        safe_print(f"  id={did:<10} state={st:<12} {title[:60]}")

    if not args.delete:
        safe_print("")
        safe_print("(list-only. Add --delete to actually delete them.)")
        return 0

    safe_print("")
    if not args.yes:
        try:
            resp = input(f"Delete ALL {len(all_deps)} sandbox deposition(s)? [y/N] ")
        except EOFError:
            resp = ""
        if resp.strip().lower() != "y":
            safe_print("Cancelled.")
            return 0

    safe_print("")
    deleted = 0
    failed = 0
    for d in all_deps:
        did = d.get("id")
        safe_print(f"Deleting id={did} ...")
        try:
            api(f"{SANDBOX_API}/deposit/depositions/{did}",
                method="DELETE", token=ZENODO_TOKEN)
            safe_print("  deleted")
            deleted += 1
        except Exception as e:
            safe_print(f"  [ERROR] {e}")
            failed += 1

    safe_print("")
    safe_print("=" * 60)
    safe_print(f" Done. Deleted: {deleted}  Failed: {failed}")
    safe_print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        safe_print("\n[ABORT] cancelled.")
        sys.exit(130)