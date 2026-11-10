# snowd — an Epic + Ikon snow tracker

Checks the Epic Pass and Ikon Pass resorts' snow reports a few times a day,
keeps the numbers in a CSV, and draws them on a map.

**Live site: <https://planspace.org/snowd/>**

![resorts](https://img.shields.io/badge/resorts-77-blue) ![reporting](https://img.shields.io/badge/with%20snow%20data-62-blue) ![source](https://img.shields.io/badge/source-resort%20snow%20reports-blue)

## Why

[OpenSnow](https://opensnow.com/explore/season-passes/epicpass/snow-report),
[OnTheSnow](https://www.onthesnow.com/epic-pass/skireport) and
[Skiresort.com](https://www.skiresort.com/en/snow-reports/epic-pass/) all do a
nice job of showing you today. I wanted to be able to look back at a whole
season afterwards — when the storms came through, how the base built up, which
places were quietly doing well in February — and have it in a file I could poke
at. So this writes the numbers down each day. That's really all it is.

A note up front: **the resorts do the actual work here.** They measure the
snow, they publish it for free, and none of them owe anybody an API. This just
reads the same pages you'd read, a few times a day, and saves what they said.
If a number looks off, it's far more likely that this got it wrong than that
the resort did.

## Where the numbers come from

Each resort has a `data_source` in the registry, naming an **adapter** — just a
pair of "where to fetch" and "how to read it". One adapter can cover several
resorts when they're built the same way.

**`vail_cms`** (35 resorts) — the page already has the snow report in it, as
JSON:

```js
FR.snowReportData = {"BaseDepth":{"Inches":"18","Centimeters":"45"}, ...}
```

Nicely, it carries **both units for every metric**, so nothing has to be
converted for a Vail resort. There's a `FR.forecasts` alongside it (5-day plus
hourly, also dual-unit), which gets saved too.

**`rcr_refresh`** (4 resorts) — Resorts of the Canadian Rockies have an
endpoint at `/refresh/refresh.php` that hands back JavaScript, assigning each
figure into the page by element id:

```js
document.getElementById('snowReportNewSnowFallPack').innerHTML= "10 ";
```

It isn't documented anywhere, so it could change without warning. Fernie,
Kicking Horse, Kimberley and Nakiska all use the same ids, so one adapter
covers the four of them and the URL comes from the domain. They're generous
with it, too — lifts open, runs open, runs groomed, which Vail's pages don't
include. Centimetres.

**`telluride`** (1 resort) — plain server-rendered HTML. Read by the label next
to each figure rather than by position, so if they rearrange the panel we get
nothing rather than the wrong number:

```html
<span>Snow depth</span> <span><strong>12"</strong></span>
```

Lifts and trails open as well. Inches.

Its "Last Updated" line is **not** a publish time — it's when the page was
rendered. Fetch it twice a minute apart and it follows the clock. Stored as a
publish time it made every single fetch look like a fresh report, so it's kept
as text and never as a timestamp, and Telluride de-duplicates on its values
like the other sources that publish no update time.

**`rusutsu`** (1 resort) — label/value pairs, published **per mountain**: West
Mt., East Mt. and Mt. Isola each get their own reading and there's no single
resort-wide figure, so we take the deepest. That's a judgement call, not
something they told us to do. Their "Update Date" is day-first (`23/10/2026`),
so that format is spelled out in the adapter — read month-first it'd be fine on
the 23rd and quietly wrong on the 10th. Centimetres.

**`kitzsteinhorn`** (1 resort) — base depth at a few stations up the mountain
(valley 0 cm, mid 50 cm, glacier 75 cm), and that's all they publish: no season
total, no recent snowfall. We take the highest station and flag it with
`summit_readings`. Their table is headed with a day and month but no year, so
the adapter picks whichever year lands nearest today. Centimetres.

**`arlberg`** (1 resort) — labelled boxes, valley and mountain depth given
separately; mountain is what base depth means everywhere else here.

Their "New snow" figure isn't captured, which might look like an oversight. It
sits right next to "Last snowfall: Wednesday", and I couldn't work out whether
it means the last 24 hours or the total from that storm. Rather than guess and
file it as 24-hour snowfall, it's left out. If you know how Arlberg means it,
that'd be a welcome correction. Centimetres.

**`mtn_powder`** (18 resorts) — every Alterra resort is served by one public
JSON feed:

```
https://mtnpowder.com/feed?resortId=60
```

No token, and `mtnpowder.com/robots.txt` allows it. (There is a `v3.json`
endpoint that wants a bearer token lifted from the page config — we don't need
it and don't use it.) Like Vail it gives **both units for every figure**, so
nothing is converted, and it adds its own publish time, lift and trail counts,
and base depth at three elevations — Summit, Mid Mountain and Base.

**Mid-mountain is the one taken**, then the base area, and the summit only when
it's all that's given. Reaching for the summit first gave outliers rather than
useful numbers: Crystal Mountain publishes 0" at the base, 0" at mid-mountain
and 70" at the summit, and Mammoth 12" / 15" / 20". Whichever was used is
recorded in `reading_level`, since picking one of three numbers is a judgement
call and shouldn't be invisible.

This also sidesteps a problem rather than solving it: five of those resorts'
own websites (Steamboat, Snowshoe, Stratton, Crystal, Tremblant) sit behind an
Incapsula WAF that refuses us even `robots.txt`. Reading the feed means never
knocking on those doors.

**`soelden`** (1 resort) — read off the **home page**, because their own
Schneebericht page builds itself in the browser and the home-page tiles don't.
Depth arrives as a valley-to-mountain range (`0-48 cm`) and we take the upper
figure. The URL is pinned to `/en`: left to its own devices the site answers
our `Accept-Language` header with English, which is how the first version of
this adapter came to work perfectly against a German test file and find nothing
at all on the real site. Now the snow tile is located by its `cm` value instead
of its label, and "14 of 31" / "14 von 31" both parse. Centimetres.

### About units

Only the Vail sites publish both units. Everywhere else we get one and compute
the other, and that's worth being clear about, because a converted number isn't
quite the same thing as a reported one. Every row records which unit actually
came from the resort (`units_native`), and the viewer marks anything computed
with `≈` — switch the toggle and Fernie's `≈3.9in` turns back into its own
`10cm`. Zero doesn't get marked, since 0 cm is 0 in either way.

### Being a decent guest

Fetching is a plain HTTP GET with an honest `User-Agent` that says what this is
and links to the repo. Every path used is allowed by the site's `robots.txt`.
It runs a few times a day, not continuously, and a run that finds nothing new
writes nothing. If you fork this, please keep it that way — these are small
operations putting data out for free.

## Coverage

| | Pass | Count | Snow data | Adapter |
|---|---|---|---|---|
| Alterra-operated | Ikon | 18 | full | `mtn_powder` |
| Vail-operated, North America | Epic | 35 | full | `vail_cms` |
| RCR, western Canada | Epic | 4 | full | `rcr_refresh` |
| Telluride | Epic | 1 | full | `telluride` |
| Rusutsu | Epic | 1 | season total | `rusutsu` |
| Kitzsteinhorn | Epic | 1 | base depth | `kitzsteinhorn` |
| Ski Arlberg | Epic | 1 | base depth | `arlberg` |
| Sölden | Epic | 1 | base depth, lifts | `soelden` |
| Vail-operated, Australia | Epic | 3 | location only | — |
| Partner / international | Epic | 12 | location only | — |

Plenty of sources publish only part of a report, and the schema just leaves the
rest blank rather than filling in a zero nobody measured. Kitzsteinhorn gives a
base depth and nothing else; Rusutsu gives a season total and nothing else, at
least until it opens in late November. A resort that's reporting but doesn't
publish whatever the map is coloured by shows as a filled grey marker saying
"not reported", which is different from the hollow "no report yet".

### The rest of Ikon

The 18 above are Alterra's own. The same feed also lists roughly 46 more Ikon
destinations — Aspen's four mountains, Alta, Snowbird, Jackson Hole, Big Sky,
Killington, Revelstoke, Taos, Niseko's four, SkiBig3, the Dolomites, the New
Zealand and Australian hills — but with a forecast and **no snow report**. Each
of those would need its own adapter against its own site, which is the same
grind as the Epic partners and runs into the same client-rendering wall. They
aren't in the registry yet.

### Telling the two passes apart

Colour on the map already carries snow depth, so the pass is carried by shape:
a circle for Epic, a square for Ikon, a diamond for a resort on both. The
squares are drawn slightly smaller than the circles they sit beside, because a
square of equal width covers about 4/π as much ink and would otherwise read as
more snow. There's a pass filter above the table, and each row says which pass
it's on.

Nothing is on both passes today, so the diamond is unused — but `passes` is a
list precisely because that can change, and a resort like Telluride turns up on
some Ikon products while being an Epic partner.

The 15 Epic resorts with no snow data are still on the map and in the table with
their coordinates — just marked "no report yet". Adding one means writing an
adapter and filling in `data_source` in `data/resorts.csv`; nothing else has to
change.

A few of those are only waiting on the calendar. Perisher, Falls Creek and
Mount Hotham ride the southern winter, so their sites are showing summer until
June. RCR's two Quebec hills (Mont-Sainte-Anne, Stoneham) run the same
`refresh.php` setup under different field names and don't post a base depth
while they're closed — worth another look when they open in December.

The rest — Verbier, Crans-Montana, Les 3 Vallées, Skirama, Saalbach, Zillertal,
Hakuba, Silvretta Montafon and Schmitten — build their numbers in the browser,
so there's nothing in the HTML to read. Reaching them would mean running a
headless browser on a schedule, which felt like more machinery than this
deserves. Could be wrong about that.

**On shared vendors.** Three of those sites use Intermaps, which does have one
tidy endpoint — `winter.intermaps.com/{map}/data?lang=en` — returning every
lift and slope with a status and a `lastUpdate`. There's no snow depth in it,
though, so using it would mostly mean adding lift counts to a snow tracker.
Left alone for now. feratel turned out to be a false lead entirely: Andermatt
mentions it in a guest-card string and fetches its own snow heights in the
browser.

Early season, most North American resorts just haven't posted yet — they show
as hollow markers and fill in as they open. A few are still showing *last*
season's final report; anything over three days old gets flagged amber in the
table.

All 59 coordinates were checked against Wikidata's ski-resort entries, and 56
sit within 8 km of a known ski area with a matching name. The other three
(Roundtop, Zell am See-Kaprun, Skirama Dolomiti) aren't clearly in that dataset
and were placed by hand, so they're the most likely to be a bit off.

## Usage

Fetch current reports, append anything new, rebuild the viewer's data:

```bash
python3 scripts/fetch.py
```

Have a look without writing anything:

```bash
python3 scripts/fetch.py --dry-run --only vail,keystone
```

Check the adapters against the live sites, rather than the saved fixtures:

```bash
python3 scripts/check_live.py
```

Open the viewer:

```bash
python3 -m http.server 8777 --directory site
```

`site/index.html` also works opened straight off disk — the data ships as
`site/data.js` rather than a `fetch()` of the CSVs, which browsers block over
`file://`.

Since those are two separately cached files, the page loads `data.js` with a
cache-busting query when it's served over http(s), though never on `file://`,
where some browsers treat the query as part of the filename. Without it a
browser can hold on to an old `data.js` next to a fresh `index.html` and show
stale numbers without saying so — and this site goes out with
`cache-control: max-age=2678400`, so "stale" can mean a month.

### Keeping it updated

`.github/workflows/update.yml` is scheduled every hour, commits any new rows,
and redeploys the site.

It doesn't actually run every hour, and isn't expected to. GitHub queues
scheduled workflows on a shared pool, starts them late and drops them when
things are busy. Scheduled every three hours, 15 of 37 slots fired over the
first five days, up to three hours late, with gaps as long as 13 hours — a
morning report could sit unread until mid-afternoon. That's fair enough for
free compute, but it can't be fixed from inside the workflow, so there are
simply more slots: hourly, in the hope of a real check every three or four
hours. Nothing here depends on punctuality: same resort, same day, same
numbers is the same row, so a run that finds nothing writes nothing, and
missing a day just leaves a small gap in the chart.

Since the schedule is a hope rather than a promise, the page says when it last
actually checked — "last checked 10:04 AM (12m ago)" — and flags it once that's
more than six hours back. That time comes from `site/status.js`, which the
fetch writes on every run and which is deliberately **not committed**: it
changes every run, and a run that finds nothing new is supposed to commit
nothing. The workflow packages the site from the fetch job's own working tree,
so the file ships with each deploy anyway. A tab left open re-reads it every
few minutes. Most checks find nothing new, so the file also carries a
fingerprint of the data it shipped with (`data_rev`, matching `rev` in
`data.js`), and the tab only downloads the data again when that has changed.

The fetch and the Pages deploy sit in the **same** workflow run on purpose. A
commit pushed with `GITHUB_TOKEN` doesn't trigger other workflows, so a
separate push-triggered Pages job would never see the data the fetch had just
committed.

To run it on your own machine instead, any scheduler will do — a launchd agent,
or `23 */3 * * * cd /path/to/snowd && /usr/bin/python3 scripts/fetch.py`. Your
cron will keep better time than GitHub's.

## Data

### `data/resorts.csv` — the registry

One row per resort: `resort_id`, `name`, `operator`, `passes`, `pass_tier`, `country`, `region`,
`state`, `domain`, `snow_report_url`, `latitude`, `longitude`, `timezone`,
`data_source`, `source_id`.

`passes` is a comma-separated list, because a resort can be on more than one.
`pass_tier` is `owned` or `partner` relative to whoever operates it. (These
replace `epic_tier`, which had the single-pass assumption baked into its
name.) `source_id` is for adapters whose endpoint can't be derived from the
domain — currently just the Mtn Powder `resortId`.

`data_source` names the adapter that reads the resort, and is empty for the
ones nothing reads yet — it's the switch that brings a resort into the fetch.
`snow_report_url` is always **the page a person would read**, and it's what the
viewer links to; when an adapter talks to some other endpoint, it works that
out itself.

### `data/snow_<season>.csv` — the history

One row per resort per published report, both units for every metric:

`observed_at`, `season`, `resort_id`, `report_updated_utc`,
`report_updated_text`, `base_depth_{in,cm}`, `season_total_{in,cm}`,
`snow_{12h,24h,48h,7d}_{in,cm}`, `overall_conditions`, `summit_readings`,
`vail_resort_id`, `units_native`, `reading_level`, `lifts_{open,total}`,
`runs_{open,total,groomed}`.

`observed_at` is when we fetched it; `report_updated_utc` is when the resort
says it last refreshed. The gap between the two is what staleness is measured
on.

A couple of sources don't publish an update time at all (Ski Arlberg, Sölden).
For those, de-duplication falls back to the values plus the fetch date, so you
get a row a day unless the numbers move, and the viewer marks the age it's
showing as the time we first read those numbers — "just now" under a column
headed *Updated* would be claiming a freshness the resort never actually said.
Kitzsteinhorn is halfway there: it publishes the day but not the hour. Stored,
that is midnight in Austria, and counting from midnight says "18h ago" about a
report that may be an hour old, so the viewer says "today" and no more.

`units_native` is `in,cm` where the source gave us both, or `in` / `cm` where
it gave one, so a converted figure can always be told apart from a reported
one. `reading_level` is `mid`, `base` or `summit` for sources that measure at
several elevations, and blank for the rest. Lift and run counts are blank
unless the source publishes them, which Vail's own resorts don't. Where they
are published, `lifts_open`, `runs_open` and `runs_groomed` hold the most seen
open at any fetch on that row's day (see Duplicate handling), and the viewer
shows them in the *Open* column and as a map metric.

### `data/forecast_<season>.csv` — the forecast

One row per resort per forecast day per fetch day: temperatures in °F and °C,
day and night snowfall ranges in both units, conditions and wind.

Daytime and overnight snowfall are kept separate and verbatim, exactly as
published — as display strings, which might be a single value (`4`), a range
(`0-2`), `Nil`, or empty. The viewer adds the two into one figure per day,
bound-wise for ranges (`0-2` plus `0-2` is `0-4`), and keeps the split in the
hover text. That's only for display; the CSV keeps both.

### Duplicate handling

A row earns its place by carrying a different measurement, or by being on a
different day: **same resort, same day, same numbers is the same row.**

Keying on the publish timestamp alone wasn't enough. A resort that touches that
stamp without changing any number — Telluride stamps its page with the render
time — produced a fresh row on every fetch, twelve identical readings in one
afternoon. Keying on the values alone is wrong too, because a resort sitting at
the same depth for a week is genuinely a week of observations, not one.

The day comes from the resort's own publish date where there is one, which is
what keeps a report left up all summer to a single row rather than one per day
of polling. Where there's no timestamp, the fetch date stands in.

"Same row" doesn't mean frozen. Lifts, runs and conditions move during the day
without the snow moving, and they used to be stuck at the day's first sighting
— usually a fetch before dawn, so a resort that started its lifts at nine
showed none turning until tomorrow. Now a fetch that matches the resort's
latest row brings those fields up to date in place, and takes the publish
stamp of the report they came from. Two rules keep that from flapping: a blank
never erases a recorded value, and the open counts only go up within a row. A
feed reporting live status drops to zero when the lifts stop for the evening,
and "0 lifts" all night about a mountain that ran twelve isn't what anyone
means by what's open.

Each fetch is weighed against that resort's *latest* row, not every row. So a
depth that goes 30 → 31 → 30 in one day is three rows, and the latest is the
current reading; matched against everything, the third was dropped as a
duplicate of the first and the superseded 31 stood as the latest. The one
exception is a report dated before the latest row's day turning up again — a
cache serving yesterday's page — which is not news.

Forecast rows are keyed on resort + target date + fetch date. A re-fetch on the
same day replaces that day's row only if the forecast actually moved, compared
on the forecast's own fields and ignoring `observed_at`. Without that, every
run would rewrite all ~180 rows with nothing but a new fetch timestamp, and the
scheduled job would commit that noise several times a day.

"Moved" has a threshold as well. These forecasts are regenerated more or less
continuously — a wind speed goes from 4.78 to 4.68, a high from 58 to 57. In
one three-hour stretch 171 of about 210 rows differed, 149 of them in nothing
but temperature, sky or wind, which at an hourly check is a commit an hour of
weather-model jitter. So a change in forecast *snow* is taken at once, and
anything else waits until the stored row is six hours old.

Same reasoning for `site/data.js` carrying `data_as_of` — the freshest
observation in the data, rather than a "generated now" stamp. It's a pure
function of the CSVs, so it only changes when they do. The wall-clock "we
looked just now" lives in two gitignored files instead: `data/last_run.json`,
the full per-resort run log, and `site/status.js`, the last-checked time and
any resorts that errored, which is what the viewer reads.

### Seasons

Season labels are worked out rather than hardcoded: northern-hemisphere seasons
get a span (`2026-27`, cutting over 1 July), southern-hemisphere a single year
(`2026`). Files are per-season and the viewer finds them by looking in the
directory, so a future season doesn't need a code change — just keep running
the fetch.

A row's season comes from **the report's own date, not the day we fetched it**.
Resorts leave last season's final report up through the summer, so a report
stamped June 2026 that we pull in November 2026 is 2025-26 data. Filing it by
fetch date would drop a bogus zero at the start of 2026-27 and make the place
look like it had opened and reported nothing. Those rows live in
`snow_2025-26.csv`, and the resort reads "not yet" for the current season until
it really does publish. Forecast rows take the season of the day they forecast.

The same idea carries through the viewer: charts are plotted against the
resort's publish time, each resort's history is ordered by it, and "newest
report" in the header is the freshest publish time across all of them.
`observed_at` only ever records when we polled. "Last checked", beside it, is
the other half: when the fetch last ran, whether or not anything had changed.

## Layout

```
data/      resorts.csv, snow_<season>.csv, forecast_<season>.csv
           last_run.json (local run log, gitignored)
scripts/   snowd.py (library + adapters), fetch.py (CLI), build_site_data.py
           test_snowd.py (offline self-checks), check_live.py (live checks)
site/      index.html (viewer), data.js (generated)
           status.js (last-checked stamp, written by each fetch, gitignored)
tests/     fixtures/ — a saved response per adapter, so the parsers can be
           tested against real markup without hitting the network
```

## Known gaps

- **Lifts and runs open** only show up where a source volunteers them — RCR,
  Telluride, Sölden and Ski Arlberg. Vail's snow report pages don't carry them
  at all, so those columns stay blank for 35 resorts.
- **No forecast for the partner resorts.** Vail's pages ship a structured
  5-day forecast. RCR publishes a two-day temperature outlook and Telluride an
  unstructured widget, so neither is captured.
- **Partial reports are normal**, and four partner sources publish a base depth
  and little else. Those resorts sort and map on base depth and read "not
  reported" for the rest.
- **The other elevations are thrown away.** Ski Arlberg gives valley and
  mountain, Sölden a range, Kitzsteinhorn three stations, and the Alterra feed
  Summit / Mid Mountain / Base. Only one is kept — `reading_level` at least
  records which — but there's still no column for the others.
- **Most of Ikon isn't here.** Only Alterra's own 18 report; the other ~46 Ikon
  destinations need per-site adapters (see Coverage).
- **No backfill.** The history starts the day the first fetch ran, and fills in
  from there.
- **These are web pages, not APIs.** When a site gets redesigned its parser
  needs updating. A resort that fails outright shows as `error` in the run
  output and `data/last_run.json`, as a warning on the Actions run, and as a
  note on that resort in the viewer. The quieter failure is a parser that still
  runs and finds nothing, which looks exactly like a resort between seasons —
  that's what `scripts/check_live.py` is for. It checks that the markup each
  parser leans on is still there, and flags an adapter where none of its
  resorts returned anything.
- **Saved fixtures can be wrong from the start.** Sölden's page negotiates
  language: captured with a plain request it came back German, while the
  fetcher sends an English `Accept-Language` and got English labels that
  matched nothing. The offline test passed happily and the live fetch returned
  nothing. Fixtures show that a parser still reads what it read before; only
  `check_live.py` shows that it still reads the site.

Corrections and additions are welcome, especially from anyone who knows how a
given resort actually means its numbers.
