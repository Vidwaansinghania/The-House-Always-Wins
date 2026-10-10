"""The House Always Wins: Senate trade reports.

The Senate site blocks scripted HTTP clients, so fetching happens in a browser tab
with senate_fetch.js. This script takes the JSON that snippet returns, keeps the
reports not seen before, writes the trades to a CSV and prints a markdown digest
in the same format as house.py.

Usage (Python 3.10+; on Windows set PYTHONIOENCODING=utf-8):
    python scripts/senate.py                  reads data/senate_inbox.json
    python scripts/senate.py --input FILE     reads another fetch
    python scripts/senate.py --all            re-read every report in the file, seen or not
    python scripts/senate.py --dry-run        print, save nothing
    python scripts/senate.py --print-fetch    print senate_fetch.js with its start date
                                              and seen list filled in, ready for the
                                              browser tool

The start date comes from the previous Senate build (house.plan), or 90 days back
when there is none.
"""

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import house  # noqa: E402  shared paths, amount parsing, digest

SEEN = house.DATA / "senate_seen.json"
TRADES_CSV = house.DATA / "senate_trades.csv"

TX_TYPES = {"Purchase": "BUY", "Sale (Full)": "SELL", "Sale (Partial)": "SELL (partial)",
            "Exchange": "EXCHANGE", "Sale": "SELL"}


def load_senators():
    house.load_legislators()  # refreshes the cached file if it is stale
    by_last = {}
    if not house.LEGISLATORS.exists():
        return by_last
    for p in json.loads(house.LEGISLATORS.read_text(encoding="utf-8")):
        t = p["terms"][-1]
        if t["type"] != "sen":
            continue
        by_last.setdefault(p["name"]["last"].lower(), []).append({
            "bioguide_id": p["id"]["bioguide"],
            "first": p["name"]["first"].lower(),
            "name": p["name"].get("official_full") or f"{p['name']['first']} {p['name']['last']}",
            "party": {"Democrat": "D", "Republican": "R"}.get(t["party"], t["party"][:1]),
            "state": t["state"],
        })
    return by_last


def match_senator(senators, first, last):
    cands = senators.get(last.strip().lower(), [])
    if len(cands) > 1:
        first = first.strip().lower().split(" ")[0]
        cands = [c for c in cands if c["first"].startswith(first) or first.startswith(c["first"])] or cands
    return cands[0] if cands else {}


def load_seen():
    return set(json.loads(SEEN.read_text())) if SEEN.exists() else set()


def print_fetch():
    """The previous Senate build less two days, or 90 days back with no previous build.
    The seen list goes in too, so the browser skips report pages already read."""
    p = house.plan("senate")
    js = (Path(__file__).resolve().parent / "senate_fetch.js").read_text(encoding="utf-8")
    print(f"// {house.scope_line(p, 'Senate')}", file=sys.stderr)
    print(js.replace("'__SINCE__'", f"'{p['since']:%m/%d/%Y}'")
            .replace("/*__SEEN__*/[]", json.dumps(sorted(load_seen()))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(house.DATA / "senate_inbox.json"))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--print-fetch", action="store_true")
    args = ap.parse_args()
    if args.print_fetch:
        print_fetch()
        return

    raw = Path(args.input).read_text(encoding="utf-8").strip()
    payload = json.loads(raw)
    if isinstance(payload, str):  # the browser tool sometimes returns the JSON string quoted
        payload = json.loads(payload)
    seen = load_seen()
    todo = [r for r in payload["reports"] if args.all or r["id"] not in seen]

    senators = load_senators()
    today = dt.date.today()
    p = house.plan("senate", today)
    if payload.get("since"):  # the window the browser actually searched
        p["since"] = house.us_date(payload["since"])
    if args.all:
        p["mode"] = "reread"
    rows, scanned = [], []
    for rep in todo:
        who = match_senator(senators, rep["first"], rep["last"])
        politician = who.get("name") or f"{rep['first'].title()} {rep['last'].title()}"
        filed = house.us_date(rep["filed"])
        if rep["kind"] == "paper" or not rep["trades"]:
            scanned.append({"doc_id": rep["id"], "politician": politician,
                            "state_district": who.get("state", ""),
                            "filed_date": filed, "pdf_url": rep["url"]})
            continue
        for cells in rep["trades"]:
            _, tdate, owner, ticker, asset, asset_type, tx, amount, comment = (cells + [""] * 9)[:9]
            tdate = house.us_date(tdate)
            amount, lo, hi = house.parse_amount(amount)
            days = (filed - tdate).days
            rows.append({
                "doc_id": rep["id"], "filed_date": filed, "politician": politician,
                "bioguide_id": who.get("bioguide_id", ""), "party": who.get("party", "?"),
                "state_district": who.get("state", ""),
                "owner": owner.upper() or "SELF", "asset": asset,
                "ticker": "" if ticker in ("--", "") else ticker,
                "asset_type": asset_type, "transaction": TX_TYPES.get(tx, tx.upper()),
                "transaction_date": tdate, "notification_date": "",
                "amount": amount, "amount_min": lo, "amount_max": hi,
                "days_to_file": days, "late": "yes" if days > house.LATE_AFTER_DAYS else "",
                "filing_status": "Amendment" if "Amendment" in rep["title"] else "",
                "description": "" if comment == "--" else comment, "pdf_url": rep["url"],
            })

    text = house.digest(rows, scanned, [], today, chamber="Senate",
                        scope=house.scope_line(p, "Senate"))
    print(text)
    if args.dry_run:
        return

    house.DIGESTS.mkdir(parents=True, exist_ok=True)
    reread = {r["doc_id"] for r in rows}
    kept = []
    if TRADES_CSV.exists():
        with TRADES_CSV.open(encoding="utf-8", newline="") as fh:
            kept = [r for r in csv.DictReader(fh) if r["doc_id"] not in reread]
    with TRADES_CSV.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=house.FIELDS)
        w.writeheader()
        w.writerows(kept)
        w.writerows({k: r.get(k, "") for k in house.FIELDS} for r in rows)
    SEEN.write_text(json.dumps(sorted(seen | {r["id"] for r in todo})))
    (house.DIGESTS / f"{today:%Y-%m-%d}-senate.md").write_text(text, encoding="utf-8")
    house.record_build("senate", {
        "mode": p["mode"], "previous": p["previous"], "since": p["since"],
        "reports": len(todo), "trades": len(rows), "paper": len(scanned),
        "doc_ids": sorted(r["id"] for r in todo),
    }, today)


if __name__ == "__main__":
    main()
