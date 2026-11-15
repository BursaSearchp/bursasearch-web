"""Search Console query data for bursasearch.com (15 Nov 2026).

Pulls query+page rows for the last N days with the play-publisher service account
(Restricted user on sc-domain:bursasearch.com) and prints:
  1. striking distance: queries at position 8-20 with real impressions (cheapest to move up)
  2. zero-click: high impressions, 0 clicks (snippet doesn't earn the click)
  3. informational: how/what/can/do queries, where the uni's own page is weakest
Writes the raw rows to gsc_queries.csv next to this file.

  python tools/gsc_queries.py [--days 28] [--min-imp 20]
"""
import argparse, csv, datetime as dt, json, os, re, sys
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

KEY = os.path.expanduser("~/.bursasearch/fresher-forge-v2-cmw4ev-121eaccbf0e3.json")
SITE = "sc-domain:bursasearch.com"

def fetch(days, dims):
    creds = service_account.Credentials.from_service_account_file(
        KEY, scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
    s = AuthorizedSession(creds)
    end = dt.date.today() - dt.timedelta(days=2)
    start = end - dt.timedelta(days=days - 1)
    rows, start_row = [], 0
    while True:
        r = s.post(f"https://searchconsole.googleapis.com/webmasters/v3/sites/{SITE.replace(':', '%3A')}/searchAnalytics/query",
                   json={"startDate": str(start), "endDate": str(end), "dimensions": dims,
                         "rowLimit": 25000, "startRow": start_row})
        r.raise_for_status()
        got = r.json().get("rows", [])
        rows += got
        if len(got) < 25000:
            return rows, start, end
        start_row += 25000

INFO = re.compile(r"^(how|what|when|who|can|do|does|is|are|will|which|why)\b|\b(apply|eligib|deadline|how much|pay back|criteria|application)\b")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--min-imp", type=int, default=20)
    a = ap.parse_args()
    rows, start, end = fetch(a.days, ["query", "page"])
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gsc_queries.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["query", "page", "clicks", "impressions", "ctr", "position"])
        for r in rows:
            q, p = r["keys"]; w.writerow([q, p, r["clicks"], r["impressions"], round(r["ctr"], 4), round(r["position"], 1)])
    tot_c = sum(r["clicks"] for r in rows); tot_i = sum(r["impressions"] for r in rows)
    print(f"{start} to {end}: {tot_c:,} clicks / {tot_i:,} impressions ({tot_c / max(tot_i, 1):.2%}) over {len(rows):,} query-page rows -> {out}\n")
    big = [r for r in rows if r["impressions"] >= a.min_imp]
    def show(title, rs, n=25):
        print(f"== {title} ({len(rs)})")
        for r in rs[:n]:
            q, p = r["keys"]
            print(f"  {r['impressions']:>5} imp {r['clicks']:>3} clk {r['ctr']:>6.1%}  pos {r['position']:>4.1f}  {q[:48]:<48} {p.replace('https://bursasearch.com', '')}")
        print()
    show("Striking distance: position 8-20", sorted([r for r in big if 8 <= r["position"] <= 20], key=lambda r: -r["impressions"]))
    show("Seen a lot, never clicked", sorted([r for r in big if r["clicks"] == 0], key=lambda r: -r["impressions"]))
    show("Informational (we can beat the uni here)", sorted([r for r in big if INFO.search(r["keys"][0])], key=lambda r: -r["impressions"]))
    kinds = {}
    for r in rows:
        p = r["keys"][1].replace("https://bursasearch.com", "")
        k = ("home" if p in ("/", "") else "hardship" if "hardship" in p else "subject" if "/subject/" in p
             else "fund" if p.count("/") >= 4 else "uni" if p.count("/") == 3 else "other")
        c, i = kinds.get(k, (0, 0)); kinds[k] = (c + r["clicks"], i + r["impressions"])
    print("== By page type")
    for k, (c, i) in sorted(kinds.items(), key=lambda x: -x[1][1]):
        print(f"  {k:<9} {i:>7,} imp {c:>5} clk {c / max(i, 1):.2%}")

if __name__ == "__main__":
    main()
