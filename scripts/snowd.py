#!/usr/bin/env python3
"""snowd — shared library: fetch and parse Vail Resorts snow reports.

Vail-operated resort sites embed their snow report server-side as JSON in a
`FR.snowReportData` assignment, carrying BOTH inches and centimeters for every
metric. That means no unit conversion is ever needed: we store the source's own
numbers in both units and let the UI pick.

Companion assignments we also harvest:
  FR.snowReportSettings  which metrics this resort publishes, + SnowAndWebcamResortId
  FR.forecasts           multi-day + hourly forecast, also dual-unit
"""

from __future__ import annotations

import csv
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"

USER_AGENT = "snowd/0.1 (personal snow tracker; +https://github.com/ajschumacher/snowd)"
TIMEOUT = 30
RETRIES = 3

# ---------------------------------------------------------------- seasons


def season_for(dt: datetime, hemisphere: str = "N") -> str:
    """Season label for a date.

    Northern hemisphere seasons straddle the new year, so they get a span
    label: 2026-11-04 -> "2026-27". The cutover is 1 July.

    Southern hemisphere seasons (Australia) sit inside one calendar year, so
    they are labelled by that year alone: 2026-08-01 -> "2026".
    """
    if hemisphere == "S":
        return str(dt.year)
    start = dt.year if dt.month >= 7 else dt.year - 1
    return f"{start}-{str(start + 1)[2:]}"


# ---------------------------------------------------------------- registry


@dataclass
class Resort:
    resort_id: str
    name: str
    operator: str
    epic_tier: str
    country: str
    region: str
    state: str
    domain: str
    snow_report_url: str
    latitude: float
    longitude: float
    timezone: str
    data_source: str

    @property
    def hemisphere(self) -> str:
        return "S" if self.latitude < 0 else "N"

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except Exception:
            return ZoneInfo("UTC")


def load_resorts(path: Path | None = None) -> list[Resort]:
    path = path or DATA / "resorts.csv"
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out.append(
                Resort(
                    resort_id=row["resort_id"],
                    name=row["name"],
                    operator=row["operator"],
                    epic_tier=row["epic_tier"],
                    country=row["country"],
                    region=row["region"],
                    state=row["state"],
                    domain=row["domain"],
                    snow_report_url=row["snow_report_url"],
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                    timezone=row["timezone"],
                    data_source=row["data_source"],
                )
            )
    return out


# ---------------------------------------------------------------- fetching


def http_get(url: str) -> str:
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
            return raw.decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 - recorded per resort
            last = exc
            if attempt == RETRIES - 1:
                break
            import time

            time.sleep(1.5 * (attempt + 1))
    raise last if last else RuntimeError("unreachable")


def _extract_assignment(html: str, name: str) -> object | None:
    """Pull `FR.<name> = <json>;` out of the page.

    Brace-matched rather than regex-greedy, so nested objects survive.
    """
    marker = f"FR.{name}"
    idx = html.find(marker)
    while idx != -1:
        eq = html.find("=", idx + len(marker))
        if eq == -1:
            return None
        # only accept a bare `=` (not `==`, and nothing but spaces between)
        between = html[idx + len(marker) : eq]
        if between.strip() == "":
            start = eq + 1
            while start < len(html) and html[start] in " \t\r\n":
                start += 1
            if start < len(html) and html[start] in "[{":
                open_ch = html[start]
                close_ch = "]" if open_ch == "[" else "}"
                depth, in_str, esc = 0, False, False
                for i in range(start, len(html)):
                    ch = html[i]
                    if in_str:
                        if esc:
                            esc = False
                        elif ch == "\\":
                            esc = True
                        elif ch == '"':
                            in_str = False
                        continue
                    if ch == '"':
                        in_str = True
                    elif ch == open_ch:
                        depth += 1
                    elif ch == close_ch:
                        depth -= 1
                        if depth == 0:
                            try:
                                return json.loads(html[start : i + 1])
                            except json.JSONDecodeError:
                                return None
                return None
        idx = html.find(marker, idx + 1)
    return None


# ---------------------------------------------------------------- parsing

#: source field -> our column stem
SNOW_METRICS = {
    "OvernightSnowfall": "snow_12h",
    "TwentyFourHourSnowfall": "snow_24h",
    "FortyEightHourSnowfall": "snow_48h",
    "SevenDaySnowfall": "snow_7d",
    "BaseDepth": "base_depth",
    "CurrentSeason": "season_total",
}

_TZ_ABBR = {
    "EST": -5, "EDT": -4, "CST": -6, "CDT": -5,
    "MST": -7, "MDT": -6, "PST": -8, "PDT": -7,
    "AKST": -9, "AKDT": -8, "HST": -10,
    "AEST": 10, "AEDT": 11, "NZST": 12, "NZDT": 13,
    "CET": 1, "CEST": 2, "GMT": 0, "UTC": 0, "JST": 9,
}


def _num(val) -> float | None:
    """Coerce a source depth string to a number, or None."""
    if val is None:
        return None
    s = str(val).strip().replace(",", "")
    if s in ("", "-", "--", "N/A", "n/a", "null"):
        return None
    # some sites emit trace values like `<1`
    s = s.lstrip("<>~")
    try:
        return float(s)
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return float(m.group()) if m else None


def _depth_pair(block) -> tuple[float | None, float | None]:
    if not isinstance(block, dict):
        return None, None
    return _num(block.get("Inches")), _num(block.get("Centimeters"))


def parse_report_timestamp(text: str, fallback_tz: ZoneInfo) -> str | None:
    """'Updated November 04, 2026 at 5:34 AM MST' -> ISO-8601 UTC.

    Returns None if the string does not carry a parseable date, so callers can
    fall back to the raw text (which we always keep).
    """
    if not text:
        return None
    s = re.sub(r"\s+", " ", str(text)).strip()
    s = re.sub(r"^updated\s+", "", s, flags=re.I)
    # trailing timezone abbreviation, if any
    tzname = None
    m = re.search(r"\b([A-Z]{2,4})\s*$", s)
    if m and m.group(1) in _TZ_ABBR:
        tzname = m.group(1)
        s = s[: m.start()].strip()
    s = re.sub(r"\bat\b", " ", s, flags=re.I)
    s = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    naive = None
    for fmt in (
        "%B %d, %Y %I:%M %p", "%B %d %Y %I:%M %p",
        "%b %d, %Y %I:%M %p", "%b %d %Y %I:%M %p",
        "%B %d, %Y %H:%M", "%b %d, %Y %H:%M",
        "%m/%d/%Y %I:%M %p", "%d/%m/%Y %H:%M",
        "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y",
    ):
        try:
            naive = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    if naive is None:
        return None
    if tzname is not None:
        aware = naive.replace(tzinfo=timezone(timedelta(hours=_TZ_ABBR[tzname])))
    else:
        aware = naive.replace(tzinfo=fallback_tz)
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class FetchResult:
    resort_id: str
    status: str                      # ok | no_report | error
    note: str = ""
    snow: dict | None = None
    forecast: list[dict] = field(default_factory=list)


def parse_page(resort: Resort, html: str, observed_at: str) -> FetchResult:
    data = _extract_assignment(html, "snowReportData")
    settings = _extract_assignment(html, "snowReportSettings") or {}
    forecasts = _extract_assignment(html, "forecasts") or []

    fc_rows = _parse_forecast(resort, forecasts, observed_at)

    if not isinstance(data, dict) or not data:
        # Pre-season and shoulder-season pages omit the component entirely.
        return FetchResult(
            resort.resort_id,
            "no_report",
            "page served but no snowReportData (likely pre-season)",
            forecast=fc_rows,
        )

    updated_text = str(data.get("LastUpdatedText") or "").strip()
    report_utc = parse_report_timestamp(updated_text, resort.tz) or ""

    # Season comes from the report's own date, not from when we fetched it.
    # Resorts leave last season's final report up through the summer, so a
    # report stamped June 2026 pulled in November 2026 is 2025-26 data and
    # belongs in that season's file — not filed as a 2026-27 reading of zero.
    if report_utc:
        stamp = datetime.strptime(report_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    else:
        stamp = datetime.now(timezone.utc)

    row: dict = {
        "observed_at": observed_at,
        "season": season_for(stamp, resort.hemisphere),
        "resort_id": resort.resort_id,
        "report_updated_utc": report_utc,
        "report_updated_text": updated_text,
        "overall_conditions": str(data.get("OverallSnowConditions") or "").strip(),
        "summit_readings": "true" if settings.get("UseSummitSnowReadings") else "false",
        "vail_resort_id": settings.get("SnowAndWebcamResortId", ""),
    }
    published = 0
    for src, stem in SNOW_METRICS.items():
        inches, cm = _depth_pair(data.get(src))
        row[f"{stem}_in"] = "" if inches is None else inches
        row[f"{stem}_cm"] = "" if cm is None else cm
        if inches is not None or cm is not None:
            published += 1
    if published == 0:
        return FetchResult(
            resort.resort_id, "no_report",
            "snowReportData present but all metrics empty",
            forecast=fc_rows,
        )
    return FetchResult(resort.resort_id, "ok", "", snow=row, forecast=fc_rows)


def _parse_forecast(resort: Resort, forecasts, observed_at: str) -> list[dict]:
    rows: list[dict] = []
    if not isinstance(forecasts, list):
        return rows
    for block in forecasts:
        if not isinstance(block, dict):
            continue
        for day in block.get("ForecastData") or []:
            if not isinstance(day, dict):
                continue
            raw_date = str(day.get("Date") or "")
            date = raw_date[:10]
            if not re.match(r"\d{4}-\d{2}-\d{2}$", date):
                continue
            # a forecast belongs to the season of the day it forecasts
            day_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            rows.append({
                "observed_at": observed_at,
                "season": season_for(day_dt, resort.hemisphere),
                "resort_id": resort.resort_id,
                "forecast_date": date,
                "high_f": _blank(_num(day.get("HighTempStandard"))),
                "low_f": _blank(_num(day.get("LowTempStandard"))),
                "high_c": _blank(_num(day.get("HighTempMetric"))),
                "low_c": _blank(_num(day.get("LowTempMetric"))),
                "snow_day_in": str(day.get("SnowFallDayStandard") or "").strip(),
                "snow_night_in": str(day.get("SnowFallNightStandard") or "").strip(),
                "snow_day_cm": str(day.get("SnowFallDayMetric") or "").strip(),
                "snow_night_cm": str(day.get("SnowFallNightMetric") or "").strip(),
                "conditions": str(day.get("WeatherShortDescription") or "").strip(),
                "wind_dir": str(day.get("Wind") or "").strip(),
                "wind_speed": _blank(_num(day.get("WindSpeed"))),
            })
        break  # first block is the resort's default location
    return rows


def _blank(v):
    return "" if v is None else v


def fetch_resort(resort: Resort, observed_at: str) -> FetchResult:
    if resort.data_source != "vail_cms" or not resort.snow_report_url:
        return FetchResult(resort.resort_id, "no_source", "no data source configured yet")
    try:
        html = http_get(resort.snow_report_url)
    except urllib.error.HTTPError as exc:
        return FetchResult(resort.resort_id, "error", f"HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001
        return FetchResult(resort.resort_id, "error", f"{type(exc).__name__}: {exc}"[:120])
    try:
        return parse_page(resort, html, observed_at)
    except Exception as exc:  # noqa: BLE001
        return FetchResult(resort.resort_id, "error", f"parse: {type(exc).__name__}: {exc}"[:120])


# ---------------------------------------------------------------- csv store

SNOW_COLUMNS = [
    "observed_at", "season", "resort_id",
    "report_updated_utc", "report_updated_text",
    "base_depth_in", "base_depth_cm",
    "season_total_in", "season_total_cm",
    "snow_12h_in", "snow_12h_cm",
    "snow_24h_in", "snow_24h_cm",
    "snow_48h_in", "snow_48h_cm",
    "snow_7d_in", "snow_7d_cm",
    "overall_conditions", "summit_readings", "vail_resort_id",
]

FORECAST_COLUMNS = [
    "observed_at", "season", "resort_id", "forecast_date",
    "high_f", "low_f", "high_c", "low_c",
    "snow_day_in", "snow_night_in", "snow_day_cm", "snow_night_cm",
    "conditions", "wind_dir", "wind_speed",
]


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})
    tmp.replace(path)


def _snow_identity(row: dict) -> tuple:
    """What makes a snow observation 'the same' observation.

    Keyed on the resort's own publish timestamp where they give us one: if the
    resort has not refreshed its report, re-running the fetch adds nothing, no
    matter how often we poll. Where no timestamp is published, fall back to the
    measured values themselves, so an unchanged report still collapses.
    """
    stamp = (row.get("report_updated_utc") or "").strip()
    text = (row.get("report_updated_text") or "").strip()
    if stamp or text:
        return (row["resort_id"], "stamp", stamp or text)
    vals = tuple(str(row.get(c, "")) for c in SNOW_COLUMNS if c.endswith(("_in", "_cm")))
    return (row["resort_id"], "values", (row.get("observed_at") or "")[:10], vals)


def append_snow(rows: list[dict], season: str) -> tuple[int, int]:
    path = DATA / f"snow_{season}.csv"
    existing = _read_csv(path)
    seen = {_snow_identity(r) for r in existing}
    added = []
    for r in rows:
        ident = _snow_identity(r)
        if ident in seen:
            continue
        seen.add(ident)
        added.append(r)
    if added:
        merged = existing + added
        merged.sort(key=lambda r: (r.get("observed_at", ""), r.get("resort_id", "")))
        _write_csv(path, SNOW_COLUMNS, merged)
    return len(added), len(existing) + len(added)


def _fc_payload(row: dict) -> tuple:
    """A forecast row's content, ignoring when we happened to fetch it."""
    return tuple(str(row.get(c, "")) for c in FORECAST_COLUMNS if c != "observed_at")


def append_forecast(rows: list[dict], season: str) -> tuple[int, int]:
    """One forecast row per resort / target date / fetch day; latest wins.

    A re-fetch on the same day replaces that day's row only when the forecast
    itself has moved. Were it replaced unconditionally, every run would rewrite
    the file with nothing but a new `observed_at` — which, under a CI job that
    commits what changed, means a commit every few hours saying nothing.
    """
    path = DATA / f"forecast_{season}.csv"
    existing = _read_csv(path)
    index: dict[tuple, dict] = {}
    order: list[tuple] = []
    for r in existing:
        key = (r["resort_id"], r["forecast_date"], (r.get("observed_at") or "")[:10])
        if key not in index:
            order.append(key)
        index[key] = r

    before = len(existing)
    changed = False
    for r in rows:
        key = (r["resort_id"], r["forecast_date"], (r.get("observed_at") or "")[:10])
        prior = index.get(key)
        if prior is not None and _fc_payload(prior) == _fc_payload(r):
            continue  # same forecast, already recorded — keep the first sighting
        if prior is None:
            order.append(key)
        index[key] = r
        changed = True

    if not changed:
        return 0, before
    merged = [index[k] for k in order]
    merged.sort(key=lambda r: (r.get("observed_at", ""), r.get("resort_id", ""), r.get("forecast_date", "")))
    _write_csv(path, FORECAST_COLUMNS, merged)
    return len(merged) - before, len(merged)
