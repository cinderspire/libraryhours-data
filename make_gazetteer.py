#!/usr/bin/env python3
"""gazetteer_src.txt -> gazetteer.json (keys are page slugs, tz validated)."""
import json
from pathlib import Path
from zoneinfo import ZoneInfo
H = Path(__file__).resolve().parent
out = {}
for line in (H / "gazetteer_src.txt").read_text().splitlines():
    if not line.strip() or line.startswith("#"):
        continue
    slug, city, region, cc, tz, lat, lon = line.split("|")
    ZoneInfo(tz)
    e = {"city": city, "country": cc, "tz": tz, "lat": float(lat), "lon": float(lon)}
    if region:
        e["region"] = region
    out[f"{slug}-library-hours"] = e
out["birmingham-central-library-opening-times"] = dict(out["birmingham-library-hours"])
out["brisbane-library-opening-hours"] = dict(out["brisbane-library-hours"])
out["christchurch-city-library-opening-hours"] = dict(out["christchurch-library-hours"])
(H / "gazetteer.json").write_text(json.dumps(out, indent=0, ensure_ascii=False, sort_keys=True))
print(len(out), "entries")
