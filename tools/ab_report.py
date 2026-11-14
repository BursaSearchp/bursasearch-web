"""Results of the 5 website page versions (generate.py AB_VARIANTS).

    python tools/ab_report.py [--since 2026-11-14] [--asc path/to/reports/latest.json] [--pg seo_uni]

Each visitor sees one version (v1..v5) for good. Taps: web events in Firestore preAuthEvents (platform=web), read
with the Google service-account key in ~/.bursasearch (never committed). Per version: visitors, the share who
started and finished the quick check, the share who tapped through to the app store, and a p-value for that tap
rate against the other four combined. Downloads: pass the weekly check's reports/latest.json; store campaigns
like seo_uni-v3 add up per version.
Rule of thumb: don't call a winner below ~150 visitors per version or while p > 0.05.
"""
import argparse
import ast
import glob
import json
import math
import os
import re
import sys
from collections import defaultdict

# generate.py builds the whole site on import, so read AB_VARIANTS out of its source instead
_SRC = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "generate.py"),
            encoding="utf-8").read()
AB_VARIANTS = next(ast.literal_eval(n.value) for n in ast.parse(_SRC).body
                   if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "AB_VARIANTS")
PROJECT = "fresher-forge-v2-cmw4ev"


def _creds():
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    keys = glob.glob(os.path.expanduser("~/.bursasearch/fresher-forge-v2-*.json"))
    if not keys:
        sys.exit("No service-account key in ~/.bursasearch")
    cr = service_account.Credentials.from_service_account_file(
        keys[0], scopes=["https://www.googleapis.com/auth/datastore"])
    cr.refresh(Request())
    return cr


def web_events(since):
    import requests
    cr = _creds()
    url = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents:runQuery"
    body = {"structuredQuery": {"from": [{"collectionId": "preAuthEvents"}], "where": {"fieldFilter": {
        "field": {"fieldPath": "platform"}, "op": "EQUAL", "value": {"stringValue": "web"}}}}}
    r = requests.post(url, headers={"Authorization": f"Bearer {cr.token}"}, json=body, timeout=300).json()
    out = []
    for x in r:
        f = x.get("document", {}).get("fields", {})
        if f:
            g = {k: next(iter(v.values())) for k, v in f.items()}
            if g.get("ts", "") >= since:
                out.append(g)
    return out


def p_two(a_n, a_k, b_n, b_k):
    if min(a_n, b_n) == 0:
        return float("nan")
    p = (a_k + b_k) / (a_n + b_n)
    se = math.sqrt(p * (1 - p) * (1 / a_n + 1 / b_n)) or 1e-9
    return math.erfc(abs(a_k / a_n - b_k / b_n) / se / math.sqrt(2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-11-14")
    ap.add_argument("--asc", help="weekly check reports/latest.json, for downloads per version")
    ap.add_argument("--pg", help="only this page type, e.g. seo_uni")
    a = ap.parse_args()

    vis = {}
    for e in web_events(a.since):
        if a.pg and e.get("pg") != a.pg:
            continue
        v = e.get("ab", "")
        if not re.fullmatch(r"v[1-9]", v or ""):
            continue                                   # the morning's 15-test arms, before the 5 versions
        d = vis.setdefault(e["device"], {"v": v, "view": 0, "start": 0, "done": 0, "tap": 0})
        name = e.get("event")
        d["view"] += name == "web_view"
        d["start"] |= name == "web_check_start"
        d["done"] |= name == "web_check_done"
        d["tap"] |= name == "web_tap"

    dl = defaultdict(int)
    if a.asc:
        camp = json.load(open(a.asc, encoding="utf-8")).get("asc", {}).get("now", {}).get("downloads", {}).get(
            "by_campaign", {})
        for c, k in camp.items():
            m = re.fullmatch(r"seo_\w+-(v[1-9])", c)
            if m:
                dl[m.group(1)] += int(k)

    n = len(vis)
    print(f"Since {a.since}: {n} visitors.\n")
    print(f"{'version':16} {'visitors':>8} {'started':>8} {'finished':>9} {'tapped':>8} {'p':>6}"
          + ("  downloads" if a.asc else ""))
    for i, (key, what) in enumerate(AB_VARIANTS, 1):
        tag = f"v{i}"
        g = [d for d in vis.values() if d["v"] == tag]
        rest = [d for d in vis.values() if d["v"] != tag]
        k = len(g)
        st, dn, tp = (sum(d[x] for d in g) for x in ("start", "done", "tap"))
        p = p_two(k, tp, len(rest), sum(d["tap"] for d in rest))
        row = (f"{tag + ' ' + key:16} {k:>8} {st / max(k, 1):>8.1%} {dn / max(k, 1):>9.1%} {tp / max(k, 1):>8.1%} "
               f"{p:>6.2f}")
        if a.asc:
            row += f"  {dl[tag]:>9}"
        print(row + "   " + what)


if __name__ == "__main__":
    main()
