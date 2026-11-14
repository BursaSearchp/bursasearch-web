"""Results of the 15 website A/B tests (generate.py AB_TESTS).

    python tools/ab_report.py [--since 2026-11-14] [--asc path/to/reports/latest.json]

Taps: web events in Firestore preAuthEvents (platform=web), read with the Google service-account key in
~/.bursasearch (never committed). One row per test: visitors in arm a (old) vs b (new), the share who finished
the check and the share who tapped through to the app store, and a two-sided p-value for the tap difference.
Downloads: pass the weekly check's reports/latest.json; store campaigns like seo_uni-7fa3 are decoded back to
each visitor's 15 arms, so downloads per arm add up across all combinations.
Rule of thumb: don't call a winner below ~150 visitors per arm or while p > 0.05.
"""
import argparse
import glob
import json
import math
import os
import re
import sys
from collections import defaultdict

import ast  # noqa: E402

# generate.py builds the whole site on import, so read AB_TESTS out of its source instead
_SRC = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "generate.py"), encoding="utf-8").read()
AB_TESTS = next(ast.literal_eval(n.value) for n in ast.parse(_SRC).body
                if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "AB_TESTS")

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
    body = {"structuredQuery": {"from": [{"collectionId": "preAuthEvents"}], "where": {"compositeFilter": {
        "op": "AND", "filters": [
            {"fieldFilter": {"field": {"fieldPath": "platform"}, "op": "EQUAL", "value": {"stringValue": "web"}}},
            {"fieldFilter": {"field": {"fieldPath": "ts"}, "op": "GREATER_THAN_OR_EQUAL",
                             "value": {"timestampValue": since + "T00:00:00Z"}}}]}}}}
    url = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents:runQuery"
    r = requests.post(url, headers={"Authorization": f"Bearer {cr.token}"}, json=body, timeout=300).json()
    if isinstance(r, dict) and "error" in r:
        # the platform+ts filter needs a composite index; fall back to platform only and filter here
        body["structuredQuery"]["where"] = body["structuredQuery"]["where"]["compositeFilter"]["filters"][0]
        r = requests.post(url, headers={"Authorization": f"Bearer {cr.token}"}, json=body, timeout=300).json()
    out = []
    for x in r:
        f = x.get("document", {}).get("fields", {})
        if not f:
            continue
        g = {k: next(iter(v.values())) for k, v in f.items()}
        if g.get("ts", "") >= since:
            out.append(g)
    return out


def p_two(a_n, a_k, b_n, b_k):
    if min(a_n, b_n) == 0:
        return float("nan")
    p = (a_k + b_k) / (a_n + b_n)
    se = math.sqrt(p * (1 - p) * (1 / a_n + 1 / b_n)) or 1e-9
    z = abs(a_k / a_n - b_k / b_n) / se
    return math.erfc(z / math.sqrt(2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-11-14")
    ap.add_argument("--asc", help="weekly check reports/latest.json, for downloads per arm")
    ap.add_argument("--pg", help="only this page type, e.g. seo_uni")
    a = ap.parse_args()

    ev = web_events(a.since)
    vis = {}
    for e in ev:
        if a.pg and e.get("pg") != a.pg:
            continue
        ab = e.get("ab", "")
        if not re.fullmatch(r"[01]{%d}" % len(AB_TESTS), ab or ""):
            continue
        v = vis.setdefault(e["device"], {"ab": ab, "view": 0, "start": 0, "done": 0, "tap": 0, "google": 0})
        name = e.get("event")
        v["view"] += name == "web_view"
        v["start"] |= name == "web_check_start"
        v["done"] |= name == "web_check_done"
        v["tap"] |= name == "web_tap"
        v["google"] |= e.get("ref") == "google"
    n = len(vis)
    taps = sum(v["tap"] for v in vis.values())
    print(f"Since {a.since}: {n} visitors, {sum(v['view'] for v in vis.values())} page views, "
          f"{sum(v['done'] for v in vis.values())} finished the check, {taps} tapped to the app store "
          f"({taps / max(n, 1):.1%}).\n")

    dl = defaultdict(lambda: [0, 0])
    if a.asc:
        camp = json.load(open(a.asc, encoding="utf-8")).get("asc", {}).get("now", {}).get("downloads", {}).get(
            "by_campaign", {})
        for c, k in camp.items():
            m = re.fullmatch(r"seo_\w+-([0-9a-f]{4})", c)
            if m:
                bits = format(int(m.group(1), 16), "0%db" % len(AB_TESTS))
                for i, b in enumerate(bits):
                    dl[i][int(b)] += int(k)

    print(f"{'test':10} {'visitors a/b':>13} {'check done a/b':>15} {'tapped a/b':>15} {'p':>6}"
          + ("  downloads a/b" if a.asc else "") + "  what b is")
    for i, (key, what) in enumerate(AB_TESTS):
        g = [[v for v in vis.values() if v["ab"][i] == arm] for arm in "01"]
        na, nb = len(g[0]), len(g[1])
        da, db = sum(v["done"] for v in g[0]), sum(v["done"] for v in g[1])
        ta, tb = sum(v["tap"] for v in g[0]), sum(v["tap"] for v in g[1])
        p = p_two(na, ta, nb, tb)
        row = (f"{key:10} {na:>6}/{nb:<6} {da / max(na, 1):>6.1%}/{db / max(nb, 1):<7.1%} "
               f"{ta / max(na, 1):>6.1%}/{tb / max(nb, 1):<7.1%} {p:>6.2f}")
        if a.asc:
            row += f"  {dl[i][0]:>4}/{dl[i][1]:<4}"
        print(row + "  " + what)


if __name__ == "__main__":
    main()
