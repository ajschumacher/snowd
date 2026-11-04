#!/usr/bin/env python3
"""Fetch current snow reports for every Epic resort with a data source.

    scripts/fetch.py                 # fetch all, append new rows
    scripts/fetch.py --dry-run       # fetch, print, write nothing
    scripts/fetch.py --only vail,keystone
    scripts/fetch.py --season 2026-27

Safe to run at any frequency: rows are de-duplicated on each resort's own
publish timestamp, so extra runs are no-ops and missed days simply leave gaps.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from snowd import (  # noqa: E402
    DATA,
    FetchResult,
    append_forecast,
    append_snow,
    fetch_resort,
    load_resorts,
    season_for,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="fetch but write nothing")
    ap.add_argument("--only", default="", help="comma-separated resort_ids")
    ap.add_argument("--season", default="", help="override season label")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-build", action="store_true",
                    help="skip rebuilding site/data.js afterwards")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    observed_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    resorts = load_resorts()
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    if wanted:
        resorts = [r for r in resorts if r.resort_id in wanted]
        missing = wanted - {r.resort_id for r in resorts}
        if missing:
            print(f"unknown resort_id(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 2

    live = [r for r in resorts if r.data_source == "vail_cms" and r.snow_report_url]
    skipped = [r for r in resorts if r not in live]

    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        results: list[FetchResult] = list(
            ex.map(lambda r: fetch_resort(r, observed_at), live)
        )

    by_id = {r.resort_id: r for r in resorts}
    ok = [r for r in results if r.status == "ok"]
    none = [r for r in results if r.status == "no_report"]
    err = [r for r in results if r.status == "error"]

    if not args.quiet:
        for res in sorted(results, key=lambda r: (r.status != "ok", r.resort_id)):
            rs = by_id[res.resort_id]
            if res.status == "ok":
                s = res.snow or {}
                print(
                    f"  ok       {rs.name:28.28} base={str(s.get('base_depth_in')):>5}in "
                    f"season={str(s.get('season_total_in')):>5}in  24h={str(s.get('snow_24h_in')):>4}in"
                    f"  ({s.get('report_updated_text') or 'no timestamp'})"
                )
            else:
                print(f"  {res.status:8} {rs.name:28.28} {res.note}")

    # Group writes by season so a season rollover mid-run still lands correctly.
    snow_rows: dict[str, list[dict]] = {}
    fc_rows: dict[str, list[dict]] = {}
    for res in results:
        if res.snow:
            season = args.season or res.snow["season"]
            snow_rows.setdefault(season, []).append(res.snow)
        for row in res.forecast:
            season = args.season or row["season"]
            fc_rows.setdefault(season, []).append(row)

    if args.dry_run:
        total_s = sum(len(v) for v in snow_rows.values())
        total_f = sum(len(v) for v in fc_rows.values())
        print(f"\ndry run: would consider {total_s} snow row(s), {total_f} forecast row(s)")
    else:
        for season, rows in sorted(snow_rows.items()):
            added, total = append_snow(rows, season)
            print(f"\nsnow_{season}.csv      +{added} new row(s), {total} total")
        for season, rows in sorted(fc_rows.items()):
            added, total = append_forecast(rows, season)
            print(f"forecast_{season}.csv  +{added} new row(s), {total} total")
        _write_status(results, by_id, observed_at, args.season, skipped)
        if not args.no_build:
            import build_site_data
            build_site_data.main()

    print(
        f"\n{len(ok)} reporting, {len(none)} no report yet, {len(err)} error(s), "
        f"{len(skipped)} without a data source"
    )
    # Errors are expected transiently; only fail hard if everything broke.
    return 1 if live and not ok and err else 0


def _write_status(results, by_id, observed_at, season_override, skipped) -> None:
    """A small run log the site uses to show freshness and per-resort health."""
    season = season_override or season_for(datetime.now(timezone.utc))
    status = {
        "observed_at": observed_at,
        "season": season,
        "resorts": {
            r.resort_id: {"status": r.status, "note": r.note}
            for r in results
        },
    }
    for rs in skipped:
        status["resorts"].setdefault(
            rs.resort_id, {"status": "no_source", "note": "no data source configured yet"}
        )
    path = DATA / "last_run.json"
    path.write_text(json.dumps(status, indent=1, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
