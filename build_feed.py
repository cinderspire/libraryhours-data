#!/usr/bin/env python3
"""Build libraries.json for the Library Hours companion app from https://libraryhours24.com.

The site publishes no machine-readable data (its /api-documentation page describes an API that
does not exist: api.libraryhours24.com does not resolve), so this builder reads the public HTML
pages listed in /sitemap.xml and extracts library records from three page templates:

  * "quick actions" pages: official name, directions address, phone, website + the first weekly
    hours table of the page (the system's main library);
  * "address / opening hours 2026" sections (<address> + table);
  * the generated "library-card id=lib-N" sets.  Their addresses, postcodes and phones are
    internally inconsistent (e.g. Adelaide with a Victorian postcode and phone prefix) so they are
    REJECTED by default (--include-generated keeps them, flagged conf="generated").

Every record is validated (IANA tz, 7 parsed days, plausible coordinates).  Records that fail
are listed in validation_report.json and are not published.

Usage:
  python3 build_feed.py --out out/                 # live site (polite, retries on 429)
  python3 build_feed.py --pages-dir cache/ --out out/   # offline, from saved pages
  python3 build_feed.py --geocode 400 ...          # fill geocache.json with Nominatim (<= N lookups)
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hoursparse as hp  # noqa: E402
import osm_source as osm  # noqa: E402
import qlever_source as ql  # noqa: E402
import regions as rg  # noqa: E402
import holidays_calc as hc  # noqa: E402

HERE = Path(__file__).resolve().parent
SITE = "https://libraryhours24.com"
UA = "LibraryHoursFeed/1.0 (+https://cinderspire.github.io/support/libraryhours.html)"
SCHEMA_VERSION = 1


# --------------------------------------------------------------------------- fetching
def http_get(url: str, tries: int = 6) -> str:
    delay = 3.0
    for _ in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 - 429 / transient network errors
            code = getattr(e, "code", None)
            if code in (404, 410):
                return ""
            time.sleep(delay)
            delay = min(delay * 1.6, 30)
    return ""


def list_pages(pages_dir: Path | None) -> dict[str, str]:
    """slug -> html for every '*-library-hours' style page."""
    pages: dict[str, str] = {}
    if pages_dir:
        for f in sorted(pages_dir.glob("*.html")):
            pages[f.stem] = f.read_text("utf-8", "replace")
        return pages
    sm = http_get(f"{SITE}/sitemap.xml")
    urls = re.findall(r"<loc>(.*?)</loc>", sm)
    want = [u for u in urls if re.search(r"-library-hours$|opening-(hours|times)$", u)]
    for u in want:
        slug = u.rstrip("/").rsplit("/", 1)[-1]
        body = http_get(u)
        if len(body) > 3000:
            pages[slug] = body
        time.sleep(0.8)
    return pages


# --------------------------------------------------------------------------- html helpers
class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.pos: list[int] = []
        self.cls: list[str] = []
        self._cur: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._tcls = ""

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        if tag == "table":
            self._cur = []
            self._tcls = dict(attrs).get("class") or ""
            self.pos.append(self.getpos()[0])
        elif tag == "tr" and self._cur is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self._cur is not None:
            if self._row:
                self._cur.append(self._row)
            self._row = None
        elif tag == "table" and self._cur is not None:
            self.tables.append(self._cur)
            self.cls.append(self._tcls)
            self._cur = None

    def handle_data(self, data):  # noqa: ANN001
        if self._cell is not None:
            self._cell.append(data)


def tables_in(fragment: str) -> list[list[list[str]]]:
    p = _Tables()
    p.feed(fragment)
    return p.tables


def strip_tags(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(re.sub(r"[ \t]+", " ", s)).strip()


def rows_to_week(table: list[list[str]]) -> tuple[list[str] | None, bool]:
    """Pick the first usable hours column.  Returns (week, inferred_closed)."""
    rows = [r for r in table if len(r) >= 2]
    if not rows:
        return None, False
    pairs = [(r[0], r[1]) for r in rows]
    w = hp.build_week(pairs)
    if w:
        return w, False
    w = hp.fill_missing_closed(pairs)
    return (w, True) if w else (None, False)


# --------------------------------------------------------------------------- classification
_PHONE_OK = re.compile(r"^\+?[\d][\d\s().\-]{6,}$")


def clean_phone(p: str | None, country: str) -> str | None:
    if not p:
        return None
    p = html.unescape(p).strip()
    if not _PHONE_OK.match(p):
        return None
    digits = re.sub(r"\D", "", p)
    if not 7 <= len(digits) <= 15:
        return None
    if p.startswith("+"):
        return "+" + digits
    if country in ("US", "CA") and len(digits) == 10:
        return f"+1{digits}"
    return digits


def classify(name: str, tw: list[str] | None) -> str:
    n = name.lower()
    if re.search(r"universit|college|campus|\blamont\b|\bwidener\b|\bsnell\b", n):
        return "university"
    if re.search(r"\bnational\b|british library|state library|library of congress|grande biblioth", n):
        return "national"
    return "public"


def week_sig(w: list[str]) -> str:
    return "|".join(w)


# --------------------------------------------------------------------------- extraction
def extract_records(slug: str, page: str, src_url: str) -> list[dict]:
    recs: list[dict] = []
    main = page
    m_main = re.search(r"<main", page)
    if m_main:
        main = page[m_main.start():]

    # --- template 1: quick actions + first weekly hours table
    qa = re.search(r"Quick Actions - ([^<]+)</h3>(.*?)Always verify", page, re.S)
    if qa:
        name = html.unescape(qa.group(1)).strip()
        blk = qa.group(2)
        dest = re.search(r"destination=([^\"&]+)", blk)
        tel = re.search(r"href=\"tel:([^\"]+)\"", blk)
        web = re.search(r"href=\"(https?://[^\"]+)\"[^>]*>\s*<span>&#127760;", blk)
        address = urllib.parse.unquote_plus(dest.group(1)) if dest else None
        tabs = tables_in(page)
        week, inferred = None, False
        for t in tabs:
            w, inf = rows_to_week([r for r in t if r and not r[0].lower().startswith("day")])
            if w:
                week, inferred = w, inf
                break
        if week:
            recs.append({
                "name": name, "address": address, "phone": tel.group(1) if tel else None,
                "url": web.group(1) if web else None, "week": week, "inferred": inferred,
                "tpl": "quick", "slug": slug, "src": src_url,
            })

    # --- template 2: <address> block + table inside the same <section>
    for sec in re.finditer(r"<section[^>]*>(.*?)</section>", page, re.S):
        body = sec.group(1)
        am = re.search(r"<address[^>]*>(.*?)</address>", body, re.S)
        if not am:
            continue
        lines = [strip_tags(x) for x in re.split(r"<br\s*/?>", am.group(1))]
        lines = [l for l in lines if l]
        nm = re.search(r"<strong>(.*?)</strong>", am.group(1), re.S)
        name = strip_tags(nm.group(1)) if nm else (lines[0] if lines else "")
        tel = re.search(r"href=\"tel:([^\"]+)\"", am.group(1))
        web = re.search(r"href=\"(https?://[^\"]+)\"", am.group(1))
        addr_lines = [l for l in lines if l != name and not l.lower().startswith("official website")
                      and not re.fullmatch(r"[+\d\s().\-]+", l)]
        tabs = tables_in(body)
        week, inferred = None, False
        for t in tabs:
            w, inf = rows_to_week([r for r in t if r and not r[0].lower().startswith("day")])
            if w:
                week, inferred = w, inf
                break
        if week and name:
            recs.append({
                "name": name, "address": ", ".join(addr_lines) or None,
                "phone": tel.group(1) if tel else None, "url": web.group(1) if web else None,
                "week": week, "inferred": inferred, "tpl": "address", "slug": slug, "src": src_url,
            })

    # --- template 3: generated library cards (id="lib-N")
    for card in re.finditer(r"<div class=\"library-card\" id=\"lib-\d+\">(.*?)</div>", page, re.S):
        body = card.group(1)
        nm = re.search(r"<h3>(.*?)</h3>", body, re.S)
        ad = re.search(r"<strong>Address:</strong>\s*(.*?)</p>", body, re.S)
        tel = re.search(r"href=\"tel:([^\"]+)\"", body)
        tabs = tables_in(body)
        week, inferred = None, False
        for t in tabs:
            w, inf = rows_to_week([r for r in t if r and not r[0].lower().startswith("day")])
            if w:
                week, inferred = w, inf
                break
        if nm and week:
            recs.append({
                "name": strip_tags(nm.group(1)), "address": strip_tags(ad.group(1)) if ad else None,
                "phone": tel.group(1) if tel else None, "url": None, "week": week,
                "inferred": inferred, "tpl": "generated", "slug": slug, "src": src_url,
            })
    return recs


# --------------------------------------------------------------------------- geocoding
def load_json(p: Path, default):  # noqa: ANN001
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return default


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    import math
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def nominatim(q: str) -> tuple[float, float] | None:
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": q, "format": "json", "limit": 1})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode())
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:  # noqa: BLE001
        return None
    return None



# --------------------------------------------------------------------------- compact record helpers
def compact(rec: dict) -> dict:
    """Drop empty fields so a 25k-record feed stays small.  Reader defaults: type=public, h24=false, gq=address."""
    out = {k: v for k, v in rec.items() if v not in (None, "", [], False) and k not in ("exceptions", "updated", "src", "s")}
    if rec.get("gq") == "address":
        out.pop("gq", None)
    if rec.get("type") == "public":
        out.pop("type", None)
    if rec.get("s") == "osm":
        out["o"] = rec["src"].rsplit("/", 2)[-2][0] + rec["src"].rsplit("/", 1)[-1]  # n123 / w45 / r6
    elif rec.get("src"):
        out["src"] = rec["src"]
    return out


def osm_city(t: dict, label: str) -> str:
    for k in ("addr:city", "addr:town", "addr:village", "addr:municipality", "addr:suburb", "addr:place"):
        v = (t.get(k) or "").strip()
        if v:
            return v
    return label


def osm_records(elements: list[dict], region: "rg.Region | None", stats: dict, seen: set[str],
                resolver: "ql.Resolver | None" = None) -> list[dict]:
    """Elements -> feed records, counting every drop reason.  `region` (Overpass path) fixes country and zone
    defaults; without it `resolver` (QLever / planet path) derives them per element."""
    out: list[dict] = []
    fallback_stats = None
    for e in elements:
        t = e["t"]
        if region is not None:
            cc, label, key = region.cc, region.label, region.key
        else:
            r = resolver.resolve(e["lat"], e["lon"], t.get("addr:country")) if resolver else None
            if r is None:
                stats.setdefault("??", {"elements": 0, "no_name": 0, "not_library": 0, "private": 0, "hours_rejected": {}, "kept": 0, "ph_off": 0})["elements"] += 1
                continue
            cc, tz_planet = r
            label = ""  # no address city: the app shows the country instead of a fake place
            key = cc
        s = stats.setdefault(key, {"elements": 0, "no_name": 0, "not_library": 0, "private": 0, "hours_rejected": {}, "kept": 0, "ph_off": 0})
        s["elements"] += 1
        if t.get("_oh_multi"):
            s["hours_rejected"]["several opening_hours values"] = s["hours_rejected"].get("several opening_hours values", 0) + 1
            continue
        name = (t.get("name") or t.get("name:en") or "").strip()
        if len(name) < 3:
            s["no_name"] += 1
            continue
        if _NOT_LIB.search(name) or t.get("amenity") not in (None, "library"):
            s["not_library"] += 1
            continue
        if t.get("access") in ("private", "no", "customers", "permit"):
            s["private"] += 1
            continue
        parsed, why = osm.parse_hours(t.get("opening_hours", ""))
        if not parsed:
            s["hours_rejected"][why] = s["hours_rejected"].get(why, 0) + 1
            continue
        lat, lon = e["lat"], e["lon"]
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        lid = "osm_" + e["id"]
        if lid in seen:
            continue
        seen.add(lid)
        addr = " ".join(x for x in (t.get("addr:housenumber"), t.get("addr:street")) if x)
        if t.get("addr:postcode") and addr:
            addr += f", {t['addr:postcode']}"
        web = t.get("website") or t.get("contact:website")
        if web and not re.match(r"https?://", web):
            web = "https://" + web
        if web and not re.match(r"https?://[^\s]+\.[^\s]+", web):
            web = None
        h24 = all(d == "24h" for d in parsed.week)
        osm_type = {"n": "node", "w": "way", "r": "relation"}[e["id"][0]]
        rec = {
            "id": lid, "name": name, "type": _classify_osm(t), "h24": h24, "address": addr or None,
            "city": osm_city(t, label), "region": (region.key.split("-", 1)[1] if region is not None and "-" in region.key else None),
            "country": cc, "lat": round(lat, 5), "lon": round(lon, 5), "gq": "address",
            "tz": rg.resolve_tz(region, lat, lon) if region is not None else tz_planet, "hours": parsed.week,
            "phone": clean_phone(t.get("phone") or t.get("contact:phone"), cc), "url": web,
            "src": f"https://www.openstreetmap.org/{osm_type}/{e['id'][1:]}", "s": "osm",
        }
        if parsed.ph_off and not h24:
            rec["ph"] = 1
            s["ph_off"] += 1
        if not any((t.get(k) or "").strip() for k in _CITY_TAGS):
            rec["_nocity"] = True
        s["kept"] += 1
        out.append(rec)
    fill_missing_cities(out, region, stats.setdefault("_cities", {}))
    return out


_CITY_TAGS = ("addr:city", "addr:town", "addr:village", "addr:municipality", "addr:suburb", "addr:place")


def fill_missing_cities(recs: list[dict], region: "rg.Region | None", stats: dict) -> None:
    """Libraries without an address city borrow the nearest city of a sibling record (<= 15 km) so they
    group and search under a real place name; otherwise they keep the region label."""
    known: dict[tuple[int, int], list[tuple[float, float, str]]] = {}
    for r in recs:
        if not r.get("_nocity"):
            known.setdefault((int(r["lat"] * 10), int(r["lon"] * 10)), []).append((r["lat"], r["lon"], r["city"]))
    filled = 0
    for r in recs:
        if not r.pop("_nocity", False):
            continue
        cx, cy = int(r["lat"] * 10), int(r["lon"] * 10)
        best, best_d = None, 50.0
        for dx in range(-5, 6):
            for dy in range(-5, 6):
                for la, lo, city in known.get((cx + dx, cy + dy), ()):
                    d = haversine_km((la, lo), (r["lat"], r["lon"]))
                    if d < best_d:
                        best, best_d = city, d
        if best:
            r["city"] = best
            filled += 1
    stats["city_borrowed"] = stats.get("city_borrowed", 0) + filled


_NOT_LIB = osm._NOT_A_LIBRARY


def _classify_osm(t: dict) -> str:
    n = (t.get("name") or "")
    ot = (t.get("operator:type") or "") + " " + (t.get("library:type") or "")
    if re.search(r"universit|college|campus|hochschule|universit\u00e4t|\buni\b|fachhochschule|\bETH\b|academic", n + " " + ot, re.I):
        return "university"
    if re.search(r"national|state library|british library|biblioth\u00e8que nationale|nationalbibliothek", n, re.I):
        return "national"
    return "public"


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return " ".join(w for w in s.split() if w not in ("the", "library", "libraries", "public", "branch", "of", "and", "city"))


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages-dir", type=Path)
    ap.add_argument("--out", type=Path, default=HERE / "out")
    ap.add_argument("--include-generated", action="store_true")
    ap.add_argument("--geocode", type=int, default=0, help="max new Nominatim lookups this run")
    ap.add_argument("--today", help="YYYY-MM-DD (tests)")
    ap.add_argument("--no-osm", action="store_true", help="site pages only")
    ap.add_argument("--osm-source", choices=["qlever", "overpass"], default="qlever")
    ap.add_argument("--qlever-cache", type=Path, default=HERE / "cache" / "qlever" / "planet.json.gz")
    ap.add_argument("--countries", default="all", help="'all' or comma list of ISO codes to keep from OSM")
    ap.add_argument("--osm-cache", type=Path, default=HERE / "cache" / "osm")
    ap.add_argument("--osm-cache-only", action="store_true", help="never query Overpass, use the cache only")
    ap.add_argument("--osm-max-age-days", type=float, default=3.0, help="cache entries younger than this are not re-queried")
    ap.add_argument("--osm-regions", default="all", help="comma list of region keys or country codes (default all)")
    ap.add_argument("--max-minutes", type=float, default=38.0, help="stop querying Overpass after this many minutes")
    ap.add_argument("--snapshot-max-bytes", type=int, default=3_000_000)
    ap.add_argument("--min-records", type=int, default=0, help="exit 2 when fewer records are published (CI gate)")
    a = ap.parse_args()

    started = time.time()
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    a.out.mkdir(parents=True, exist_ok=True)
    gaz = load_json(HERE / "gazetteer.json", {})
    geocache: dict = load_json(HERE / "geocache.json", {})

    pages = list_pages(a.pages_dir)
    raw: list[dict] = []
    for slug, page in pages.items():
        raw.extend(extract_records(slug, page, f"{SITE}/{slug}"))

    sig_pages: dict[str, set[str]] = {}
    for r in raw:
        sig_pages.setdefault(week_sig(r["week"]), set()).add(r["slug"])

    rejected: list[dict] = []
    libs: list[dict] = []
    seen: set[str] = set()
    new_lookups = 0
    for r in raw:
        why: list[str] = []
        g = gaz.get(r["slug"])
        if not g:
            why.append("no gazetteer entry for page slug")
        if r["tpl"] == "generated" and not a.include_generated:
            why.append("generated card set (inconsistent address/postcode/phone)")
        if not r["address"]:
            why.append("no street address")
        if len(sig_pages[week_sig(r["week"])]) >= 6:
            why.append("weekly pattern is the site-wide template default")
        if g:
            try:
                ZoneInfo(g["tz"])
            except Exception:  # noqa: BLE001
                why.append(f"invalid tz {g['tz']}")
        if why:
            rejected.append({"name": r["name"], "page": r["slug"], "reasons": why})
            continue
        city, country, tz = g["city"], g["country"], g["tz"]
        lat, lon, gq = g["lat"], g["lon"], "city"
        key = f"{r['address']}, {city}, {country}"
        hit = geocache.get(key)
        if hit is None and new_lookups < a.geocode and time.time() - started < a.max_minutes * 12:
            new_lookups += 1
            time.sleep(1.1)
            res = nominatim(key)
            hit = list(res) if res else False
            geocache[key] = hit
        if hit and haversine_km((hit[0], hit[1]), (g["lat"], g["lon"])) <= g.get("radius", 35):
            lat, lon, gq = round(hit[0], 5), round(hit[1], 5), "address"
        name = re.sub(r"\s+", " ", r["name"]).strip()
        lid = "lh_" + hashlib.sha1(f"{name}|{city}|{country}".encode()).hexdigest()[:10]
        if lid in seen:
            rejected.append({"name": name, "page": r["slug"], "reasons": ["duplicate id"]})
            continue
        seen.add(lid)
        week = r["week"]
        h24 = all(d == "24h" for d in week)
        rec = {
            "id": lid, "name": name, "type": classify(name, week), "h24": h24, "address": r["address"], "city": city,
            "region": g.get("region"), "country": country, "lat": lat, "lon": lon, "gq": gq, "tz": tz, "hours": week,
            "phone": clean_phone(r["phone"], country), "url": r["url"], "src": r["src"], "s": "site",
        }
        if not h24:
            rec["ph"] = 1  # site pages state libraries typically close on public holidays
        if r["inferred"]:
            rec["note"] = "Days not listed on the source page are treated as closed."
        libs.append(rec)
    if new_lookups:
        (HERE / "geocache.json").write_text(json.dumps(geocache, indent=0, ensure_ascii=False, sort_keys=True))

    # ------------------------------------------------------------- OpenStreetMap, per region
    osm_stats: dict = {}
    osm_run = {"regions": 0, "fetched": 0, "fresh_cache": 0, "stale_cache": 0, "missing": [], "log": [], "dup_of_site": 0}
    if not a.no_osm:
        by_cell: dict = {}
        for l in libs:
            by_cell.setdefault((int(l["lat"] * 50), int(l["lon"] * 50)), []).append((l["lat"], l["lon"], _norm(l["name"])))
        batches: list[tuple[list[dict], "rg.Region | None"]] = []
        if a.osm_source == "qlever":
            els, src = ql.fetch(a.qlever_cache, a.osm_max_age_days, a.osm_cache_only)
            osm_run["source"] = "qlever"
            osm_run["qlever"] = src
            osm_run["regions"] = 1
            if els is None:
                osm_run["missing"].append("planet")
            else:
                osm_run["elements_total"] = len(els)
                batches.append((els, None))
        else:
            wanted = [x for x in rg.all_regions() if a.osm_regions == "all" or x.key in a.osm_regions.split(",") or x.cc in a.osm_regions.split(",")]
            fetcher = None if a.osm_cache_only else osm.Fetcher(deadline=started + a.max_minutes * 60)
            for region in osm.region_order(a.osm_cache, wanted):
                els, src = osm.load_region(a.osm_cache, region, fetcher, a.osm_max_age_days, a.osm_cache_only)
                osm_run["regions"] += 1
                if els is None:
                    osm_run["missing"].append(region.key)
                    continue
                osm_run[{"fetched": "fetched", "fresh-cache": "fresh_cache", "stale-cache": "stale_cache"}[src]] += 1
                batches.append((els, region))
            if fetcher:
                osm_run["log"] = fetcher.log[-40:]
        resolver = ql.Resolver() if a.osm_source == "qlever" else None
        only = None if a.countries == "all" else set(a.countries.split(","))
        for els, region in batches:
            for rec in osm_records(els, region, osm_stats, seen, resolver):
                if only is not None and rec["country"] not in only:
                    continue
                nm = _norm(rec["name"])
                cx, cy = int(rec["lat"] * 50), int(rec["lon"] * 50)
                if any(nn and haversine_km((la, lo), (rec["lat"], rec["lon"])) < 1.2 and (nn == nm or nn in nm or nm in nn)
                       for dx in (-1, 0, 1) for dy in (-1, 0, 1) for (la, lo, nn) in by_cell.get((cx + dx, cy + dy), ())):
                    osm_run["dup_of_site"] += 1
                    continue
                libs.append(rec)

    libs.sort(key=lambda x: (x["country"], x["city"], x["name"]))
    holidays = {cc: hc.exceptions_for(cc, None, today) for cc in sorted({l["country"] for l in libs if l.get("ph")})}
    doc = {
        "schema": SCHEMA_VERSION, "generated": today.isoformat(), "source": SITE, "count": len(libs),
        "attribution": "Hours: libraryhours24.com and (c) OpenStreetMap contributors, ODbL 1.0, https://www.openstreetmap.org/copyright",
        "exceptions_through": hc.horizon(today).isoformat(), "holidays": holidays,
        "libraries": [compact(l) for l in libs],
    }
    compact_json = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    (a.out / "libraries.json").write_text(compact_json, "utf-8")
    snap, dropped = snapshot_gz(doc, a.snapshot_max_bytes)
    (a.out / "libraries.json.gz").write_bytes(snap)
    (a.out / "LICENSE-DATA.txt").write_text((HERE / "DATA_LICENSE.txt").read_text("utf-8") if (HERE / "DATA_LICENSE.txt").exists() else "(c) OpenStreetMap contributors, ODbL 1.0\n")

    problems = validate_compact(doc)
    by_country: dict = {}
    for l in libs:
        c = by_country.setdefault(l["country"], {"site": 0, "osm": 0})
        c[l.get("s", "site")] += 1
    rates = {}
    for key, s in osm_stats.items():
        if key.startswith("_"):
            continue
        cc = key.split("-")[0]
        r = rates.setdefault(cc, {"elements": 0, "kept": 0, "hours_rejected": {}, "no_name": 0, "not_library": 0, "private": 0, "ph_off": 0})
        for k in ("elements", "kept", "no_name", "not_library", "private", "ph_off"):
            r[k] += s[k]
        for why, n in s["hours_rejected"].items():
            r["hours_rejected"][why] = r["hours_rejected"].get(why, 0) + n
    for r in rates.values():
        with_hours = r["elements"] - r["no_name"] - r["not_library"] - r["private"]
        r["parse_rate"] = round(r["kept"] / with_hours, 3) if with_hours > 0 else None
    report = {
        "generated": today.isoformat(), "pages": len(pages), "raw_site_records": len(raw), "published": len(libs),
        "by_source": {s: sum(1 for l in libs if l.get("s") == s) for s in ("site", "osm")},
        "countries": sorted(by_country), "by_country": by_country, "cities": len({(l["city"], l["country"]) for l in libs}),
        "street_level": sum(1 for l in libs if l["gq"] == "address"), "city_level_coords": sum(1 for l in libs if l["gq"] == "city"),
        "validation_problems": problems, "osm_run": osm_run, "osm_parse_rates_by_country": rates,
        "bytes_json": len(compact_json.encode()), "bytes_snapshot_gz": len(snap), "snapshot_dropped": dropped,
        "minutes": round((time.time() - started) / 60, 1), "rejected_site_detail": rejected[:400],
    }
    (a.out / "validation_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in report.items() if k not in ("rejected_site_detail", "osm_run")}, ensure_ascii=False, indent=1))
    print("osm_run:", json.dumps({k: v for k, v in osm_run.items() if k != "log"}))
    if problems:
        return 1
    if a.min_records and len(libs) < a.min_records:
        print(f"only {len(libs)} records (< {a.min_records})", file=sys.stderr)
        return 2
    return 0


def snapshot_gz(doc: dict, max_bytes: int) -> tuple[bytes, int]:
    """gzip of the feed for bundling in the app.  When it exceeds max_bytes the least-documented OSM
    records are dropped (deterministically) until it fits; the full feed is never trimmed."""
    def pack(d: dict) -> bytes:
        return gzip.compress(json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9, mtime=0)
    data = pack(doc)
    if len(data) <= max_bytes:
        return data, 0
    recs = doc["libraries"]
    def score(r: dict) -> tuple:
        return (r.get("o") is None, bool(r.get("address")) + bool(r.get("url")) + bool(r.get("phone")), r["id"])
    order = sorted(range(len(recs)), key=lambda i: score(recs[i]))
    lo, hi = 0, len(order)
    best = data
    while lo < hi:  # binary search on the number of dropped records
        mid = (lo + hi) // 2
        drop = set(order[:mid])
        cand = pack({**doc, "count": len(recs) - mid, "libraries": [r for i, r in enumerate(recs) if i not in drop]})
        if len(cand) <= max_bytes:
            best, hi = cand, mid
        else:
            lo = mid + 1
    return best, lo


def validate_compact(doc: dict) -> list[str]:
    """Same checks as validate() but for the compact on-disk form."""
    out: list[str] = []
    ids: set[str] = set()
    for l in doc["libraries"]:
        i = l.get("id", "?")
        if i in ids:
            out.append(f"{i}: duplicate id")
        ids.add(i)
        try:
            ZoneInfo(l["tz"])
        except Exception:  # noqa: BLE001
            out.append(f"{i}: bad tz")
        if not (-90 <= l.get("lat", 999) <= 90 and -180 <= l.get("lon", 999) <= 180):
            out.append(f"{i}: bad coordinates")
        w = l.get("hours", [])
        if len(w) != 7:
            out.append(f"{i}: hours needs 7 entries")
        for d in w:
            if d in ("closed", "24h"):
                continue
            for rg_ in d.split(","):
                m = re.fullmatch(r"(\d\d):(\d\d)-(\d\d):(\d\d)", rg_)
                if not m:
                    out.append(f"{i}: bad range {rg_}")
                elif int(m.group(1)) > 23 or int(m.group(2)) > 59 or int(m.group(4)) > 59 or int(m.group(3)) * 60 + int(m.group(4)) > 1440:
                    out.append(f"{i}: impossible time {rg_}")
            try:
                ok = osm.ranges_ok(d)
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                out.append(f"{i}: bad or overlapping ranges {d}")
        if not l.get("name") or not l.get("country"):
            out.append(f"{i}: missing name/country")
        if l.get("ph") and l["country"] not in doc.get("holidays", {}):
            out.append(f"{i}: ph flag without holiday table for {l['country']}")
    for cc, ex in doc.get("holidays", {}).items():
        for e in ex:
            try:
                dt.date.fromisoformat(e["d"])
            except Exception:  # noqa: BLE001
                out.append(f"holidays {cc}: bad date")
    return out


def validate(doc: dict) -> list[str]:
    """Back-compat: validates either form."""
    return validate_compact(doc)


if __name__ == "__main__":
    raise SystemExit(main())
