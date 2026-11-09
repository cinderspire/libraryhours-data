# FEED SOURCE UPDATE (2026-11-09): QLever, built locally

OSM now comes from **QLever** (`https://qlever.dev/api/osm-planet`, one SPARQL query for all `amenity=library` with `opening_hours`,
~28,900 objects in seconds) via `qlever_source.py`; the Overpass per-region code stays as `--osm-source overpass`.
Country = `addr:country` when it is a valid ISO code, else the zone.tab country of the zone found by `timezonefinder`
(venv: `python3 -m venv .venv && .venv/bin/pip install timezonefinder`). Zones newer than the app's tz database are aliased
(`TZ_ALIASES`, e.g. America/Nuuk -> America/Godthab; America/Coyhaique dropped). Same strict hours parser as before.

Local build (about 1 minute; `--pages-dir` is optional, without it the ~180 site pages are fetched live, 5-6 minutes):
```
cd research/feed
.venv/bin/python build_feed.py --out out_full --geocode 0 --min-records 2000          # fetches QLever, caches cache/qlever/planet.json.gz
.venv/bin/python build_feed.py --out out_full --osm-cache-only ...                     # rebuild from the cached QLever answer
```
Publishable files: `out/libraries.json`, `out/libraries.json.gz`, `out/validation_report.json`, `out/LICENSE-DATA.txt`.
Result of 2026-11-09: 24,912 libraries (54 site + 24,858 OSM), 187 countries, 8,527 cities, 0 validation problems, 8.6 MB json / 1.59 MB gz.

The GitHub Actions section below still describes the (blocked) Overpass cron; when the workflow scope exists, change its build step to
`python3 build_feed.py --out public --geocode 0 --min-records 2000` (QLever is the default source) and drop the Overpass cache steps.

---

# Running the feed on GitHub Actions (cinderspire/libraryhours-data)

Overpass is unreliable from the build Mac, so the real feed is built on GitHub Actions. The builder, the unit tests and the
workflow are ready; only the repo has to be created. Nothing here needs a secret or a token beyond the default `GITHUB_TOKEN`.

## 1. Repo layout (push exactly this; flat root, no sub-folders except `.github`)
```
libraryhours-data/
  .github/workflows/feed.yml     <- copy of research/feed/workflow.yml
  build_feed.py                  builder (site pages + OpenStreetMap per region), CLI below
  regions.py                     117 Overpass regions: 50 US states + DC, 13 CA provinces, 8 AU states, 16 DE Laender,
                                 13 FR regions, 4 GB nations, IE NZ AT CH NL BE ES IT SE NO DK FI; tz defaults/overrides
  osm_source.py                  Overpass fetcher (3 mirrors, backoff, per-region cache), opening_hours parser
  hoursparse.py                  parser for the libraryhours24.com text hours
  holidays_calc.py               typical public-holiday closures (computed, 2 years ahead)
  gazetteer.json  gazetteer_src.txt  make_gazetteer.py   city centres + zones for the site pages
  geocache.json                  Nominatim cache for the site records (so Actions never geocodes)
  test_feed.py                   32 unit tests (run by the workflow before building)
  DATA_LICENSE.txt               ODbL notice, copied to the site as LICENSE-DATA.txt
  requirements.txt               timezonefinder (exact zones from coordinates)
  .gitignore                     cache/ out/ __pycache__/ .osm-cache/
```
Do not push `cache/`, `out/` or `__pycache__/`. All of these files are in
`/Users/mac/Desktop/Saas Projeleri/apps/08-library-hours/research/feed/` (copy the list above; `workflow.yml` becomes `.github/workflows/feed.yml`).

## 2. Repo settings
1. Settings > Pages > Build and deployment > Source: **GitHub Actions**.
2. Settings > Actions > General > Workflow permissions: read is enough (the workflow declares `pages: write`, `id-token: write`).
3. Run Actions > build-feed > **Run workflow** once. Later it runs daily at 03:17 UTC.

## 3. What a run does (timeout 50 min)
1. `pip install timezonefinder`; restore the per-region Overpass cache (`actions/cache`, newest `osm-*`).
2. `python3 -m unittest test_feed` (32 tests).
3. `python3 build_feed.py --out public --osm-cache .osm-cache --max-minutes 36 --geocode 0 --min-records 2000`
   - site pages: ~180 pages from libraryhours24.com (polite, retries on 429); 54-ish usable libraries;
   - OpenStreetMap: one Overpass query per region (`amenity=library` + `opening_hours`), oldest cache first, three mirrors rotated with
     5/10/20/40/60 s backoff, 240 s request timeout, stops querying after 36 minutes and then uses cached regions
     (a region fetched by an earlier run stays valid for 3 days; stale cache beats nothing). Progress is cumulative across runs.
   - only records whose hours parse completely into the 7-day model are kept (`PH off` understood; seasonal, week-numbered,
     sunrise, open-ended, school-holiday, public-holiday-hours and appointment-only tags are rejected, never guessed);
   - outputs `public/libraries.json` (full feed), `public/libraries.json.gz` (gzip of the same, fits < 3 MB by construction; the app
     bundles this file), `public/validation_report.json`, `public/LICENSE-DATA.txt`.
4. Sanity gate: publish only if there are **no validation problems**, **>= 2,000 records**, >= 60 % of the currently published
   count and the gz snapshot is <= 3,000,000 bytes. Otherwise the job fails and the previous feed stays live.
5. Deploy to Pages: `https://cinderspire.github.io/libraryhours-data/libraries.json` (the app's `FEED_URL`).

## 4. What to send back
- the run URL and the content of `validation_report.json` (published count, `by_country`, `osm_run.missing`, `minutes`);
- **parse rate per country** is in the same file under `osm_parse_rates_by_country` (kept / with-hours-and-named, reject reasons);
- then I bundle a fresh snapshot: download `libraries.json.gz` from the Pages site, put it at `app/assets/data/libraries.json.gz`.
  (Local check on Switzerland, real data: 546 OSM elements, 461 kept = 85.5 % parse rate, 59 libraries observe `PH off`.)

## 5. Local commands (same code)
```
python3 -m unittest -v test_feed
python3 build_feed.py --out out --osm-regions CH                  # one region (CH works through overpass.osm.ch even from the Mac)
python3 build_feed.py --out out --osm-cache-only --osm-regions all  # rebuild from cached regions, no network for OSM
```
Feed schema 1 (additive): top-level `holidays` (country -> typical closure dates, shared), per record `ph:1` (observes public holidays),
`o` (OSM object, e.g. `n123`), omitted defaults (`type=public`, `gq=address`, `h24` derived). The app reads both this and the older form.
