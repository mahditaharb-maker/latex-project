"""Delete one Zenodo deposition by ID. Token read from ZENODO_TOKEN env var."""
import os
import sys
import urllib.request
import urllib.error

if len(sys.argv) != 2:
    print("Usage: zenodo_delete_one.py <deposition_id>")
    sys.exit(2)

token = os.environ.get("ZENODO_TOKEN", "").strip()
if not token:
    print("[ERROR] ZENODO_TOKEN not set.")
    sys.exit(1)

did = sys.argv[1].strip()
url = f"https://zenodo.org/api/deposit/depositions/{did}"

req = urllib.request.Request(url, method="DELETE")
req.add_header("Authorization", "Bearer " + token)

try:
    with urllib.request.urlopen(req, timeout=60) as r:
        print(f"  Deleted (HTTP {r.status}).")
        sys.exit(0)
except urllib.error.HTTPError as e:
    print(f"  HTTP {e.code} {e.reason}")
    try:
        print("  " + e.read().decode("utf-8", errors="replace")[:300])
    except Exception:
        pass
    sys.exit(1)
except Exception as e:
    print(f"  [ERROR] {e}")
    sys.exit(1)