"""Coverage check: does every page type on the built site have the quick check, the app look and the A/B tracking?

    python tools/page_coverage.py        (run from the repo root after generate.py has built the site)

Columns: pages, pages with a quiz box (#chk / #fchk / #mchk), pages in the app look (#091723), pages carrying the
current A/B key (bs_v2). Redirect stubs for pages that dropped out of the data are counted on their own line.
Every live type should show quiz = app look = tracked = pages.
"""
import collections
import glob
import re

LISTS = ("grants", "biggest-bursaries", "household-income-under-25k", "cost-of-living", "closing-soon",
         "highest-value", "more-universities", "hardship-funds")
c, q, look, ab = (collections.Counter() for _ in range(4))
for f in glob.glob("bursaries/**/index.html", recursive=True):
    s = open(f, encoding="utf-8").read()
    parts = f.replace("\\", "/").split("/")[1:-1]
    if "Moved | BursaSearch" in s:
        t = "redirect stub"
    elif not parts:
        t = "hub"
    elif parts[0] in ("circumstance", "subject", "region"):
        t = parts[0]
    elif parts[0] in LISTS:
        t = "other list/answer"
    elif len(parts) == 1:
        t = "university"
    elif len(parts) == 2 and parts[1] == "hardship-funds":
        t = "uni hardship"
    elif len(parts) == 3 and parts[1] == "subject":
        t = "uni x subject"
    elif len(parts) == 2:
        t = "fund"
    else:
        t = "other"
    c[t] += 1
    q[t] += bool(re.search(r'id="(chk|fchk|mchk)"', s))
    look[t] += "#091723" in s
    ab[t] += "bs_v2" in s
print(f"{'type':18}{'pages':>6}{'quiz':>6}{'app look':>9}{'tracked':>8}")
for t, n in c.most_common():
    print(f"{t:18}{n:>6}{q[t]:>6}{look[t]:>9}{ab[t]:>8}")
print("total", sum(c.values()))
