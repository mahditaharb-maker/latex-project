"""
zenodo_check.py - Verify a Zenodo token against production and sandbox.
"""
import json
import os
import sys
import urllib.error
import urllib.request

PROD    = "https://zenodo.org/api"
SANDBOX = "https://sandbox.zenodo.org/api"

token = os.environ.get("ZENODO_TOKEN", "").strip()
if not token:
    print("[ERROR] ZENODO_TOKEN is not set.")
    sys.exit(1)

print("=" * 60)
print(" Zenodo Token Checker")
print("=" * 60)
print(f"Token length:  {len(token)}")
print(f"Token prefix:  {token[:4]}...")
print()

def probe(label, base):
    url = base + "/deposit/depositions"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8")
            data = json.loads(body) if body else []
            print(f"[OK]    {label}: token accepted ({len(data)} depositions visible)")
            return True
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        print(f"[FAIL]  {label}: HTTP {e.code} {e.reason}")
        if body:
            print(f"        {body[:300]}")
        return False
    except Exception as e:
        print(f"[FAIL]  {label}: {e}")
        return False

print("Probing production (https://zenodo.org)...")
prod_ok = probe("production", PROD)
print()
print("Probing sandbox    (https://sandbox.zenodo.org)...")
sb_ok = probe("sandbox", SANDBOX)
print()

print("=" * 60)
if prod_ok and not sb_ok:
    print(" VERDICT: This is a valid PRODUCTION token.")
    print("          Your earlier 403 was something else — retry the run.")
elif sb_ok and not prod_ok:
    print(" VERDICT: This is a SANDBOX token.")
    print("          You need a DIFFERENT token from https://zenodo.org")
    print("          (no 'sandbox.' in the URL).")
elif prod_ok and sb_ok:
    print(" VERDICT: Token works on BOTH environments (unusual).")
    print("          Retry the production run.")
else:
    print(" VERDICT: Token rejected by BOTH environments.")
    print("          Possible causes:")
    print("            - token is malformed (missed/extra characters)")
    print("            - token was already revoked")
    print("            - token was created without deposit:write scope")
    print("          Create a fresh token at:")
    print("            https://zenodo.org/account/settings/applications/tokens/new/")
print("=" * 60)