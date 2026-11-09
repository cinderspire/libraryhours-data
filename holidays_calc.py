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


def _observed_us(d: dt.date) -> dt.date:
    """A fixed-date federal holiday that falls on a Sunday is observed on the Monday."""
    return d + dt.timedelta(days=1) if d.weekday() == 6 else d


def holidays(country: str, y: int) -> list[tuple[dt.date, str]]:
    e = easter(y)
    gf, em = e - dt.timedelta(days=2), e + dt.timedelta(days=1)
    asc, wm, cc = e + dt.timedelta(days=39), e + dt.timedelta(days=50), e + dt.timedelta(days=60)
    D = lambda m, d: dt.date(y, m, d)  # noqa: E731
    j1 = (D(1, 1), "New Year's Day")
    d25 = (D(12, 25), "Christmas Day")
    d26 = (D(12, 26), "Boxing Day")
    st26 = (D(12, 26), "St Stephen's Day")
    lab = (D(5, 1), "Labour Day")
    epi = (D(1, 6), "Epiphany")
    aug15 = (D(8, 15), "Assumption Day")
    allsaints = (D(11, 1), "All Saints' Day")
    if country == "US":
        return [(_observed_us(D(1, 1)), "New Year's Day"), (nth_weekday(y, 1, 0, 3), "Martin Luther King Jr. Day"),
                (nth_weekday(y, 2, 0, 3), "Presidents' Day"), (last_weekday(y, 5, 0), "Memorial Day"),
                (_observed_us(D(6, 19)), "Juneteenth"), (_observed_us(D(7, 4)), "Independence Day"),
                (nth_weekday(y, 9, 0, 1), "Labor Day"), (nth_weekday(y, 10, 0, 2), "Columbus Day"),
                (_observed_us(D(11, 11)), "Veterans Day"), (nth_weekday(y, 11, 3, 4), "Thanksgiving"),
                (_observed_us(D(12, 25)), "Christmas Day")]
    if country == "CA":
        return [j1, (gf, "Good Friday"), (D(5, 25) - dt.timedelta(days=(D(5, 25).weekday() % 7) or 7), "Victoria Day"),
                (D(7, 1), "Canada Day"), (nth_weekday(y, 9, 0, 1), "Labour Day"),
                (nth_weekday(y, 10, 0, 2), "Thanksgiving"), (D(11, 11), "Remembrance Day"), d25, d26]
    if country == "GB":
        return [j1, (gf, "Good Friday"), (em, "Easter Monday"), (nth_weekday(y, 5, 0, 1), "Early May bank holiday"),
                (last_weekday(y, 5, 0), "Spring bank holiday"), (last_weekday(y, 8, 0), "Summer bank holiday"), d25, d26]
    if country == "AU":
        return [j1, (D(1, 26), "Australia Day"), (gf, "Good Friday"), (em, "Easter Monday"),
                (D(4, 25), "Anzac Day"), d25, d26]
    if country == "NZ":
        return [j1, (D(1, 2), "Day after New Year's Day"), (D(2, 6), "Waitangi Day"),
                (gf, "Good Friday"), (em, "Easter Monday"), (D(4, 25), "Anzac Day"), d25, d26]
    if country == "IE":
        return [j1, (D(3, 17), "St Patrick's Day"), (em, "Easter Monday"), d25, st26]
    if country == "DE":
        return [j1, (gf, "Good Friday"), (em, "Easter Monday"), lab, (asc, "Ascension Day"), (wm, "Whit Monday"),
                (D(10, 3), "German Unity Day"), (D(12, 25), "Christmas Day"), (D(12, 26), "Second Day of Christmas")]
    if country == "AT":
        return [j1, epi, (em, "Easter Monday"), (D(5, 1), "Staatsfeiertag"), (asc, "Ascension Day"), (wm, "Whit Monday"),
                (cc, "Corpus Christi"), aug15, (D(10, 26), "National Day"), allsaints, (D(12, 8), "Immaculate Conception"),
                d25, (D(12, 26), "St Stephen's Day")]
    if country == "CH":
        return [j1, (gf, "Good Friday"), (em, "Easter Monday"), (asc, "Ascension Day"), (wm, "Whit Monday"),
                (D(8, 1), "Swiss National Day"), d25, st26]
    if country == "FR":
        return [j1, (em, "Easter Monday"), lab, (D(5, 8), "Victory in Europe Day"), (asc, "Ascension Day"),
                (wm, "Whit Monday"), (D(7, 14), "Bastille Day"), aug15, allsaints, (D(11, 11), "Armistice Day"), d25]
    if country == "IT":
        return [j1, epi, (em, "Easter Monday"), (D(4, 25), "Liberation Day"), lab, (D(6, 2), "Republic Day"), aug15,
                allsaints, (D(12, 8), "Immaculate Conception"), d25, st26]
    if country == "ES":
        return [j1, epi, (gf, "Good Friday"), lab, aug15, (D(10, 12), "National Day"), allsaints,
                (D(12, 6), "Constitution Day"), (D(12, 8), "Immaculate Conception"), d25]
    if country == "PL":
        return [j1, epi, (em, "Easter Monday"), (D(5, 1), "Labour Day"), (D(5, 3), "Constitution Day"), (cc, "Corpus Christi"),
                aug15, allsaints, (D(11, 11), "Independence Day"), d25, (D(12, 26), "Second Day of Christmas")]
    if country == "NL":
        return [j1, (em, "Easter Monday"), (D(4, 27), "King's Day"), (asc, "Ascension Day"), (wm, "Whit Monday"),
                d25, (D(12, 26), "Second Day of Christmas")]
    if country == "BE":
        return [j1, (em, "Easter Monday"), lab, (asc, "Ascension Day"), (wm, "Whit Monday"), (D(7, 21), "Belgian National Day"),
                aug15, allsaints, (D(11, 11), "Armistice Day"), d25]
    if country == "SK":
        return [j1, epi, (gf, "Good Friday"), (em, "Easter Monday"), lab, (D(5, 8), "Victory over Fascism Day"),
                (D(7, 5), "Saints Cyril and Methodius"), (D(8, 29), "Slovak National Uprising"), allsaints,
                (D(11, 17), "Freedom and Democracy Day"), (D(12, 24), "Christmas Eve"), d25, (D(12, 26), "St Stephen's Day")]
    if country == "CZ":
        return [j1, (gf, "Good Friday"), (em, "Easter Monday"), lab, (D(5, 8), "Liberation Day"),
                (D(7, 5), "Saints Cyril and Methodius"), (D(7, 6), "Jan Hus Day"), (D(9, 28), "Statehood Day"),
                (D(10, 28), "Independence Day"), (D(11, 17), "Freedom and Democracy Day"), (D(12, 24), "Christmas Eve"),
                d25, (D(12, 26), "St Stephen's Day")]
    if country == "PT":
        return [j1, (gf, "Good Friday"), (D(4, 25), "Freedom Day"), lab, (D(6, 10), "Portugal Day"), aug15,
                (D(10, 5), "Republic Day"), allsaints, (D(12, 1), "Restoration of Independence"),
                (D(12, 8), "Immaculate Conception"), d25]
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
