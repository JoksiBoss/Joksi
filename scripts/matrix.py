"""Build the job matrix: enabled creators (or the one picked in Run workflow)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from clipper.common import load_config

channel = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower().lstrip("@")
source = (sys.argv[2] if len(sys.argv) > 2 else "").strip()
cfg = load_config()
if channel:
    ids = [c["id"] for c in cfg if channel in (c["id"].lower(), str(c["channel"]).lower().lstrip("@"))]
elif source:
    ids = [next(c["id"] for c in cfg if c["enabled"])]
else:
    ids = [c["id"] for c in cfg if c["enabled"]]
print("matrix:", ids, file=sys.stderr)
with open(os.environ.get("GITHUB_OUTPUT", "/dev/stdout"), "a") as f:
    f.write(f"matrix={json.dumps(ids)}\nany={'true' if ids else 'false'}\n")
