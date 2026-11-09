"""OpenStreetMap source (ODbL): `amenity=library` objects that carry an `opening_hours` tag, fetched
per country / US state / Canadian province from Overpass.

Design
  * one query per region (regions.py), cached per region in cache/osm/<key>.json with a fetch time;
  * endpoints are rotated per attempt with exponential backoff; a global deadline stops querying
    (cached regions are still used, so a run never publishes less because Overpass was slow);
  * only a conservative subset of the opening_hours grammar is accepted.  Anything seasonal,
    week-numbered, sunrise-based, open-ended or with a public-holiday *opening* time is rejected, never
    guessed.  `PH off` is understood (the library observes public holidays);
  * per-region statistics (elements, named, parsed, kept, reject reasons) feed the parse-rate report.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import regions as rg

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
]
UA = "LibraryHoursFeed/1.0 (+https://cinderspire.github.io/support/libraryhours.html)"
_DAYS = {"Mo": 0, "Tu": 1, "We": 2, "Th": 3, "Fr": 4, "Sa": 5, "Su": 6}
_T = r"(?:(?:[01]?\d|2[0-3]):[0-5]\d|24:00)"
_DAYTOK = r"(?:Mo|Tu|We|Th|Fr|Sa|Su|PH|SH)"
_SEL = rf"{_DAYTOK}(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?"


def _expand_days(sel: str) -> Optional[tuple[set[int], bool]]:
    """-> (weekday set, mentions PH). None when the selector is not understood."""
    out: set[int] = set()
    ph = False
    for part in sel.split(","):
        part = part.strip()
        if part == "PH":
            ph = True
            continue
        if part == "SH":
            continue  # school holidays: ignored
        m = re.fullmatch(r"(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?", part)
        if not m:
            return None
        a = _DAYS[m.group(1)]
        b = _DAYS[m.group(2)] if m.group(2) else a
        i = a
        out.add(i)
        while i != b:
            i = (i + 1) % 7
            out.add(i)
    return out, ph


def ranges_ok(day: str) -> bool:
    """Ranges of one day must be ascending and non-overlapping (a past-midnight range must be last)."""
    if day in ("closed", "24h"):
        return True
    prev_end = -1
    parts = day.split(",")
    for i, rg_ in enumerate(parts):
        a, b = rg_.split("-")
        s = int(a[:2]) * 60 + int(a[3:])
        e = int(b[:2]) * 60 + int(b[3:])
        if e <= s:
            if i != len(parts) - 1:
                return False
            e += 1440
        if s < prev_end:
            return False
        prev_end = e
    return True


class Parsed:
    __slots__ = ("week", "ph_off")

    def __init__(self, week: list[str], ph_off: bool) -> None:
        self.week = week
        self.ph_off = ph_off


def parse_hours(tag: str) -> tuple[Optional[Parsed], str]:
    """OSM opening_hours -> (Parsed | None, reject reason)."""
    s = tag.strip()
    if not s:
        return None, "empty"
    if len(s) > 240:
        return None, "too long"
    if s in ("24/7", "Mo-Su 00:00-24:00"):
        return Parsed(["24h"] * 7, False), ""
    core = re.sub(rf"\b(?:Mo|Tu|We|Th|Fr|Sa|Su|PH|SH|off|closed)\b|24/7", "", s)
    if not re.fullmatch(r"[0-9:,;\- \t]*", core):
        return None, "unsupported syntax"
    if re.search(r"\bSH\b", s):
        return None, "school-holiday rule"
    # ';' separates rules where a later rule overrides earlier ones for the days it names.
    # ',' before a new day selector adds more rules to the SAME group ("Tu,Th 09-11, Tu 16-19" gives Tu two ranges).
    week: list[Optional[str]] = [None] * 7
    ph_off = False
    for group in [g.strip() for g in s.split(";") if g.strip()]:
        group = re.sub(rf"(\d|off|closed)\s*,\s*(?={_SEL}(?:\s|,|$))", r"\1|", group)
        acc: dict[int, list[str]] = {}
        closed_days: set[int] = set()
        all_day: set[int] = set()
        for rule in [r.strip() for r in group.split("|") if r.strip()]:
            m = re.fullmatch(rf"((?:{_SEL})(?:\s*,\s*(?:{_SEL}))*)?\s*(.*)", rule)
            if not m:
                return None, "unsupported syntax"
            sel, rest = m.group(1), m.group(2).strip()
            ph = False
            if sel:
                ex = _expand_days(sel)
                if ex is None:
                    return None, "unsupported syntax"
                days, ph = ex
            else:
                days = set(range(7))
            low = rest.lower()
            if low in ("off", "closed"):
                closed_days |= days
                if ph:
                    ph_off = True
                continue
            if rest in ("24/7", "00:00-24:00"):
                all_day |= days
                continue
            ranges = [x.strip() for x in rest.split(",")]
            norm = []
            for rg_ in ranges:
                mm = re.fullmatch(rf"({_T})\s*-\s*({_T})", rg_)
                if not mm or mm.group(1) == mm.group(2):
                    return None, "unsupported syntax"
                a1, b1 = mm.group(1), mm.group(2)
                norm.append("{:02d}:{}-{:02d}:{}".format(int(a1.split(":")[0]), a1.split(":")[1], int(b1.split(":")[0]), b1.split(":")[1]))
            if ph:
                return None, "PH with opening hours"
            for d in days:
                acc.setdefault(d, []).extend(norm)
        for d in closed_days:
            if d not in acc and d not in all_day:
                week[d] = "closed"
        for d in all_day:
            week[d] = "24h"
        for d, rs in acc.items():
            rs = sorted(set(rs))
            week[d] = ",".join(rs)
    if all(w is None for w in week):
        return None, "no weekday rule"
    out = [w if w is not None else "closed" for w in week]
    if all(w == "closed" for w in out):
        return None, "never open"
    if any(not ranges_ok(w) for w in out):
        return None, "overlapping ranges"
    return Parsed(out, ph_off), ""


def parse_opening_hours(tag: str) -> Optional[list[str]]:
    """Back-compat helper: just the 7 strings."""
    p, _ = parse_hours(tag)
    return p.week if p else None


_NOT_A_LIBRARY = re.compile(
    r"little free|book\s?(exchange|cabinet|box|swap|crossing)|b[uü]cherschrank|b[uü]cherzelle|bookcase|tauschschrank|boîte à livres|"
    r"boite a livres|street library|biblio\s?box|toy library|ludoth[eè]que|spielothek|tool library|seed library|livres en libert[eé]|"
    r"leesbox|boekenkast|minibiblioteca|bibliocabina|telefonzelle|phone booth", re.I)

_KEEP_TAGS = (
    "name", "name:en", "opening_hours", "phone", "contact:phone", "website", "contact:website", "addr:housenumber",
    "addr:street", "addr:city", "addr:town", "addr:village", "addr:municipality", "addr:suburb", "addr:place",
    "addr:postcode", "operator", "operator:type", "access", "library:type", "amenity")


def slim(elements: list[dict]) -> list[dict]:
    out = []
    for e in elements:
        t = e.get("tags", {})
        c = e.get("center") or {"lat": e.get("lat"), "lon": e.get("lon")}
        if c.get("lat") is None or c.get("lon") is None:
            continue
        out.append({"id": f"{e['type'][0]}{e['id']}", "lat": c["lat"], "lon": c["lon"],
                    "t": {k: t[k] for k in _KEEP_TAGS if k in t}})
    return out


class Fetcher:
    """Rotates endpoints with exponential backoff.  `post` is injectable for tests."""

    def __init__(self, deadline: float | None = None, post: Callable[[str, bytes, int], bytes] | None = None,
                 sleep: Callable[[float], None] = time.sleep, endpoints: list[str] | None = None) -> None:
        self.deadline = deadline
        self.post = post or self._post
        self.sleep = sleep
        self.endpoints = endpoints or ENDPOINTS
        self.rotation = 0
        self.log: list[str] = []

    @staticmethod
    def _post(url: str, body: bytes, timeout: int) -> bytes:
        req = urllib.request.Request(url, data=body, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def expired(self) -> bool:
        return self.deadline is not None and time.time() > self.deadline

    def fetch(self, region: rg.Region, attempts: int = 6, request_timeout: int = 240) -> Optional[list[dict]]:
        body = urllib.parse.urlencode({"data": rg.overpass_query(region)}).encode()
        eps = list(region.endpoints) + [e for e in self.endpoints if e not in region.endpoints]
        delay = 5.0
        for i in range(attempts):
            if self.expired():
                self.log.append(f"{region.key}: deadline")
                return None
            ep = eps[(self.rotation + i) % len(eps)]
            try:
                raw = self.post(ep, body, request_timeout)
                data = json.loads(raw.decode("utf-8"))
                if "elements" not in data:
                    raise ValueError("no elements")
                if data.get("remark") and not data["elements"]:
                    raise ValueError(str(data["remark"])[:80])  # runtime/memory error remark
                self.rotation = (self.rotation + i + 1) % len(eps)
                return slim(data["elements"])
            except Exception as e:  # noqa: BLE001 - network / rate limit / bad JSON: back off, next mirror
                self.log.append(f"{region.key}: {ep.split('/')[2]} failed ({type(e).__name__})")
                self.sleep(delay)
                delay = min(delay * 2, 60)
        return None


def load_region(cache_dir: Path, region: rg.Region, fetcher: Fetcher | None, max_age_days: float,
                cache_only: bool = False, now: float | None = None) -> tuple[Optional[list[dict]], str]:
    """-> (elements | None, source) with source in {'fresh-cache', 'fetched', 'stale-cache', 'none'}."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / f"{region.key}.json"
    now = now if now is not None else time.time()
    cached = None
    if f.exists():
        try:
            cached = json.loads(f.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            cached = None
    if cached and now - cached.get("fetched", 0) < max_age_days * 86400:
        return cached["elements"], "fresh-cache"
    if not cache_only and fetcher is not None:
        els = fetcher.fetch(region)
        if els is not None:
            f.write_text(json.dumps({"fetched": now, "elements": els}, ensure_ascii=False, separators=(",", ":")), "utf-8")
            fetcher.sleep(3.0)  # etiquette: one query at a time, with a pause between regions
            return els, "fetched"
    if cached:
        return cached["elements"], "stale-cache"
    return None, "none"


def region_order(cache_dir: Path, regions_: list[rg.Region]) -> list[rg.Region]:
    """Oldest (or missing) cache first, so a time-limited run refreshes what is stalest."""
    def age_key(r: rg.Region) -> float:
        f = cache_dir / f"{r.key}.json"
        try:
            return json.loads(f.read_text("utf-8")).get("fetched", 0)
        except Exception:  # noqa: BLE001
            return 0
    return sorted(regions_, key=age_key)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
