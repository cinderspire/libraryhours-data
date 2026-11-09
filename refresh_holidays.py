"""Recompute only the shared `holidays` table of an already built feed (out/libraries.json) with the
current holidays_calc rules, then rewrite libraries.json, libraries.json.gz and the byte counts in
validation_report.json.  Use after changing holidays_calc.py when the OSM data itself did not change."""
from __future__ import annotations

import datetime as dt
import gzip
import json
import sys
from pathlib import Path

import holidays_calc as hc

HERE = Path(__file__).resolve().parent


def main(out: Path) -> int:
    doc = json.loads((out / "libraries.json").read_text("utf-8"))
    today = dt.date.fromisoformat(doc["generated"])
    need = sorted({lib["country"] for lib in doc["libraries"] if lib.get("ph")})
    doc["holidays"] = {cc: hc.exceptions_for(cc, None, today) for cc in need}
    doc["exceptions_through"] = hc.horizon(today).isoformat()
    raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    (out / "libraries.json").write_text(raw, "utf-8")
    gz = gzip.compress(raw.encode("utf-8"), 9, mtime=0)
    (out / "libraries.json.gz").write_bytes(gz)
    rep = out / "validation_report.json"
    if rep.exists():
        r = json.loads(rep.read_text("utf-8"))
        r["bytes_json"], r["bytes_snapshot_gz"] = len(raw.encode()), len(gz)
        rep.write_text(json.dumps(r, indent=1, ensure_ascii=False), "utf-8")
    print(f"holidays for {len(need)} countries, json {len(raw)} bytes, gz {len(gz)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "out"))
