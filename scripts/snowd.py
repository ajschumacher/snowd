#!/usr/bin/env python3
"""snowd — shared library: fetch and parse resort snow reports.

Each resort's `data_source` in the registry names an **adapter**: a pair of
"where to GET" and "how to read what comes back". One adapter can serve many
resorts, which is the whole point — the four western RCR sites publish byte-for
-byte the same field names, so they cost one parser between them, not four.

  vail_cms      Vail-operated sites. The page embeds its snow report
                server-side as `FR.snowReportData`, carrying BOTH inches and
                centimeters for every metric, plus `FR.snowReportSettings` and
                a dual-unit `FR.forecasts`.
  rcr_refresh   Resorts of the Canadian Rockies. An undocumented endpoint,
                /refresh/refresh.php, returns JavaScript that assigns each
                figure into the page by element id. Centimetres only.
  telluride     Server-rendered HTML, read by the visible label beside each
                figure rather than by markup position. Inches only.
  rusutsu       Label/value pairs, published per mountain rather than per
                resort; the deepest wins. Centimetres only.
  kitzsteinhorn Base depth at several measuring stations up the mountain, and
                nothing else — the highest station wins. Centimetres only.
  arlberg       Labelled boxes giving valley and mountain depth separately.
                No timestamp published. Centimetres only.
  soelden       Labelled tiles on the HOME page — its own snow-report page
                renders client-side. Depth as a valley-to-mountain range.
                No timestamp published. Centimetres only.

Unlike Vail, the partner sources publish a single unit, so the other one is
computed. Every row records which unit it was actually given in
(`units_native`), so a converted figure is never passed off as the resort's own
number.
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
                    data_source=(row.get("data_source") or "").strip(),
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
    s = re.sub(r"^(?:last\s+)?updated\s*:?\s+", "", s, flags=re.I)
    # RCR writes "NOVEMBER 5, 2026 | 7:26am"
    s = s.replace("|", " ")
    # trailing timezone abbreviation, if any
    tzname = None
    m = re.search(r"\b([A-Z]{2,4})\s*$", s)
    if m and m.group(1) in _TZ_ABBR:
        tzname = m.group(1)
        s = s[: m.start()].strip()
    s = re.sub(r"\bat\b", " ", s, flags=re.I)
    s = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", s, flags=re.I)
    # "7:26am" -> "7:26 am", so %p can match
    s = re.sub(r"(\d)\s*([AaPp])\.?([Mm])\.?(?=\s|$)", r"\1 \2\3", s)
    s = re.sub(r"\s+", " ", s).strip()
    naive = None
    for fmt in (
        "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
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
    return FetchResult(resort.resort_id, "ok", "",
                       snow=fill_units(row, "in,cm"), forecast=fc_rows)


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


# ---------------------------------------------------------------- units

#: the six depth metrics every source is mapped onto
DEPTH_STEMS = ("base_depth", "season_total", "snow_12h", "snow_24h", "snow_48h", "snow_7d")


def fill_units(row: dict, native: str) -> dict:
    """Compute the unit a source does not publish, and record which it gave us.

    Vail hands us inches *and* centimetres, so nothing is ever computed there.
    Every partner source publishes exactly one, so the other is arithmetic — and
    that has to be visible, or a derived number would read as the resort's own.
    `units_native` is that record: "in,cm", "cm" or "in".
    """
    row["units_native"] = native
    for stem in DEPTH_STEMS:
        inches, cm = row.get(f"{stem}_in", ""), row.get(f"{stem}_cm", "")
        if inches == "" and cm != "":
            row[f"{stem}_in"] = round(float(cm) / 2.54, 1)
        elif cm == "" and inches != "":
            row[f"{stem}_cm"] = round(float(inches) * 2.54, 1)
    return row


_TAG = re.compile(r"<[^>]*>")
_SUBTITLE = re.compile(r"<span[^>]*subtitle[^>]*>.*?</span>", re.S | re.I)


def _text(html: str) -> str:
    """Visible text of a small HTML fragment.

    RCR's condition fields carry a styled "UPPER MTN" caption alongside the
    actual word, so that span is dropped whole rather than flattened — else
    every resort would report its conditions as "UPPER MTN".
    """
    if not html:
        return ""
    out = _SUBTITLE.sub(" ", str(html))
    out = _TAG.sub(" ", out)
    for ent, ch in (("&nbsp;", " "), ("&amp;", "&"), ("&deg;", "°"),
                    ("&quot;", '"'), ("&#8211;", "-"), ("&#039;", "'")):
        out = out.replace(ent, ch)
    return re.sub(r"\s+", " ", out).strip()


def _int(val):
    n = _num(val)
    return "" if n is None else int(n)


# ---------------------------------------------------------------- rcr_refresh

#: Resorts of the Canadian Rockies. All four western sites run the same CMS and
#: publish the same element ids, so this map is the whole adapter's per-resort
#: configuration — there isn't any. Centimetres throughout (the page labels
#: each figure "CM").
RCR_FIELDS = {
    "snowReportNewSnowFallOvernight": "snow_12h",
    "snowReportNewSnowFall24": "snow_24h",
    "snowReportNewSnowFall48": "snow_48h",
    "snowReportNewSnowFall7days": "snow_7d",
    "snowReportNewSnowFallPack": "base_depth",
    "snowReportNewSnowFallYTD": "season_total",
}

RCR_COUNTS = {
    "snowReportLiftsOpen": "lifts_open",
    "snowReportRunsOpen": "runs_open",
    "snowReportRunsGroomed": "runs_groomed",
}

#: `getElementById('x').innerHTML = "y";` — either quote character, escapes
#: respected, so an apostrophe inside a value does not truncate it.
_RCR_ASSIGN = re.compile(
    r"""getElementById\(\s*['"]([A-Za-z0-9_]+)['"]\s*\)\s*\.innerHTML\s*=\s*"""
    r"""(['"])((?:\\.|(?!\2).)*)\2""",
    re.S,
)


def rcr_url(resort: Resort) -> str:
    return f"https://{resort.domain}/refresh/refresh.php"


def parse_rcr(resort: Resort, body: str, observed_at: str) -> FetchResult:
    fields = {m.group(1): m.group(3) for m in _RCR_ASSIGN.finditer(body)}
    if not fields:
        return FetchResult(resort.resort_id, "error",
                           "refresh.php returned no innerHTML assignments")

    # Two stamps are published; the one on the snow block is the one that
    # describes these numbers. The lift and terrain blocks carry their own,
    # often months stale, and must not be mistaken for it.
    updated_text = _text(fields.get("snowReportTH_SnowreportRecentSnowSaveDate")
                         or fields.get("snowReportTH_SnowreportTodaySaveDate") or "")
    report_utc = parse_report_timestamp(updated_text, resort.tz) or ""
    stamp = (datetime.strptime(report_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
             if report_utc else datetime.now(timezone.utc))

    row: dict = {
        "observed_at": observed_at,
        "season": season_for(stamp, resort.hemisphere),
        "resort_id": resort.resort_id,
        "report_updated_utc": report_utc,
        "report_updated_text": updated_text,
        "overall_conditions": _text(fields.get("snowReportUpperMtnConditions")),
        "summit_readings": "true",          # the upper-mountain reading
        "vail_resort_id": "",
    }
    published = 0
    for src, stem in RCR_FIELDS.items():
        cm = _num(fields.get(src))
        row[f"{stem}_in"] = ""
        row[f"{stem}_cm"] = "" if cm is None else cm
        if cm is not None:
            published += 1
    for src, col in RCR_COUNTS.items():
        row[col] = _int(fields.get(src))

    if published == 0:
        return FetchResult(resort.resort_id, "no_report",
                           "refresh.php reachable but every snow field empty")
    return FetchResult(resort.resort_id, "ok", snow=fill_units(row, "cm"))


# ---------------------------------------------------------------- telluride

#: Telluride renders server-side, and labels each figure in the markup beside
#: it. Reading by label rather than by position survives a reordering of the
#: panel, which a positional parser would silently misattribute.
TELLURIDE_LABELS = {
    "snow depth": "base_depth",
    "season total": "season_total",
    "snow overnight": "snow_12h",
    "last 24 hours": "snow_24h",
    "last 48 hours": "snow_48h",
    "last 7 days": "snow_7d",
}

_TSR_PAIR = re.compile(
    r"<span>\s*([^<]{3,40}?)\s*</span>\s*<span>\s*<strong>\s*([^<]*?)\s*</strong>", re.S | re.I)
_TSR_COND = re.compile(
    r"tsr-report-snow-condition.*?</strong>\s*</span>\s*(?:<br\s*/?>)?\s*<span>\s*([^<]*?)\s*</span>",
    re.S | re.I)
_TSR_UPDATED = re.compile(r"Last Updated:\s*([^<&]+)", re.I)
_TSR_WRAPPER = re.compile(r"tsr-report-app-lifts-wrapper(.*?)</div>", re.S | re.I)
_TSR_RATIO = re.compile(r">\s*(\d+)\s*/\s*(\d+)\s*<")


def telluride_url(resort: Resort) -> str:
    return resort.snow_report_url


def parse_telluride(resort: Resort, html: str, observed_at: str) -> FetchResult:
    pairs = {m.group(1).strip().lower(): m.group(2) for m in _TSR_PAIR.finditer(html)}
    if not any(k in pairs for k in TELLURIDE_LABELS):
        return FetchResult(resort.resort_id, "no_report",
                           "page served but no labelled snow figures")

    m = _TSR_UPDATED.search(html)
    updated_text = _text(m.group(1)) if m else ""
    report_utc = parse_report_timestamp(updated_text, resort.tz) or ""
    stamp = (datetime.strptime(report_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
             if report_utc else datetime.now(timezone.utc))

    cond = _TSR_COND.search(html)
    row: dict = {
        "observed_at": observed_at,
        "season": season_for(stamp, resort.hemisphere),
        "resort_id": resort.resort_id,
        "report_updated_utc": report_utc,
        "report_updated_text": updated_text,
        "overall_conditions": _text(cond.group(1)) if cond else "",
        "summit_readings": "false",
        "vail_resort_id": "",
    }
    published = 0
    for label, stem in TELLURIDE_LABELS.items():
        inches = _num(pairs.get(label))
        row[f"{stem}_in"] = "" if inches is None else inches
        row[f"{stem}_cm"] = ""
        if inches is not None:
            published += 1

    # Two "x/y" panels: the lifts tab also shows "On hold", the trails tab
    # "Groomed". Identify them by that, not by which comes first in the page.
    for block in _TSR_WRAPPER.finditer(html):
        chunk = block.group(1)
        ratio = _TSR_RATIO.search(chunk)
        if not ratio:
            continue
        opened, total = int(ratio.group(1)), int(ratio.group(2))
        if re.search(r"groomed", chunk, re.I):
            row["runs_open"], row["runs_total"] = opened, total
        elif re.search(r"on hold", chunk, re.I):
            row["lifts_open"], row["lifts_total"] = opened, total

    if published == 0:
        return FetchResult(resort.resort_id, "no_report", "snow panel present but empty")
    return FetchResult(resort.resort_id, "ok", snow=fill_units(row, "in"))


# ---------------------------------------------------------------- rusutsu

#: Rusutsu publishes a `<dt>label</dt><dd>value</dd>` pair per figure, per
#: mountain. The English page has misspelled one of those labels for a while;
#: accepting both spellings costs nothing and keeps base depth from silently
#: vanishing if they fix it — or if they don't.
RUSUTSU_LABELS = {
    "snow accumulation": "base_depth",
    "snow sccumulation": "base_depth",   # sic, as published
    "24-hour snowfall": "snow_24h",
    "total snowfall": "season_total",
}

_RUS_PAIR = re.compile(r"<dt>\s*([^<]{3,40}?)\s*</dt>\s*<dd>\s*([^<]*?)\s*</dd>", re.S | re.I)
_RUS_UPDATED = re.compile(r"Update Date\s*:\s*([^<]+)", re.I)
#: "23/10/2026(Fri.) 12:32" — day first, which the generic parser would read
#: as a month on any date below the 13th. Pinned here, where the format is known.
_RUS_DMY = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\([^)]*\))?\s*(\d{1,2}:\d{2})?")


def rusutsu_url(resort: Resort) -> str:
    return resort.snow_report_url


def parse_rusutsu(resort: Resort, html: str, observed_at: str) -> FetchResult:
    pairs = [(m.group(1).strip().lower(), m.group(2)) for m in _RUS_PAIR.finditer(html)]
    if not any(lab in RUSUTSU_LABELS for lab, _ in pairs):
        return FetchResult(resort.resort_id, "no_report", "page served but no labelled figures")

    updated_text, report_utc = "", ""
    m = _RUS_UPDATED.search(html)
    if m:
        updated_text = _text(m.group(1))
        d = _RUS_DMY.search(updated_text)
        if d:
            iso = f"{d.group(3)}-{int(d.group(2)):02d}-{int(d.group(1)):02d}"
            report_utc = parse_report_timestamp(
                f"{iso} {d.group(4) or '00:00'}", resort.tz) or ""
    stamp = (datetime.strptime(report_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
             if report_utc else datetime.now(timezone.utc))

    # Rusutsu is three mountains — West, East and Mt. Isola — each with its own
    # reading, and publishes no single resort-wide figure. We take the deepest,
    # which is the one a skier is choosing the resort for.
    best: dict[str, float] = {}
    for label, raw in pairs:
        stem = RUSUTSU_LABELS.get(label)
        v = _num(raw)
        if stem is None or v is None:
            continue
        best[stem] = v if stem not in best else max(best[stem], v)

    quality = ""
    for label, raw in pairs:
        if label == "snow quality" and _text(raw) not in ("", "--", "-"):
            quality = _text(raw)
            break

    row: dict = {
        "observed_at": observed_at,
        "season": season_for(stamp, resort.hemisphere),
        "resort_id": resort.resort_id,
        "report_updated_utc": report_utc,
        "report_updated_text": updated_text,
        "overall_conditions": quality,
        "summit_readings": "false",    # per-mountain readings, not summit vs base
        "vail_resort_id": "",
    }
    for stem in DEPTH_STEMS:
        row[f"{stem}_in"] = ""
        row[f"{stem}_cm"] = best.get(stem, "")
    if not best:
        return FetchResult(resort.resort_id, "no_report",
                           "report panel present but every figure is '--' (pre-season)")
    return FetchResult(resort.resort_id, "ok", snow=fill_units(row, "cm"))


# ------------------------------------------------------------ kitzsteinhorn

_KITZ_TABLE = re.compile(r'weather-table--snow.*?</details>', re.S | re.I)
_KITZ_ROW = re.compile(
    r'weather-table__location">\s*([^<]+?)\s*<br\s*/?>\s*<small>\s*([\d.,]+)\s*m\s*</small>.*?'
    r'weather-table__temperature">(.*?)</div>', re.S | re.I)
_KITZ_MONTH = re.compile(r'weather-table__month">\s*([A-Za-zä]+)\s*<', re.I)
_KITZ_DAY = re.compile(r'weather-table__day">\s*(\d{1,2})\s*<', re.I)


def kitzsteinhorn_url(resort: Resort) -> str:
    return resort.snow_report_url


def parse_kitzsteinhorn(resort: Resort, html: str, observed_at: str) -> FetchResult:
    table = _KITZ_TABLE.search(html)
    if not table:
        return FetchResult(resort.resort_id, "no_report", "no snow-depth table on the page")
    block = table.group(0)

    # Several measuring stations up the mountain. The glacier reading is the
    # one the resort leads with, and the only one comparable to a "base depth"
    # elsewhere, so take the highest station rather than the first listed.
    stations = []
    for m in _KITZ_ROW.finditer(block):
        metres = _num(m.group(2).replace(".", "").replace(",", ""))
        depth = _num(_text(m.group(3)))
        if metres is not None and depth is not None:
            stations.append((metres, _text(m.group(1)), depth))
    if not stations:
        return FetchResult(resort.resort_id, "no_report", "snow table present but no readings")
    stations.sort()
    depth = stations[-1][2]

    # The table is headed by a day and month with no year. Choose the year that
    # puts it nearest today, so a 31 December fetch of a 1 January table does
    # not land twelve months out.
    mon, day = _KITZ_MONTH.search(block), _KITZ_DAY.search(block)
    report_utc, updated_text = "", ""
    if mon and day:
        now = datetime.now(timezone.utc)
        updated_text = f"{mon.group(1)} {day.group(1)}"
        best = None
        for year in (now.year - 1, now.year, now.year + 1):
            iso = parse_report_timestamp(f"{updated_text}, {year}", resort.tz)
            if not iso:
                continue
            cand = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if best is None or abs((cand - now).days) < abs((best - now).days):
                best = cand
        if best is not None:
            report_utc = best.strftime("%Y-%m-%dT%H:%M:%SZ")
            updated_text = f"{updated_text}, {best.year}"
    stamp = (datetime.strptime(report_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
             if report_utc else datetime.now(timezone.utc))

    row: dict = {
        "observed_at": observed_at,
        "season": season_for(stamp, resort.hemisphere),
        "resort_id": resort.resort_id,
        "report_updated_utc": report_utc,
        "report_updated_text": updated_text,
        "overall_conditions": "",
        "summit_readings": "true",
        "vail_resort_id": "",
    }
    for stem in DEPTH_STEMS:
        row[f"{stem}_in"] = ""
        row[f"{stem}_cm"] = ""
    row["base_depth_cm"] = depth
    return FetchResult(resort.resort_id, "ok", snow=fill_units(row, "cm"))


# ------------------------------------------------------------- ski arlberg

#: Ski Arlberg labels each figure in a sibling div. Valley and mountain are
#: given separately; "mountain" is what every other source in this project
#: means by base depth.
ARLBERG_LABELS = {"mountain": "base_depth"}

_ARL_ITEM = re.compile(
    r'snow-report-box__item">\s*<div>\s*([^<]{3,30}?)\s*</div>.*?'
    r'snow-report-box__item-info">(.*?)</div>', re.S | re.I)
_ARL_LIFTS = re.compile(r'Lift facilities.*?live-info__content.*?<div>\s*(\d+)\s+open', re.S | re.I)


def arlberg_url(resort: Resort) -> str:
    return resort.snow_report_url


def parse_arlberg(resort: Resort, html: str, observed_at: str) -> FetchResult:
    items = {m.group(1).strip().lower(): _text(m.group(2)) for m in _ARL_ITEM.finditer(html)}
    if not items:
        return FetchResult(resort.resort_id, "no_report", "no snow-report items on the page")

    row: dict = {
        "observed_at": observed_at,
        "season": season_for(datetime.now(timezone.utc), resort.hemisphere),
        "resort_id": resort.resort_id,
        # The page publishes no timestamp at all, so de-duplication falls back
        # to the values plus the fetch date: one row a day unless they move.
        "report_updated_utc": "",
        "report_updated_text": "",
        "overall_conditions": "",
        "summit_readings": "true",
        "vail_resort_id": "",
    }
    cond = items.get("consistency", "")
    if cond.lower() not in ("", "no info", "-", "--"):
        row["overall_conditions"] = cond

    published = 0
    for stem in DEPTH_STEMS:
        row[f"{stem}_in"] = ""
        row[f"{stem}_cm"] = ""
    for label, stem in ARLBERG_LABELS.items():
        v = _num(items.get(label))
        if v is not None:
            row[f"{stem}_cm"] = v
            published += 1

    # "New snow" is deliberately not mapped. It sits beside "Last snowfall:
    # Wednesday" with no stated window, so it is not necessarily a 24-hour
    # figure, and filing it as snow_24h would be inventing a measurement.

    m = _ARL_LIFTS.search(html)
    if m:
        row["lifts_open"] = int(m.group(1))

    if published == 0:
        return FetchResult(resort.resort_id, "no_report", "snow boxes present but no depth")
    return FetchResult(resort.resort_id, "ok", snow=fill_units(row, "cm"))


# ---------------------------------------------------------------- soelden

#: Sölden's own Schneebericht page renders client-side; its homepage does not,
#: and carries the same figures as labelled tiles. So this adapter reads the
#: homepage while the registry still points a reader at the report page.
_SOL_TILE = re.compile(
    r'live-info-tile__title[^>]*>\s*([^<]*?)\s*</span>\s*'
    r'<span[^>]*fst--bodytext--small[^>]*>\s*([^<]*?)\s*</span>', re.S)
_SOL_RANGE = re.compile(r'(\d+(?:[.,]\d+)?)\s*-\s*(\d+(?:[.,]\d+)?)\s*cm')
#: "14 of 31" / "14 von 31" — the separator word is whichever language was
#: served, so it is matched as "not digits" rather than spelled out. Slopes
#: read "41.3 of 147.2km" and are excluded by the trailing unit.
_SOL_OF = re.compile(r'^(\d+(?:[.,]\d+)?)\s*[^\d]{1,6}\s*(\d+(?:[.,]\d+)?)\s*$')
#: whichever language the tiles came back in
_SOL_LIFT_LABELS = ("lifts", "lifte")


def soelden_url(resort: Resort) -> str:
    # Pinned to a locale. Left to negotiate, the site answers our
    # Accept-Language header with the English page, so a German label would
    # match in a browser and never in the fetch.
    return f"https://www.{resort.domain}/en"


def parse_soelden(resort: Resort, html: str, observed_at: str) -> FetchResult:
    tiles = {label.strip().lower(): value.strip() for value, label in _SOL_TILE.findall(html)}
    if not tiles:
        return FetchResult(resort.resort_id, "no_report", "no live-info tiles on the page")

    row: dict = {
        "observed_at": observed_at,
        "season": season_for(datetime.now(timezone.utc), resort.hemisphere),
        "resort_id": resort.resort_id,
        "report_updated_utc": "",      # no stamp published; see parse_arlberg
        "report_updated_text": "",
        "overall_conditions": "",
        "summit_readings": "true",
        "vail_resort_id": "",
    }
    for stem in DEPTH_STEMS:
        row[f"{stem}_in"] = ""
        row[f"{stem}_cm"] = ""

    # "0-48 cm" is valley to mountain, not a margin of error. The upper figure
    # is the mountain, which is what base depth means everywhere else here.
    # Found by the shape of the value rather than by its label, since "cm" is
    # the same word in every language this page is served in.
    published = 0
    for value in tiles.values():
        m = _SOL_RANGE.search(value)
        if m:
            row["base_depth_cm"] = _num(m.group(2).replace(",", "."))
            published += 1
            break

    for label in _SOL_LIFT_LABELS:
        lifts = _SOL_OF.search(tiles.get(label, ""))
        if lifts:
            row["lifts_open"] = int(float(lifts.group(1).replace(",", ".")))
            row["lifts_total"] = int(float(lifts.group(2).replace(",", ".")))
            break
    # "Pisten" is given in kilometres open, not a count of runs, so it has
    # nowhere to go in a schema whose run columns are counts.

    if published == 0:
        return FetchResult(resort.resort_id, "no_report",
                           "tiles present but no snow figure (likely out of season)")
    return FetchResult(resort.resort_id, "ok", snow=fill_units(row, "cm"))


# ---------------------------------------------------------------- dispatch


@dataclass
class Adapter:
    """Where to GET a resort's report, and how to read what comes back."""

    url: object      # Resort -> str
    parse: object    # (Resort, str, str) -> FetchResult


ADAPTERS: dict[str, Adapter] = {
    "vail_cms": Adapter(lambda r: r.snow_report_url, parse_page),
    "rcr_refresh": Adapter(rcr_url, parse_rcr),
    "telluride": Adapter(telluride_url, parse_telluride),
    "rusutsu": Adapter(rusutsu_url, parse_rusutsu),
    "kitzsteinhorn": Adapter(kitzsteinhorn_url, parse_kitzsteinhorn),
    "arlberg": Adapter(arlberg_url, parse_arlberg),
    "soelden": Adapter(soelden_url, parse_soelden),
}


def fetch_resort(resort: Resort, observed_at: str) -> FetchResult:
    """Fetch and parse one resort, isolating every failure to that resort.

    A broken site, a changed page or a parser bug must cost one row, never the
    run — the other resorts' numbers are still good.
    """
    adapter = ADAPTERS.get(resort.data_source)
    if adapter is None:
        return FetchResult(resort.resort_id, "no_source", "no data source configured yet")
    try:
        url = adapter.url(resort)
    except Exception as exc:  # noqa: BLE001
        return FetchResult(resort.resort_id, "error", f"url: {type(exc).__name__}: {exc}"[:120])
    if not url:
        return FetchResult(resort.resort_id, "error",
                           f"{resort.data_source} needs a snow_report_url")
    try:
        body = http_get(url)
    except urllib.error.HTTPError as exc:
        return FetchResult(resort.resort_id, "error", f"HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001
        return FetchResult(resort.resort_id, "error", f"{type(exc).__name__}: {exc}"[:120])
    try:
        return adapter.parse(resort, body, observed_at)
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
    # Which unit the source actually published; the other one is computed.
    "units_native",
    # Only some sources publish these. Vail's snow report page does not embed
    # them at all, so they stay blank for Vail-operated resorts.
    "lifts_open", "lifts_total", "runs_open", "runs_total", "runs_groomed",
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
