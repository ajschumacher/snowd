#!/usr/bin/env python3
"""Fetch current snow reports for every Epic resort with a data source.

    scripts/fetch.py                 # fetch all, append new rows
    scripts/fetch.py --dry-run       # fetch, print, write nothing
    scripts/fetch.py --only vail,keystone
    scripts/fetch.py --season 2026-27

Fine to run at whatever frequency suits: a report with the same numbers on the
same day is the same row, so extra runs add nothing and missed days just leave
gaps. Please don't run it in a tight loop, though — these are small operations
giving the data away.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from snowd import (
    ADAPTERS,  # noqa: E402
    DATA,
    REPO,
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

    # Any resort whose data_source names a registered adapter is fetchable; the
    # adapter decides what URL that means.
    live = [r for r in resorts if r.data_source in ADAPTERS]
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
        # A partial run (--only) hasn't checked everything, so it doesn't get
        # to tell the site that everything was just checked.
        _write_status(results, by_id, observed_at, args.season, skipped,
                      full_run=not wanted)
        if not args.no_build:
            import build_site_data
            build_site_data.main()

    print(
        f"\n{len(ok)} reporting, {len(none)} no report yet, {len(err)} error(s), "
        f"{len(skipped)} without a data source"
    )
    # Errors are expected transiently; only fail hard if everything broke.
    return 1 if live and not ok and err else 0


def _write_status(results, by_id, observed_at, season_override, skipped, full_run=True) -> None:
    """A small run log, and the "last checked" stamp the site shows.

    Two files, neither committed. `data/last_run.json` is the full per-resort
    log. `site/status.js` is the part the viewer wants: when this run happened
    and which resorts it couldn't read.

    That stamp can't live in `site/data.js`, which is deliberately a pure
    function of the CSVs so that a run finding nothing new commits nothing. But
    "we looked and nothing had changed" is exactly what someone staring at an
    eleven-hour-old number wants to know, so it travels separately: CI ships
    this file with the site on every run without ever committing it.
    """
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

    errors = {r.resort_id: r.note for r in results if r.status == "error"}
    if full_run:
        site_status = {
            "checked_at": observed_at,
            "ok": sum(1 for r in results if r.status == "ok"),
            "no_report": sum(1 for r in results if r.status == "no_report"),
            "errors": errors,
        }
        (REPO / "site").mkdir(parents=True, exist_ok=True)
        (REPO / "site" / "status.js").write_text(
            "/* Written by scripts/fetch.py on every run - not committed. */\n"
            f"window.SNOWD_STATUS = {json.dumps(site_status, sort_keys=True)};\n",
            encoding="utf-8",
        )

    _ci_report(results, by_id, errors)


def _ci_report(results, by_id, errors) -> None:
    """On GitHub Actions, put the outcome where it can be seen without opening a log.

    A resort that errors doesn't fail the run — one site being down shouldn't
    cost the other sixty — which also means nothing draws attention to it. A
    warning annotation and a line in the run summary is the least that can be
    done about that.
    """
    for rid, note in sorted(errors.items()):
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::warning title=snowd fetch::{by_id[rid].name}: {note}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    lines = [
        "### Snow fetch",
        "",
        f"{counts.get('ok', 0)} reporting · {counts.get('no_report', 0)} no report yet · "
        f"{counts.get('error', 0)} error(s)",
        "",
    ]
    if errors:
        lines += ["| Resort | Error |", "|---|---|"]
        lines += [f"| {by_id[rid].name} | {note} |" for rid, note in sorted(errors.items())]
        lines.append("")
    with open(summary, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
