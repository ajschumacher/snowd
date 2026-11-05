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
    # The Alterra feed stamps its reports in ISO 8601 with an offset. Left
    # unparsed these fall back to the fetch time, throwing away the one thing
    # this project is built on.
    check("ISO 8601 with a -0700 offset",
          snowd.parse_report_timestamp("2026-11-05T10:18:17-0700", mt),
          "2026-11-05T17:18:17Z")
    check("ISO 8601 with a colon in the offset",
          snowd.parse_report_timestamp("2026-11-05T10:01:12-08:00", mt),
          "2026-11-05T18:01:12Z")
    check("offset wins over the resort's timezone",
          snowd.parse_report_timestamp("2026-11-05T10:00:00+0900", mt),
          "2026-11-05T01:00:00Z")
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
    # Keyword-built on purpose: positional args silently shift whenever a
    # field is added to the registry.
    r = snowd.Resort(resort_id="x", name="X", operator="op", passes="epic",
                     pass_tier="owned", country="US", region="Rockies", state="CO",
                     domain="x.com", snow_report_url="https://x", latitude=39.0,
                     longitude=-106.0, timezone="America/Denver", data_source="vail_cms")
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
    # "Last Updated" on this page is the render time, not a publish time —
    # fetched twice a minute apart it follows the clock. Recorded as a publish
    # time it made every fetch look like a fresh report.
    check("render clock is not stored as a publish time",
          out.snow["report_updated_utc"], "")
    check("...but the text it showed is kept",
          out.snow["report_updated_text"], "November 5, 2026 at 7:52am")
    # The lifts panel and the trails panel look identical; they are told apart
    # by "On hold" vs "Groomed", not by which appears first.
    check("lifts panel", (out.snow["lifts_open"], out.snow["lifts_total"]), (0, 17))
    check("trails panel", (out.snow["runs_open"], out.snow["runs_total"]), (0, 149))

    check("no labelled figures -> no_report",
          snowd.ADAPTERS["telluride"].parse(r, "<html>summer</html>", "t").status,
          "no_report")


def t_rusutsu():
    body = (FIXTURES / "rusutsu_snow_report.html").read_text(encoding="utf-8")
    r = _resort("rusutsu")
    check("rusutsu is on its own adapter", r.data_source, "rusutsu")
    out = snowd.ADAPTERS["rusutsu"].parse(r, body, "2026-11-05T16:00:00Z")
    check("rusutsu status", out.status, "ok")
    # Captured pre-season: every per-mountain figure reads "--", and only the
    # season total carries a real number. "--" must read as absent, not zero.
    check("'--' is absent, not zero", out.snow["base_depth_cm"], "")
    check("a real zero is kept", out.snow["season_total_cm"], 0.0)
    check("native unit is cm", out.snow["units_native"], "cm")
    # "23/10/2026(Fri.) 12:32" is day-first; read as month-first it would be
    # a different date entirely for any day below the 13th.
    check("day-first date, JST", out.snow["report_updated_utc"], "2026-10-23T03:32:00Z")

    # three mountains, three readings, no resort-wide figure: deepest wins
    many = ("<dl><dt>Snow Accumulation</dt><dd>90cm</dd></dl>"
            "<dl><dt>Snow Accumulation</dt><dd>140cm</dd></dl>"
            "<dl><dt>Snow Accumulation</dt><dd>120cm</dd></dl>"
            "<dl><dt>Snow Quality</dt><dd>--</dd></dl>"
            "<dl><dt>Snow Quality</dt><dd>Powder</dd></dl>")
    out = snowd.ADAPTERS["rusutsu"].parse(r, many, "t")
    check("deepest mountain wins", out.snow["base_depth_cm"], 140.0)
    check("first real snow quality wins over '--'",
          out.snow["overall_conditions"], "Powder")

    # the page has shipped "Snow Sccumulation" for a while; both must work
    typo = "<dl><dt>Snow Sccumulation</dt><dd>55cm</dd></dl>"
    check("misspelled label still read",
          snowd.ADAPTERS["rusutsu"].parse(r, typo, "t").snow["base_depth_cm"], 55.0)

    check("no labelled pairs -> no_report",
          snowd.ADAPTERS["rusutsu"].parse(r, "<html>closed</html>", "t").status, "no_report")
    check("every figure '--' -> no_report",
          snowd.ADAPTERS["rusutsu"].parse(
              r, "<dl><dt>Snow Accumulation</dt><dd>--cm</dd></dl>", "t").status, "no_report")


def t_kitzsteinhorn():
    body = (FIXTURES / "kitzsteinhorn_weather.html").read_text(encoding="utf-8")
    r = _resort("kitzsteinhorn")
    check("kitzsteinhorn is on its own adapter", r.data_source, "kitzsteinhorn")
    out = snowd.ADAPTERS["kitzsteinhorn"].parse(r, body, "2026-11-05T16:00:00Z")
    check("kitzsteinhorn status", out.status, "ok")
    # Langwied 0cm, Alpincenter 50cm, Glacier 75cm — the highest station, not
    # the first listed, and not the valley.
    check("highest station wins", out.snow["base_depth_cm"], 75.0)
    check("converted to inches", out.snow["base_depth_in"], 29.5)
    check("flagged as an upper-mountain reading", out.snow["summit_readings"], "true")
    check("this source publishes base depth only", out.snow["season_total_cm"], "")
    # The table is headed "Nov / 05" with no year at all.
    check("year inferred onto the table's day and month",
          out.snow["report_updated_utc"], "2026-11-04T23:00:00Z")

    check("no snow table -> no_report",
          snowd.ADAPTERS["kitzsteinhorn"].parse(r, "<html>summer</html>", "t").status,
          "no_report")
    check("table but no readings -> no_report",
          snowd.ADAPTERS["kitzsteinhorn"].parse(
              r, '<div class="weather-table--snow"></details>', "t").status, "no_report")


def t_arlberg():
    body = (FIXTURES / "skiarlberg_snow_report.html").read_text(encoding="utf-8")
    r = _resort("ski-arlberg")
    check("arlberg is on its own adapter", r.data_source, "arlberg")
    out = snowd.ADAPTERS["arlberg"].parse(r, body, "2026-11-05T17:00:00Z")
    check("arlberg status", out.status, "ok")
    # Valley and mountain are published separately; base depth means mountain.
    check("mountain depth taken", out.snow["base_depth_cm"], 0.0)
    check("lifts open", out.snow["lifts_open"], 0)
    check("no timestamp is published", out.snow["report_updated_utc"], "")
    # "New snow" has no stated window - it sits beside "Last snowfall:
    # Wednesday" - so it must not be filed as a 24-hour measurement.
    check("new snow is not passed off as 24h", out.snow.get("snow_24h_cm", ""), "")
    # "No Info" is the page's way of saying nothing, not a condition.
    check("'No Info' is not a condition", out.snow["overall_conditions"], "")

    cond = ('<div class="snow-report-box__item"> <div>Consistency</div>'
            '<div class="snow-report-box__item-info"><span>Powder</span></div></div>'
            '<div class="snow-report-box__item"> <div>Mountain</div>'
            '<div class="snow-report-box__item-info"><span>120 cm</span></div></div>')
    out = snowd.ADAPTERS["arlberg"].parse(r, cond, "t")
    check("a real condition is kept", out.snow["overall_conditions"], "Powder")
    check("mountain depth read", out.snow["base_depth_cm"], 120.0)

    check("no items -> no_report",
          snowd.ADAPTERS["arlberg"].parse(r, "<html>nope</html>", "t").status, "no_report")


def t_soelden():
    body = (FIXTURES / "soelden_home.html").read_text(encoding="utf-8")
    r = _resort("soelden")
    check("soelden is on its own adapter", r.data_source, "soelden")
    # The registry points a reader at the snow-report page, but that page
    # renders client-side — the adapter reads the home page instead.
    # Left to negotiate, the site answers our Accept-Language with English,
    # so the locale is pinned rather than guessed.
    check("adapter reads a pinned locale of the home page",
          snowd.soelden_url(r), "https://www.soelden.com/en")
    check("registry still links the human page",
          r.snow_report_url, "https://www.soelden.com/de/live-infos/schneebericht")
    out = snowd.ADAPTERS["soelden"].parse(r, body, "2026-11-05T17:00:00Z")
    check("soelden status", out.status, "ok")
    # "0-48 cm" is valley to mountain, not a margin of error.
    check("upper bound of the range is base depth", out.snow["base_depth_cm"], 48.0)
    check("converted to inches", out.snow["base_depth_in"], 18.9)
    check("lifts open", (out.snow["lifts_open"], out.snow["lifts_total"]), (14, 31))
    # The same page in German must parse identically: the snow tile is found
    # by its "cm" value, and "von"/"of" is matched as "not digits".
    de = ('<span class="live-info-tile__title a">0-48 cm</span>'
          '<span class="fst--bodytext--small b">Schneebericht</span>'
          '<span class="live-info-tile__title a">14 von 31</span>'
          '<span class="fst--bodytext--small b">Lifte</span>'
          '<span class="live-info-tile__title a">41,3 von 147,2km</span>'
          '<span class="fst--bodytext--small b">Pisten</span>')
    deo = snowd.ADAPTERS["soelden"].parse(r, de, "t")
    check("German page parses the same depth", deo.snow["base_depth_cm"], 48.0)
    check("German page parses the same lifts",
          (deo.snow["lifts_open"], deo.snow["lifts_total"]), (14, 31))
    check("slope kilometres are not mistaken for lifts",
          deo.snow["lifts_total"] != 147, True)
    # "Pisten" is kilometres open, not a count, so it has nowhere to go.
    check("piste kilometres are not filed as a run count",
          out.snow.get("runs_open", ""), "")
    check("no timestamp is published", out.snow["report_updated_utc"], "")

    check("no tiles -> no_report",
          snowd.ADAPTERS["soelden"].parse(r, "<html>nope</html>", "t").status, "no_report")
    check("tiles but no snow tile -> no_report",
          snowd.ADAPTERS["soelden"].parse(
              r, '<span class="live-info-tile__title x">5,0 °C</span>'
                 '<span class="fst--bodytext--small y">Wetter</span>', "t").status,
          "no_report")


def t_mtn_powder():
    body = (FIXTURES / "mtn_powder_mammoth.json").read_text(encoding="utf-8")
    r = _resort("mammoth")
    check("mammoth is on the mtn_powder adapter", r.data_source, "mtn_powder")
    check("feed url is built from source_id",
          snowd.mtn_powder_url(r), "https://mtnpowder.com/feed?resortId=60")
    out = snowd.ADAPTERS["mtn_powder"].parse(r, body, "2026-11-05T18:30:00Z")
    check("mtn_powder status", out.status, "ok")
    # Dual-unit at source, so neither side is computed. Mammoth publishes
    # 12" at Main Lodge, 15" at McCoy Station and 20" at the summit; the
    # mid-mountain figure is the one taken.
    check("mid-mountain inches verbatim", out.snow["base_depth_in"], 15.0)
    check("mid-mountain cm verbatim (38, not 38.1)", out.snow["base_depth_cm"], 38.0)
    check("elevation recorded", out.snow["reading_level"], "mid")
    check("season total", (out.snow["season_total_in"], out.snow["season_total_cm"]), (12.0, 30.0))
    check("recorded as dual-unit", out.snow["units_native"], "in,cm")
    check("not claimed as a summit reading", out.snow["summit_readings"], "false")
    check("lifts", (out.snow["lifts_open"], out.snow["lifts_total"]), (2, 25))
    check("trails", (out.snow["runs_open"], out.snow["runs_total"]), (3, 181))
    # ISO 8601 with an offset; left unparsed this silently became the fetch time.
    check("publish time parsed", out.snow["report_updated_utc"], "2026-11-05T18:01:12Z")

    # One side of a metric can be "--" while the other has a number. That is a
    # gap in the feed, and the missing side gets computed from the one given.
    check("'--' inches filled from the cm that was given",
          out.snow["snow_7d_in"], 0.0)

    # Taking the summit produces outliers, not a useful number: Crystal
    # Mountain publishes 0" at the base, 0" mid and 70" at the summit.
    crystal = """{"SnowReport": {"LastUpdate": "2026-10-30T09:27:32-0700",
      "BaseArea": {"BaseIn": "0", "BaseCm": "0"},
      "MidMountainArea": {"BaseIn": "0", "BaseCm": "0"},
      "SummitArea": {"BaseIn": "70", "BaseCm": "178"}}}"""
    out = snowd.ADAPTERS["mtn_powder"].parse(r, crystal, "t")
    check("mid-mountain wins over a summit outlier", out.snow["base_depth_in"], 0.0)
    check("...and the level says which was used", out.snow["reading_level"], "mid")

    # Several of them publish only one of the three.
    base_only = """{"SnowReport": {"LastUpdate": "2026-11-05T10:00:00-0700",
      "SummitArea": {"BaseIn": "--", "BaseCm": "--"},
      "MidMountainArea": {"BaseIn": "--", "BaseCm": "--"},
      "BaseArea": {"BaseIn": "30", "BaseCm": "76"}}}"""
    out = snowd.ADAPTERS["mtn_powder"].parse(r, base_only, "t")
    check("falls back to the base area", out.snow["base_depth_in"], 30.0)
    check("level says base", out.snow["reading_level"], "base")

    summit_only = """{"SnowReport": {"LastUpdate": "2026-11-05T10:00:00-0700",
      "SummitArea": {"BaseIn": "44", "BaseCm": "112"}}}"""
    out = snowd.ADAPTERS["mtn_powder"].parse(r, summit_only, "t")
    check("summit used only when it is all there is", out.snow["base_depth_in"], 44.0)
    check("...and is flagged as a summit reading", out.snow["summit_readings"], "true")

    # Lots of resorts sit in this feed with a forecast and no snow report.
    check("forecast-only entry -> no_report",
          snowd.ADAPTERS["mtn_powder"].parse(
              r, '{"Name":"Alta","Forecast":{}}', "t").status, "no_report")
    check("every figure '--' -> no_report",
          snowd.ADAPTERS["mtn_powder"].parse(
              r, '{"SnowReport":{"SummitArea":{"BaseIn":"--"}}}', "t").status, "no_report")
    check("not JSON -> error",
          snowd.ADAPTERS["mtn_powder"].parse(r, "<html>nope</html>", "t").status, "error")


# ---------------------------------------------------------------- dedup
def t_dedup():
    a = {"resort_id": "vail", "report_updated_utc": "2026-11-04T12:34:00Z",
         "report_updated_text": "x", "observed_at": "2026-11-04T19:00:00Z",
         "base_depth_in": 18}
    b = dict(a, observed_at="2026-11-04T23:00:00Z")   # same report, fetched later
    check("same publish stamp collapses",
          snowd._snow_identity(a), snowd._snow_identity(b))

    c = dict(a, report_updated_utc="2026-11-05T12:34:00Z", report_updated_text="y")
    check("a new day is a new row",
          snowd._snow_identity(a) != snowd._snow_identity(c), True)

    # Telluride stamps its page with the render time, so every fetch carried a
    # new "publish" time with identical numbers — twelve rows in an afternoon.
    bumped = dict(a, report_updated_utc="2026-11-04T19:58:00Z",
                  observed_at="2026-11-04T19:58:10Z")
    check("same day + same numbers collapses however the stamp moves",
          snowd._snow_identity(a), snowd._snow_identity(bumped))
    moved = dict(bumped, base_depth_in=19)
    check("...but a changed number is a new row",
          snowd._snow_identity(a) != snowd._snow_identity(moved), True)

    # A report left up all summer must not become one row per day of polling.
    june = {"resort_id": "stowe", "report_updated_utc": "2026-06-23T22:21:00Z",
            "observed_at": "2026-11-04T19:00:00Z", "base_depth_in": 0}
    june_later = dict(june, observed_at="2026-11-20T19:00:00Z")
    check("an old report stays one row however long we poll it",
          snowd._snow_identity(june), snowd._snow_identity(june_later))

    # no timestamp at all -> fall back to values + fetch date
    n1 = {"resort_id": "z", "report_updated_utc": "", "report_updated_text": "",
          "observed_at": "2026-11-04T19:00:00Z", "base_depth_in": 10}
    # (no stamp: the day comes from when we fetched instead)
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
    bad_pass = [r.resort_id for r in rs if not r.pass_list]
    check("every resort names at least one pass", bad_pass, [])
    unknown_pass = sorted({p for r in rs for p in r.pass_list} - {"epic", "ikon"})
    check("no unexpected pass names", unknown_pass, [])
    no_sid = [r.resort_id for r in rs if r.data_source == "mtn_powder" and not r.source_id]
    check("every mtn_powder resort has a source_id", no_sid, [])
    sids = [r.source_id for r in rs if r.data_source == "mtn_powder"]
    check("mtn_powder source_ids are unique", len(sids), len(set(sids)))
    check("southern resorts detected",
          sorted(r.resort_id for r in rs if r.hemisphere == "S"),
          ["falls-creek", "mount-hotham", "perisher"])


def main() -> int:
    for fn in (t_seasons, t_timestamps, t_numbers, t_extraction,
               t_parse_page, t_units, t_text, t_rcr, t_telluride,
               t_rusutsu, t_kitzsteinhorn, t_arlberg, t_soelden, t_mtn_powder,
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
