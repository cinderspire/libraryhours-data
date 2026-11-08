#!/usr/bin/env python3
"""Unit tests for the feed builder: python3 -m unittest -v test_feed.py"""
import datetime as dt
import gzip
import json
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_feed as b  # noqa: E402
import holidays_calc as hc  # noqa: E402
import hoursparse as hp  # noqa: E402
import osm_source as osm  # noqa: E402

HERE = Path(__file__).resolve().parent


class HoursParse(unittest.TestCase):
    def test_ranges(self):
        cases = {
            "9:00 AM - 8:00 PM": "09:00-20:00",
            "9am-8pm": "09:00-20:00",
            "10:00 AM – 9 PM": "10:00-21:00",
            "8:00 AM - Midnight": "08:00-24:00",
            "12:00 PM - 5:00 PM": "12:00-17:00",
            "12:00 AM - 6:00 AM": "00:00-06:00",
            "9-5pm": "09:00-17:00",
            "12-5pm": "12:00-17:00",
            "9:00 AM - 1:00 PM, 2:00 PM - 5:00 PM": "09:00-13:00,14:00-17:00",
            "8:00 AM - 2:00 AM": "08:00-02:00",
            "Closed": "closed",
            "24 hours": "24h",
            "Open 24 hours": "24h",
            "09:00 - 17:30": "09:00-17:30",
        }
        for src, want in cases.items():
            self.assertEqual(hp.parse_ranges(src), want, src)

    def test_rejects(self):
        for s in ["", "varies", "by appointment", "9:00", "25:00 - 26:00", "9 AM - 9 AM"]:
            self.assertIsNone(hp.parse_ranges(s), s)

    def test_days(self):
        self.assertEqual(hp.parse_days("Monday - Thursday"), [0, 1, 2, 3])
        self.assertEqual(hp.parse_days("Mon–Fri"), [0, 1, 2, 3, 4])
        self.assertEqual(hp.parse_days("Sat & Sun"), [5, 6])
        self.assertEqual(hp.parse_days("Friday - Monday"), [0, 4, 5, 6])
        self.assertEqual(hp.parse_days("Daily"), list(range(7)))
        self.assertEqual(hp.parse_days("Weekends"), [5, 6])
        self.assertIsNone(hp.parse_days("Holidays"))

    def test_week(self):
        rows = [("Monday - Thursday", "9:00 AM - 8:00 PM"), ("Friday", "9 AM - 6 PM"), ("Saturday", "10 AM - 5 PM"), ("Sunday", "Closed")]
        self.assertEqual(hp.build_week(rows), ["09:00-20:00"] * 4 + ["09:00-18:00", "10:00-17:00", "closed"])
        self.assertIsNone(hp.build_week(rows[:3]))
        self.assertEqual(hp.fill_missing_closed(rows[:3])[6], "closed")
        self.assertIsNone(hp.fill_missing_closed(rows[:1]))


class OsmHours(unittest.TestCase):
    def test_supported(self):
        w = osm.parse_opening_hours("Mo-Fr 09:00-18:00; Sa 10:00-14:00; Su off")
        self.assertEqual(w, ["09:00-18:00"] * 5 + ["10:00-14:00", "closed"])
        self.assertEqual(osm.parse_opening_hours("24/7"), ["24h"] * 7)
        self.assertEqual(osm.parse_opening_hours("Mo-Fr 10:00-20:00; PH off")[5], "closed")
        self.assertEqual(osm.parse_opening_hours("Mo-Sa 08:00-02:00")[0], "08:00-02:00")
        self.assertEqual(osm.parse_opening_hours("Mo,We 09:00-12:00,13:00-17:00")[2], "09:00-12:00,13:00-17:00")

    def test_unsupported_is_rejected_not_guessed(self):
        for t in ["Jun-Aug Mo-Fr 09:00-17:00", "Mo-Fr 09:00+", "Mo-Fr sunrise-sunset", "week 1-4 Mo 10:00-12:00",
                  'Mo-Fr 09:00-17:00 "by appointment"', "Mo-Su off", "", "Mo-Fr 09:00-09:00", "Dec 25 off"]:
            self.assertIsNone(osm.parse_opening_hours(t), t)


class Holidays(unittest.TestCase):
    def test_easter(self):
        self.assertEqual(hc.easter(2026), dt.date(2026, 4, 5))
        self.assertEqual(hc.easter(2027), dt.date(2027, 3, 28))

    def test_us(self):
        d = {x: n for x, n in hc.holidays("US", 2026)}
        self.assertEqual(d[dt.date(2026, 11, 26)], "Thanksgiving")
        self.assertEqual(d[dt.date(2026, 5, 25)], "Memorial Day")
        self.assertEqual(d[dt.date(2026, 9, 7)], "Labor Day")

    def test_gb(self):
        d = {x: n for x, n in hc.holidays("GB", 2026)}
        self.assertIn(dt.date(2026, 4, 3), d)   # Good Friday
        self.assertIn(dt.date(2026, 4, 6), d)   # Easter Monday
        self.assertIn(dt.date(2026, 8, 31), d)  # summer bank holiday
        self.assertIn(dt.date(2026, 12, 26), d)

    def test_horizon_and_sorted(self):
        ex = hc.exceptions_for("US", None, dt.date(2026, 11, 9))
        self.assertEqual(ex[0]["d"], "2026-11-26")
        self.assertEqual(ex, sorted(ex, key=lambda e: e["d"]))
        self.assertLessEqual(ex[-1]["d"], "2027-12-31")


class Gazetteer(unittest.TestCase):
    def test_every_tz_valid_and_coords_sane(self):
        gaz = json.loads((HERE / "gazetteer.json").read_text())
        self.assertGreater(len(gaz), 100)
        for slug, g in gaz.items():
            ZoneInfo(g["tz"])
            self.assertTrue(-90 <= g["lat"] <= 90 and -180 <= g["lon"] <= 180, slug)
            self.assertEqual(len(g["country"]), 2, slug)


class Extraction(unittest.TestCase):
    PAGE_ADDRESS = """<section><h2>Leeds Central Library — Leeds Library Hours</h2><address>
        <strong>Leeds Central Library</strong><br>Calverley St, Leeds LS1 3AB<br>
        <a href="tel:+441133786030">+44 113 378 6030</a><br>
        <a href="https://www.leeds.gov.uk/libraries">Official Website</a></address>
        <table><tr><th>Day</th><th>Hours</th></tr>
        <tr><td>Monday–Friday</td><td>9:00 AM – 7:00 PM</td></tr>
        <tr><td>Saturday</td><td>9:00 AM – 5:00 PM</td></tr>
        <tr><td>Sunday</td><td>10:00 AM – 4:00 PM</td></tr></table></section>"""

    def test_address_template(self):
        recs = b.extract_records("leeds-library-hours", self.PAGE_ADDRESS, "x")
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["name"], "Leeds Central Library")
        self.assertEqual(r["address"], "Calverley St, Leeds LS1 3AB")
        self.assertEqual(r["week"], ["09:00-19:00"] * 5 + ["09:00-17:00", "10:00-16:00"])
        self.assertEqual(r["url"], "https://www.leeds.gov.uk/libraries")

    def test_phone_cleaning(self):
        self.assertEqual(b.clean_phone("+44 113 378 6030", "GB"), "+441133786030")
        self.assertEqual(b.clean_phone("4047301700", "US"), "+14047301700")
        self.assertIsNone(b.clean_phone("+44 (0)1onal937 546060", "GB"))
        self.assertIsNone(b.clean_phone(None, "GB"))

    def test_validate_catches_bad_records(self):
        doc = {"libraries": [{"id": "a", "name": "A", "city": "X", "country": "US", "lat": 95, "lon": 0, "tz": "Mars/Base", "hours": ["closed"] * 6,
                              "exceptions": [{"d": "nope"}]}]}
        problems = b.validate(doc)
        self.assertGreaterEqual(len(problems), 3)

    def test_published_snapshot_if_present(self):
        p = HERE.parent.parent / "app" / "assets" / "data" / "libraries.json.gz"
        if not p.exists():
            self.skipTest("no snapshot")
        doc = json.loads(gzip.decompress(p.read_bytes()))
        self.assertEqual(b.validate(doc), [])
        self.assertLess(p.stat().st_size, 3 * 1024 * 1024)


import gzip
import tempfile
import time

import regions as rg


class OsmGrammar2(unittest.TestCase):
    def test_additive_commas_vs_override_semicolons(self):
        w = osm.parse_hours("Tu,Th 09:00-11:00, Tu 16:00-19:00, We-Fr 14:00-17:00; Mo,Sa off")[0].week
        self.assertEqual(w[1], "09:00-11:00,16:00-19:00")
        self.assertEqual(w[3], "09:00-11:00,14:00-17:00")
        self.assertEqual(w[0], "closed")
        w = osm.parse_hours("Mo-Fr 09:00-18:00; We 10:00-14:00")[0].week
        self.assertEqual(w[2], "10:00-14:00")  # later rule overrides

    def test_ph(self):
        p, _ = osm.parse_hours("Mo-Fr 10:00-20:00; PH off")
        self.assertTrue(p.ph_off)
        p, why = osm.parse_hours("Mo-Fr 10:00-20:00; PH 10:00-12:00")
        self.assertIsNone(p)
        self.assertEqual(why, "PH with opening hours")
        p, _ = osm.parse_hours("Mo-Fr 10:00-20:00")
        self.assertFalse(p.ph_off)

    def test_rejections(self):
        cases = {"Mo-Fr 09:00-17:00; SH off": "school-holiday rule", '"sur rendez-vous"': "unsupported syntax",
                 "Apr-Oct Mo 10:00-12:00": "unsupported syntax", "Mo 09:00-12:00,11:00-14:00": "overlapping ranges",
                 "Mo-Su off": "never open", "": "empty", "Mo-Fr sunrise-sunset": "unsupported syntax"}
        for t, why in cases.items():
            p, w = osm.parse_hours(t)
            self.assertIsNone(p, t)
            self.assertEqual(w, why, t)

    def test_midnight_and_24h_forms(self):
        self.assertEqual(osm.parse_hours("Mo-Sa 08:00-02:00")[0].week[0], "08:00-02:00")
        self.assertEqual(osm.parse_hours("Mo-Su 00:00-24:00")[0].week, ["24h"] * 7)

    def test_ranges_ok(self):
        self.assertTrue(osm.ranges_ok("09:00-12:00,13:00-17:00"))
        self.assertTrue(osm.ranges_ok("08:00-02:00"))
        self.assertFalse(osm.ranges_ok("20:00-02:00,03:00-05:00"))
        self.assertFalse(osm.ranges_ok("09:00-12:00,11:00-14:00"))


class Regions(unittest.TestCase):
    def test_coverage_and_tz(self):
        rs = rg.all_regions()
        keys = {r.key for r in rs}
        self.assertEqual(len([k for k in keys if k.startswith("US-")]), 51)
        self.assertEqual(len([k for k in keys if k.startswith("CA-")]), 13)
        for cc in rg.COUNTRIES:
            self.assertTrue(any(r.cc == cc for r in rs), cc)
        for r in rs:
            ZoneInfo(r.tz)
        self.assertEqual(len(keys), len(rs))

    def test_query(self):
        q = rg.overpass_query([r for r in rg.all_regions() if r.key == "US-CA"][0])
        self.assertIn('"ISO3166-2"="US-CA"', q)
        self.assertIn('["opening_hours"]', q)
        q = rg.overpass_query([r for r in rg.all_regions() if r.key == "IE"][0])
        self.assertIn('"ISO3166-1"="IE"', q)

    def test_tz_overrides_without_timezonefinder(self):
        by = {r.key: r for r in rg.all_regions()}
        rg._tzf_tried, rg._tzf = True, None
        self.assertEqual(rg.resolve_tz(by["US-TX"], 31.76, -106.5), "America/Denver")
        self.assertEqual(rg.resolve_tz(by["US-TX"], 29.7, -95.4), "America/Chicago")
        self.assertEqual(rg.resolve_tz(by["ES"], 28.1, -15.4), "Atlantic/Canary")
        self.assertEqual(rg.resolve_tz(by["AU-NSW"], -31.95, 141.45), "Australia/Sydney")


class FetcherTests(unittest.TestCase):
    def region(self, key="CH"):
        return [r for r in rg.all_regions() if r.key == key][0]

    def test_rotation_and_backoff(self):
        calls, sleeps = [], []

        def post(url, body, timeout):
            calls.append(url)
            if len(calls) < 3:
                raise OSError("down")
            return json.dumps({"elements": [{"type": "node", "id": 1, "lat": 1.0, "lon": 2.0, "tags": {"name": "A Lib", "opening_hours": "24/7", "junk": "x"}}]}).encode()
        f = osm.Fetcher(post=post, sleep=sleeps.append)
        els = f.fetch(self.region("US-CA"))
        self.assertEqual(len(els), 1)
        self.assertEqual(els[0]["id"], "n1")
        self.assertNotIn("junk", els[0]["t"])
        self.assertEqual(len(set(calls)), 3)  # three different mirrors were tried
        self.assertEqual(sleeps, [5.0, 10.0])

    def test_gives_up_and_deadline(self):
        f = osm.Fetcher(post=lambda *a: (_ for _ in ()).throw(OSError("x")), sleep=lambda s: None)
        self.assertIsNone(f.fetch(self.region("US-CA"), attempts=4))
        g = osm.Fetcher(deadline=time.time() - 1, post=lambda *a: b"{}", sleep=lambda s: None)
        self.assertIsNone(g.fetch(self.region("US-CA")))

    def test_remark_without_elements_is_a_failure(self):
        f = osm.Fetcher(post=lambda *a: b'{"elements":[],"remark":"runtime error: out of memory"}', sleep=lambda s: None)
        self.assertIsNone(f.fetch(self.region("US-CA"), attempts=2))

    def test_region_cache(self):
        with tempfile.TemporaryDirectory() as d:
            n = []

            def post(u, b, t):
                n.append(1)
                return b'{"elements":[]}'
            f = osm.Fetcher(post=post, sleep=lambda s: None)
            r = self.region("IE")
            els, src = osm.load_region(Path(d), r, f, 3)
            self.assertEqual(src, "fetched")
            els, src = osm.load_region(Path(d), r, f, 3)
            self.assertEqual(src, "fresh-cache")
            self.assertEqual(len(n), 1)
            els, src = osm.load_region(Path(d), r, osm.Fetcher(post=lambda *a: (_ for _ in ()).throw(OSError()), sleep=lambda s: None), 0)
            self.assertEqual(src, "stale-cache")  # fetch failed, stale cache beats nothing
            _, src = osm.load_region(Path(d), self.region("NO"), None, 3, cache_only=True)
            self.assertEqual(src, "none")

    def test_order_is_stalest_first(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "IE.json").write_text(json.dumps({"fetched": 100, "elements": []}))
            (Path(d) / "NO.json").write_text(json.dumps({"fetched": 50, "elements": []}))
            rs = [r for r in rg.all_regions() if r.key in ("IE", "NO", "FI")]
            self.assertEqual([r.key for r in osm.region_order(Path(d), rs)], ["FI", "NO", "IE"])


class OsmRecords(unittest.TestCase):
    def els(self):
        def e(i, name, hours, **t):
            return {"id": f"n{i}", "lat": 47.37 + i / 1000, "lon": 8.54, "t": {"name": name, "opening_hours": hours, **t}}
        return [
            e(1, "Stadtbibliothek Zürich", "Mo-Fr 10:00-19:00; Sa 10:00-16:00; PH off", **{"addr:street": "Hauptstr", "addr:housenumber": "1", "addr:city": "Zürich", "website": "example.ch/x"}),
            e(2, "Bücherschrank am Platz", "24/7"),
            e(3, "", "Mo-Fr 09:00-17:00"),
            e(4, "Uni Bibliothek", "Mo-Fr 08:00-22:00", **{"operator:type": "university"}),
            e(5, "Private Lib", "Mo-Fr 08:00-12:00", access="private"),
            e(6, "Seasonal Lib", "Jun-Aug Mo-Fr 09:00-17:00"),
            e(7, "Night Lib", "24/7"),
        ]

    def test_filters_and_stats(self):
        stats, seen = {}, set()
        reg = [r for r in rg.all_regions() if r.key == "CH"][0]
        recs = b.osm_records(self.els(), reg, stats, seen)
        self.assertEqual([r["name"] for r in recs], ["Stadtbibliothek Zürich", "Uni Bibliothek", "Night Lib"])
        s = stats["CH"]
        self.assertEqual((s["elements"], s["kept"], s["no_name"], s["not_library"], s["private"]), (7, 3, 1, 1, 1))
        self.assertEqual(s["hours_rejected"], {"unsupported syntax": 1})
        self.assertEqual(recs[0]["ph"], 1)
        self.assertEqual(recs[0]["city"], "Zürich")
        self.assertEqual(recs[0]["url"], "https://example.ch/x")
        self.assertEqual(recs[0]["tz"], "Europe/Zurich")
        self.assertEqual(recs[1]["type"], "university")
        self.assertTrue(recs[2]["h24"])
        self.assertNotIn("ph", recs[2])
        # same elements again produce nothing (ids already seen)
        self.assertEqual(b.osm_records(self.els(), reg, {}, seen), [])

    def test_missing_city_borrows_from_nearest_sibling(self):
        reg = [r for r in rg.all_regions() if r.key == "CH"][0]
        els = [
            {"id": "n1", "lat": 47.370, "lon": 8.540, "t": {"name": "Alpha Bibliothek", "opening_hours": "Mo-Fr 09:00-17:00", "addr:city": "Zürich"}},
            {"id": "n2", "lat": 47.372, "lon": 8.545, "t": {"name": "Beta Bibliothek", "opening_hours": "Mo-Fr 09:00-17:00"}},
            {"id": "n3", "lat": 46.000, "lon": 7.000, "t": {"name": "Gamma Bibliothek", "opening_hours": "Mo-Fr 09:00-17:00"}},
        ]
        recs = {r["name"]: r for r in b.osm_records(els, reg, {}, set())}
        self.assertEqual(recs["Beta Bibliothek"]["city"], "Zürich")
        self.assertEqual(recs["Gamma Bibliothek"]["city"], "Switzerland")  # nothing within 15 km: region label
        self.assertNotIn("_nocity", recs["Beta Bibliothek"])

    def test_compact(self):
        reg = [r for r in rg.all_regions() if r.key == "CH"][0]
        rec = b.osm_records(self.els(), reg, {}, set())[0]
        c = b.compact(rec)
        for k in ("src", "s", "exceptions", "updated", "gq", "type", "h24"):
            self.assertNotIn(k, c)
        self.assertEqual(c["o"], "n1")
        self.assertEqual(c["ph"], 1)

    def test_snapshot_cap(self):
        recs = [{"id": f"osm_n{i}", "o": f"n{i}", "name": f"Library {i} " + "x" * 40, "city": "C", "country": "CH", "lat": 1.0 + i * 1e-5,
                 "lon": 2.0, "tz": "Europe/Zurich", "hours": ["09:00-17:00"] * 7, "address": f"{i} Street"} for i in range(4000)]
        doc = {"schema": 1, "generated": "2026-01-01", "count": len(recs), "holidays": {}, "libraries": recs}
        full = b.snapshot_gz(doc, 10_000_000)
        self.assertEqual(full[1], 0)
        cap = b.snapshot_gz(doc, 20_000)
        self.assertLessEqual(len(cap[0]), 20_000)
        self.assertGreater(cap[1], 0)
        self.assertEqual(json.loads(gzip.decompress(cap[0]))["count"], len(recs) - cap[1])

    def test_validate_compact_flags_overlap_and_ph_without_table(self):
        doc = {"holidays": {}, "libraries": [{"id": "a", "name": "A", "city": "X", "country": "US", "lat": 1, "lon": 2, "tz": "UTC",
                                                 "hours": ["09:00-12:00,11:00-14:00"] + ["closed"] * 6, "ph": 1}]}
        problems = b.validate_compact(doc)
        self.assertTrue(any("overlapping" in p for p in problems))
        self.assertTrue(any("ph flag" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
