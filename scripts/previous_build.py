"""The House Always Wins: read the previous build before a run starts.

Prints, for each chamber, whether this run is an update or a build from scratch,
the date of the previous build, and the filing window the collectors will read.
After collection it can also write the rows this build added to a run folder, so
the analysis agents read only what is new.

Usage (Python 3.10+; on Windows set PYTHONIOENCODING=utf-8):
    python scripts/previous_build.py                     the plan, as text
    python scripts/previous_build.py --json              the plan, as JSON
    python scripts/previous_build.py --new-trades DIR    write DIR/new_trades.csv from
                                                         today's build (--build DATE for another)
"""

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import house  # noqa: E402  shared paths and the build log

CSVS = {"house": house.TRADES_CSV, "senate": house.DATA / "senate_trades.csv"}


def count_rows(path):
    if not path.exists():
        return 0, 0
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return len({r["doc_id"] for r in rows}), len(rows)


def previous_summary(today):
    """The newest run summary written before today, if any."""
    if not house.RUNS.exists():
        return None
    older = sorted(p for p in house.RUNS.glob("*/summary.md") if p.parent.name[:10] < str(today))
    return older[-1] if older else None


def the_plan(today):
    out = {"today": str(today), "chambers": {}}
    for chamber in ("house", "senate"):
        p = house.plan(chamber, today)
        reports, trades = count_rows(CSVS[chamber])
        out["chambers"][chamber] = {
            "mode": p["mode"], "previous": p["previous"] and str(p["previous"]),
            "since": str(p["since"]), "scope": house.scope_line(p, chamber.title()),
            "collected_so_far": {"reports": reports, "trades": trades},
        }
    modes = {c["mode"] for c in out["chambers"].values()}
    out["mode"] = "scratch" if modes == {"scratch"} else "update" if modes == {"update"} else "mixed"
    summary = previous_summary(today)
    out["previous_summary"] = str(summary.relative_to(house.HOME)) if summary else None
    digests = sorted(house.DIGESTS.glob("*.md")) if house.DIGESTS.exists() else []
    newest = max((d.name[:10] for d in digests), default=None)
    out["previous_digests"] = [d.name for d in digests if d.name[:10] == newest]
    return out


def write_new_trades(run_dir, build_date):
    builds = house.load_builds()
    entry = next((b for b in builds if b["built"] == build_date), None)
    if entry is None:
        sys.exit(f"No build dated {build_date} in {house.BUILDS}. Run the collectors first.")
    run_dir.mkdir(parents=True, exist_ok=True)
    out_path = run_dir / "new_trades.csv"
    counts = {}
    with out_path.open("w", encoding="utf-8", newline="") as out:
        w = csv.DictWriter(out, fieldnames=["chamber"] + house.FIELDS)
        w.writeheader()
        for chamber, path in CSVS.items():
            ids = set(entry.get(chamber, {}).get("doc_ids", []))
            n = 0
            if ids and path.exists():
                with path.open(encoding="utf-8", newline="") as fh:
                    for r in csv.DictReader(fh):
                        if r["doc_id"] in ids:
                            w.writerow({"chamber": chamber, **r})
                            n += 1
            counts[chamber] = n
    print(f"{out_path}: {counts['house']} House trades, {counts['senate']} Senate trades")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--new-trades", metavar="DIR", help="write DIR/new_trades.csv")
    ap.add_argument("--build", help="build date for --new-trades, default today")
    args = ap.parse_args()
    today = dt.date.today()

    if args.new_trades:
        write_new_trades(Path(args.new_trades), args.build or str(today))
        return

    p = the_plan(today)
    if args.json:
        print(json.dumps(p, indent=1))
        return
    print(f"Run mode: {p['mode']}")
    for chamber, c in p["chambers"].items():
        so_far = c["collected_so_far"]
        print(f"{chamber.title()}: {c['scope']} Collected so far: "
              f"{so_far['reports']} reports, {so_far['trades']} trades.")
    print(f"Previous summary: {p['previous_summary'] or 'none'}")
    print(f"Previous digests: {', '.join(p['previous_digests']) or 'none'}")


if __name__ == "__main__":
    main()
