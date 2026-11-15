"""Score the 15 Nov title split test (style a vs b) from Search Console.

Each page's style is in title_test.json (written by generate.py). Compares the
two groups' CTR by page type, only from the date Google could have seen the new
titles. Position is shown too: if one group sits higher, its CTR edge is partly
position, not the title.

  python tools/title_test_report.py [--since 2026-11-16]
Don't call it before ~3-4 weeks; a gap needs both groups at 2,000+ impressions.
"""
import argparse, datetime as dt, json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gsc_queries import KEY, SITE  # noqa
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

ap = argparse.ArgumentParser(); ap.add_argument("--since", default="2026-11-16"); a = ap.parse_args()
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
arms = json.load(open(os.path.join(root, "title_test.json"), encoding="utf-8"))["pages"]
s = AuthorizedSession(service_account.Credentials.from_service_account_file(
    KEY, scopes=["https://www.googleapis.com/auth/webmasters.readonly"]))
end = dt.date.today() - dt.timedelta(days=2)
r = s.post(f"https://searchconsole.googleapis.com/webmasters/v3/sites/{SITE.replace(':', '%3A')}/searchAnalytics/query",
           json={"startDate": a.since, "endDate": str(end), "dimensions": ["page"], "rowLimit": 25000})
r.raise_for_status()
g = {}
for row in r.json().get("rows", []):
    path = row["keys"][0].replace("https://bursasearch.com", "")
    arm = arms.get(path)
    if not arm:
        continue
    kind = "uni" if path.count("/") == 3 else "fund"
    c = g.setdefault((kind, arm), [0, 0, 0.0, 0])
    c[0] += row["clicks"]; c[1] += row["impressions"]; c[2] += row["position"] * row["impressions"]; c[3] += 1
print(f"{a.since} to {end}")
for kind in ("uni", "fund"):
    print(f"\n{kind} pages   style  pages  impressions  clicks    CTR   avg pos")
    res = {}
    for arm in ("a", "b"):
        c, i, pw, n = g.get((kind, arm), [0, 0, 0, 0])
        res[arm] = (c, i)
        print(f"              {arm}    {n:>5}  {i:>11,}  {c:>6}  {c / max(i, 1):>5.2%}   {pw / max(i, 1):>5.1f}")
    (ca, ia), (cb, ib) = res["a"], res["b"]
    if ia and ib:
        p = (ca + cb) / (ia + ib); se = math.sqrt(max(p * (1 - p) * (1 / ia + 1 / ib), 1e-12))
        z = (cb / ib - ca / ia) / se
        print(f"   b vs a: z = {z:+.2f}  ({'real difference' if abs(z) >= 1.96 else 'not proven yet'})")
