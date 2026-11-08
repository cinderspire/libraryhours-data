"""Typical public-holiday closures per country, computed (not copied from the site: its holiday
page lists 2025 dates under a 2026 heading).  These are *typical* closures; the app labels them
"typically closed" and always tells the user to confirm with the library."""
from __future__ import annotations

import datetime as dt


def easter(year: int) -> dt.date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return dt.date(year, month, day)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def last_weekday(year: int, month: int, weekday: int) -> dt.date:
    d = dt.date(year + (month == 12), (month % 12) + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def holidays(country: str, y: int) -> list[tuple[dt.date, str]]:
    e = easter(y)
    gf, em = e - dt.timedelta(days=2), e + dt.timedelta(days=1)
    j1 = (dt.date(y, 1, 1), "New Year's Day")
    d25 = (dt.date(y, 12, 25), "Christmas Day")
    d26 = (dt.date(y, 12, 26), "Boxing Day")
    if country == "US":
        return [j1, (last_weekday(y, 5, 0), "Memorial Day"), (dt.date(y, 7, 4), "Independence Day"),
                (nth_weekday(y, 9, 0, 1), "Labor Day"), (nth_weekday(y, 11, 3, 4), "Thanksgiving"), d25]
    if country == "CA":
        return [j1, (gf, "Good Friday"), (dt.date(y, 7, 1), "Canada Day"),
                (nth_weekday(y, 9, 0, 1), "Labour Day"), (nth_weekday(y, 10, 0, 2), "Thanksgiving"), d25, d26]
    if country == "GB":
        return [j1, (gf, "Good Friday"), (em, "Easter Monday"), (nth_weekday(y, 5, 0, 1), "Early May bank holiday"),
                (last_weekday(y, 5, 0), "Spring bank holiday"), (last_weekday(y, 8, 0), "Summer bank holiday"), d25, d26]
    if country == "AU":
        return [j1, (dt.date(y, 1, 26), "Australia Day"), (gf, "Good Friday"), (em, "Easter Monday"),
                (dt.date(y, 4, 25), "Anzac Day"), d25, d26]
    if country == "NZ":
        return [j1, (dt.date(y, 1, 2), "Day after New Year's Day"), (dt.date(y, 2, 6), "Waitangi Day"),
                (gf, "Good Friday"), (em, "Easter Monday"), (dt.date(y, 4, 25), "Anzac Day"), d25, d26]
    if country == "IE":
        return [j1, (dt.date(y, 3, 17), "St Patrick's Day"), (em, "Easter Monday"), d25, d26]
    return [j1, d25]


def horizon(today: dt.date) -> dt.date:
    return dt.date(today.year + 1, 12, 31)


def exceptions_for(country: str, region: str | None, today: dt.date) -> list[dict]:
    out = []
    start = today - dt.timedelta(days=7)
    end = horizon(today)
    for y in range(today.year, today.year + 2):
        for d, n in holidays(country, y):
            if start <= d <= end:
                out.append({"d": d.isoformat(), "c": 1, "n": n})
    return sorted(out, key=lambda x: x["d"])
