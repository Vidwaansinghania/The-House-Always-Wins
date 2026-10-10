"""The House Always Wins: House of Representatives trade reports.

Downloads the House Clerk's filing index, finds periodic transaction reports
(PTRs) not seen before, reads each PDF, and writes the trades to a CSV plus a
markdown digest. Paper filings are scanned images with no text layer; those are
listed for a manual check instead of being guessed at.

Each run starts from the previous build, recorded in data/builds.json. With a
previous build it reads reports filed since then (less a few days of overlap,
because the Clerk's index posts some filings late) that it has not read before.
With no previous build it builds from scratch over the last 90 days.

Usage (Python 3.10+ with pypdf; on Windows set PYTHONIOENCODING=utf-8):
    python scripts/house.py                       new reports since the previous build
    python scripts/house.py --since 2026-09-04    re-read reports filed on or after a date
    python scripts/house.py --dry-run             parse and print, save nothing
    python scripts/house.py --parse-file X.pdf    debug the parser on one PDF
"""

import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from pypdf import PdfReader

SKILL_DIR = Path(__file__).resolve().parent.parent


def _home():
    """Where data and outputs live. A one-line home.txt beside SKILL.md can point
    somewhere else (relative to the skill folder, or absolute). Without it,
    everything is written inside the skill or repository folder itself."""
    pointer = SKILL_DIR / "home.txt"
    if pointer.exists():
        target = Path(pointer.read_text(encoding="utf-8").strip())
        return (target if target.is_absolute() else SKILL_DIR / target).resolve()
    return SKILL_DIR


HOME = _home()
DATA = HOME / "data"
DIGESTS = HOME / "digests"
INFOGRAPHICS = HOME / "infographics"
RUNS = HOME / "runs"
SEEN = DATA / "house_seen.json"
TRADES_CSV = DATA / "house_trades.csv"
LEGISLATORS = DATA / "legislators-current.json"
BUILDS = DATA / "builds.json"

UA = {"User-Agent": "Mozilla/5.0 (personal research script; the-house-always-wins)"}
INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc}.pdf"
LEGISLATORS_URL = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
FROM_SCRATCH_DAYS = 90  # no previous build: read this far back
# Days re-checked before the previous build. The House index posts weekend and some
# weekday filings a day or more late; the Senate list is same-day. Seen IDs are skipped.
OVERLAP_DAYS = {"house": 7, "senate": 2}
LATE_AFTER_DAYS = 45  # STOCK Act: report no later than 45 days after the trade

FIELDS = [
    "doc_id", "filed_date", "politician", "bioguide_id", "party", "state_district",
    "owner", "asset", "ticker", "asset_type", "transaction", "transaction_date",
    "notification_date", "amount", "amount_min", "amount_max", "days_to_file",
    "late", "filing_status", "description", "pdf_url",
]

TX_TYPES = {"P": "BUY", "S": "SELL", "S (partial)": "SELL (partial)", "E": "EXCHANGE"}
OWNERS = {"SP": "SPOUSE", "JT": "JOINT", "DC": "DEPENDENT CHILD"}

CORE = re.compile(
    r"^(?P<pre>.*?)(?P<type>S \(partial\)|P|S|E)\s?"
    r"(?P<tdate>\d\d/\d\d/\d{4})\s?(?P<ndate>\d\d/\d\d/\d{4})\s*(?P<amt>.*)$"
)
HEADER_LINES = {"ID Owner Asset Transaction", "Type", "Date Notification", "Date",
                "Amount Cap.", "Gains >", "$200?"}
STATUS = re.compile(r"^F\s+S\s+:\s*(.*)$")
SUBHOLDING = re.compile(r"^S\s+O\s*:\s*(.*)$")
DESCRIPTION = re.compile(r"^(?:D|C)\s+:\s*(.*)$")


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def us_date(s):
    m, d, y = map(int, s.strip().split("/"))
    return dt.date(y, m, d)


# ---------- build log ----------

def load_builds():
    return json.loads(BUILDS.read_text(encoding="utf-8")) if BUILDS.exists() else []


def previous_build(chamber):
    """Date of the newest build that read this chamber, or None. Builds made before
    builds.json existed are found from their digest filenames."""
    dates = [dt.date.fromisoformat(b["built"]) for b in load_builds() if chamber in b]
    if not dates and DIGESTS.exists():
        for f in DIGESTS.glob(f"*-{chamber}.md"):
            try:
                dates.append(dt.date.fromisoformat(f.name[:10]))
            except ValueError:
                pass
    return max(dates) if dates else None


def plan(chamber, today=None):
    """Update from the previous build, or build from scratch when there is none."""
    today = today or dt.date.today()
    prev = previous_build(chamber)
    if prev is None:
        return {"chamber": chamber, "mode": "scratch", "previous": None,
                "since": today - dt.timedelta(days=FROM_SCRATCH_DAYS)}
    return {"chamber": chamber, "mode": "update", "previous": prev,
            "since": prev - dt.timedelta(days=OVERLAP_DAYS[chamber])}


def scope_line(p, chamber):
    if p["mode"] == "scratch":
        return (f"Built from scratch: no previous {chamber} build, so this reads every "
                f"report filed on or after {p['since']} ({FROM_SCRATCH_DAYS} days).")
    if p["mode"] == "reread":
        return f"Re-read: every report filed on or after {p['since']}, read before or not."
    return (f"Update since the build of {p['previous']}: reports filed on or after "
            f"{p['since']} that were not read before.")


def record_build(key, info, today=None):
    """Merge one chamber's results (or the infographic's) into today's builds.json entry.
    A second run the same day adds to the first rather than replacing it."""
    today = today or dt.date.today()
    builds = load_builds()
    entry = next((b for b in builds if b["built"] == str(today)), None)
    if entry is None:
        entry = {"built": str(today)}
        builds.append(entry)
    old = entry.get(key)
    if old and "doc_ids" in info:
        for k in ("reports", "trades", "paper"):
            info[k] = old.get(k, 0) + info.get(k, 0)
        info["doc_ids"] = sorted(set(old.get("doc_ids", [])) | set(info["doc_ids"]))
        info["since"] = min(str(old["since"]), str(info["since"]))
    entry[key] = info
    builds.sort(key=lambda b: b["built"])
    DATA.mkdir(parents=True, exist_ok=True)
    BUILDS.write_text(json.dumps(builds, indent=1, default=str), encoding="utf-8")


# ---------- index ----------

def load_index(today, since):
    years = range(min(since.year, today.year), today.year + 1)
    reports = []
    for year in years:
        z = zipfile.ZipFile(io.BytesIO(fetch(INDEX_URL.format(year=year))))
        root = ET.fromstring(z.read(f"{year}FD.xml"))
        for f in root:
            if f.findtext("FilingType") != "P":
                continue
            name = " ".join(x for x in (f.findtext("Prefix"), f.findtext("First"),
                                        f.findtext("Last"), f.findtext("Suffix")) if x)
            reports.append({
                "doc_id": f.findtext("DocID"),
                "year": f.findtext("Year") or str(year),
                "filed": us_date(f.findtext("FilingDate")),
                "name": name,
                "state_district": f.findtext("StateDst") or "",
            })
    return reports


# ---------- legislators (bioguide id and party) ----------

def load_legislators():
    DATA.mkdir(parents=True, exist_ok=True)
    fresh = LEGISLATORS.exists() and (
        time.time() - LEGISLATORS.stat().st_mtime < 7 * 86400)
    if not fresh:
        try:
            LEGISLATORS.write_bytes(fetch(LEGISLATORS_URL))
        except Exception as e:  # stale copy is better than none
            print(f"warning: legislators file not refreshed ({e})", file=sys.stderr)
    if not LEGISLATORS.exists():
        return {}
    by_seat = {}
    for p in json.loads(LEGISLATORS.read_text(encoding="utf-8")):
        t = p["terms"][-1]
        if t["type"] != "rep":
            continue
        seat = f"{t['state']}{int(t.get('district', 0)):02d}"
        by_seat[seat] = {
            "bioguide_id": p["id"]["bioguide"],
            "name": p["name"].get("official_full")
                    or f"{p['name']['first']} {p['name']['last']}",
            "party": {"Democrat": "D", "Republican": "R"}.get(t["party"], t["party"][:1]),
        }
    return by_seat


# ---------- PDF parsing ----------

def parse_amount(raw):
    raw = " ".join(raw.split())
    m = re.search(r"\$([\d,]+)\s*-\s*\$([\d,]+)", raw)
    if m:
        return raw, int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
    m = re.search(r"Over \$([\d,]+)", raw)
    if m:
        return raw, int(m.group(1).replace(",", "")) + 1, None
    return raw, None, None


def pdf_text(data):
    # Small-caps labels ("Filing Status", "Subholding Of") extract with null bytes
    text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    return text.replace("\x00", " ")


def parse_ptr(text):
    """Return (header, trades). trades is empty if the PDF has no text layer."""
    header = {}
    for key, pat in (("name", r"Name:\s*(.+)"), ("status", r"Status:\s*(.+)"),
                     ("state_district", r"State/District:\s*(\S+)")):
        m = re.search(pat, text)
        header[key] = m.group(1).strip() if m else ""

    lines = [ln.strip() for ln in text.splitlines()]
    try:
        start = lines.index("$200?") + 1
    except ValueError:
        return header, []
    body = []
    for ln in lines[start:]:
        if ln.startswith("* For the complete list"):
            break
        if not ln or ln in HEADER_LINES or re.fullmatch(r"Filing ID #\d+", ln):
            continue
        body.append(ln)

    trades, pending, cur, amount_open = [], [], None, False
    for ln in body:
        m = STATUS.match(ln)
        if m and cur:
            cur["filing_status"] = m.group(1).strip()
            amount_open = False
            continue
        m = SUBHOLDING.match(ln)
        if m and cur:
            continue
        m = DESCRIPTION.match(ln)
        if m and cur:
            cur["description"] = m.group(1).strip()
            continue
        m = CORE.match(ln)
        if m:
            cur = {
                "asset_raw": " ".join(pending + [m.group("pre")]).strip(),
                "transaction": TX_TYPES[m.group("type")],
                "transaction_date": us_date(m.group("tdate")),
                "notification_date": us_date(m.group("ndate")),
                "amount_raw": m.group("amt").strip(),
                "filing_status": "",
                "description": "",
            }
            trades.append(cur)
            pending = []
            amount_open = True
            continue
        if cur and amount_open:
            amt = cur["amount_raw"]
            unfinished = not amt or amt.endswith("-") or amt.endswith("Over")
            tail = re.match(r"^(.*?)\s*(\$[\d,]+)$", ln) if unfinished else None
            if tail:  # rest of the amount, possibly after the asset name's tail
                if tail.group(1):
                    cur["asset_raw"] += " " + tail.group(1)
                cur["amount_raw"] += " " + tail.group(2)
            else:  # asset name split across a page break
                cur["asset_raw"] += " " + ln
            continue
        pending.append(ln)

    for t in trades:
        asset = re.sub(r"^\d{6,}\s*", "", t.pop("asset_raw"))  # amended reports prefix a row ID
        owner = "SELF"
        head = asset.split(" ", 1)
        if head[0] in OWNERS and len(head) > 1:
            owner, asset = OWNERS[head[0]], head[1]
        tick = re.findall(r"\(([A-Z][A-Z0-9.\-/]*)\)", asset)
        kind = re.findall(r"\[([A-Z]{2})\]", asset)
        t["owner"] = owner
        t["asset"] = re.sub(r"\s*\[[A-Z]{2}\]\s*$", "", asset).strip()
        t["ticker"] = tick[-1] if tick else ""
        t["asset_type"] = kind[-1] if kind else ""
        t["amount"], t["amount_min"], t["amount_max"] = parse_amount(t.pop("amount_raw"))
    return header, trades


# ---------- digest ----------

def fmt_amount(t):
    lo, hi = t["amount_min"], t["amount_max"]
    if lo is None:
        return t["amount"] or "?"
    short = lambda n: f"${n / 1e6:g}M" if n >= 1e6 else f"${n / 1e3:g}K"
    return f"{short(lo - 1 if lo % 1000 == 1 else lo)}–{short(hi)}" if hi else f"over {short(lo - 1)}"


def digest(rows, scanned, failed, run_date, chamber="House", scope=""):
    out = [f"# The House Always Wins: {chamber} trades, {run_date:%Y-%m-%d}", ""]
    if scope:
        out += [scope, ""]
    filers = {r["doc_id"] for r in rows} | {s["doc_id"] for s in scanned}
    out.append(f"{len(filers)} new {chamber} reports, {len(rows)} trades read, "
               f"{len(scanned)} paper filings to check by hand.")
    out.append("")
    if not rows and not scanned:
        out.append("Nothing new since the last run.")
    big = sorted((r for r in rows if (r["amount_min"] or 0) >= 250001),
                 key=lambda r: -(r["amount_min"] or 0))
    if big:
        out += ["## Largest trades", "",
                "| Politician | Ticker | Trade | Amount | Traded | Filed |",
                "|---|---|---|---|---|---|"]
        for r in big[:15]:
            out.append(f"| {r['politician']} ({r['party']}-{r['state_district']}) | "
                       f"{r['ticker'] or r['asset'][:30]} | {r['transaction']} | "
                       f"{fmt_amount(r)} | {r['transaction_date']} | {r['filed_date']} |")
        out.append("")
    if rows:
        out.append("## By politician")
        by = {}
        for r in rows:
            by.setdefault((r["politician"], r["party"], r["state_district"], r["doc_id"],
                           r["filed_date"], r["pdf_url"]), []).append(r)
        for (name, party, seat, doc, filed, url), trs in sorted(
                by.items(), key=lambda kv: (kv[0][4], kv[0][0]), reverse=True):
            buys = sum(t["transaction"] == "BUY" for t in trs)
            sells = sum(t["transaction"].startswith("SELL") for t in trs)
            late = sum(t["late"] == "yes" for t in trs)
            n = lambda k, word: f"{k} {word}" + ("" if k == 1 else "s")
            out += ["", f"### {name} ({party}-{seat})", "",
                    f"Filed {filed}, {n(len(trs), 'trade')} ({n(buys, 'buy')}, {n(sells, 'sell')})"
                    + (f", {late} filed more than {LATE_AFTER_DAYS} days after the trade" if late else "")
                    + f". [Report]({url})", "",
                    "| Ticker | Asset | Trade | Amount | Traded | Owner |",
                    "|---|---|---|---|---|---|"]
            for t in trs:
                asset = t["asset"] if len(t["asset"]) <= 45 else t["asset"][:42] + "..."
                out.append(f"| {t['ticker'] or '-'} | {asset} | {t['transaction']} | "
                           f"{fmt_amount(t)} | {t['transaction_date']} | {t['owner'].title()} |")
    if scanned:
        out += ["", "## Paper filings to check by hand", "",
                "These were filed on paper and scanned, so there is no text to read.", ""]
        for s in scanned:
            out.append(f"- {s['politician']} ({s['state_district']}), filed {s['filed_date']}: "
                       f"[{s['doc_id']}]({s['pdf_url']})")
    if failed:
        out += ["", "## Could not download", ""]
        out += [f"- {f['name']} ({f['state_district']}), {f['doc_id']}: {f['error']}" for f in failed]
    return "\n".join(out) + "\n"


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="re-read reports filed on or after YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--parse-file", help="parse one local PDF and print the trades")
    args = ap.parse_args()

    if args.parse_file:
        header, trades = parse_ptr(pdf_text(Path(args.parse_file).read_bytes()))
        print(json.dumps({"header": header, "trades": trades}, indent=1, default=str))
        return

    DATA.mkdir(parents=True, exist_ok=True)
    DIGESTS.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()
    p = plan("house", today)
    if args.since:
        p = {**p, "mode": "reread", "since": dt.date.fromisoformat(args.since)}
    print(scope_line(p, "House"), file=sys.stderr)
    reports = load_index(today, p["since"])
    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    # Downloads that failed in an earlier build are retried even if older than the window
    retry = {d for b in load_builds() for d in b.get("house", {}).get("failed", [])} - seen

    if args.since:
        todo = [r for r in reports if r["filed"] >= p["since"]]
    else:
        todo = [r for r in reports if r["doc_id"] not in seen
                and (r["filed"] >= p["since"] or r["doc_id"] in retry)]
    todo.sort(key=lambda r: (r["filed"], r["doc_id"]))

    seats = load_legislators()
    rows, scanned, failed, done = [], [], [], []
    for i, rep in enumerate(todo):
        url = PDF_URL.format(year=rep["year"], doc=rep["doc_id"])
        if i:
            time.sleep(0.5)
        try:
            text = pdf_text(fetch(url))
        except Exception as e:
            failed.append({**rep, "error": str(e)})
            continue
        done.append(rep["doc_id"])
        who = seats.get(rep["state_district"], {})
        politician = who.get("name") or rep["name"]
        header, trades = parse_ptr(text)
        if not trades:
            scanned.append({"doc_id": rep["doc_id"], "politician": politician,
                            "state_district": rep["state_district"],
                            "filed_date": rep["filed"], "pdf_url": url})
            continue
        for t in trades:
            days = (rep["filed"] - t["transaction_date"]).days
            rows.append({
                "doc_id": rep["doc_id"], "filed_date": rep["filed"],
                "politician": politician, "bioguide_id": who.get("bioguide_id", ""),
                "party": who.get("party", "?"), "state_district": rep["state_district"],
                **t, "days_to_file": days, "late": "yes" if days > LATE_AFTER_DAYS else "",
                "pdf_url": url,
            })

    text = digest(rows, scanned, failed, today, scope=scope_line(p, "House"))
    print(text)
    if args.dry_run:
        return

    # Re-read reports replace their old rows, so a parser fix plus --since corrects the CSV
    reread = {r["doc_id"] for r in rows}
    kept = []
    if TRADES_CSV.exists():
        with TRADES_CSV.open(encoding="utf-8", newline="") as fh:
            kept = [r for r in csv.DictReader(fh) if r["doc_id"] not in reread]
    with TRADES_CSV.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(kept)
        w.writerows({k: r.get(k, "") for k in FIELDS} for r in rows)
    SEEN.write_text(json.dumps(sorted(seen | set(done))))
    (DIGESTS / f"{today:%Y-%m-%d}-house.md").write_text(text, encoding="utf-8")
    record_build("house", {
        "mode": p["mode"], "previous": p["previous"], "since": p["since"],
        "reports": len(done), "trades": len(rows), "paper": len(scanned),
        "failed": [f["doc_id"] for f in failed], "doc_ids": sorted(done),
    }, today)


if __name__ == "__main__":
    main()
