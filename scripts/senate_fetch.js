// The House Always Wins: Senate fetch.
// Runs inside a real browser tab on efdsearch.senate.gov, after the terms box is
// ticked. The site's firewall rejects scripted HTTP clients, so this cannot run
// from Python. `senate.py --print-fetch` prints this file with __SINCE__ replaced
// by a start date as MM/DD/YYYY and the seen list filled in; run that output, not
// this file. Reports already read are left out of the result entirely.
// Returns a JSON string for senate.py.
const SINCE = '__SINCE__';
const SEEN = new Set(/*__SEEN__*/[]);
const tok = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1];
if (!tok) throw new Error('no csrftoken cookie: open efdsearch.senate.gov and tick the terms box first');
const sleep = ms => new Promise(r => setTimeout(r, ms));

const list = [];
for (let start = 0; ; start += 100) {  // the search caps each page at 100 results
  const body = new URLSearchParams({
    start: String(start), length: '100', report_types: '[11]', filer_types: '[]',
    submitted_start_date: SINCE + ' 00:00:00', submitted_end_date: '',
    candidate_state: '', senator_state: '', office_id: '', first_name: '', last_name: '',
    csrfmiddlewaretoken: tok,
  });
  const r = await fetch('/search/report/data/', {
    method: 'POST', body,
    headers: {'X-CSRFToken': tok, 'Content-Type': 'application/x-www-form-urlencoded'},
  });
  if (!r.ok) throw new Error('search failed with ' + r.status + ': is the terms box ticked?');
  const j = await r.json();
  list.push(...j.data);
  if (j.data.length < 100) break;
}

const reports = [];
for (const [first, last, filer, link, filed] of list) {
  const href = (link.match(/href="([^"]+)"/) || [])[1];
  const id = href.split('/').filter(Boolean).pop();
  if (SEEN.has(id)) continue;
  const kind = href.includes('/paper/') ? 'paper' : 'ptr';
  const rep = {
    id, kind, filed,
    first: first.trim(), last: last.trim(), filer,
    title: link.replace(/<[^>]+>/g, '').trim(),
    url: 'https://efdsearch.senate.gov' + href, trades: [],
  };
  if (kind === 'ptr') {
    await sleep(300);
    const page = await (await fetch(href)).text();
    const doc = new DOMParser().parseFromString(page, 'text/html');
    // columns: # | Transaction Date | Owner | Ticker | Asset Name | Asset Type | Type | Amount | Comment
    for (const tr of doc.querySelectorAll('table tbody tr')) {
      rep.trades.push([...tr.querySelectorAll('td')].map(td => td.textContent.trim().replace(/\s+/g, ' ')));
    }
  }
  reports.push(rep);
}
JSON.stringify({since: SINCE, fetched: new Date().toISOString(), reports});
