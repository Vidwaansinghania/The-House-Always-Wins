"""The House Always Wins: build the infographic.

Reads the House and Senate trade CSVs and writes a self-contained HTML page of
charts to infographics/<today>.html and infographics/latest.html.

Usage (Python 3.10+; on Windows set PYTHONIOENCODING=utf-8):
    python scripts/build_infographic.py              filings from the last 30 days of data
    python scripts/build_infographic.py --days 7     a shorter window
    python scripts/build_infographic.py --all        every trade collected so far
"""

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import house  # noqa: E402  shared paths

TEMPLATE = house.SKILL_DIR / "assets" / "infographic_template.html"
SOURCES = (("H", house.TRADES_CSV), ("S", house.DATA / "senate_trades.csv"))
# Longer suffixes first, so " - Class A" goes before " Class A"
TRIM = [" - American Depositary Shares", " - Common Stock", " Common Stock",
        " - Class A", " Class A", " Ordinary Shares"]


def load(days, everything):
    rows = []
    for chamber, path in SOURCES:
        if path.exists():
            with path.open(encoding="utf-8", newline="") as fh:
                rows += [(chamber, r) for r in csv.DictReader(fh)]
    if not rows:
        sys.exit("No trades collected yet. Run house.py or senate.py first.")
    if not everything:
        newest = max(dt.date.fromisoformat(r["filed_date"]) for _, r in rows)
        cutoff = newest - dt.timedelta(days=days - 1)
        rows = [(c, r) for c, r in rows if dt.date.fromisoformat(r["filed_date"]) >= cutoff]
    return rows


def compact(chamber, r):
    """One trade as the positional array the template's script expects."""
    tx = r["transaction"]
    side = "B" if tx == "BUY" else "S" if tx == "SELL" else "P" if tx.startswith("SELL") else "X"
    name = r["asset"]
    for cut in TRIM:
        name = name.replace(cut, "")
    num = lambda v: int(float(v)) if v else 0
    lo = num(r["amount_min"])
    return [chamber, r["politician"], r["party"], r["state_district"], r["ticker"], name[:60],
            r["asset_type"], side, r["transaction_date"], r["filed_date"], lo,
            num(r["amount_max"]) or lo, r["owner"].title(), num(r["days_to_file"]), r["pdf_url"]]


def long_date(d):
    return f"{d.day} {d:%B %Y}"


def between(a, b):
    if a == b:
        return f"on {long_date(a)}"
    if (a.year, a.month) == (b.year, b.month):
        return f"between {a.day} and {long_date(b)}"
    if a.year == b.year:
        return f"between {a.day} {a:%B} and {long_date(b)}"
    return f"between {long_date(a)} and {long_date(b)}"


def from_to(a, b):
    return between(a, b).replace("between ", "from ").replace(" and ", " to ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="window of filing dates, ending at the newest")
    ap.add_argument("--all", action="store_true", help="every trade collected so far")
    args = ap.parse_args()

    rows = load(args.days, args.all)
    filed = sorted(dt.date.fromisoformat(r["filed_date"]) for _, r in rows)
    traded = sorted(dt.date.fromisoformat(r["transaction_date"]) for _, r in rows)
    today = dt.date.today()
    # "</" inside a filing's asset name must not close the page's <script> tag
    payload = json.dumps([compact(c, r) for c, r in rows], ensure_ascii=False,
                         separators=(",", ":")).replace("</", "<\\/")

    page = TEMPLATE.read_text(encoding="utf-8")
    for key, value in (("__FILED_SPAN__", between(filed[0], filed[-1])),
                       ("__TRADE_SPAN__", from_to(traded[0], traded[-1])),
                       ("__BUILT__", long_date(today)),
                       ("__DATA__", payload)):
        page = page.replace(key, value)

    house.INFOGRAPHICS.mkdir(parents=True, exist_ok=True)
    for name in (f"{today:%Y-%m-%d}.html", "latest.html"):
        (house.INFOGRAPHICS / name).write_text(page, encoding="utf-8")
    house.record_build("infographic", {
        "file": f"infographics/{today:%Y-%m-%d}.html", "trades": len(rows),
        "filed_from": filed[0], "filed_to": filed[-1],
        "window": "all" if args.all else f"{args.days} days",
    }, today)
    print(f"{len(rows)} trades filed {filed[0]} to {filed[-1]}: {house.INFOGRAPHICS / 'latest.html'}")


if __name__ == "__main__":
    main()
