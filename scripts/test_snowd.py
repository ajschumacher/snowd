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
    check("RCR pipe format",
          snowd.parse_report_timestamp("NOVEMBER 5, 2026 | 7:26am", mt),
          "2026-11-05T14:26:00Z")
    check("date with no time at all",
          snowd.parse_report_timestamp("Nov 5, 2026", mt), "2026-11-05T07:00:00Z")
    check("Telluride 'Last Updated:' prefix",
          snowd.parse_report_timestamp("Last Updated: November 5, 2026 at 7:52am", mt),
          "2026-11-05T14:52:00Z")
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


# ---------------------------------------------------------------- units
def t_units():
    row = {"base_depth_in": "", "base_depth_cm": 25.0,
           "season_total_in": 12.0, "season_total_cm": ""}
    for stem in snowd.DEPTH_STEMS:
        row.setdefault(f"{stem}_in", "")
        row.setdefault(f"{stem}_cm", "")
    out = snowd.fill_units(dict(row), "cm")
    check("cm fills inches", out["base_depth_in"], 9.8)
    check("inches fills cm", out["season_total_cm"], 30.5)
    check("native unit recorded", out["units_native"], "cm")
    check("a blank metric stays blank", out["snow_24h_in"], "")

    # a dual-unit source must come through untouched - no rounding drift
    both = {f"{s}_{u}": "" for s in snowd.DEPTH_STEMS for u in ("in", "cm")}
    both.update({"base_depth_in": 18.0, "base_depth_cm": 45.0})
    out = snowd.fill_units(both, "in,cm")
    check("dual-unit inches untouched", out["base_depth_in"], 18.0)
    check("dual-unit cm untouched (45, not 45.7)", out["base_depth_cm"], 45.0)


def t_text():
    check("tags stripped", snowd._text("<b>Powder</b>"), "Powder")
    # RCR puts a styled caption inside the conditions field; flattening it
    # would make every resort's conditions read "UPPER MTN".
    check("subtitle span dropped whole",
          snowd._text("Powder<span class='rcr-element-box-conditions-subtitle'>UPPER MTN</span>"),
          "Powder")
    check("caption-only field is empty, not 'UPPER MTN'",
          snowd._text("<span class='x-conditions-subtitle'>UPPER MTN</span>"), "")
    check("entities decoded", snowd._text("Jones &amp; Co&nbsp;"), "Jones & Co")
    check("empty stays empty", snowd._text(""), "")


# ------------------------------------------------------- partner adapters
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def _resort(rid):
    return {r.resort_id: r for r in snowd.load_resorts()}[rid]


def t_rcr():
    body = (FIXTURES / "rcr_fernie_refresh.js").read_text(encoding="utf-8")
    r = _resort("fernie")
    check("fernie is on the rcr adapter", r.data_source, "rcr_refresh")
    check("rcr url derives from the domain",
          snowd.rcr_url(r), "https://skifernie.com/refresh/refresh.php")
    out = snowd.ADAPTERS["rcr_refresh"].parse(r, body, "2026-11-05T15:00:00Z")
    check("rcr status", out.status, "ok")
    check("base depth in cm, verbatim", out.snow["base_depth_cm"], 10.0)
    check("base depth converted to inches", out.snow["base_depth_in"], 3.9)
    check("season total", out.snow["season_total_cm"], 35.0)
    check("overnight snowfall", out.snow["snow_12h_cm"], 0.0)
    check("native unit is cm", out.snow["units_native"], "cm")
    check("lifts open captured", out.snow["lifts_open"], 0)
    check("runs groomed captured", out.snow["runs_groomed"], 1)
    # The snow block's own stamp, not the lift block's (which is months old).
    check("stamp comes from the snow block",
          out.snow["report_updated_utc"], "2026-11-05T13:26:00Z")
    check("conditions field holds no caption text", out.snow["overall_conditions"], "")

    # an apostrophe in a value must not truncate the next field
    tricky = ("""document.getElementById('SnowreportComments').innerHTML= "it's deep";\n"""
              """document.getElementById('snowReportNewSnowFallPack').innerHTML= "22 ";""")
    out = snowd.ADAPTERS["rcr_refresh"].parse(r, tricky, "t")
    check("apostrophe does not swallow the next field",
          out.snow["base_depth_cm"], 22.0)

    check("no assignments at all -> error",
          snowd.ADAPTERS["rcr_refresh"].parse(r, "<html>down</html>", "t").status, "error")
    check("assignments but no snow -> no_report",
          snowd.ADAPTERS["rcr_refresh"].parse(
              r, "document.getElementById('snowReportRunsOpen').innerHTML= \"0\";",
              "t").status, "no_report")


def t_telluride():
    body = (FIXTURES / "telluride_snow_report.html").read_text(encoding="utf-8")
    r = _resort("telluride")
    check("telluride is on its own adapter", r.data_source, "telluride")
    out = snowd.ADAPTERS["telluride"].parse(r, body, "2026-11-05T15:00:00Z")
    check("telluride status", out.status, "ok")
    check("snow depth read by its label", out.snow["base_depth_in"], 12.0)
    check("season total read by its label", out.snow["season_total_in"], 12.0)
    check("overnight read by its label", out.snow["snow_12h_in"], 6.0)
    check("7-day read by its label", out.snow["snow_7d_in"], 6.0)
    check("inches converted to cm", out.snow["base_depth_cm"], 30.5)
    check("native unit is inches", out.snow["units_native"], "in")
    check("conditions", out.snow["overall_conditions"], "Variable")
    check("stamp parsed (7:52am MST)",
          out.snow["report_updated_utc"], "2026-11-05T14:52:00Z")
    # The lifts panel and the trails panel look identical; they are told apart
    # by "On hold" vs "Groomed", not by which appears first.
    check("lifts panel", (out.snow["lifts_open"], out.snow["lifts_total"]), (0, 17))
    check("trails panel", (out.snow["runs_open"], out.snow["runs_total"]), (0, 149))

    check("no labelled figures -> no_report",
          snowd.ADAPTERS["telluride"].parse(r, "<html>summer</html>", "t").status,
          "no_report")


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
    unknown = sorted({r.data_source for r in rs if r.data_source} - set(snowd.ADAPTERS))
    check("every data_source names a real adapter", unknown, [])
    # rcr_refresh derives its endpoint from the domain, but the registry's url
    # is still the page a person reads, and the viewer links to it.
    no_url = [r.resort_id for r in rs if r.data_source and not r.snow_report_url]
    check("every configured resort has a report url", no_url, [])
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
               t_parse_page, t_units, t_text, t_rcr, t_telluride,
               t_dedup, t_forecast_payload, t_registry):
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
