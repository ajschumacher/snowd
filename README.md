# snowd — Epic Pass snow tracker

Tracks snow conditions across the Epic Pass resorts over a season, stores the
history as CSV in this repo, and ships a static map + table viewer.

**Live site: <https://planspace.org/snowd/>** — updated every four hours.

![resorts](https://img.shields.io/badge/resorts-59-blue) ![reporting](https://img.shields.io/badge/with%20snow%20data-44-blue) ![source](https://img.shields.io/badge/source-resort%20snow%20reports-blue)

## Why this exists

[OpenSnow](https://opensnow.com/explore/season-passes/epicpass/snow-report),
[OnTheSnow](https://www.onthesnow.com/epic-pass/skireport) and
[Skiresort.com](https://www.skiresort.com/en/snow-reports/epic-pass/) all show
current Epic Pass conditions. What none of them give you is a **history you
own** — a per-resort record of how base depth and cumulative snowfall moved
through the season, in a file you can query. That's the gap this fills.

## Where the numbers come from

Each resort's `data_source` names an **adapter** — a pair of "where to GET" and
"how to read what comes back". One adapter can serve many resorts, which is the
point: the four western RCR sites publish byte-for-byte the same field names,
so they cost one parser between them, not four.

**`vail_cms`** (35 resorts) — the page embeds its snow report server-side:

```js
FR.snowReportData = {"BaseDepth":{"Inches":"18","Centimeters":"45"}, ...}
```

It carries **both units for every metric**, so nothing is ever converted for a
Vail resort. The page also has `FR.forecasts` (5-day + hourly, dual-unit),
captured too.

**`rcr_refresh`** (4 resorts) — Resorts of the Canadian Rockies run an
undocumented endpoint, `/refresh/refresh.php`, which returns JavaScript that
assigns each figure into the page by element id:

```js
document.getElementById('snowReportNewSnowFallPack').innerHTML= "10 ";
```

Fernie, Kicking Horse, Kimberley and Nakiska use identical ids, so the adapter
needs no per-resort configuration at all — the endpoint derives from the
domain. It gives *more* than Vail does: lifts and runs open, and runs groomed.
Centimetres only.

**`telluride`** (1 resort) — server-rendered HTML, read by the visible label
beside each figure rather than by markup position, so a reordering of the panel
cannot silently misattribute a number:

```html
<span>Snow depth</span> <span><strong>12"</strong></span>
```

Also lifts and trails open. Inches only.

**`rusutsu`** (1 resort) — label/value pairs in the page, published **per
mountain** rather than per resort: West Mt., East Mt. and Mt. Isola each get
their own reading and there is no resort-wide figure, so the deepest wins. Its
"Update Date" is day-first (`23/10/2026`), which a month-first reading would
silently turn into a different date for any day below the 13th, so that format
is pinned in the adapter rather than guessed. Centimetres only.

**`kitzsteinhorn`** (1 resort) — base depth at several measuring stations up
the mountain (valley 0 cm, mid 50 cm, glacier 75 cm) and *nothing else*: no
season total, no recent snowfall. The highest station wins, flagged
`summit_readings`. Its table is headed by a day and month with **no year**, so
the adapter picks the year that puts the date nearest today. Centimetres only.

**`arlberg`** (1 resort) — labelled boxes giving valley and mountain depth
separately; mountain is what base depth means everywhere else here. Its "New
snow" figure is deliberately **not** captured: it sits beside "Last snowfall:
Wednesday" with no stated window, so filing it as 24-hour snowfall would be
inventing a measurement. Centimetres only.

**`soelden`** (1 resort) — read off the **home page**, because its own
Schneebericht page renders client-side and the home-page tiles do not. Depth
comes as a valley-to-mountain range (`0-48 cm`); the upper figure is the
mountain. The locale is pinned to `/en`: left to negotiate, the site answers
our `Accept-Language` with English, so a German label would match in a browser
and never in the fetch. The snow tile is found by its `cm` value rather than
its label, and "14 of 31" / "14 von 31" is matched as "digits, not-digits,
digits", so either language parses. Centimetres only.

### Units, and what "converted" means

Only Vail publishes both units. The partner sources publish one, so the other
is arithmetic — and converting is not the same as reporting. Every row records
which unit it was actually handed in (`units_native`), and the viewer marks a
computed figure `≈`: switch units and Fernie's `≈3.9in` becomes its own
`10cm`. Zero is exempt — 0 cm is exactly 0 in.

Fetching is a plain HTTP GET with an honest `User-Agent`. Every path used is
permitted by its site's `robots.txt`.

## Coverage

| | Count | Snow data | Adapter |
|---|---|---|---|
| Vail-operated, North America | 35 | ✅ full | `vail_cms` |
| RCR, western Canada | 4 | ✅ full | `rcr_refresh` |
| Telluride | 1 | ✅ full | `telluride` |
| Rusutsu | 1 | season total | `rusutsu` |
| Kitzsteinhorn | 1 | base depth | `kitzsteinhorn` |
| Ski Arlberg | 1 | base depth | `arlberg` |
| Sölden | 1 | base depth, lifts | `soelden` |
| Vail-operated, Australia | 3 | location only | — |
| Partner / international | 12 | location only | — |

Not every source publishes every metric, and the schema does not pretend
otherwise: Kitzsteinhorn gives a base depth and nothing else, Rusutsu a season
total and nothing else (its per-mountain depths read `--` until it opens in
late November). A resort that reports some metrics but not the one the map is
coloured by shows as a filled grey marker reading "not reported", distinct from
the hollow "no report yet".

The 15 resorts without snow data are **mapped and in the table** with
coordinates and metadata, marked "no report yet". Adding one means writing an
adapter and setting its `data_source` in `data/resorts.csv`; no schema or UI
change is needed.

Three of those are blocked by the calendar rather than by the site: Perisher,
Falls Creek and Mount Hotham ski the southern winter, and their sites serve
summer content until June. RCR's two Quebec resorts (Mont-Sainte-Anne,
Stoneham) run the same `refresh.php` mechanism under different field names, and
publish no base depth at all while closed — worth revisiting once they open in
December. The rest (Verbier, Crans-Montana, Les 3 Vallées, Skirama, Saalbach,
Zillertal, Hakuba, Silvretta Montafon and Schmitten) publish nothing in their
server HTML at all — every figure arrives client-side — so each would need a
headless browser rather than another parser.

**On shared vendors.** Intermaps, which three of these sites use, does expose a
single uniform endpoint — `winter.intermaps.com/{map}/data?lang=en`, returning
every lift and slope with a status and a `lastUpdate`. It carries **no snow
depth**, so it would add lift counts to one resort already read here and give
two others lift-only rows in a snow tracker. Not taken. feratel turned out not
to be a snow vendor on these sites at all: Andermatt mentions it only as a
guest-card string, and fetches its own `snowHeightMountain` client-side.

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

Because those are two separately cached files, the page loads `data.js` with a
cache-busting query when served over http(s) (never on `file://`, where some
browsers read the query as part of the filename). Without it a browser can
hold a stale `data.js` against a fresh `index.html` and quietly show old
numbers — and this site is served with `cache-control: max-age=2678400`, so
"stale" means up to 31 days.

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

`data_source` names the adapter that reads this resort, and is empty for the
ones nothing reads yet — it is the switch that brings a resort into the fetch.
`snow_report_url` is always **the page a person reads**, and what the viewer
links to; where an adapter talks to a different endpoint it derives that
itself.

### `data/snow_<season>.csv` — the history

One row per resort per published report. Both units for every metric:

`observed_at`, `season`, `resort_id`, `report_updated_utc`,
`report_updated_text`, `base_depth_{in,cm}`, `season_total_{in,cm}`,
`snow_{12h,24h,48h,7d}_{in,cm}`, `overall_conditions`, `summit_readings`,
`vail_resort_id`, `units_native`, `lifts_{open,total}`,
`runs_{open,total,groomed}`.

`observed_at` is when we fetched; `report_updated_utc` is when the resort says
it last refreshed. The gap between them is what staleness is measured on.

Some sources publish no update time at all (Ski Arlberg, Sölden). For those,
de-duplication falls back to the values plus the fetch date — one row a day
unless the numbers move — and the viewer marks the age it shows, because
"just now" under a column headed *Updated* would claim a freshness the resort
never stated.

`units_native` is `in,cm` where the source published both and `in` or `cm`
where it published one — so a converted figure is always identifiable as one.
The lift and run counts are blank unless the source publishes them.

### `data/forecast_<season>.csv` — the forecast

One row per resort per forecast day per fetch day: temperatures in °F and °C,
day/night snowfall ranges in both units, conditions and wind.

Daytime and overnight snowfall are stored separately and verbatim, exactly as
the source publishes them — as display strings, which may be a single value
(`4`), a range (`0-2`), `Nil`, or empty. The viewer adds the two together into
one figure per date, bound-wise for ranges (`0-2` plus `0-2` is `0-4`), and
keeps the split in the cell's hover text. Only the display combines them; the
CSV keeps both.

### Duplicate handling

A snow row is "the same row" when it carries the same resort and the same
publish timestamp, so re-running the fetch never appends a second copy of an
unchanged report. Where a resort publishes no timestamp, the measured values
plus the fetch date are used instead.

Forecast rows are keyed on resort + target date + fetch date. A re-fetch on the
same day replaces that day's row only when the forecast has actually moved —
compared on the forecast's own fields, ignoring `observed_at`. Without that,
every run would rewrite all ~180 rows with nothing but a new fetch timestamp,
and the four-hourly CI job would commit noise six times a day.

For the same reason `site/data.js` carries `data_as_of` (the freshest
observation in the data) rather than a "generated now" stamp: it is a pure
function of the CSVs, so it only changes when they do. `data/last_run.json` is
a local run log and is gitignored — the CI logs carry the same information.

### Seasons

Season labels are derived, not hardcoded: northern-hemisphere seasons get a
span (`2026-27`, cutover 1 July), southern-hemisphere a single year (`2026`).
Files are per-season and the viewer discovers them from the directory, so a
future season needs no code change — just keep running the fetch.

A row's season comes from **the report's own date, not the date we fetched
it**. Resorts leave last season's final report up all summer, so a report
stamped June 2026 that we pull in November 2026 is 2025-26 data; filing it by
fetch date would plant a bogus zero reading at the start of 2026-27 and make
the resort look like it had opened and reported nothing. Those rows live in
`snow_2025-26.csv`, and the resort correctly reads "not yet" for the current
season until it actually publishes. Forecast rows likewise take the season of
the day they forecast.

The same principle runs through the viewer: charts are plotted against the
resort's publish time, a resort's history is ordered by it, and "newest
report" in the header is the freshest publish time across all resorts.
`observed_at` records only when we polled, and is a fallback for the rare
resort that publishes no timestamp at all.

## Layout

```
data/      resorts.csv, snow_<season>.csv, forecast_<season>.csv
           last_run.json (local run log, gitignored)
scripts/   snowd.py (library + adapters), fetch.py (CLI), build_site_data.py
           test_snowd.py (offline self-checks)
site/      index.html (viewer), data.js (generated)
tests/     fixtures/ — a captured response per adapter, so the parsers are
           tested against real markup without touching the network
```

## Known gaps

- **Lifts and runs open** are captured only where the source volunteers them
  (RCR and Telluride). Vail's snow report page does not embed them at all, and
  its lift-status pages are not uniform across resorts, so for the 35
  Vail-operated resorts those columns stay blank.
- **No forecast for the partner resorts.** Vail's page ships a structured
  5-day forecast; RCR publishes only a two-day temperature outlook and
  Telluride an unstructured widget, so neither is captured.
- **Partial reports are normal.** Four partner sources publish a base depth and
  little else. Those resorts sort and map on base depth and show "not reported"
  for the rest, rather than a zero that was never measured.
- **No backfill.** History starts the day the first fetch ran; the charts fill
  in from there.
- **Partner resorts** have no snow source yet (see Coverage).
- The resort sites are a CMS, not an API. If Vail changes the page structure,
  the parser needs updating — a resort that starts failing shows as `error` in
  the run output and `data/last_run.json`.
