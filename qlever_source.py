"""OpenStreetMap libraries via QLever (https://qlever.dev/api/osm-planet), one global SPARQL query.

Why: Overpass is slow and rate limited; QLever answers "all amenity=library with opening_hours" for the whole
planet (~29,000 objects) in seconds.  Data licence is unchanged: (c) OpenStreetMap contributors, ODbL.

Output of fetch() is the same "slim element" list the Overpass path produces
  {"id": "n123", "lat": .., "lon": .., "t": {osm tags}}
so osm_records() in build_feed.py handles both sources.  Country / time zone are resolved per element
(addr:country, else the zone.tab country of the time zone found by timezonefinder)."""
from __future__ import annotations

import gzip
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

ENDPOINT = "https://qlever.dev/api/osm-planet"
UA = "LibraryHoursFeed/1.0 (+https://cinderspire.github.io/support/libraryhours.html)"
HERE = Path(__file__).resolve().parent

# (SPARQL variable, OSM tag).  Order is the column order of the result.
FIELDS = [
    ("name", "name"), ("nameen", "name:en"), ("city", "addr:city"), ("town", "addr:town"), ("village", "addr:village"),
    ("municipality", "addr:municipality"), ("suburb", "addr:suburb"), ("street", "addr:street"), ("hn", "addr:housenumber"),
    ("pc", "addr:postcode"), ("country", "addr:country"), ("phone", "phone"), ("cphone", "contact:phone"),
    ("website", "website"), ("cwebsite", "contact:website"), ("access", "access"), ("optype", "operator:type"),
]

QUERY = ("PREFIX osmkey: <https://www.openstreetmap.org/wiki/Key:>\n"
         "PREFIX geo: <http://www.opengis.net/ont/geosparql#>\n"
         "SELECT ?osm ?oh ?wkt " + " ".join(f"?{v}" for v, _ in FIELDS) + " WHERE {\n"
         '  ?osm osmkey:amenity "library" ; osmkey:opening_hours ?oh .\n'
         + "".join(f"  OPTIONAL {{ ?osm osmkey:{tag} ?{v} }}\n" for v, tag in FIELDS)
         + "  ?osm geo:hasGeometry/geo:asWKT ?wkt .\n}")


def centroid(wkt: str) -> Optional[tuple[float, float]]:
    """(lat, lon) of a WKT POINT / LINESTRING / POLYGON / MULTI* (bounding-box centre of its vertices)."""
    nums = re.findall(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", wkt.split("(", 1)[-1])
    if len(nums) < 2:
        return None
    xs = [float(n) for n in nums[0::2]]
    ys = [float(n) for n in nums[1::2]]
    if wkt.strip().upper().startswith("POINT"):
        return ys[0], xs[0]
    return (min(ys) + max(ys)) / 2, (min(xs) + max(xs)) / 2


def _osm_id(uri: str) -> Optional[str]:
    m = re.search(r"openstreetmap\.org/(node|way|relation)/(\d+)$", uri)
    return f"{m.group(1)[0]}{m.group(2)}" if m else None


def rows_to_elements(bindings: list[dict]) -> list[dict]:
    """SPARQL JSON bindings -> slim elements (rows with several values per tag are merged: first wins)."""
    out: dict[str, dict] = {}
    for b in bindings:
        oid = _osm_id(b.get("osm", {}).get("value", ""))
        wkt = b.get("wkt", {}).get("value")
        if not oid or not wkt:
            continue
        ll = centroid(wkt)
        if ll is None:
            continue
        e = out.get(oid)
        if e is None:
            tags = {"opening_hours": b["oh"]["value"], "amenity": "library"}
            e = out[oid] = {"id": oid, "lat": ll[0], "lon": ll[1], "t": tags}
        for v, tag in FIELDS:
            if v in b and tag not in e["t"]:
                e["t"][tag] = b[v]["value"]
        if "opening_hours" in e["t"] and b["oh"]["value"] != e["t"]["opening_hours"]:
            e["t"]["_oh_multi"] = "1"  # several opening_hours values on one object: ambiguous, skipped later
    return list(out.values())


def _post(query: str, timeout: int) -> bytes:
    data = urllib.parse.urlencode({"query": query}).encode()
    req = urllib.request.Request(ENDPOINT, data=data, headers={
        "User-Agent": UA, "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch(cache: Path, max_age_days: float = 2.0, cache_only: bool = False,
          post: Callable[[str, int], bytes] | None = None, sleep: Callable[[float], None] = time.sleep,
          now: float | None = None) -> tuple[Optional[list[dict]], str]:
    """-> (elements | None, 'fresh-cache' | 'fetched' | 'stale-cache' | 'none').  Cache = gzip JSON."""
    post = post or _post
    now = now if now is not None else time.time()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cached = None
    if cache.exists():
        try:
            cached = json.loads(gzip.decompress(cache.read_bytes()))
        except Exception:  # noqa: BLE001
            cached = None
    if cached and now - cached.get("fetched", 0) < max_age_days * 86400:
        return cached["elements"], "fresh-cache"
    if not cache_only:
        delay = 5.0
        for attempt in range(4):
            try:
                data = json.loads(post(QUERY, 900).decode("utf-8"))
                els = rows_to_elements(data["results"]["bindings"])
                if len(els) < 1000:
                    raise ValueError(f"suspiciously small result: {len(els)}")
                cache.write_bytes(gzip.compress(json.dumps({"fetched": now, "elements": els}, ensure_ascii=False,
                                                           separators=(",", ":")).encode("utf-8"), 9, mtime=0))
                return els, "fetched"
            except Exception:  # noqa: BLE001 - network / server error: back off
                sleep(delay)
                delay = min(delay * 3, 120)
    if cached:
        return cached["elements"], "stale-cache"
    return None, "none"


# ----------------------------------------------------------------------------- country / zone resolution
def load_zone_countries(path: Path | None = None) -> dict[str, str]:
    """tz name -> ISO country from zone.tab (the copy next to this file, else the system one)."""
    for p in ([path] if path else []) + [HERE / "zone.tab", Path("/usr/share/zoneinfo/zone.tab")]:
        if p and p.exists():
            out = {}
            for ln in p.read_text("utf-8").splitlines():
                if ln and not ln.startswith("#"):
                    f = ln.split("\t")
                    if len(f) >= 3:
                        out[f[2]] = f[0]
            return out
    return {}


def load_country_names(path: Path | None = None) -> dict[str, str]:
    for p in ([path] if path else []) + [HERE / "iso3166.tab", Path("/usr/share/zoneinfo/iso3166.tab")]:
        if p and p.exists():
            return {f[0]: f[1] for f in (ln.split("\t") for ln in p.read_text("utf-8").splitlines() if ln and not ln.startswith("#")) if len(f) >= 2}
    return {}


_tf = None
_tf_tried = False


def timezone_at(lat: float, lon: float) -> Optional[str]:
    global _tf, _tf_tried
    if not _tf_tried:
        _tf_tried = True
        try:
            from timezonefinder import TimezoneFinder  # type: ignore
            _tf = TimezoneFinder()
        except Exception:  # noqa: BLE001
            _tf = None
    if _tf is None:
        return None
    try:
        return _tf.timezone_at(lat=lat, lng=lon)
    except Exception:  # noqa: BLE001
        return None


# Zones newer than the app's bundled tz database (timezone 0.10.x) -> an equivalent older zone with the same
# present-day offset rules.  America/Coyhaique has no equivalent and is dropped.
TZ_ALIASES = {
    "America/Fort_Nelson": "America/Phoenix", "America/Nuuk": "America/Godthab", "Asia/Barnaul": "Asia/Krasnoyarsk",
    "Asia/Tomsk": "Asia/Krasnoyarsk", "Asia/Famagusta": "Asia/Nicosia", "Asia/Qostanay": "Asia/Almaty",
    "Asia/Yangon": "Asia/Rangoon", "Europe/Astrakhan": "Europe/Samara", "Europe/Saratov": "Europe/Samara",
    "Europe/Ulyanovsk": "Europe/Samara", "Europe/Kirov": "Europe/Moscow",
}
UNSUPPORTED_TZ = {"America/Coyhaique"}


class Resolver:
    """Per-element (country, tz).  Injectable `tz_at` for tests."""

    def __init__(self, tz_at: Callable[[float, float], Optional[str]] | None = None) -> None:
        self.tz_at = tz_at or timezone_at
        self.zone_cc = load_zone_countries()
        self.valid_cc = set(self.zone_cc.values())
        self.names = load_country_names()

    def resolve(self, lat: float, lon: float, addr_country: str | None) -> Optional[tuple[str, str]]:
        tz = self.tz_at(lat, lon)
        if not tz:
            return None
        cc = (addr_country or "").strip().upper()
        if not (len(cc) == 2 and cc in self.valid_cc):
            cc = self.zone_cc.get(tz, "")
        if not cc or tz in UNSUPPORTED_TZ:
            return None
        return cc, TZ_ALIASES.get(tz, tz)
