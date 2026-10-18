# The House Always Wins

A tracker for the stock trades that members of the US Congress disclose under the
STOCK Act. It reads the official filings directly, the House Clerk's PDF reports
and the Senate's eFD site, and turns them into a CSV of every trade, a markdown
digest of each batch, and a self-contained HTML infographic.

Members of Congress must report a trade within 45 days. The reports are public but
awkward: the House publishes each one as a PDF, and the Senate puts its search
behind a terms page and a firewall that blocks scripts. This project does the
reading.

## What a run produces

| Output | What it is |
|---|---|
| `data/house_trades.csv`, `data/senate_trades.csv` | One row per trade: politician, party, seat, owner, asset, ticker, buy or sell, trade date, filing date, amount bracket, days to file, a late flag, and a link to the source report |
| `digests/YYYY-MM-DD-house.md`, `-senate.md` | Who filed, what they traded, the largest trades, and the paper filings to check by hand |
| `infographics/latest.html` | One page of charts: trades per politician, trade and filing timelines, value sold against bought, amount brackets, reporting lag against the 45-day limit, account owners, top tickers, overlaps, and a searchable table |
| `data/builds.json` | One entry per build: date, mode, window, counts and the report IDs read. The next build starts from it |
| `runs/YYYY-MM-DD/` | When run as a Claude Code skill: the run record, the trades this build added, three analysis files and a written summary |

## How it works

**House.** The Clerk publishes a yearly ZIP with an XML index of every filing,
rebuilt each weekday morning. `scripts/house.py` downloads it, picks out the
periodic transaction reports it hasn't seen, downloads each PDF and parses the
transaction table with `pypdf`. The parser handles the quirks of the Clerk's PDFs:
small-caps labels that extract with null bytes, row IDs on amended reports, and
asset names or amounts split across page breaks. Paper filings are scanned images
with no text, so they are listed for a manual check.

**Senate.** `efdsearch.senate.gov` returns 403 to scripted HTTP clients, so the
fetch runs inside a real browser tab. After you accept the site's terms,
`scripts/senate_fetch.js` pages through the search API and reads each electronic
report's table, returning JSON. `scripts/senate.py` turns that JSON into the same
CSV and digest format as the House.

**Builds.** Each run starts from the previous one. `scripts/previous_build.py` reads
`data/builds.json` and says, per chamber, whether this is an update (reports filed
since the last build, with a few days' overlap because the House index posts some
filings late) or a build from scratch (the last 90 days). The seen lists stop the
overlap from producing duplicate rows.

Party, BioGuide ID and state come from the public
[congress-legislators](https://github.com/unitedstates/congress-legislators) dataset,
cached for a week.

## Quickstart

Python 3.10 or newer.

```bash
git clone <this repo> the-house-always-wins
cd the-house-always-wins
pip install -r requirements.txt
```

House trades from the last 90 days (the first run), then everything filed since the previous build on each later run:

```bash
python scripts/house.py
```

Senate trades:

1. Open `https://efdsearch.senate.gov/search/home/` in a browser and tick the box accepting the terms (see below).
2. Run `python scripts/senate.py --print-fetch` to get `scripts/senate_fetch.js` with its start date filled in. Open the browser's developer console on that tab, paste the output and run it.
3. Save the JSON string it returns as `data/senate_inbox.json`.
4. Run `python scripts/senate.py`.

Build the infographic:

```bash
python scripts/build_infographic.py
```

On Windows, set `PYTHONIOENCODING=utf-8` first.

| Script | Useful flags |
|---|---|
| `previous_build.py` | No flags prints the plan for the next build; `--json`; `--new-trades DIR` writes the trades the latest build added to `DIR/new_trades.csv` |
| `house.py` | `--since YYYY-MM-DD` re-reads reports from that date, `--dry-run` saves nothing, `--parse-file X.pdf` debugs one report |
| `senate.py` | `--input FILE`, `--all` re-reads every report in the file, `--dry-run` |
| `build_infographic.py` | `--days N` window of filing dates (default 30), `--all` |

## Using it as a Claude Code skill

Copy or clone this folder into `.claude/skills/the-house-always-wins/` in your
project. `SKILL.md` runs it as a staged workflow:

| Stage | What happens |
|---|---|
| 0 · Previous build | `previous_build.py` sets the mode and window for each chamber |
| 1 · Collect | The House and Senate readers, with Claude driving a browser for the Senate fetch |
| 2 · Analyse | Three subagents at once: a checker that tests the new rows against the source PDFs, a patterns analyst, and a context analyst for committee seats and price moves |
| 3 · Write up | One subagent writes the run summary from the three files, without recounting |
| 4 · Build | The infographic |

The agent prompts are in [references/role-prompts.md](references/role-prompts.md).
Claude asks before accepting the Senate's terms, and the agents are told to describe
filings and never motive.

By default, output goes inside this folder. To send it somewhere else, put a
one-line `home.txt` next to `SKILL.md` containing a path, relative to this folder
or absolute. `home.txt`, `data/`, `digests/`, `runs/` and `infographics/` are all
in `.gitignore`.

## The Senate's terms

Searching the Senate's reports means agreeing that you understand 5 U.S.C. app.
§ 105(c), which makes it unlawful to obtain or use a report for any unlawful
purpose, for a commercial purpose (other than news media dissemination), to
establish anyone's credit rating, or to solicit money. Read the terms yourself
before you tick the box. This repository ships code only, no collected reports.

## Limits

- Paper filings are not read. They are listed with links for a manual check.
- Amounts are reported as brackets, such as $1,001 to $15,000, so every total is a range.
- A trade can be up to 45 days old when it is filed, and late filings are older still.
- Parsing is tested against the Clerk's current PDF layout and the Senate's current HTML. If either changes, the parser needs updating; `house.py --parse-file` and the trade-count check in `CLAUDE.md` catch most breakage.

## A note on reading the data

A disclosure shows that a trade happened and was reported. It says nothing about
why, and it is not evidence of wrongdoing; many of these accounts are run by
advisers. Nothing here is investment advice.

## Licence

Apache-2.0. See [LICENSE](LICENSE). Anyone who uses or builds on this work must keep the [NOTICE](NOTICE) file and credit Vidwaan Singhania.
