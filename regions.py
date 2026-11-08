"""Overpass query regions (country / US state / Canadian province / big-country subdivisions) and
time-zone resolution for OpenStreetMap libraries.

A region key is stable and doubles as the cache file name.  `iso` is matched against the OSM
`ISO3166-1` (countries) or `ISO3166-2` (subdivisions) tag of an administrative area.
Time zones: `timezonefinder` (pip, polygon based, exact) when installed - the GitHub Actions
workflow installs it - otherwise the region default plus a few longitude/latitude overrides for
the US/CA/AU states that span several zones."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Region:
    key: str          # e.g. "US-CA"
    cc: str           # ISO 3166-1 alpha-2 of the country the records belong to
    iso: str          # value of ISO3166-1 or ISO3166-2
    label: str        # human label (also used as the city fallback when a record has no address city)
    tz: str           # default IANA zone
    level: int = 2    # 1 = ISO3166-1 area, 2 = ISO3166-2 area
    endpoints: tuple = ()   # optional endpoint override (e.g. a country-specific mirror)


_US = {
    "AL": ("Alabama", "America/Chicago"), "AK": ("Alaska", "America/Anchorage"), "AZ": ("Arizona", "America/Phoenix"),
    "AR": ("Arkansas", "America/Chicago"), "CA": ("California", "America/Los_Angeles"), "CO": ("Colorado", "America/Denver"),
    "CT": ("Connecticut", "America/New_York"), "DE": ("Delaware", "America/New_York"), "DC": ("District of Columbia", "America/New_York"),
    "FL": ("Florida", "America/New_York"), "GA": ("Georgia", "America/New_York"), "HI": ("Hawaii", "Pacific/Honolulu"),
    "ID": ("Idaho", "America/Boise"), "IL": ("Illinois", "America/Chicago"), "IN": ("Indiana", "America/Indiana/Indianapolis"),
    "IA": ("Iowa", "America/Chicago"), "KS": ("Kansas", "America/Chicago"), "KY": ("Kentucky", "America/New_York"),
    "LA": ("Louisiana", "America/Chicago"), "ME": ("Maine", "America/New_York"), "MD": ("Maryland", "America/New_York"),
    "MA": ("Massachusetts", "America/New_York"), "MI": ("Michigan", "America/Detroit"), "MN": ("Minnesota", "America/Chicago"),
    "MS": ("Mississippi", "America/Chicago"), "MO": ("Missouri", "America/Chicago"), "MT": ("Montana", "America/Denver"),
    "NE": ("Nebraska", "America/Chicago"), "NV": ("Nevada", "America/Los_Angeles"), "NH": ("New Hampshire", "America/New_York"),
    "NJ": ("New Jersey", "America/New_York"), "NM": ("New Mexico", "America/Denver"), "NY": ("New York", "America/New_York"),
    "NC": ("North Carolina", "America/New_York"), "ND": ("North Dakota", "America/Chicago"), "OH": ("Ohio", "America/New_York"),
    "OK": ("Oklahoma", "America/Chicago"), "OR": ("Oregon", "America/Los_Angeles"), "PA": ("Pennsylvania", "America/New_York"),
    "RI": ("Rhode Island", "America/New_York"), "SC": ("South Carolina", "America/New_York"), "SD": ("South Dakota", "America/Chicago"),
    "TN": ("Tennessee", "America/Chicago"), "TX": ("Texas", "America/Chicago"), "UT": ("Utah", "America/Denver"),
    "VT": ("Vermont", "America/New_York"), "VA": ("Virginia", "America/New_York"), "WA": ("Washington", "America/Los_Angeles"),
    "WV": ("West Virginia", "America/New_York"), "WI": ("Wisconsin", "America/Chicago"), "WY": ("Wyoming", "America/Denver"),
}
_CA = {
    "BC": ("British Columbia", "America/Vancouver"), "AB": ("Alberta", "America/Edmonton"), "SK": ("Saskatchewan", "America/Regina"),
    "MB": ("Manitoba", "America/Winnipeg"), "ON": ("Ontario", "America/Toronto"), "QC": ("Quebec", "America/Toronto"),
    "NB": ("New Brunswick", "America/Moncton"), "NS": ("Nova Scotia", "America/Halifax"), "PE": ("Prince Edward Island", "America/Halifax"),
    "NL": ("Newfoundland and Labrador", "America/St_Johns"), "YT": ("Yukon", "America/Whitehorse"),
    "NT": ("Northwest Territories", "America/Yellowknife"), "NU": ("Nunavut", "America/Iqaluit"),
}
_AU = {
    "NSW": ("New South Wales", "Australia/Sydney"), "VIC": ("Victoria", "Australia/Melbourne"), "QLD": ("Queensland", "Australia/Brisbane"),
    "SA": ("South Australia", "Australia/Adelaide"), "WA": ("Western Australia", "Australia/Perth"), "TAS": ("Tasmania", "Australia/Hobart"),
    "NT": ("Northern Territory", "Australia/Darwin"), "ACT": ("Australian Capital Territory", "Australia/Sydney"),
}
_DE = {
    "BW": "Baden-Wuerttemberg", "BY": "Bavaria", "BE": "Berlin", "BB": "Brandenburg", "HB": "Bremen", "HH": "Hamburg", "HE": "Hesse",
    "MV": "Mecklenburg-Vorpommern", "NI": "Lower Saxony", "NW": "North Rhine-Westphalia", "RP": "Rhineland-Palatinate",
    "SL": "Saarland", "SN": "Saxony", "ST": "Saxony-Anhalt", "SH": "Schleswig-Holstein", "TH": "Thuringia",
}
_FR = {
    "ARA": "Auvergne-Rhone-Alpes", "BFC": "Bourgogne-Franche-Comte", "BRE": "Brittany", "CVL": "Centre-Val de Loire", "COR": "Corsica",
    "GES": "Grand Est", "HDF": "Hauts-de-France", "IDF": "Ile-de-France", "NOR": "Normandy", "NAQ": "Nouvelle-Aquitaine",
    "OCC": "Occitanie", "PDL": "Pays de la Loire", "PAC": "Provence-Alpes-Cote d'Azur",
}
_GB = {"ENG": ("England", "Europe/London"), "SCT": ("Scotland", "Europe/London"), "WLS": ("Wales", "Europe/London"), "NIR": ("Northern Ireland", "Europe/London")}
_SINGLE = [  # cc, iso, label, tz
    ("IE", "Ireland", "Europe/Dublin"), ("NZ", "New Zealand", "Pacific/Auckland"), ("AT", "Austria", "Europe/Vienna"),
    ("CH", "Switzerland", "Europe/Zurich"), ("NL", "Netherlands", "Europe/Amsterdam"), ("BE", "Belgium", "Europe/Brussels"),
    ("ES", "Spain", "Europe/Madrid"), ("IT", "Italy", "Europe/Rome"), ("SE", "Sweden", "Europe/Stockholm"),
    ("NO", "Norway", "Europe/Oslo"), ("DK", "Denmark", "Europe/Copenhagen"), ("FI", "Finland", "Europe/Helsinki"),
]


def all_regions() -> list[Region]:
    out: list[Region] = []
    for k, (label, tz) in _US.items():
        out.append(Region(f"US-{k}", "US", f"US-{k}", label, tz))
    for k, (label, tz) in _CA.items():
        out.append(Region(f"CA-{k}", "CA", f"CA-{k}", label, tz))
    for k, (label, tz) in _AU.items():
        out.append(Region(f"AU-{k}", "AU", f"AU-{k}", label, tz))
    for k, label in _DE.items():
        out.append(Region(f"DE-{k}", "DE", f"DE-{k}", label, "Europe/Berlin"))
    for k, label in _FR.items():
        out.append(Region(f"FR-{k}", "FR", f"FR-{k}", label, "Europe/Paris"))
    for k, (label, tz) in _GB.items():
        out.append(Region(f"GB-{k}", "GB", f"GB-{k}", label, tz))
    for cc, label, tz in _SINGLE:
        eps = ("https://overpass.osm.ch/api/interpreter",) if cc == "CH" else ()
        out.append(Region(cc, cc, cc, label, tz, level=1, endpoints=eps))
    return out


COUNTRIES = ["US", "GB", "IE", "CA", "AU", "NZ", "DE", "AT", "CH", "NL", "BE", "FR", "ES", "IT", "SE", "NO", "DK", "FI"]


def overpass_query(r: Region, timeout: int = 240) -> str:
    tag = "ISO3166-1" if r.level == 1 else "ISO3166-2"
    return (f'[out:json][timeout:{timeout}];area["{tag}"="{r.iso}"]->.a;'
            'nwr["amenity"="library"]["opening_hours"](area.a);out center tags;')


_tzf = None
_tzf_tried = False


def _timezonefinder():
    global _tzf, _tzf_tried
    if not _tzf_tried:
        _tzf_tried = True
        try:
            from timezonefinder import TimezoneFinder  # type: ignore
            _tzf = TimezoneFinder()
        except Exception:  # noqa: BLE001 - optional dependency
            _tzf = None
    return _tzf


def resolve_tz(r: Region, lat: float, lon: float) -> str:
    """IANA zone for a point inside region r."""
    f = _timezonefinder()
    if f is not None:
        try:
            z = f.timezone_at(lat=lat, lng=lon)
            if z:
                return z
        except Exception:  # noqa: BLE001
            pass
    k = r.key
    if r.cc == "ES" and lon < -12:
        return "Atlantic/Canary"
    if k == "US-TX" and lon < -104.0:
        return "America/Denver"
    if k == "US-FL" and lon < -85.2:
        return "America/Chicago"
    if k == "US-KY" and lon < -85.4:
        return "America/Chicago"
    if k == "US-TN" and lon > -85.5:
        return "America/New_York"
    if k == "US-IN" and lon < -86.8:
        return "America/Chicago"
    if k == "US-MI" and lon < -87.5 and lat > 45.5:
        return "America/Chicago"
    if k == "US-ND" and lon < -102.0:
        return "America/Denver"
    if k == "US-SD" and lon < -101.0:
        return "America/Denver"
    if k == "US-NE" and lon < -101.5:
        return "America/Denver"
    if k == "US-KS" and lon < -101.7:
        return "America/Denver"
    if k == "US-OR" and lon > -117.3 and lat < 44.5:
        return "America/Boise"
    if k == "US-ID" and lat > 45.8:
        return "America/Los_Angeles"
    if k == "CA-ON" and lon < -90.0:
        return "America/Winnipeg"
    if k == "AU-NSW" and lon < 141.2 and lat < -31.0:
        return "Australia/Broken_Hill"
    return r.tz
