#!/usr/bin/env python3
"""Bundle the CSVs into site/data.js for the viewer.

The viewer is a single static page. Browsers block `fetch()` of a local CSV
over file://, so the data ships as a plain script that assigns a global - which
works equally when the page is opened straight off disk, served over HTTP, or
published as an artifact.

Season files are discovered from the data directory, so adding a future season
needs no code change here.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from snowd import DATA, REPO, load_resorts  # noqa: E402

SITE = REPO / "site"


def _f(v):
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def discover_seasons() -> list[str]:
    seasons = set()
    for p in DATA.glob("snow_*.csv"):
        m = re.fullmatch(r"snow_(.+)\.csv", p.name)
        if m:
            seasons.add(m.group(1))
    for p in DATA.glob("forecast_*.csv"):
        m = re.fullmatch(r"forecast_(.+)\.csv", p.name)
        if m:
            seasons.add(m.group(1))
    # newest first
    return sorted(seasons, reverse=True)


def latest_forecast(rows: list[dict]) -> list[dict]:
    """The most recent forecast per resort: one row per forecast day.

    "Most recent" is the latest fetch *day*, not the latest fetch timestamp.
    A same-day re-fetch only rewrites the forecast days that actually moved
    (see `append_forecast`), so the rows of one day's forecast carry a mix of
    timestamps. Selecting on the single newest timestamp showed only the days
    that had changed in the last run — a resort would drop from five forecast
    days to four, or to one, until the next UTC day.
    """
    latest_day: dict[str, str] = {}
    for row in rows:
        rid, day = row["resort_id"], (row.get("observed_at") or "")[:10]
        if day > latest_day.get(rid, ""):
            latest_day[rid] = day
    best: dict[tuple, dict] = {}
    for row in rows:
        rid, t = row["resort_id"], row.get("observed_at") or ""
        if t[:10] != latest_day.get(rid):
            continue
        key = (rid, row["forecast_date"])
        if key not in best or t >= (best[key].get("observed_at") or ""):
            best[key] = row
    return list(best.values())


def main() -> int:
    resorts = load_resorts()
    out = {
        # Deliberately NOT wall-clock time. This file is committed by CI every
        # few hours; a "generated now" stamp would make it differ on every run
        # and produce a commit even when no resort had published anything new.
        # Derived from the data, the file is a pure function of the CSVs, so it
        # changes only when the data does. It also answers the more useful
        # question: how fresh are these numbers, not when did a script last run.
        "data_as_of": "",
        "seasons": discover_seasons(),
        "resorts": [
            {
                "id": r.resort_id, "name": r.name, "tier": r.pass_tier,
                "passes": r.pass_list,
                "operator": r.operator, "country": r.country, "region": r.region,
                "state": r.state, "lat": r.latitude, "lon": r.longitude,
                "url": f"https://www.{r.domain}", "report": r.snow_report_url,
                "src": r.data_source, "tz": r.timezone,
            }
            for r in resorts
        ],
        "snow": {},
        "forecast": {},
    }

    for season in out["seasons"]:
        per: dict[str, list[dict]] = {}
        for row in _rows(DATA / f"snow_{season}.csv"):
            per.setdefault(row["resort_id"], []).append({
                "t": row["observed_at"],
                "up": row.get("report_updated_utc") or "",
                "upText": row.get("report_updated_text") or "",
                "base_in": _f(row.get("base_depth_in")),
                "base_cm": _f(row.get("base_depth_cm")),
                "tot_in": _f(row.get("season_total_in")),
                "tot_cm": _f(row.get("season_total_cm")),
                "h12_in": _f(row.get("snow_12h_in")), "h12_cm": _f(row.get("snow_12h_cm")),
                "h24_in": _f(row.get("snow_24h_in")), "h24_cm": _f(row.get("snow_24h_cm")),
                "h48_in": _f(row.get("snow_48h_in")), "h48_cm": _f(row.get("snow_48h_cm")),
                "d7_in": _f(row.get("snow_7d_in")), "d7_cm": _f(row.get("snow_7d_cm")),
                "cond": row.get("overall_conditions") or "",
                "summit": row.get("summit_readings") == "true",
                # Which unit the resort published. Where it is only one of the
                # two, the viewer marks the other as computed rather than
                # presenting arithmetic as the resort's own figure.
                "nat": row.get("units_native") or "in,cm",
                "lifts": _f(row.get("lifts_open")), "liftsN": _f(row.get("lifts_total")),
                "runs": _f(row.get("runs_open")), "runsN": _f(row.get("runs_total")),
                "groomed": _f(row.get("runs_groomed")),
            })
        for v in per.values():
            v.sort(key=lambda x: x["t"])
        out["snow"][season] = per

        fper: dict[str, list[dict]] = {}
        for row in latest_forecast(_rows(DATA / f"forecast_{season}.csv")):
            rid = row["resort_id"]
            fper.setdefault(rid, []).append({
                "date": row["forecast_date"],
                "hi_f": _f(row.get("high_f")), "lo_f": _f(row.get("low_f")),
                "hi_c": _f(row.get("high_c")), "lo_c": _f(row.get("low_c")),
                "day_in": row.get("snow_day_in") or "", "night_in": row.get("snow_night_in") or "",
                "day_cm": row.get("snow_day_cm") or "", "night_cm": row.get("snow_night_cm") or "",
                "cond": row.get("conditions") or "",
                "wind": row.get("wind_dir") or "",
            })
        for v in fper.values():
            v.sort(key=lambda x: x["date"])
        out["forecast"][season] = fper

    # Freshest observation anywhere in the data.
    stamps = [
        obs["t"]
        for season in out["snow"].values()
        for rows in season.values()
        for obs in rows
        if obs.get("t")
    ]
    out["data_as_of"] = max(stamps) if stamps else ""

    SITE.mkdir(parents=True, exist_ok=True)
    target = SITE / "data.js"
    payload = json.dumps(out, separators=(",", ":"), allow_nan=False)
    target.write_text(
        "/* Generated by scripts/build_site_data.py - do not edit by hand. */\n"
        f"window.SNOWD_DATA = {payload};\n",
        encoding="utf-8",
    )
    n_obs = sum(len(v) for v in out["snow"].get(out["seasons"][0], {}).values()) if out["seasons"] else 0
    print(f"site/data.js  {target.stat().st_size / 1024:.1f} KB  "
          f"{len(out['resorts'])} resorts, {len(out['seasons'])} season(s), {n_obs} observations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
