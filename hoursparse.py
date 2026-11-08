"""Parsing of the human-written opening-hours strings found on libraryhours24.com.

Output convention (used by the feed and mirrored by the Dart engine):
  a weekly schedule is a list of 7 strings, Monday..Sunday.  Each string is
    "closed"                      closed all day
    "24h"                         open the whole day
    "HH:MM-HH:MM[,HH:MM-HH:MM]"   one or more ranges; a close <= open means the
                                   range runs past midnight into the next day;
                                   "24:00" is allowed as a close value.
"""
from __future__ import annotations

import re
from typing import Optional

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_ALIASES = {
    "mon": 0, "monday": 0, "mo": 0,
    "tue": 1, "tues": 1, "tuesday": 1, "tu": 1,
    "wed": 2, "weds": 2, "wednesday": 2, "we": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "th": 3,
    "fri": 4, "friday": 4, "fr": 4,
    "sat": 5, "saturday": 5, "sa": 5,
    "sun": 6, "sunday": 6, "su": 6,
}
_DASH = r"[-–—‒−]"


def _day_index(token: str) -> Optional[int]:
    return _DAY_ALIASES.get(token.strip().lower().rstrip(".,"))


def parse_days(spec: str) -> Optional[list[int]]:
    """'Monday - Thursday' -> [0,1,2,3]; 'Sat & Sun' -> [5,6]; None if not a day spec."""
    s = spec.strip().lower()
    s = re.sub(r"\(.*?\)", "", s).strip()
    s = s.replace(" ", " ")
    if not s:
        return None
    if s in ("daily", "every day", "everyday", "all days", "7 days", "monday to sunday"):
        return list(range(7))
    if s in ("weekdays", "weekday"):
        return [0, 1, 2, 3, 4]
    if s in ("weekends", "weekend"):
        return [5, 6]
    out: list[int] = []
    for part in re.split(r"\s*(?:,|&|\band\b|/)\s*", s):
        part = part.strip()
        if not part:
            return None
        m = re.fullmatch(rf"([a-z]+)\.?\s*(?:{_DASH}|\bto\b|\bthrough\b)\s*([a-z]+)\.?", part)
        if m:
            a, b = _day_index(m.group(1)), _day_index(m.group(2))
            if a is None or b is None:
                return None
            i = a
            out.append(i)
            while i != b:
                i = (i + 1) % 7
                out.append(i)
            continue
        i = _day_index(part)
        if i is None:
            return None
        out.append(i)
    return sorted(set(out))


_TIME = re.compile(
    r"^\s*(?:(noon|midday)|(midnight)|(\d{1,2})(?:[:.](\d{2}))?\s*([ap]\.?m\.?)?)\s*$", re.I)


def _t(token: str, default_suffix: Optional[str] = None) -> Optional[tuple[int, Optional[str]]]:
    """Return (minutes, explicit suffix 'am'/'pm'/None).  Midnight = 0 (caller maps close->1440)."""
    m = _TIME.match(token)
    if not m:
        return None
    if m.group(1):
        return 12 * 60, "pm"
    if m.group(2):
        return 0, "midnight"
    h = int(m.group(3))
    mi = int(m.group(4) or 0)
    suf = (m.group(5) or "").replace(".", "").lower() or None
    if mi > 59 or h > 24:
        return None
    if suf:
        if h < 1 or h > 12:
            return None
        if suf == "am":
            h = 0 if h == 12 else h
        else:
            h = 12 if h == 12 else h + 12
    elif default_suffix is None and h == 24:
        return 24 * 60, None
    return h * 60 + mi, suf


def _fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def parse_ranges(text: str) -> Optional[str]:
    """'9:00 AM - 8:00 PM, 1pm-5pm' -> '09:00-20:00,13:00-17:00'; 'Closed' -> 'closed'; '24 hours' -> '24h'."""
    s = text.strip().replace(" ", " ")
    low = s.lower()
    if not s:
        return None
    if re.fullmatch(r"(closed|close|n/?a|-|—)", low) or low.startswith("closed"):
        return "closed"
    if re.search(r"\b24\s*(?:hours?|hrs?|/7)\b|\bopen 24\b|\b24h\b|all day", low) and not re.search(r"\d\s*(?:am|pm)", low):
        return "24h"
    low = re.sub(r"\(.*?\)", "", low)
    chunks = [c for c in re.split(r"\s*(?:,|;|&|\band\b)\s*", low) if c.strip()]
    ranges: list[str] = []
    for c in chunks:
        m = re.fullmatch(rf"\s*(.+?)\s*(?:{_DASH}|\bto\b)\s*(.+?)\s*", c)
        if not m:
            return None
        a_raw, b_raw = m.group(1), m.group(2)
        b = _t(b_raw)
        if b is None:
            return None
        a = _t(a_raw)
        if a is None:
            return None
        a_min, a_suf = a
        b_min, b_suf = b
        if a_suf is None and b_suf in ("am", "pm"):
            # '9-5pm' / '12-5pm': borrow the end suffix, flip to am if it would run backwards
            a2 = _t(a_raw + b_suf)
            if a2 is None:
                return None
            a_min = a2[0]
            if a_min >= b_min and b_min > 0:
                a3 = _t(a_raw + "am")
                if a3 is not None:
                    a_min = a3[0]
        if b_suf == "midnight":
            b_min = 24 * 60
        if a_min == b_min:
            return None
        ranges.append(f"{_fmt(a_min)}-{_fmt(b_min)}")
    return ",".join(ranges) if ranges else None


def build_week(rows: list[tuple[str, str]]) -> Optional[list[str]]:
    """rows = [(day-spec, time-spec)] -> 7 strings, or None unless all 7 days are determined."""
    week: list[Optional[str]] = [None] * 7
    for dspec, tspec in rows:
        days = parse_days(dspec)
        val = parse_ranges(tspec)
        if days is None or val is None:
            continue
        for d in days:
            week[d] = val
    if any(w is None for w in week):
        # Sites often omit days that are closed; only accept when at least 5 days are known and
        # the caller allows it (see fill_missing_closed).  Default: reject.
        return None
    return [w for w in week if w is not None]


def fill_missing_closed(rows: list[tuple[str, str]]) -> Optional[list[str]]:
    """Like build_week but days that are absent become closed when >= 5 days were listed explicitly."""
    week: list[Optional[str]] = [None] * 7
    for dspec, tspec in rows:
        days = parse_days(dspec)
        val = parse_ranges(tspec)
        if days is None or val is None:
            continue
        for d in days:
            week[d] = val
    known = sum(1 for w in week if w is not None)
    if known < 5:
        return None
    return [w if w is not None else "closed" for w in week]
