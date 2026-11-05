#!/usr/bin/env python3
"""Check every adapter against the live sites.

    python3 scripts/check_live.py [--only id,id]

The offline tests in `test_snowd.py` prove each parser still reads its captured
fixture. That is not the same as proving it still reads the site: a fixture is
frozen the day it was taken, and a site can change underneath it. Sölden did
exactly that in a way no fixture could catch — the page negotiates language, so
the capture came back German while the fetcher, which sends an English
Accept-Language header, got English labels that matched nothing.

So this asks three questions of the real response:

  1. does the fetch and parse still work, or does it error;
  2. are the markers each parser depends on still in the page;
  3. is every resort on a given adapter silently reporting nothing at once,
     which is what a quiet breakage looks like from the outside.

It needs the network, so it is not part of the test suite. Run it after
touching an adapter, or when a resort's columns look emptier than they should.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from snowd import ADAPTERS, fetch_resort, http_get, load_resorts  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="", help="comma-separated resort_ids")
    args = ap.parse_args()

    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    resorts = [r for r in load_resorts() if r.data_source in ADAPTERS]
    if wanted:
        resorts = [r for r in resorts if r.resort_id in wanted]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    problems: list[str] = []
    by_adapter: dict[str, list[str]] = {}

    for r in sorted(resorts, key=lambda r: (r.data_source, r.resort_id)):
        adapter = ADAPTERS[r.data_source]
        url = adapter.url(r)
        try:
            body = http_get(url)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR    {r.resort_id:24} {type(exc).__name__}: {exc}"[:110])
            problems.append(f"{r.resort_id}: cannot fetch {url}")
            by_adapter.setdefault(r.data_source, []).append("error")
            continue

        missing = [m for m in adapter.markers if m not in body]
        res = fetch_resort(r, now)
        by_adapter.setdefault(r.data_source, []).append(res.status)

        note = ""
        if res.status == "error":
            problems.append(f"{r.resort_id}: {res.note}")
            note = res.note
        elif missing and res.status == "ok":
            # Parsed anyway, but on markup that has moved — the next change
            # is the one that breaks it.
            problems.append(f"{r.resort_id}: parsed, but markers gone: {', '.join(missing)}")
            note = "markers gone: " + ", ".join(missing)
        elif missing:
            note = "absent: " + ", ".join(m[:28] for m in missing)

        print(f"  {res.status:9} {r.resort_id:24} {len(body):>8}B  {note}"[:150])

    # A whole adapter reporting nothing at once is what quiet breakage looks
    # like. One resort between seasons is ordinary; all of them is not.
    for name, statuses in sorted(by_adapter.items()):
        if len(statuses) > 1 and all(s != "ok" for s in statuses):
            problems.append(f"{name}: not one of its {len(statuses)} resorts returned data")

    print()
    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems:
            print("  ✗ " + p)
        return 1
    print(f"all {len(resorts)} live checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
