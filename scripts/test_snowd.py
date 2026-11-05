#!/usr/bin/env python3
"""Self-checks for the parsing and de-duplication logic.

    python3 scripts/test_snowd.py

No network, no dependencies.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import snowd  # noqa: E402

FAILED = []


def check(label, got, want):
    if got != want:
        FAILED.append(f"{label}\n     got:  {got!r}\n     want: {want!r}")


# ---------------------------------------------------------------- seasons
def t_seasons():
    d = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    check("Nov -> span season", snowd.season_for(d("2026-11-04")), "2026-27")
    check("Jan -> previous year's season", snowd.season_for(d("2027-01-15")), "2026-27")
    check("June -> previous season still", snowd.season_for(d("2027-06-30")), "2026-27")
    check("July -> new season", snowd.season_for(d("2027-07-01")), "2027-28")
    check("century roll", snowd.season_for(d("2099-12-01")), "2099-00")
    check("southern hemisphere", snowd.season_for(d("2026-08-01"), "S"), "2026")


# ---------------------------------------------------------------- timestamps
def t_timestamps():
    mt = ZoneInfo("America/Denver")
    check("MST report",
          snowd.parse_report_timestamp("Updated November 04, 2026 at 5:34 AM MST", mt),
          "2026-11-04T12:34:00Z")
    check("MDT report",
          snowd.parse_report_timestamp("Updated June 23, 2026 at 4:22 PM MDT", mt),
          "2026-06-23T22:22:00Z")
    check("PDT report",
          snowd.parse_report_timestamp("Updated October 21, 2026 at 7:36 AM PDT", mt),
          "2026-10-21T14:36:00Z")
    check("no timezone falls back to resort tz",
          snowd.parse_report_timestamp("Updated November 04, 2026 at 5:34 AM", mt),
          "2026-11-04T12:34:00Z")
    check("ordinal suffix",
          snowd.parse_report_timestamp("Updated November 4th, 2026 at 5:34 AM MST", mt),
          "2026-11-04T12:34:00Z")
    check("unparseable -> None",
          snowd.parse_report_timestamp("Updated recently", mt), None)
    check("empty -> None", snowd.parse_report_timestamp("", mt), None)


# ---------------------------------------------------------------- numbers
def t_numbers():
    check("plain int", snowd._num("18"), 18.0)
    check("decimal", snowd._num("18.5"), 18.5)
    check("thousands separator", snowd._num("1,200"), 1200.0)
    check("trace value", snowd._num("<1"), 1.0)
    check("dash -> None", snowd._num("-"), None)
    check("empty -> None", snowd._num(""), None)
    check("None -> None", snowd._num(None), None)
    check("n/a -> None", snowd._num("N/A"), None)


# ---------------------------------------------------------------- extraction
def t_extraction():
    html = """
      <script>
        FR.snowReportData = {"BaseDepth":{"Inches":"18","Centimeters":"45"},
          "LastUpdatedText":"Updated November 04, 2026 at 5:34 AM MST"};
        FR.snowReportSettings = {"SnowAndWebcamResortId":3,"UseSummitSnowReadings":false};
        FR.forecasts = [{"ForecastData":[{"Date":"2026-11-04T07:00:00Z","HighTempStandard":46}]}];
      </script>"""
    data = snowd._extract_assignment(html, "snowReportData")
    check("extracted base depth", data["BaseDepth"]["Inches"], "18")
    check("extracted settings id",
          snowd._extract_assignment(html, "snowReportSettings")["SnowAndWebcamResortId"], 3)
    check("extracted forecast list length",
          len(snowd._extract_assignment(html, "forecasts")), 1)
    check("absent key -> None", snowd._extract_assignment(html, "nopeNotHere"), None)

    # a nested brace inside a string must not end the object early
    tricky = 'FR.snowReportData = {"OverallSnowConditions":"a } b \\" c","BaseDepth":{"Inches":"7"}};'
    got = snowd._extract_assignment(tricky, "snowReportData")
    check("brace inside string survives", got["BaseDepth"]["Inches"], "7")
    check("escaped quote survives", got["OverallSnowConditions"], 'a } b " c')


# ---------------------------------------------------------------- parse_page
def t_parse_page():
    r = snowd.Resort("x", "X", "op", "owned", "US", "Rockies", "CO", "x.com",
                     "https://x", 39.0, -106.0, "America/Denver", "vail_cms")
    ok = snowd.parse_page(r, """
        FR.snowReportData = {"BaseDepth":{"Inches":"18","Centimeters":"45"},
          "CurrentSeason":{"Inches":"6","Centimeters":"15"},
          "OverallSnowConditions":"Early Season",
          "LastUpdatedText":"Updated November 04, 2026 at 5:34 AM MST"};
        FR.snowReportSettings = {"SnowAndWebcamResortId":3};
      """, "2026-11-04T19:00:00Z")
    check("status ok", ok.status, "ok")
    check("base inches", ok.snow["base_depth_in"], 18.0)
    check("base cm", ok.snow["base_depth_cm"], 45.0)
    check("season total", ok.snow["season_total_in"], 6.0)
    check("missing metric is blank", ok.snow["snow_24h_in"], "")
    check("timestamp parsed", ok.snow["report_updated_utc"], "2026-11-04T12:34:00Z")

    check("season comes from the report date", ok.snow["season"], "2026-27")

    # Resorts leave last season's final report up all summer. Pulled in
    # November, a June report is still 2025-26 data and must not be filed as a
    # 2026-27 reading of zero.
    stale = snowd.parse_page(r, """
        FR.snowReportData = {"BaseDepth":{"Inches":"0","Centimeters":"0"},
          "CurrentSeason":{"Inches":"0","Centimeters":"0"},
          "LastUpdatedText":"Updated June 23, 2026 at 4:24 PM MDT"};
      """, "2026-11-05T01:04:55Z")
    check("stale June report files under the previous season",
          stale.snow["season"], "2025-26")
    check("...while observed_at still records when we fetched it",
          stale.snow["observed_at"], "2026-11-05T01:04:55Z")

    # with no parseable timestamp, fall back to fetch time
    no_stamp = snowd.parse_page(r, 'FR.snowReportData = {"BaseDepth":{"Inches":"5"}};', "t")
    check("no timestamp still parses", no_stamp.status, "ok")
    check("no timestamp leaves report_updated_utc blank",
          no_stamp.snow["report_updated_utc"], "")

    # pre-season: page renders without the component at all
    check("no component -> no_report",
          snowd.parse_page(r, "<html>nothing here</html>", "t").status, "no_report")
    # present but entirely empty
    check("empty metrics -> no_report",
          snowd.parse_page(r, 'FR.snowReportData = {"BaseDepth":{"Inches":"","Centimeters":""}};',
                           "t").status, "no_report")


# ---------------------------------------------------------------- dedup
def t_dedup():
    a = {"resort_id": "vail", "report_updated_utc": "2026-11-04T12:34:00Z",
         "report_updated_text": "x", "observed_at": "2026-11-04T19:00:00Z",
         "base_depth_in": 18}
    b = dict(a, observed_at="2026-11-04T23:00:00Z")   # same report, fetched later
    check("same publish stamp collapses",
          snowd._snow_identity(a), snowd._snow_identity(b))

    c = dict(a, report_updated_utc="2026-11-05T12:34:00Z", report_updated_text="y")
    check("new publish stamp is a new row",
          snowd._snow_identity(a) != snowd._snow_identity(c), True)

    # no timestamp at all -> fall back to values + fetch date
    n1 = {"resort_id": "z", "report_updated_utc": "", "report_updated_text": "",
          "observed_at": "2026-11-04T19:00:00Z", "base_depth_in": 10}
    n2 = dict(n1, observed_at="2026-11-04T23:00:00Z")
    n3 = dict(n1, observed_at="2026-11-05T19:00:00Z")
    n4 = dict(n1, base_depth_in=12)
    check("no stamp, same day + values collapses",
          snowd._snow_identity(n1), snowd._snow_identity(n2))
    check("no stamp, next day is a new row",
          snowd._snow_identity(n1) != snowd._snow_identity(n3), True)
    check("no stamp, changed value is a new row",
          snowd._snow_identity(n1) != snowd._snow_identity(n4), True)


# ---------------------------------------------------------------- forecast churn
def t_forecast_payload():
    """A re-fetch must not rewrite a forecast row that has not actually moved.

    Otherwise the CI job commits every few hours with nothing but a new
    observed_at in the diff.
    """
    base = {c: "" for c in snowd.FORECAST_COLUMNS}
    base.update({"resort_id": "vail", "forecast_date": "2026-11-06", "season": "2026-27",
                 "observed_at": "2026-11-04T12:00:00Z", "high_f": "63.0",
                 "conditions": "Cloudy", "wind_dir": "WSW"})
    later = dict(base, observed_at="2026-11-04T20:00:00Z")      # same forecast
    moved = dict(later, conditions="Scattered T-Storms")        # genuinely changed

    check("same forecast ignores fetch time",
          snowd._fc_payload(base), snowd._fc_payload(later))
    check("changed conditions is a different payload",
          snowd._fc_payload(base) != snowd._fc_payload(moved), True)
    check("observed_at excluded from payload",
          "observed_at" not in [c for c in snowd.FORECAST_COLUMNS
                                if str(base.get(c, "")) in snowd._fc_payload(base)
                                and c == "observed_at"], True)

    # values arriving as floats must compare equal to the strings read back from CSV
    from_csv = dict(base, high_f="63.0")
    from_fetch = dict(base, high_f=63.0)
    check("float and CSV-string compare equal",
          snowd._fc_payload(from_csv), snowd._fc_payload(from_fetch))


# ---------------------------------------------------------------- registry
def t_registry():
    rs = snowd.load_resorts()
    check("registry non-empty", len(rs) > 0, True)
    ids = [r.resort_id for r in rs]
    check("resort_ids unique", len(ids), len(set(ids)))
    bad_lat = [r.resort_id for r in rs if not (-90 <= r.latitude <= 90)]
    bad_lon = [r.resort_id for r in rs if not (-180 <= r.longitude <= 180)]
    check("latitudes in range", bad_lat, [])
    check("longitudes in range", bad_lon, [])
    no_url = [r.resort_id for r in rs if r.data_source == "vail_cms" and not r.snow_report_url]
    check("every vail_cms resort has a url", no_url, [])
    bad_tz = []
    for r in rs:
        try:
            ZoneInfo(r.timezone)
        except Exception:
            bad_tz.append(r.resort_id)
    check("timezones all valid", bad_tz, [])
    check("southern resorts detected",
          sorted(r.resort_id for r in rs if r.hemisphere == "S"),
          ["falls-creek", "mount-hotham", "perisher"])


def main() -> int:
    for fn in (t_seasons, t_timestamps, t_numbers, t_extraction,
               t_parse_page, t_dedup, t_forecast_payload, t_registry):
        fn()
    if FAILED:
        print(f"FAILED ({len(FAILED)}):")
        for f in FAILED:
            print("  ✗ " + f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
