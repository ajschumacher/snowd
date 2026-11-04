# snowd — Epic Pass snow tracker

Tracks snow conditions across the Epic Pass resorts over a season, stores the
history as CSV in this repo, and ships a static map + table viewer.

**Live site: <https://ajschumacher.github.io/snowd/>** — updated every four hours.

![resorts](https://img.shields.io/badge/resorts-59-blue) ![source](https://img.shields.io/badge/source-resort%20snow%20reports-blue)

## Why this exists

[OpenSnow](https://opensnow.com/explore/season-passes/epicpass/snow-report),
[OnTheSnow](https://www.onthesnow.com/epic-pass/skireport) and
[Skiresort.com](https://www.skiresort.com/en/snow-reports/epic-pass/) all show
current Epic Pass conditions. What none of them give you is a **history you
own** — a per-resort record of how base depth and cumulative snowfall moved
through the season, in a file you can query. That's the gap this fills.

## Where the numbers come from

Every Vail-operated resort site embeds its snow report server-side as JSON:

```js
FR.snowReportData = {"BaseDepth":{"Inches":"18","Centimeters":"45"}, ...}
```

Two things follow from that, and they matter:

- **Nothing is ever converted.** The source publishes both inches and
  centimetres for every metric, so both are stored verbatim. The viewer shows
  inches by default and can switch to cm without any arithmetic in between.
- **It is the resort's own number**, the same one on their public snow report
  page — not a third party's estimate.

The page also carries `FR.forecasts` (5-day + hourly, dual-unit), which is
captured too.

Fetching is a plain HTTP GET with an honest `User-Agent`; `/the-mountain/` and
`/api/` are permitted by these sites' `robots.txt`.

## Coverage

| | Count | Snow data |
|---|---|---|
| Vail-operated, North America | 35 | ✅ full |
| Vail-operated, Australia | 3 | location only |
| Partner / international | 21 | location only |

The 24 resorts without snow data are **mapped and in the table** with
coordinates and metadata, marked "no report yet" — each is on its own bespoke
platform (Verbier, Hakuba, Telluride, RCR Canada, the Austrian and French
partners, and the southern-hemisphere resorts). Adding one is a matter of
writing a parser and setting its `data_source` in `data/resorts.csv`; no schema
or UI change is needed.

Early in the season most North American resorts have not published a report
yet — they appear as hollow markers and fill in as they open. Some still show
*last* season's final report; anything older than three days is flagged amber
in the table and called out in the detail panel.

All 59 coordinates were checked against Wikidata's ski-resort set: 56 sit
within 8 km of a known ski area with a matching name. The other three
(Roundtop, Zell am See-Kaprun, Skirama Dolomiti) are absent from or ambiguous
in that dataset and were anchored deliberately — see the comments in the
validation note below.

## Usage

Fetch current reports, append new rows, rebuild the viewer's data:

```bash
python3 scripts/fetch.py
```

Other options:

```bash
python3 scripts/fetch.py --dry-run --only vail,keystone
```

Then open the viewer:

```bash
python3 -m http.server 8777 --directory site
```

`site/index.html` also works opened straight off disk — the data ships as
`site/data.js` rather than a `fetch()` of the CSVs, which browsers block over
`file://`.

### Keeping it updated

`.github/workflows/update.yml` runs every four hours (at :45), commits any new
rows, and redeploys the site. Nothing depends on it being punctual: rows are
keyed on each resort's own publish timestamp, so a run that finds no fresh
report writes nothing and commits nothing, and skipping three days just leaves
a gap.

The fetch and the Pages deploy are deliberately in the **same** workflow run. A
commit pushed with `GITHUB_TOKEN` does not trigger other workflows, so a
separate push-triggered Pages workflow would never see the data the fetch had
just committed.

To run it locally instead, any scheduler works — e.g. a launchd agent or
`45 */4 * * * cd /path/to/snowd && /usr/bin/python3 scripts/fetch.py`.

## Data

### `data/resorts.csv` — the registry

One row per resort: `resort_id`, `name`, `operator`, `epic_tier`, `country`,
`region`, `state`, `domain`, `snow_report_url`, `latitude`, `longitude`,
`timezone`, `data_source`.

`data_source` is `vail_cms` for resorts we can read and empty otherwise — it is
the switch that brings a resort into the fetch.

### `data/snow_<season>.csv` — the history

One row per resort per published report. Both units for every metric:

`observed_at`, `season`, `resort_id`, `report_updated_utc`,
`report_updated_text`, `base_depth_{in,cm}`, `season_total_{in,cm}`,
`snow_{12h,24h,48h,7d}_{in,cm}`, `overall_conditions`, `summit_readings`,
`vail_resort_id`.

`observed_at` is when we fetched; `report_updated_utc` is when the resort says
it last refreshed. The gap between them is what staleness is measured on.

### `data/forecast_<season>.csv` — the forecast

One row per resort per forecast day per fetch day: temperatures in °F and °C,
day/night snowfall ranges in both units, conditions and wind.

### Duplicate handling

A snow row is "the same row" when it carries the same resort and the same
publish timestamp, so re-running the fetch never appends a second copy of an
unchanged report. Where a resort publishes no timestamp, the measured values
plus the fetch date are used instead. Forecast rows are keyed on resort +
target date + fetch date, latest write winning.

### Seasons

Season labels are derived, not hardcoded: northern-hemisphere seasons get a
span (`2026-27`, cutover 1 July), southern-hemisphere a single year (`2026`).
Files are per-season and the viewer discovers them from the directory, so a
future season needs no code change — just keep running the fetch.

## Layout

```
data/      resorts.csv, snow_<season>.csv, forecast_<season>.csv, last_run.json
scripts/   snowd.py (library), fetch.py (CLI), build_site_data.py
site/      index.html (viewer), data.js (generated)
```

## Known gaps

- **Lifts, runs and acres open** are not captured. They are not embedded in the
  snow report page the way the snow numbers are, and the lift-status pages are
  not uniform across resorts — it would need a second per-resort scraper.
- **No backfill.** History starts the day the first fetch ran; the charts fill
  in from there.
- **Partner resorts** have no snow source yet (see Coverage).
- The resort sites are a CMS, not an API. If Vail changes the page structure,
  the parser needs updating — a resort that starts failing shows as `error` in
  the run output and `data/last_run.json`.
