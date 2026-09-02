"""
Generates static, SEO-indexable university bursary pages from the bursary
dataset. Run from inside the bursasearch-web repo:

    python generate.py

Reads the dataset via load_rows() (live Apps Script endpoint if SEO_DATA_URL
is set, else the local _source_data.csv), writes bursaries/<slug>/index.html
per university (>=2 real bursaries), one rollup page for single-entry
universities, a hub index, sitemap.xml and robots.txt.
"""
import csv
import html
import json
import os
import re
import urllib.request
from collections import defaultdict
from datetime import date, datetime

SITE_URL = "https://bursasearch.com"
# Apple has no equivalent of Play's &referrer= campaign tracking from a plain
# URL — attributing App Store installs to this site needs a provider/campaign
# token from App Analytics in App Store Connect (one-time setup only the
# account owner can do), then the link becomes
# https://apps.apple.com/app/id6795890396?pt=<providerID>&ct=<campaignToken>&mt=8
APP_STORE_URL = "https://apps.apple.com/app/id6795890396"
# &referrer= is Google Play's documented custom-campaign format — installs
# that came through this link now show up in Play Console's Acquisition
# reports under source "bursasearch_web" / campaign "seo_site", instead of
# being invisible in the "organic" bucket like every other install.
PLAY_URL = (
    "https://play.google.com/store/apps/details?id=fresherforgev2.com"
    "&referrer=utm_source%3Dbursasearch_web%26utm_medium%3Dreferral%26utm_campaign%3Dseo_site"
)
OG_IMAGE = f"{SITE_URL}/og-image.png"
OUT_DIR = "bursaries"
TODAY = date.today().isoformat()
LASTMOD_FILE = ".lastmod.json"
# IndexNow (Bing/Yandex/Seznam) instant-crawl key — public by design, must
# match the contents of <key>.txt at the site root. Only submitted when
# running from live data (SEO_DATA_URL set), not on local dev runs.
INDEXNOW_KEY = "1649b53c675bec5c36669aaed0ae9f4e"
# Apps Script ?action=seo endpoint (same loadDataset_()/patchDataset_()
# pipeline as the live matcher — see Filtration/appscriptfilter/AppScriptCode.txt
# in the main bursa_project repo). When unset, falls back to a local
# _source_data.csv for manual/offline runs.
SEO_DATA_URL = os.environ.get("SEO_DATA_URL")

# ── Data load ────────────────────────────────────────────────────────────────
def load_rows():
    if SEO_DATA_URL:
        with urllib.request.urlopen(SEO_DATA_URL, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, list) or not data:
            raise RuntimeError(f"SEO_DATA_URL returned no rows: {str(data)[:200]}")
        return data
    with open("_source_data.csv", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

rows = load_rows()

def clean(v):
    # Apps Script cell values come through JSON as whatever type the sheet
    # cell was (numbers, booleans, ISO date strings), not always a str like
    # csv.DictReader always gives us — normalise before calling .strip().
    if v is None:
        return ""
    v = str(v).strip()
    return "" if v.lower() in ("any", "n/a", "-", "") else v

def norm_uni_key(u):
    """Collapses name variants ('The X', 'X, The', 'X/Welsh Name', 'X ' vs
    'X') down to one grouping key, so the same real university never gets
    split across multiple near-duplicate pages."""
    u2 = re.sub(r"^the\s+", "", u.strip(), flags=re.I)
    u2 = re.sub(r",?\s*the\s*$", "", u2, flags=re.I)
    u2 = u2.split("/")[0].strip()
    u2 = re.sub(r"\s*\(.*?\)\s*$", "", u2)
    return re.sub(r"\s+", " ", u2).lower().strip()

# Group raw rows by normalised key, but keep a real display name per group
# (shortest variant, with no leading "The" / trailing ", The" / slash suffix,
# properly-cased minor words — reads cleanest as a page title).
MINOR_WORDS = {"of", "and", "the", "in", "for", "at", "on", "de"}

def cap_penalty(n):
    """Counts wrongly title-cased minor words ('University Of X' vs the
    correct 'University of X'), so we can prefer the properly-cased variant
    when two names are otherwise tied."""
    words = n.split()
    return sum(1 for w in words[1:] if w.lower() in MINOR_WORDS and w[:1].isupper())

_raw_by_key = defaultdict(list)
_names_by_key = defaultdict(set)
for row in rows:
    uni = clean(row.get("University", ""))
    if not uni:
        continue
    key = norm_uni_key(uni)
    _raw_by_key[key].append(row)
    _names_by_key[key].add(uni)

by_uni = {}
for key in sorted(_raw_by_key):
    entries = _raw_by_key[key]
    names = _names_by_key[key]
    # Sort key ends on the string itself so ties resolve identically on
    # every run — `names` is a set, and set iteration order is hash-seed
    # randomised per process, so without this the canonical display name
    # (and therefore the page's title/H1/canonical URL text) could silently
    # flip between regenerations.
    canonical = min(
        names,
        key=lambda n: (n.lower().startswith("the "), "/" in n, len(n), cap_penalty(n), n),
    )
    by_uni[canonical] = entries

multi = {u: r for u, r in by_uni.items() if len(r) >= 2}
singles = {u: r for u, r in by_uni.items() if len(r) == 1}

# ── Helpers ──────────────────────────────────────────────────────────────────
def slugify(s):
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")

def esc(s):
    return html.escape(s, quote=True)

# ── Per-grant page lookups (populated in the build section, phase 1, before
#    any listing page is rendered so its rows can link straight to the fund's
#    own page). ─────────────────────────────────────────────────────────────
FUND_SLUGS_FILE = "fund_slugs.json"
FUND_URLS = {}           # fund_key -> "/bursaries/<uni-slug>/<fund-slug>/"
CANON_BY_KEY = {}        # norm_uni_key -> (canonical display name, uni slug)
UNI_SUBJECT_PAGES = {}   # uni slug -> [(subject slug, subject h1, count), ...]
SUBJECT_UNI_PAGES = {}   # subject slug -> [(uni name, uni slug, count), ...]

def load_fund_slugs():
    try:
        with open(FUND_SLUGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def fund_key(uni_name, fund_name):
    return norm_uni_key(uni_name) + "|" + re.sub(r"\s+", " ", clean(fund_name).lower())

def fund_href_for(row, uni_hint=None):
    """The row's own grant-page path, or None. uni_hint = canonical uni name
    when the caller already knows it (a university page); otherwise the row's
    raw University is resolved through CANON_BY_KEY (the cross-cutting pages)."""
    name = clean(row.get("Bursary Name", ""))
    if not name:
        return None
    if uni_hint:
        return FUND_URLS.get(fund_key(uni_hint, name))
    raw = clean(row.get("University", ""))
    resolved = CANON_BY_KEY.get(norm_uni_key(raw)) if raw else None
    return FUND_URLS.get(fund_key(resolved[0], name)) if resolved else None

def load_lastmod():
    try:
        with open(LASTMOD_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def write_page(url, path, content, lastmod_map, changed_urls):
    """Writes a page, records its sitemap lastmod — bumped to TODAY only when
    the content actually changed from the last run, not on every
    regeneration (a sitemap where every URL's lastmod reads 'today'
    regardless of real changes is a known anti-pattern Google's own guidance
    warns can get the whole sitemap's lastmod signal discounted) — and
    appends to changed_urls when it did, for the IndexNow submission below."""
    existing = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    if existing != content or url not in lastmod_map:
        lastmod_map[url] = TODAY
        changed_urls.append(url)
    return url

def format_amount(v):
    """The CSV always had Amount pre-formatted as text ('£3,000'). The live
    Apps Script feed returns whatever cell type the sheet actually has, so a
    plain numeric cell (3000) arrives as a bare number with no currency
    symbol — add one back rather than showing a naked '3000' on the page."""
    v = clean(v)
    if not v or not re.fullmatch(r"[\d,]+(\.\d+)?", v):
        return v
    n = float(v.replace(",", ""))
    return f"£{n:,.0f}" if n == int(n) else f"£{n:,.2f}"

def format_deadline(v):
    """Same issue as Amount: a real Sheet date cell serialises to an ISO
    timestamp ('2026-08-27T23:00:00.000Z'), not the human text the CSV had."""
    v = clean(v)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T", v)
    if not m:
        return v
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return v
    return f"{d.day} {d.strftime('%B %Y')}"

def parse_deadline_date(v):
    """Best-effort parse of a Deadline cell into a comparable date, across
    both the live ISO-timestamp format and the CSV's human-text format —
    used to build the 'closing soon' page. Returns None for 'Any' or
    anything unparseable (open-ended bursaries don't belong on that page)."""
    v = clean(v)
    if not v:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T", v)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None

def max_amount_value(v):
    """Best-effort single numeric value for ranking by amount — uses the
    highest number found (e.g. '£500 - £3,000' -> 3000), for the
    'highest-value' page. Returns 0 (sorts last) when nothing numeric."""
    nums = [int(n.replace(",", "")) for n in re.findall(r"£?\s?([\d,]{2,7})", clean(v))]
    return max(nums) if nums else 0

ELIGIBILITY_FIELDS = [
    ("Fee status", "Fee status"),
    ("Study level", "Study level"),
    ("UK region", "Region"),
    ("Required nationality", "Nationality"),
    ("Home country", "Home country"),
    ("Household income", "Household income"),
    ("Armed forces background?", "Armed forces background"),
    ("First generation to go to university?", "First-gen student"),
    ("Vulnerabilities (multi-select)", "Circumstances"),
    ("Minimum grade", "Minimum grade"),
    ("Course Year", "Course year"),
]

def eligibility_badges(row):
    badges = []
    for col, label in ELIGIBILITY_FIELDS:
        v = clean(row.get(col, ""))
        if v:
            badges.append(f"{label}: {v}")
    return badges[:4]  # keep each card scannable, not a wall of chips

_ROLLING_HINTS = ("rolling", "ongoing", "no deadline", "any time", "anytime",
                  "year-round", "year round", "open all year", "continuous")

def deadline_line(row):
    """A single 'Deadline …' / 'Rolling …' line for a bursary row, honest about
    what the data actually says: a parseable future date (flagged 'closing
    soon' inside 60 days), an open-ended cell, or — when there's no deadline
    data at all — nothing (never a fabricated 'automatic')."""
    raw = clean(row.get("Deadline", ""))
    if not raw:
        return ""
    d = parse_deadline_date(raw)
    if d:
        if d < date.today():
            return ""  # a one-off deadline that's already passed — stale, hide it
        soon = (d - date.today()).days <= 60
        txt = f"Deadline {format_deadline(raw)}" + (" · closing soon" if soon else "")
        return f'<span class="dl{" soon" if soon else ""}">{esc(txt)}</span>'
    if any(h in raw.lower() for h in _ROLLING_HINTS):
        return '<span class="dl">Rolling — apply any time</span>'
    return f'<span class="dl">Deadline: {esc(raw)}</span>'

def bursary_row(row, uni=None, uni_href=None, fund_href=None):
    """One fund as a row in a bordered list. `uni`/`uni_href` show + link the
    provider (used on the cross-cutting pages). `fund_href` links the name to
    the fund's own page — wired now, populated once per-grant pages exist."""
    name = clean(row.get("Bursary Name", "")) or "Bursary"
    amount = format_amount(row.get("Amount", ""))
    link = clean(row.get("Application URL", "")) or clean(row.get("Link", ""))
    subject = clean(row.get("Study subject", ""))
    chips = eligibility_badges(row)
    if subject:
        chips = [f"Subject: {subject}"] + chips
    chips = chips[:3]

    if fund_href:
        name_html = f'<a class="nm" href="{esc(fund_href)}">{esc(name)}</a>'
    else:
        name_html = f'<span class="nm">{esc(name)}</span>'
    amt_html = f'<span class="amt">{esc(amount)}</span>' if amount else ""
    if uni and uni_href:
        prov = f'<div class="prov"><a href="{esc(uni_href)}">{esc(uni)}</a></div>'
    elif uni:
        prov = f'<div class="prov">{esc(uni)}</div>'
    else:
        prov = ""
    chip_html = "".join(f'<span class="chip">{esc(c)}</span>' for c in chips)
    chips_block = f'<div class="chips">{chip_html}</div>' if chip_html else ""
    dl = deadline_line(row)
    go = (
        f'<a class="go" href="{esc(link)}" target="_blank" rel="noopener">Official details →</a>'
        if link else ""
    )
    foot = f'<div class="foot{" split" if dl else ""}">{dl}{go}</div>' if (dl or go) else ""
    return (
        '<div class="row">'
        f'<div class="top">{name_html}{amt_html}</div>'
        f'{prov}{chips_block}{foot}'
        '</div>'
    )

def amount_range_text(entries):
    """Best-effort human summary like '£500–£3,000' from the raw Amount strings."""
    nums = []
    for r in entries:
        for n in re.findall(r"£?\s?([\d,]{2,7})", clean(r.get("Amount", ""))):
            try:
                nums.append(int(n.replace(",", "")))
            except ValueError:
                pass
    if not nums:
        return None
    lo, hi = min(nums), max(nums)
    if lo == hi:
        return f"£{lo:,}"
    return f"£{lo:,}–£{hi:,}"

# ── Presentation layer ──────────────────────────────────────────────────────
# The website is deliberately committed to ONE light theme: it's a public
# reference/directory that sits next to gov.uk and university pages, and it
# is intentionally NOT styled like the (dark, soft-rounded) app — near-square
# corners, hairline rules instead of shadows. Headings: Archivo. Body: Inter.
# The Sora wordmark is the only visual tie to the app.
SITE_CSS = """
*{box-sizing:border-box;}
:root{
  --paper:#FBFCFD; --ground:#EEF2F5; --ink:#0B2233; --ink-soft:#46596B;
  --ink-mute:#7C8FA0; --line:#D7DEE4; --teal:#0E7C7B; --teal-ink:#0A5C5B;
  --teal-wash:#E8F2F1; --navy:#0B2233; --warn:#B5561E; --maxw:960px;
}
html{-webkit-text-size-adjust:100%;}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif;
  font-size:15px; line-height:1.6; -webkit-font-smoothing:antialiased;
}
a{color:var(--teal-ink); text-decoration:none;}
a:hover{text-decoration:underline;}
strong{font-weight:600;}
:focus-visible{outline:2px solid var(--teal); outline-offset:2px;}

.hdr{
  position:sticky; top:0; z-index:20; display:flex; align-items:center;
  gap:14px 20px; flex-wrap:wrap; padding:8px 20px; min-height:56px;
  background:rgba(251,252,253,.94); backdrop-filter:blur(6px);
  border-bottom:1px solid var(--line);
}
.brand{display:flex; align-items:center; gap:8px; font-family:"Sora",sans-serif;
  font-weight:700; font-size:16px; letter-spacing:-.01em; color:var(--ink);}
.brand:hover{text-decoration:none;}
.brand .mk{width:24px; height:24px; border-radius:4px; background:var(--navy);
  display:flex; align-items:center; justify-content:center; flex-shrink:0;}
.brand .mk svg{width:14px; height:14px;}
.brand .s{color:var(--teal);}
.nav{display:flex; flex-wrap:wrap; gap:6px 15px;}
.nav a{font-size:13.5px; font-weight:500; color:var(--ink-soft);}
.nav a:hover{color:var(--teal-ink); text-decoration:none;}
.hdr .grow{flex:1;}
.btn{display:inline-flex; align-items:center; gap:7px; font-weight:600;
  font-size:13.5px; background:var(--teal); color:#fff; padding:8px 14px;
  border-radius:3px; border:1px solid var(--teal); cursor:pointer;}
.btn:hover{background:var(--teal-ink); text-decoration:none; color:#fff;}
.btn.lg{font-size:15px; padding:11px 20px;}

.hero{background:var(--navy); color:#fff; padding:54px 20px 46px;}
.hero .in{max-width:var(--maxw); margin:0 auto;}
.hero h1{font-family:"Archivo",sans-serif; font-weight:700;
  font-size:clamp(28px,4.6vw,44px); line-height:1.07; letter-spacing:-.02em;
  margin:0 0 14px; text-wrap:balance; max-width:19ch;}
.hero p{font-size:17px; color:#A9C0D1; margin:0 0 24px; max-width:54ch;}
.hero .actions{display:flex; align-items:center; gap:16px; flex-wrap:wrap;}
.hero .stores{font-size:13px; color:#8AA3B6;}
.facts{display:flex; flex-wrap:wrap; gap:22px 30px; margin:32px 0 0;
  border-top:1px solid rgba(255,255,255,.14); padding-top:20px;}
.facts b{display:block; font-family:"Archivo",sans-serif; font-weight:700;
  font-size:22px; font-variant-numeric:tabular-nums;}
.facts span{font-size:12.5px; color:#8AA3B6;}

.wrap{max-width:var(--maxw); margin:0 auto; padding:0 20px 64px;}
.crumb{display:flex; flex-wrap:wrap; gap:6px; font-size:12.5px;
  color:var(--ink-mute); padding:15px 0; border-bottom:1px solid var(--line);}
.crumb a{color:var(--ink-mute);}
h1.page{font-family:"Archivo",sans-serif; font-weight:700;
  font-size:clamp(25px,3.6vw,33px); line-height:1.13; letter-spacing:-.015em;
  margin:24px 0 12px; text-wrap:balance;}
.lede{font-size:15.5px; color:var(--ink-soft); max-width:66ch; margin:0 0 22px;}
.wrap h2{font-family:"Archivo",sans-serif; font-weight:600; font-size:13.5px;
  letter-spacing:.06em; text-transform:uppercase; color:var(--ink-soft);
  margin:40px 0 14px; padding-left:11px; border-left:3px solid var(--teal);}

.match{background:var(--teal-wash); border:1px solid #C9E3E1; border-radius:3px;
  padding:16px 18px; margin:20px 0 6px; display:flex; align-items:center;
  gap:18px; flex-wrap:wrap;}
.match .txt{flex:1; min-width:240px;}
.match p{margin:0; font-size:14px; color:var(--teal-ink); line-height:1.5;}
.match .pts{margin:7px 0 0; font-size:12px; font-weight:600; color:var(--teal-ink);
  letter-spacing:.01em;}
.match .btn{white-space:nowrap;}
.match .stores{font-size:12px; color:var(--ink-mute);}
.match .stores a{color:var(--ink-mute);}

.ctastrip{background:var(--navy); color:#fff; border-radius:3px; padding:18px 20px;
  margin:28px 0 6px; display:flex; align-items:center; gap:18px; flex-wrap:wrap;}
.ctastrip p{margin:0; flex:1; min-width:240px; font-size:14px; line-height:1.5; color:#fff;}
.ctastrip .sub{display:block; margin-top:4px; font-size:12px; color:#A9C0D1;}
.ctastrip .btn{background:#fff; color:var(--teal-ink); border-color:#fff; white-space:nowrap;}
.ctastrip .btn:hover{background:var(--teal-wash); color:var(--teal-ink);}

.list{border:1px solid var(--line); border-radius:3px; overflow:hidden;
  background:var(--paper);}
.list .row{padding:14px 17px; border-top:1px solid var(--line);}
.list .row:first-child{border-top:0;}
.list .row:hover{background:#F4F8F8;}
.row .top{display:flex; align-items:baseline; gap:14px;}
.row .nm{font-family:"Archivo",sans-serif; font-weight:600; font-size:15.5px;
  color:var(--ink); flex:1;}
a.nm:hover{color:var(--teal-ink);}
.row .amt{font-weight:600; font-size:14.5px; color:var(--teal-ink);
  font-variant-numeric:tabular-nums; white-space:nowrap;}
.row .prov{font-size:12.5px; color:var(--ink-mute); margin-top:2px;}
.row .chips{display:flex; flex-wrap:wrap; gap:6px; margin:8px 0 7px;}
.row .chip{font-size:11.5px; font-weight:500; color:var(--ink-soft);
  border:1px solid var(--line); border-radius:2px; padding:2px 7px;}
.row .foot{display:flex; align-items:center; justify-content:flex-end;
  gap:12px; flex-wrap:wrap; margin-top:4px;}
.row .foot.split{justify-content:space-between;}
.row .chips + .foot{margin-top:0;}
.row .dl{font-size:12.5px; color:var(--ink-mute);}
.row .dl.soon{color:var(--warn); font-weight:600;}
.row .go{font-size:12.5px; font-weight:600; white-space:nowrap;}

.tiles{display:grid; grid-template-columns:repeat(auto-fill,minmax(228px,1fr));
  gap:10px;}
.tiles a{display:block; background:var(--paper); border:1px solid var(--line);
  border-radius:3px; padding:12px 14px;}
.tiles a:hover{border-color:var(--teal); text-decoration:none;}
.tiles a b{display:block; font-family:"Archivo",sans-serif; font-weight:600;
  font-size:14px; color:var(--ink);}
.tiles a span{display:block; font-size:12.5px; color:var(--ink-mute);
  margin-top:2px; font-variant-numeric:tabular-nums;}

h1.page + .sub{font-size:13px; color:var(--ink-mute); margin:-4px 0 16px;
  letter-spacing:.01em;}
.kv{border:1px solid var(--line); border-radius:3px; overflow:hidden; margin:18px 0;}
.kv > div{display:flex; gap:14px; padding:10px 15px; border-top:1px solid var(--line);
  font-size:13.5px;}
.kv > div:first-child{border-top:0;}
.kv dt{flex:0 0 132px; color:var(--ink-mute);}
.kv dd{margin:0; flex:1; font-weight:500;}
.kv dd a{font-weight:600;}
.crit{margin:10px 0 0; padding:0 0 0 20px;}
.crit li{margin:0 0 6px; font-size:14px; color:var(--ink-soft);}
.crit + p.note{font-size:12.5px; color:var(--ink-mute); margin-top:10px;}
.applybtn{margin:10px 0 0;}

.steps{display:grid; grid-template-columns:repeat(3,1fr); gap:12px;}
.steps .step{border:1px solid var(--line); border-radius:3px; padding:15px;
  background:var(--paper);}
.steps .step i{display:inline-flex; width:23px; height:23px; border-radius:2px;
  background:var(--teal-wash); color:var(--teal-ink); font-family:"Archivo",sans-serif;
  font-weight:700; font-size:12.5px; align-items:center; justify-content:center;
  margin-bottom:8px; font-style:normal;}
.steps .step b{display:block; font-family:"Archivo",sans-serif; font-size:14px;
  margin-bottom:3px;}
.steps .step p{margin:0; font-size:13px; color:var(--ink-soft);}

.faq details{border-top:1px solid var(--line); padding:12px 0;}
.faq details:first-child{border-top:0;}
.faq summary{font-family:"Archivo",sans-serif; font-weight:600; font-size:14px;
  cursor:pointer; list-style:none; display:flex; justify-content:space-between;
  gap:12px;}
.faq summary::-webkit-details-marker{display:none;}
.faq summary::after{content:"+"; color:var(--teal); font-weight:700;}
.faq details[open] summary::after{content:"\\2013";}
.faq p{font-size:13.5px; color:var(--ink-soft); margin:9px 0 0;}

.ftr{background:var(--ground); border-top:1px solid var(--line);
  margin-top:52px; padding:32px 20px 26px;}
.ftr .cols{max-width:var(--maxw); margin:0 auto; display:flex; gap:44px;
  flex-wrap:wrap;}
.ftr .col b{display:block; font-family:"Archivo",sans-serif; font-size:11.5px;
  letter-spacing:.08em; text-transform:uppercase; color:var(--ink-mute);
  margin-bottom:9px;}
.ftr .col a{display:block; font-size:13px; color:var(--ink-soft); margin-bottom:6px;}
.ftr .fine{max-width:var(--maxw); margin:24px auto 0; padding-top:16px;
  border-top:1px solid var(--line); font-size:12px; color:var(--ink-mute);}

.ctabar{display:none;}
@media (max-width:760px){
  .ctabar{position:fixed; left:0; right:0; bottom:0; z-index:30; display:flex;
    align-items:center; gap:10px; padding:9px 12px 9px 14px; background:var(--navy);
    border-top:1px solid rgba(255,255,255,.12);}
  .ctabar p{margin:0; flex:1; font-size:12.5px; color:#fff; line-height:1.3;}
  .ctabar .btn{padding:8px 13px; font-size:13px;}
  .ctabar .x{background:none; border:0; color:#8AA3B6; font-size:20px;
    line-height:1; padding:2px 4px; cursor:pointer;}
  body.has-bar{padding-bottom:60px;}
  .steps{grid-template-columns:1fr;}
  .ftr .cols{gap:26px;}
}
"""

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Archivo:wght@500;600;700&family=Inter:wght@400;500;600&'
    'family=Sora:wght@700;800&display=swap">'
)

LOGO_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" '
    'stroke-linecap="round"><circle cx="10" cy="10" r="6"></circle>'
    '<line x1="14.6" y1="14.6" x2="20" y2="20"></line></svg>'
)

NAV_LINKS = [
    ("/bursaries/", "Universities"),
    ("/bursaries/#circumstance", "Circumstance"),
    ("/bursaries/#subject", "Subject"),
    ("/bursaries/#region", "Region"),
    ("/bursaries/closing-soon/", "Closing soon"),
]

def header_html():
    nav = "".join(f'<a href="{h}">{esc(t)}</a>' for h, t in NAV_LINKS)
    return (
        '<header class="hdr">'
        f'<a class="brand" href="/"><span class="mk">{LOGO_SVG}</span>'
        '<span>Bursa<span class="s">Search</span></span></a>'
        f'<nav class="nav">{nav}</nav>'
        '<span class="grow"></span>'
        '<a class="btn" href="/get">Get matched — free</a>'
        '</header>'
    )

def footer_html():
    return (
        '<footer class="ftr"><div class="cols">'
        '<div class="col"><b>Browse</b>'
        '<a href="/bursaries/">By university</a>'
        '<a href="/bursaries/#circumstance">By circumstance</a>'
        '<a href="/bursaries/#subject">By subject</a>'
        '<a href="/bursaries/#region">By region</a></div>'
        '<div class="col"><b>Popular</b>'
        '<a href="/bursaries/closing-soon/">Closing soon</a>'
        '<a href="/bursaries/highest-value/">Highest value</a>'
        '<a href="/bursaries/circumstance/care-leavers/">Care leaver bursaries</a>'
        '<a href="/bursaries/circumstance/low-income-students/">Low-income bursaries</a></div>'
        '<div class="col"><b>About</b>'
        f'<a href="{APP_STORE_URL}">iOS app</a>'
        f'<a href="{PLAY_URL}">Android app</a>'
        '<a href="https://bursasearchp.github.io/bursasearch-legal/support.html">Support</a></div>'
        '</div>'
        '<p class="fine">Bursary details are compiled from official university and '
        'provider pages and can change &mdash; always confirm on the official link '
        'before applying. &copy; Bursa Group Ltd.</p></footer>'
    )

STICKY_BAR = """<div class="ctabar" id="ctabar">
<p>Match every UK bursary &middot; track applications &middot; deadline alerts</p>
<a class="btn" href="/get">Get matched</a>
<button class="x" type="button" aria-label="Dismiss" onclick="try{localStorage.setItem('bs_cta_x','1')}catch(e){}document.getElementById('ctabar').style.display='none'">&times;</button>
<script>try{if(localStorage.getItem('bs_cta_x'))document.getElementById('ctabar').style.display='none'}catch(e){}</script>
</div>"""

GET_REDIRECT_HTML = (
    '<!doctype html><html lang="en"><head><meta charset="UTF-8">'
    '<meta name="robots" content="noindex"><title>Get the BursaSearch app</title>'
    f'<meta http-equiv="refresh" content="0;url={APP_STORE_URL}">'
    f'<script>var a={json.dumps(APP_STORE_URL)},p={json.dumps(PLAY_URL)};'
    'location.replace(/android/i.test(navigator.userAgent||"")?p:a);</script>'
    f'</head><body>Opening the app&hellip; <a href="{APP_STORE_URL}">App Store</a> '
    f'&middot; <a href="{PLAY_URL}">Google Play</a></body></html>'
)

def jsonld_script(obj_json):
    return f'<script type="application/ld+json">{obj_json}</script>'

def match_callout(context_phrase):
    return (
        '<div class="match"><div class="txt">'
        f'<p><strong>Match {esc(context_phrase)} to your circumstances</strong> &mdash; and '
        'every other UK bursary. The free app checks university, national and independent '
        'funds against your details, tells you which ones you qualify for and why, then '
        'tracks each application and reminds you before the deadline.</p>'
        '<p class="pts">Every source, one place &middot; Eligibility explained &middot; '
        'Deadline reminders</p></div>'
        '<a class="btn" href="/get">Get matched &mdash; free</a>'
        f'<span class="stores"><a href="{APP_STORE_URL}">App Store</a> &middot; '
        f'<a href="{PLAY_URL}">Google Play</a></span>'
        '</div>'
    )

CTA_STRIP = (
    '<div class="ctastrip">'
    '<p><strong>Get matched to every fund you qualify for</strong> &mdash; across '
    'universities, national bodies and independent trusts. Track your applications and get '
    'a reminder before every deadline.'
    '<span class="sub">Free &middot; no account needed to browse &middot; iOS &amp; Android</span></p>'
    '<a class="btn" href="/get">Get the app</a>'
    '</div>'
)

def render_shell(*, title, description, canonical, body, hero="", sticky="", schema=""):
    """The one page template for the whole site. `title`/`description` arrive
    already escaped by the caller (same as the old PAGE_TEMPLATE contract)."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<meta name="description" content="{description}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<link rel="canonical" href="{canonical}">
{FONTS}
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="website">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{OG_IMAGE}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{OG_IMAGE}">
{schema}
<style>{SITE_CSS}</style>
</head>
<body class="{'has-bar' if sticky else ''}">
{header_html()}
{hero}
<main class="wrap">
{body}
</main>
{footer_html()}
{sticky}
</body>
</html>
"""

def faq_block(uni_name, count, scope_phrase="this university"):
    items = [
        ("Is this list free to use?",
         f"Yes. Every bursary listed here for {uni_name} is free to browse — no account or payment needed to see what's available."),
        ("Do I apply through BursaSearch?",
         "No — every listing links directly to the official university or provider page, and you apply there. We connect you straight to the source, not through a form with us."),
        ("How do I know which ones I actually qualify for?",
         f"This page lists what's publicly available for {scope_phrase}. The free BursaSearch app checks your circumstances — income, region, fee status and more — against every UK bursary, including national and independent funds beyond this list, tells you exactly which ones you qualify for and why, then tracks your applications and reminds you before each deadline."),
    ]
    out = []
    for q, a in items:
        out.append(f"""
        <details>
          <summary>{esc(q)}</summary>
          <p>{esc(a)}</p>
        </details>""")
    return "".join(out)

def faq_jsonld(uni_name, scope_phrase="this university"):
    items = [
        ("Is this list free to use?",
         f"Yes. Every bursary listed here for {uni_name} is free to browse — no account or payment needed to see what's available."),
        ("Do I apply through BursaSearch?",
         "No — every listing links directly to the official university or provider page, and you apply there. We connect you straight to the source, not through a form with us."),
        ("How do I know which ones I actually qualify for?",
         f"This page lists what's publicly available for {scope_phrase}. The free BursaSearch app checks your circumstances against every UK bursary, including national and independent funds beyond this list, tells you exactly which ones you qualify for and why, then tracks your applications and reminds you before each deadline."),
    ]
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {"@type": "Answer", "text": a},
            }
            for q, a in items
        ],
    })

def breadcrumb_jsonld(crumbs):
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, "item": url}
            for i, (name, url) in enumerate(crumbs)
        ],
    })

def crumb_html(trail):
    """trail = list of (label, href_or_None); the last item renders unlinked."""
    parts = []
    for i, (label, href) in enumerate(trail):
        if href and i < len(trail) - 1:
            parts.append(f'<a href="{href}">{esc(label)}</a>')
        else:
            parts.append(f'<span>{esc(label)}</span>')
    return '<nav class="crumb">' + '<span>›</span>'.join(parts) + '</nav>'

def content_body(*, trail, h1, lede, context_phrase, count, rows_html, faq_html, related_html):
    """Assembles the shared body of a listing page (university / rollup /
    circumstance / subject / region / closing-soon / highest-value)."""
    return (
        crumb_html(trail)
        + f'<h1 class="page">{esc(h1)}</h1>'
        + f'<p class="lede">{esc(lede)}</p>'
        + match_callout(context_phrase)
        + f'<h2>{esc(f"{count} funds currently listed")}</h2>'
        + f'<div class="list">{rows_html}</div>'
        + CTA_STRIP
        + '<h2>Common questions</h2>'
        + f'<div class="faq">{faq_html}</div>'
        + related_html
    )

def related_links_html(entries, exclude=None):
    """Cross-links a page out to the circumstance/subject/region pages its
    own bursaries actually qualify for — used on university pages (nothing
    to exclude) and on the tag pages themselves (excluding their own family
    member, e.g. the Care Leavers page shouldn't link to itself), so these
    clusters connect to each other instead of only being reachable from the
    hub, which is a dead end for crawl depth and for students who'd
    genuinely want to see them."""
    links = []
    for slug, h1, noun_phrase, filt in CIRCUMSTANCES:
        if exclude == ("circumstance", slug):
            continue
        if any(filt(r) for r in entries):
            links.append((f"/bursaries/circumstance/{slug}/", h1))
    for slug, h1, noun_phrase, filt in SUBJECTS:
        if exclude == ("subject", slug):
            continue
        if any(filt(r) for r in entries):
            links.append((f"/bursaries/subject/{slug}/", h1))
    for slug, h1, noun_phrase in REGIONS:
        if exclude == ("region", slug):
            continue
        needles = REGION_MATCH[slug]
        if any(any(n in clean(r.get("UK region", "")).lower() for n in needles) for r in entries):
            links.append((f"/bursaries/region/{slug}/", h1))
    if not links:
        return ""
    items = "".join(f'<a href="{esc(href)}"><b>{esc(label)}</b></a>' for href, label in links)
    return f'<h2>Also see</h2><div class="tiles">{items}</div>'

def render_page(uni_name, entries, slug):
    entries_sorted = sorted(entries, key=lambda r: clean(r.get("Bursary Name", "")))
    rows_html = "".join(
        bursary_row(r, fund_href=fund_href_for(r, uni_name)) for r in entries_sorted
    )
    count = len(entries)
    amt_range = amount_range_text(entries)
    amt_bit = f" worth {amt_range}" if amt_range else ""
    lede = (
        f"{count} verified bursaries and scholarships{amt_bit} currently listed for students at "
        f"{uni_name} — each linking straight to the official source, no forms with us. Our app also "
        f"matches you to additional grants beyond this list, based on your specific circumstances."
    )
    title = f"{uni_name} Bursaries & Scholarships ({date.today().year}) | BursaSearch"
    description = (
        f"{count} verified bursaries and scholarships for {uni_name} students{amt_bit}. "
        f"See eligibility, deadlines and official application links."
    )
    canonical = f"{SITE_URL}/bursaries/{slug}/"
    subj_pages = UNI_SUBJECT_PAGES.get(slug, [])
    subj_block = ""
    if subj_pages:
        subj_block = f'<h2>Subject-specific funds at {esc(uni_name)}</h2>' + tiles_html([
            (f"/bursaries/{slug}/subject/{s}/", f"{SUBJECT_LABEL.get(s, s)} bursaries", f"{c} funds")
            for s, _, c in sorted(subj_pages, key=lambda t: -t[2])
        ])
    body = content_body(
        trail=[("Home", "/"), ("Bursaries by university", "/bursaries/"), (uni_name, None)],
        h1=f"{uni_name} Bursaries & Scholarships",
        lede=lede,
        context_phrase=f"these {count} {uni_name} funds",
        count=count,
        rows_html=rows_html,
        faq_html=faq_block(uni_name, count),
        related_html=subj_block + related_links_html(entries),
    )
    schema = jsonld_script(faq_jsonld(uni_name)) + jsonld_script(breadcrumb_jsonld([
        ("BursaSearch", f"{SITE_URL}/"),
        ("Bursaries by university", f"{SITE_URL}/bursaries/"),
        (uni_name, canonical),
    ]))
    return render_shell(title=esc(title), description=esc(description),
                        canonical=canonical, body=body, sticky=STICKY_BAR, schema=schema)

# ── Rollup page for single-entry universities ───────────────────────────────
def render_rollup(singles):
    all_rows = []
    for uni, rows_ in singles.items():
        for r in rows_:
            all_rows.append((uni, r))
    all_rows.sort(key=lambda t: t[0])
    rows_html = "".join(bursary_row(r, uni=uni) for uni, r in all_rows)
    count = len(all_rows)
    title = f"More UK University Bursaries ({date.today().year}) | BursaSearch"
    description = f"{count} additional verified UK university bursaries and scholarships, one per institution."
    canonical = f"{SITE_URL}/bursaries/more-universities/"
    lede = (
        f"{count} more verified bursaries, one each from smaller listings across UK universities — "
        f"each linking straight to the official source, no forms with us. Our app also matches you "
        f"to additional grants beyond this list, based on your specific circumstances."
    )
    body = content_body(
        trail=[("Home", "/"), ("Bursaries by university", "/bursaries/"), ("More universities", None)],
        h1="More UK University Bursaries",
        lede=lede,
        context_phrase="these funds",
        count=count,
        rows_html=rows_html,
        faq_html=faq_block("these universities", count),
        related_html="",
    )
    schema = jsonld_script(faq_jsonld("these universities")) + jsonld_script(breadcrumb_jsonld([
        ("BursaSearch", f"{SITE_URL}/"),
        ("Bursaries by university", f"{SITE_URL}/bursaries/"),
        ("More universities", canonical),
    ]))
    return render_shell(title=esc(title), description=esc(description),
                        canonical=canonical, body=body, sticky=STICKY_BAR, schema=schema)

# ── Circumstance-based pages (cut across universities, tag-based not
#    partition-based — the same bursary can legitimately appear on more
#    than one of these, e.g. a low-income care-leaver bursary). ─────────────
def vuln_has(row, *needles):
    """True if any comma-separated part of the Vulnerabilities cell CONTAINS
    any needle (case-insensitive). Substring, not exact — the sheet's label
    wording drifts over time ('Estranged from family' -> 'Estranged',
    'Disability' -> 'Disabled / long-term health condition', 'Refugee or
    asylum seeker' -> 'Refugee / asylum seeker'), and an exact match silently
    empties whole circumstance pages when it does."""
    parts = [p.strip().lower() for p in
             clean(row.get("Vulnerabilities (multi-select)", "")).split(",")]
    return any(any(n in p for n in needles) for p in parts)

def _is_international(row):
    fs = clean(row.get("Fee status", "")).lower()
    return "overseas" in fs or "international" in fs

CIRCUMSTANCES = [
    # (slug, h1, plural noun phrase used in copy, filter fn)
    ("low-income-students", "Bursaries for Low-Income Students",
     "low-income students", lambda r: vuln_has(r, "low income", "fsm", "free school meal")),
    ("care-leavers", "Bursaries for Care Leavers",
     "care leavers", lambda r: vuln_has(r, "care leaver", "care experienced", "care-experienced")),
    ("international-students", "Bursaries for International Students in the UK",
     "international students", _is_international),
    ("estranged-students", "Bursaries for Estranged Students",
     "estranged students", lambda r: vuln_has(r, "estranged")),
    ("disabled-students", "Bursaries for Disabled Students",
     "disabled students", lambda r: vuln_has(r, "disab")),
    ("refugees-and-asylum-seekers", "Scholarships for Refugees and Asylum Seekers",
     "refugees and asylum seekers", lambda r: vuln_has(r, "refugee", "asylum")),
]

def render_tag_page(kind, slug, h1, noun_phrase, rows_matched, crumb_label, lede_tail, canon_by_key,
                     sort_key=None, limit=None, extra_html=""):
    """Shared renderer for the cross-cutting tag pages (circumstance, region,
    subject, plus the two standalone ranked pages closing-soon/highest-value
    which pass slug="" and their own sort_key/limit) — same shape, just a
    different filter axis and URL prefix."""
    entries_sorted = sorted(
        rows_matched,
        key=sort_key or (lambda r: (clean(r.get("University", "")), clean(r.get("Bursary Name", "")))),
    )
    if limit:
        entries_sorted = entries_sorted[:limit]
    row_parts = []
    for r in entries_sorted:
        raw_uni = clean(r.get("University", ""))
        resolved = canon_by_key.get(norm_uni_key(raw_uni)) if raw_uni else None
        uni_href = f"/bursaries/{resolved[1]}/" if resolved else None
        row_parts.append(bursary_row(
            r, uni=raw_uni or None, uni_href=uni_href, fund_href=fund_href_for(r),
        ))
    rows_html = "".join(row_parts)
    count = len(entries_sorted)
    amt_range = amount_range_text(entries_sorted)
    amt_bit = f" worth {amt_range}" if amt_range else ""
    n_unis = len({clean(r.get("University", "")) for r in entries_sorted if clean(r.get("University", ""))})
    lede = (
        f"{count} verified bursaries and scholarships{amt_bit} for {noun_phrase}, across {n_unis} "
        f"UK universities — each linking straight to the official source, no forms with us. Our app "
        f"also matches you to additional grants beyond this list, based on {lede_tail}."
    )
    title = f"{h1} ({date.today().year}) | BursaSearch"
    description = (
        f"{count} verified bursaries and scholarships for {noun_phrase} at {n_unis} UK universities{amt_bit}. "
        f"See eligibility, deadlines and official application links."
    )
    path_suffix = f"{kind}/{slug}/" if slug else f"{kind}/"
    canonical = f"{SITE_URL}/bursaries/{path_suffix}"
    body = content_body(
        trail=[("Home", "/"), (crumb_label, "/bursaries/"), (h1, None)],
        h1=h1,
        lede=lede,
        context_phrase=f"these {count} funds",
        count=count,
        rows_html=rows_html,
        faq_html=faq_block(noun_phrase, count, scope_phrase=noun_phrase),
        related_html=extra_html + related_links_html(rows_matched, exclude=(kind, slug)),
    )
    schema = jsonld_script(faq_jsonld(noun_phrase, scope_phrase=noun_phrase)) + jsonld_script(
        breadcrumb_jsonld([
            ("BursaSearch", f"{SITE_URL}/"),
            (crumb_label, f"{SITE_URL}/bursaries/"),
            (h1, canonical),
        ])
    )
    return render_shell(title=esc(title), description=esc(description),
                        canonical=canonical, body=body, sticky=STICKY_BAR, schema=schema)

def render_circumstance_page(slug, h1, noun_phrase, rows_matched, canon_by_key):
    return render_tag_page(
        "circumstance", slug, h1, noun_phrase, rows_matched,
        "Bursaries by circumstance", "your full circumstances", canon_by_key,
    )

# ── Subject-based pages — the subjects with enough real cross-university
#    volume to be worth a page. The raw "Study subject" cell is mostly clean
#    single labels ("Engineering", "Nursing", "Law", …) but only ~37% filled,
#    so this is a curated list, not "every category". ─────────────────────────
def _subj(row):
    return clean(row.get("Study subject", "")).lower()

def _is_medicine(row):
    # "Veterinary Medicine" contains "medicine" too — keep the two subjects
    # distinct rather than one page silently absorbing the other's rows.
    s = _subj(row)
    return bool(re.search(r"\bmedicine\b", s)) and "veterinary" not in s

def _subj_match(pattern):
    return lambda r: bool(re.search(pattern, _subj(r)))

SUBJECTS = [
    # (slug, h1, plural noun phrase used in copy, filter fn operating on the row)
    ("law", "Bursaries for Law Students", "law students", _subj_match(r"\blaw\b")),
    ("business", "Bursaries for Business Students", "business students",
     _subj_match(r"\bbusiness\b")),
    ("medicine", "Bursaries for Medicine Students", "medicine students", _is_medicine),
    ("nursing", "Bursaries for Nursing Students", "nursing students",
     _subj_match(r"\bnursing\b")),
    ("engineering", "Bursaries for Engineering Students", "engineering students",
     _subj_match(r"\bengineering\b")),
    ("computer-science", "Bursaries for Computer Science Students",
     "computer science students", _subj_match(r"\bcomput")),
    ("art-and-design", "Bursaries for Art & Design Students", "art and design students",
     _subj_match(r"\bart\b|\bdesign\b|fine art")),
    ("music", "Bursaries for Music Students", "music students", _subj_match(r"\bmusic\b")),
    ("veterinary-studies", "Bursaries for Veterinary Students", "veterinary students",
     lambda r: "veterinary" in _subj(r)),
]
# Short label per subject, for the "<Label> bursaries at <University>" pages.
SUBJECT_LABEL = {
    "law": "Law", "business": "Business", "medicine": "Medicine", "nursing": "Nursing",
    "engineering": "Engineering", "computer-science": "Computer Science",
    "art-and-design": "Art & Design", "music": "Music", "veterinary-studies": "Veterinary",
}

def render_subject_page(slug, h1, noun_phrase, rows_matched, canon_by_key):
    unis = SUBJECT_UNI_PAGES.get(slug, [])
    extra = ""
    if unis:
        tiles = tiles_html([
            (f"/bursaries/{us}/subject/{slug}/", un, f"{c} funds")
            for un, us, c in sorted(unis, key=lambda t: -t[2])
        ])
        label = SUBJECT_LABEL.get(slug, "these")
        extra = f'<h2>{esc(label)} bursaries by university</h2>' + tiles
    return render_tag_page(
        "subject", slug, h1, noun_phrase, rows_matched,
        "Bursaries by subject", "your subject and full circumstances", canon_by_key,
        extra_html=extra,
    )

# ── University × subject pages — "engineering bursaries at Bath". Only where
#    a named university genuinely has >=2 funds for that subject (~124 pairs
#    in the data); the rest would be thin. Nested under the university. ──────
UNI_SUBJECT_MIN = 2

def render_uni_subject_page(uni_name, uni_slug, subj_slug, matched):
    label = SUBJECT_LABEL.get(subj_slug, "Subject")
    count = len(matched)
    h1 = f"{label} Bursaries at {uni_name}"
    canonical = f"{SITE_URL}/bursaries/{uni_slug}/subject/{subj_slug}/"
    title = f"{label} Bursaries at {uni_name} ({date.today().year}) | BursaSearch"
    amt = amount_range_text(matched)
    amt_bit = f" worth {amt}" if amt else ""
    lede = (
        f"{count} verified {label.lower()} bursaries and scholarships{amt_bit} for "
        f"{uni_name} students — each linking straight to the official page. Our app also "
        f"matches you to funds beyond this list, based on your subject and circumstances."
    )
    description = (
        f"{count} verified {label.lower()} bursaries and scholarships for {uni_name} "
        f"students{amt_bit}. See eligibility, deadlines and official application links."
    )
    rows_html = "".join(
        bursary_row(r, fund_href=fund_href_for(r, uni_name))
        for r in sorted(matched, key=lambda r: clean(r.get("Bursary Name", "")))
    )
    # cross-links: the standalone subject page, the university page, and the
    # other subjects that university has a page for.
    others = [(s, sh1, c) for s, sh1, c in UNI_SUBJECT_PAGES.get(uni_slug, []) if s != subj_slug]
    also = [(f"/bursaries/subject/{subj_slug}/", f"All {label.lower()} bursaries (UK)"),
            (f"/bursaries/{uni_slug}/", f"All {uni_name} bursaries")]
    also += [(f"/bursaries/{uni_slug}/subject/{s}/",
              f"{SUBJECT_LABEL.get(s, s)} bursaries at {uni_name}") for s, _, _ in others]
    also_tiles = tiles_html([(h, t, None) for h, t in also])
    body = content_body(
        trail=[("Home", "/"), ("Bursaries by university", "/bursaries/"),
               (uni_name, f"/bursaries/{uni_slug}/"), (f"{label} bursaries", None)],
        h1=h1,
        lede=lede,
        context_phrase=f"these {count} {label.lower()} funds at {uni_name}",
        count=count,
        rows_html=rows_html,
        faq_html=faq_block(f"{label.lower()} students at {uni_name}", count,
                           scope_phrase=f"{label.lower()} students at {uni_name}"),
        related_html=f"<h2>Also see</h2>{also_tiles}",
    )
    schema = jsonld_script(faq_jsonld(f"{label.lower()} students at {uni_name}",
                                      scope_phrase=f"{label.lower()} students at {uni_name}")) + \
        jsonld_script(breadcrumb_jsonld([
            ("BursaSearch", f"{SITE_URL}/"),
            ("Bursaries by university", f"{SITE_URL}/bursaries/"),
            (uni_name, f"{SITE_URL}/bursaries/{uni_slug}/"),
            (h1, canonical),
        ]))
    return render_shell(title=esc(title), description=esc(description),
                        canonical=canonical, body=body, sticky=STICKY_BAR, schema=schema)

# ── Region-based pages (same cross-cutting shape as circumstance pages —
#    "UK region" is a free-text field, sometimes multi-region, so match by
#    substring containment rather than exact equality). ─────────────────────
REGIONS = [
    # (slug, h1, plural noun phrase used in copy)
    ("scotland", "Bursaries for Students in Scotland", "students in Scotland"),
    ("south-east", "Bursaries for Students in the South East", "students in the South East"),
    ("wales", "Bursaries for Students in Wales", "students in Wales"),
    ("london", "Bursaries for Students in London", "students in London"),
    ("north-west", "Bursaries for Students in the North West", "students in the North West"),
    ("south-west", "Bursaries for Students in the South West", "students in the South West"),
    ("north-east", "Bursaries for Students in the North East", "students in the North East"),
    ("yorkshire", "Bursaries for Students in Yorkshire", "students in Yorkshire"),
    ("east-of-england", "Bursaries for Students in the East of England", "students in the East of England"),
    ("east-midlands", "Bursaries for Students in the East Midlands", "students in the East Midlands"),
    ("west-midlands", "Bursaries for Students in the West Midlands", "students in the West Midlands"),
]
REGION_MATCH = {  # slug -> substring(s) to match against the raw "UK region" cell
    "scotland": ["scotland"], "south-east": ["south east"], "wales": ["wales"],
    "london": ["london"], "north-west": ["north west"], "south-west": ["south west"],
    "north-east": ["north east"], "yorkshire": ["yorkshire"],
    "east-of-england": ["east of england"], "east-midlands": ["east midlands"],
    "west-midlands": ["west midlands"],
}

def render_region_page(slug, h1, noun_phrase, rows_matched, canon_by_key):
    return render_tag_page(
        "region", slug, h1, noun_phrase, rows_matched,
        "Bursaries by region", "your region and full circumstances", canon_by_key,
    )

# ── Per-grant pages ─────────────────────────────────────────────────────────
# One URL per individual fund that carries enough real data to make a page
# that isn't thin. Nested under the university (/bursaries/<uni>/<fund>/) for
# topical relevance and a natural breadcrumb.
FUND_PAGE_MIN_SIGNALS = 2
FUND_SIGNAL_FIELDS = [
    "Amount", "Deadline", "Fee status", "Study subject",
    "Vulnerabilities (multi-select)", "Household income", "Home country",
    "Required nationality", "Course Year", "AI Notes", "Extra Requirement",
]

def fund_has_page(row):
    if not clean(row.get("Bursary Name", "")):
        return False
    if not (clean(row.get("Application URL", "")) or clean(row.get("Link", ""))):
        return False
    return sum(1 for f in FUND_SIGNAL_FIELDS if clean(row.get(f, ""))) >= FUND_PAGE_MIN_SIGNALS

def assign_fund_slug(fkey, name, pinned, used):
    """Slug for a fund within its university. `pinned` = fund_slugs.json — a
    fund keeps its slug across runs (a light rename mustn't move the URL), so
    a known fund returns its pin unconditionally. `used` = slugs already taken
    for this university this run (pre-seeded with this uni's pins), so only a
    genuinely new fund needs the -2/-3 collision suffix."""
    if fkey in pinned:
        return pinned[fkey]
    base = slugify(name) or "bursary"
    if base == "subject":       # reserved: /bursaries/<uni>/subject/<subj>/
        base = "subject-fund"
    s, i = base, 2
    while s in used:
        s, i = f"{base}-{i}", i + 1
    pinned[fkey] = s
    return s

_SCHOLARSHIP_WORDS = ("scholarship", "award", "prize", "medal", "studentship")
_HARDSHIP_WORDS = ("hardship", "emergency", "crisis", "financial difficulty",
                   "in financial need", "support fund", "access to learning")

def infer_fund_type(row):
    n = clean(row.get("Bursary Name", "")).lower()
    if any(w in n for w in _HARDSHIP_WORDS):
        return "Hardship fund"
    if any(w in n for w in _SCHOLARSHIP_WORDS):
        return "Scholarship"
    return "Bursary"

def eligibility_audience_phrase(row):
    """The single strongest 'who it's for' phrase, for the lede + schema."""
    if vuln_has(row, "care leaver", "care experienced", "care-experienced"):
        return "care-experienced students"
    if vuln_has(row, "estranged"):
        return "students estranged from their families"
    if vuln_has(row, "refugee", "asylum"):
        return "students with refugee or asylum-seeker backgrounds"
    if vuln_has(row, "disab"):
        return "disabled students"
    hh = clean(row.get("Household income", ""))
    if hh:
        return f"students with a household income of {hh}"
    if vuln_has(row, "low income", "fsm", "free school meal"):
        return "students from lower-income households"
    subj = clean(row.get("Study subject", ""))
    if subj and len(subj) < 40:
        return f"{subj.lower()} students"
    fs = clean(row.get("Fee status", "")).lower()
    if "overseas" in fs or "international" in fs:
        return "international students"
    return ""

def deadline_text(row):
    """Plain-text deadline for the key-facts list."""
    raw = clean(row.get("Deadline", ""))
    if not raw:
        return "Set annually — check the official page"
    d = parse_deadline_date(raw)
    if d:
        return format_deadline(raw) if d >= date.today() else "Set annually — check the official page"
    if any(h in raw.lower() for h in _ROLLING_HINTS):
        return "Rolling — no fixed date"
    return raw

def fund_lede(row, uni_name, ftype):
    name = clean(row.get("Bursary Name", ""))
    amount = format_amount(row.get("Amount", ""))
    amt = f" worth {amount}" if amount else ""
    aud = eligibility_audience_phrase(row)
    aud_bit = f" for {aud}" if aud else ""
    d = parse_deadline_date(row.get("Deadline", ""))
    raw = clean(row.get("Deadline", ""))
    if d and d >= date.today():
        dl = f" Applications for {date.today().year}/{str(date.today().year + 1)[2:]} close on {format_deadline(raw)}."
    elif raw and any(h in raw.lower() for h in _ROLLING_HINTS):
        dl = " It runs on a rolling basis, so there's no fixed deadline."
    else:
        dl = ""
    return (
        f"The {name} is a {ftype.lower()}{amt}{aud_bit} at {uni_name}. Every detail "
        f"here is taken from the official page — you apply directly with {uni_name}, "
        f"not through us.{dl}"
    )

def eligibility_lines(row):
    out = []
    hh = clean(row.get("Household income", ""))
    if hh:
        out.append(f"Your household income is {hh}.")
    v = clean(row.get("Vulnerabilities (multi-select)", ""))
    if v:
        parts = [p.strip() for p in v.split(",") if p.strip()]
        out.append("Your circumstances include: " + ", ".join(parts).lower() + ".")
    fs = clean(row.get("Fee status", ""))
    if fs and fs.lower() != "any":
        out.append(f"Your fee status is {fs}.")
    nat = clean(row.get("Required nationality", ""))
    if nat:
        out.append(f"You are a national of {nat}.")
    hc = clean(row.get("Home country", ""))
    if hc:
        out.append(f"You are ordinarily resident in {hc}.")
    subj = clean(row.get("Study subject", ""))
    if subj:
        out.append(f"You are studying {subj}, or a closely related course.")
    lvl = clean(row.get("Study level", ""))
    if lvl:
        out.append(f"You are studying at {lvl.lower()} level.")
    yr = clean(row.get("Course Year", ""))
    if yr:
        out.append(f"You are in course year {yr}.")
    grade = clean(row.get("Minimum grade", ""))
    if grade:
        out.append(f"You have achieved at least {grade}.")
    extra = clean(row.get("Extra Requirement", ""))
    if extra:
        out.append(extra if extra.rstrip().endswith((".", "!", "?")) else extra.rstrip() + ".")
    return out

def fund_faq_items(row, uni_name):
    name = clean(row.get("Bursary Name", ""))
    amount = format_amount(row.get("Amount", ""))
    a_amt = (
        f"The {name} is worth {amount}."
        if amount else
        f"{uni_name} doesn't publish a single fixed figure for the {name} — check the "
        "official page for the current amount and how it's paid."
    )
    d = parse_deadline_date(row.get("Deadline", ""))
    raw = clean(row.get("Deadline", ""))
    if d and d >= date.today():
        a_dl = f"Applications for the {name} close on {format_deadline(raw)}."
    elif raw and any(h in raw.lower() for h in _ROLLING_HINTS):
        a_dl = f"The {name} has no fixed deadline — you can apply at any point during the year."
    else:
        a_dl = (
            f"{uni_name} sets the deadline for the {name} each year. Check the official "
            "page for the current closing date."
        )
    return [
        (f"How much is the {name}?", a_amt),
        (f"What is the deadline for the {name}?", a_dl),
        ("Do I apply through BursaSearch?",
         f"No. You apply directly with {uni_name} using the official link on this page — "
         "BursaSearch is not part of the application."),
    ]

def monetary_grant_jsonld(row, uni_name, canonical, description):
    obj = {
        "@context": "https://schema.org",
        "@type": "MonetaryGrant",
        "name": clean(row.get("Bursary Name", "")),
        "description": description,
        "url": canonical,
        "funder": {"@type": "CollegeOrUniversity", "name": uni_name},
    }
    val = max_amount_value(row.get("Amount", ""))
    if val > 0:
        obj["amount"] = {"@type": "MonetaryAmount", "currency": "GBP", "value": val}
    aud = eligibility_audience_phrase(row)
    if aud:
        obj["audience"] = {"@type": "EducationalAudience", "audienceType": aud}
    return json.dumps(obj)

def render_fund_page(row, uni_name, uni_slug, fund_slug, sibling_specs):
    name = clean(row.get("Bursary Name", ""))
    ftype = infer_fund_type(row)
    official = clean(row.get("Application URL", "")) or clean(row.get("Link", ""))
    canonical = f"{SITE_URL}/bursaries/{uni_slug}/{fund_slug}/"
    lede = fund_lede(row, uni_name, ftype)
    description = (lede[:157].rsplit(" ", 1)[0] + "…") if len(lede) > 158 else lede
    title = f"{name} — {uni_name} ({date.today().year}) | BursaSearch"

    kv = [("Amount", esc(format_amount(row.get("Amount", "")) or "See official page")),
          ("Type", esc(ftype)),
          ("Deadline", esc(deadline_text(row)))]
    lvl = clean(row.get("Study level", ""))
    if lvl:
        kv.append(("Study level", esc(lvl)))
    fs = clean(row.get("Fee status", ""))
    if fs and fs.lower() != "any":
        kv.append(("Fee status", esc(fs)))
    subj = clean(row.get("Study subject", ""))
    if subj:
        kv.append(("Subject", esc(subj)))
    kv.append(("Administered by", f'<a href="/bursaries/{uni_slug}/">{esc(uni_name)}</a>'))
    kv_html = '<dl class="kv">' + "".join(
        f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in kv
    ) + "</dl>"

    crit = eligibility_lines(row)
    if crit:
        crit_html = ('<ul class="crit">' + "".join(f"<li>{esc(c)}</li>" for c in crit)
                     + '</ul><p class="note">This is a summary — always confirm the full '
                       'eligibility rules on the official page before applying.</p>')
    else:
        crit_html = ('<p class="note">The official page has the full eligibility rules for '
                     f'the {esc(name)}.</p>')

    faq_items = fund_faq_items(row, uni_name)
    faq_html = "".join(
        f"<details><summary>{esc(q)}</summary><p>{esc(a)}</p></details>" for q, a in faq_items
    )

    sib = [s for s in sibling_specs if s[3] != fund_slug][:5]
    sib_tiles = "".join(
        f'<a href="/bursaries/{uni_slug}/{s[3]}/"><b>{esc(s[0])}</b></a>' for s in sib
    )
    sib_tiles += (f'<a href="/bursaries/{uni_slug}/"><b>See all {uni_name} bursaries →</b>'
                  '</a>')

    body = (
        crumb_html([
            ("Home", "/"),
            ("Bursaries by university", "/bursaries/"),
            (uni_name, f"/bursaries/{uni_slug}/"),
            (name, None),
        ])
        + f'<h1 class="page">{esc(name)}</h1>'
        + f'<p class="sub">{esc(uni_name)} &middot; {esc(ftype)}</p>'
        + f'<p class="lede">{esc(lede)}</p>'
        + kv_html
        + '<h2>Who can apply</h2>'
        + crit_html
        + match_callout(f"the {name}")
        + '<h2>How to apply</h2>'
        + f'<p>Apply directly to {esc(uni_name)} — BursaSearch doesn\'t process '
          'applications. The official page has the current form and closing date.</p>'
        + (f'<p class="applybtn"><a class="btn" href="{esc(official)}" target="_blank" '
           'rel="noopener">Open the official page →</a></p>' if official else "")
        + '<h2>Common questions</h2>'
        + f'<div class="faq">{faq_html}</div>'
        + f'<h2>Other funds at {esc(uni_name)}</h2>'
        + f'<div class="tiles">{sib_tiles}</div>'
        + related_links_html([row])
        + CTA_STRIP
    )
    schema = (
        jsonld_script(monetary_grant_jsonld(row, uni_name, canonical, description))
        + jsonld_script(json.dumps({
            "@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q,
                 "acceptedAnswer": {"@type": "Answer", "text": a}}
                for q, a in faq_items
            ],
        }))
        + jsonld_script(breadcrumb_jsonld([
            ("BursaSearch", f"{SITE_URL}/"),
            ("Bursaries by university", f"{SITE_URL}/bursaries/"),
            (uni_name, f"{SITE_URL}/bursaries/{uni_slug}/"),
            (name, canonical),
        ]))
    )
    return render_shell(title=esc(title), description=esc(description),
                        canonical=canonical, body=body, sticky=STICKY_BAR, schema=schema)

def tiles_html(items):
    """items = list of (href, title, sub_or_None) → a .tiles grid."""
    out = []
    for href, title, sub in items:
        sub_html = f'<span>{esc(sub)}</span>' if sub else ""
        out.append(f'<a href="{href}"><b>{esc(title)}</b>{sub_html}</a>')
    return f'<div class="tiles">{"".join(out)}</div>'

def render_hub(uni_list, singles_count, circumstance_counts, subject_counts, region_counts):
    n_total = sum(c for _, _, c in uni_list) + singles_count
    n_unis = len(uni_list) + singles_count
    canonical = f"{SITE_URL}/bursaries/"
    title = "UK University Bursaries & Scholarships — Browse by University | BursaSearch"
    description = (
        f"Browse verified bursaries and scholarships at {n_unis} UK universities, "
        f"covering {n_total} funds in total. Free to search."
    )
    lede = (
        f"{n_total} verified bursaries and scholarships across {n_unis} UK universities — "
        "each linking straight to the official source, no forms with us. Pick your "
        "university below, or let the app match you to these plus national and "
        "independent grants."
    )
    uni_items = [(f"/bursaries/{slug}/", name,
                  f"{c} bursar{'y' if c == 1 else 'ies'}") for name, slug, c in uni_list]
    uni_items.append(("/bursaries/more-universities/", "More universities",
                      f"{singles_count} bursaries"))
    body = (
        crumb_html([("Home", "/"), ("Bursaries", None)])
        + '<h1 class="page">UK University Bursaries &amp; Scholarships</h1>'
        + f'<p class="lede">{esc(lede)}</p>'
        + '<h2>Quick links</h2>'
        + tiles_html([
            ("/bursaries/closing-soon/", "Bursaries closing soon", None),
            ("/bursaries/highest-value/", "Highest-value bursaries", None),
        ])
        + '<h2 id="circumstance">Browse by circumstance</h2>'
        + tiles_html([(f"/bursaries/circumstance/{s}/", h1, f"{c} funds")
                      for s, h1, c in circumstance_counts])
        + '<h2 id="subject">Browse by subject</h2>'
        + tiles_html([(f"/bursaries/subject/{s}/", h1, f"{c} funds")
                      for s, h1, c in subject_counts])
        + '<h2 id="region">Browse by region</h2>'
        + tiles_html([(f"/bursaries/region/{s}/", h1, f"{c} funds")
                      for s, h1, c in region_counts])
        + '<h2>Browse by university</h2>'
        + tiles_html(uni_items)
    )
    schema = jsonld_script(breadcrumb_jsonld([
        ("BursaSearch", f"{SITE_URL}/"),
        ("Bursaries", canonical),
    ]))
    return render_shell(title=esc(title), description=esc(description),
                        canonical=canonical, body=body, schema=schema)

def submit_indexnow(urls):
    """Tells Bing/Yandex/Seznam about changed URLs immediately instead of
    waiting for their crawler to notice — free, no auth beyond the public
    key file already hosted at the site root. Only runs against live data
    (SEO_DATA_URL set); a local dev run shouldn't spam this on every tweak."""
    if not urls or not SEO_DATA_URL or os.environ.get("SKIP_INDEXNOW"):
        return
    if len(urls) > 800:
        # A mass regeneration (e.g. a site-wide template change) — don't fire
        # thousands of "instant crawl" pings; the sitemap covers it.
        print(f"IndexNow: skipping bulk submit of {len(urls)} URL(s).")
        return
    payload = json.dumps({
        "host": "bursasearch.com",
        "key": INDEXNOW_KEY,
        "keyLocation": f"{SITE_URL}/{INDEXNOW_KEY}.txt",
        "urlList": urls,
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            "https://api.indexnow.org/indexnow",
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"IndexNow: submitted {len(urls)} URL(s), status {resp.status}")
    except Exception as e:
        # Non-fatal — a failed instant-crawl ping shouldn't fail the build,
        # the pages are already live and in the sitemap regardless.
        print(f"IndexNow submission failed (non-fatal): {e}")

# ── Build ────────────────────────────────────────────────────────────────────
os.makedirs(OUT_DIR, exist_ok=True)
lastmod_map = load_lastmod()
changed_urls = []

# ── Phase 1: slugs + the fund-page URL map, BEFORE any page is rendered, so
#    every listing page's rows can link straight to the fund's own page. ─────
uni_list = [(uni, slugify(uni), len(entries)) for uni, entries in sorted(multi.items())]

# norm_uni_key -> (canonical display name, slug). Also feeds fund_href_for().
canon_by_key = {norm_uni_key(name): (name, slug) for name, slug, _ in uni_list}
CANON_BY_KEY.update(canon_by_key)

# Assign a stable slug to every fund that clears the gate, keyed per
# university; collect specs for phase 3 (rendering the grant pages).
fund_slugs = load_fund_slugs()
fund_specs_by_uni = {}  # uni slug -> [(fund_name, row, uni_name, fund_slug), ...]
for uni, uslug, _ in uni_list:
    entries = multi[uni]
    gated = sorted((r for r in entries if fund_has_page(r)),
                   key=lambda r: clean(r.get("Bursary Name", "")))
    used, specs = set(), []
    # Pinned slugs first so a fresh one can't land on a name a pin will reclaim.
    for r in gated:
        fk = fund_key(uni, clean(r.get("Bursary Name", "")))
        if fk in fund_slugs and fund_slugs[fk] not in used:
            used.add(fund_slugs[fk])
    for r in gated:
        name = clean(r.get("Bursary Name", ""))
        fk = fund_key(uni, name)
        fslug = assign_fund_slug(fk, name, fund_slugs, used)
        used.add(fslug)
        FUND_URLS[fk] = f"/bursaries/{uslug}/{fslug}/"
        specs.append((name, r, uni, fslug))
    if specs:
        fund_specs_by_uni[uslug] = specs

# University × subject viability — a page only where a named university has
# >= UNI_SUBJECT_MIN funds for that subject. Populated before rendering so the
# university pages and the standalone subject pages can cross-link into it.
uni_subject_specs = []  # (uni_name, uni_slug, subj_slug, matched_rows)
for uni, uslug, _ in uni_list:
    entries = multi[uni]
    for sslug, sh1, _snoun, sfilt in SUBJECTS:
        m = [r for r in entries if sfilt(r) and clean(r.get("Bursary Name", ""))]
        if len(m) >= UNI_SUBJECT_MIN:
            UNI_SUBJECT_PAGES.setdefault(uslug, []).append((sslug, sh1, len(m)))
            SUBJECT_UNI_PAGES.setdefault(sslug, []).append((uni, uslug, len(m)))
            uni_subject_specs.append((uni, uslug, sslug, m))

# ── Phase 2: render every listing page. ─────────────────────────────────────
for uni, slug, count in uni_list:
    d = os.path.join(OUT_DIR, slug)
    os.makedirs(d, exist_ok=True)
    url = f"{SITE_URL}/bursaries/{slug}/"
    write_page(url, os.path.join(d, "index.html"), render_page(uni, multi[uni], slug), lastmod_map, changed_urls)

# university × subject pages
uni_subject_urls = []
for uni_name, uslug, sslug, m in uni_subject_specs:
    sd = os.path.join(OUT_DIR, uslug, "subject", sslug)
    os.makedirs(sd, exist_ok=True)
    url = f"{SITE_URL}/bursaries/{uslug}/subject/{sslug}/"
    write_page(url, os.path.join(sd, "index.html"),
               render_uni_subject_page(uni_name, uslug, sslug, m), lastmod_map, changed_urls)
    uni_subject_urls.append(url)

# rollup
d = os.path.join(OUT_DIR, "more-universities")
os.makedirs(d, exist_ok=True)
rollup_url = f"{SITE_URL}/bursaries/more-universities/"
write_page(rollup_url, os.path.join(d, "index.html"), render_rollup(singles), lastmod_map, changed_urls)

# circumstance pages
circumstance_counts = []
for slug, h1, noun_phrase, filt in CIRCUMSTANCES:
    matched = [r for r in rows if filt(r) and clean(r.get("Bursary Name", ""))]
    if not matched:
        continue
    d = os.path.join(OUT_DIR, "circumstance", slug)
    os.makedirs(d, exist_ok=True)
    url = f"{SITE_URL}/bursaries/circumstance/{slug}/"
    write_page(url, os.path.join(d, "index.html"), render_circumstance_page(slug, h1, noun_phrase, matched, canon_by_key), lastmod_map, changed_urls)
    circumstance_counts.append((slug, h1, len(matched)))

# subject pages
subject_counts = []
for slug, h1, noun_phrase, filt in SUBJECTS:
    matched = [r for r in rows if filt(r) and clean(r.get("Bursary Name", ""))]
    if not matched:
        continue
    d = os.path.join(OUT_DIR, "subject", slug)
    os.makedirs(d, exist_ok=True)
    url = f"{SITE_URL}/bursaries/subject/{slug}/"
    write_page(url, os.path.join(d, "index.html"), render_subject_page(slug, h1, noun_phrase, matched, canon_by_key), lastmod_map, changed_urls)
    subject_counts.append((slug, h1, len(matched)))

# region pages
region_counts = []
for slug, h1, noun_phrase in REGIONS:
    needles = REGION_MATCH[slug]
    matched = [
        r for r in rows
        if clean(r.get("Bursary Name", ""))
        and any(n in clean(r.get("UK region", "")).lower() for n in needles)
    ]
    if not matched:
        continue
    d = os.path.join(OUT_DIR, "region", slug)
    os.makedirs(d, exist_ok=True)
    url = f"{SITE_URL}/bursaries/region/{slug}/"
    write_page(url, os.path.join(d, "index.html"), render_region_page(slug, h1, noun_phrase, matched, canon_by_key), lastmod_map, changed_urls)
    region_counts.append((slug, h1, len(matched)))

# ── Closing soon — bursaries with a real, parseable deadline in the next 60
#    days. Genuinely time-sensitive: this list's membership actually changes
#    day to day as deadlines pass and new ones come into range, unlike a
#    blindly-stamped lastmod (see write_page) — real freshness, not faked. ──
today_d = date.today()
closing_matched = [
    r for r in rows
    if clean(r.get("Bursary Name", ""))
    and (lambda d: d is not None and 0 <= (d - today_d).days <= 60)(parse_deadline_date(r.get("Deadline", "")))
]
closing_soon_counts = []
if closing_matched:
    d = os.path.join(OUT_DIR, "closing-soon")
    os.makedirs(d, exist_ok=True)
    url = f"{SITE_URL}/bursaries/closing-soon/"
    page = render_tag_page(
        "closing-soon", "", "Bursaries Closing Soon", "students applying before the deadline",
        closing_matched, "Bursaries", "your deadline and full circumstances", canon_by_key,
        sort_key=lambda r: parse_deadline_date(r.get("Deadline", "")), limit=60,
    )
    write_page(url, os.path.join(d, "index.html"), page, lastmod_map, changed_urls)
    closing_soon_counts.append(("closing-soon", "Bursaries Closing Soon", len(closing_matched)))

# ── Highest value — genuinely useful/shareable ranked content, not just a
#    template filled in per category. ────────────────────────────────────────
valued_matched = [
    r for r in rows
    if clean(r.get("Bursary Name", "")) and max_amount_value(r.get("Amount", "")) > 0
]
highest_value_counts = []
if valued_matched:
    d = os.path.join(OUT_DIR, "highest-value")
    os.makedirs(d, exist_ok=True)
    url = f"{SITE_URL}/bursaries/highest-value/"
    page = render_tag_page(
        "highest-value", "", "Highest-Value UK Bursaries and Scholarships", "students seeking the highest-value awards",
        valued_matched, "Bursaries", "your full circumstances", canon_by_key,
        sort_key=lambda r: -max_amount_value(r.get("Amount", "")), limit=60,
    )
    write_page(url, os.path.join(d, "index.html"), page, lastmod_map, changed_urls)
    highest_value_counts.append(("highest-value", "Highest-Value UK Bursaries and Scholarships", len(valued_matched)))

# ── Phase 3: one page per individual fund that cleared the gate. ────────────
fund_urls = []
for uslug, specs in fund_specs_by_uni.items():
    d = os.path.join(OUT_DIR, uslug)
    for name, r, uni_name, fslug in specs:
        fd = os.path.join(d, fslug)
        os.makedirs(fd, exist_ok=True)
        url = f"{SITE_URL}/bursaries/{uslug}/{fslug}/"
        write_page(url, os.path.join(fd, "index.html"),
                   render_fund_page(r, uni_name, uslug, fslug, specs),
                   lastmod_map, changed_urls)
        fund_urls.append(url)

# Persist the fund slug map so a light rename in the sheet doesn't churn URLs.
with open(FUND_SLUGS_FILE, "w", encoding="utf-8") as f:
    json.dump(fund_slugs, f, indent=0, sort_keys=True)

# hub (/bursaries/) — generated from the shared template
hub_url = f"{SITE_URL}/bursaries/"
write_page(hub_url, os.path.join(OUT_DIR, "index.html"),
           render_hub(uni_list, len(singles), circumstance_counts, subject_counts, region_counts),
           lastmod_map, changed_urls)

# home page (/) — hand-authored index.html at the repo root (the TikTok
# onboarding splash: logo + tagline + direct App Store / Google Play links).
# NOT generated here; it's in the sitemap, so give it a lastmod from its own
# file mtime rather than omitting it or always stamping it "today".
home_url = f"{SITE_URL}/"
if os.path.exists("index.html"):
    lastmod_map.setdefault(home_url, date.fromtimestamp(os.path.getmtime("index.html")).isoformat())

# /get — client-side redirect to the right app store (noindex; kept out of
# the sitemap on purpose).
os.makedirs("get", exist_ok=True)
with open(os.path.join("get", "index.html"), "w", encoding="utf-8") as f:
    f.write(GET_REDIRECT_HTML)

# sitemap — every URL's lastmod comes from lastmod_map (only bumped above
# when that page's content actually changed), not blindly stamped TODAY.
urls = [home_url, hub_url, rollup_url]
urls += [f"{SITE_URL}/bursaries/{slug}/" for _, slug, _ in uni_list]
urls += [f"{SITE_URL}/bursaries/circumstance/{slug}/" for slug, _, _ in circumstance_counts]
urls += [f"{SITE_URL}/bursaries/subject/{slug}/" for slug, _, _ in subject_counts]
urls += [f"{SITE_URL}/bursaries/region/{slug}/" for slug, _, _ in region_counts]
urls += [f"{SITE_URL}/bursaries/closing-soon/" for _ in closing_soon_counts]
urls += [f"{SITE_URL}/bursaries/highest-value/" for _ in highest_value_counts]
urls += uni_subject_urls
urls += fund_urls
sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
for u in urls:
    lm = lastmod_map.get(u, TODAY)
    sitemap += f"  <url><loc>{u}</loc><lastmod>{lm}</lastmod></url>\n"
sitemap += "</urlset>\n"
with open("sitemap.xml", "w", encoding="utf-8") as f:
    f.write(sitemap)

# Persist lastmod_map for next run, pruned to only URLs still in this build
# (so a retired page's stale date doesn't linger forever).
with open(LASTMOD_FILE, "w", encoding="utf-8") as f:
    json.dump({u: lastmod_map[u] for u in urls if u in lastmod_map}, f, indent=0, sort_keys=True)

with open("robots.txt", "w", encoding="utf-8") as f:
    f.write(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")

submit_indexnow(changed_urls)

print(
    f"Built {len(uni_list)} university pages + {len(fund_urls)} fund pages + "
    f"{len(uni_subject_urls)} uni×subject + 1 rollup + "
    f"{len(circumstance_counts)} circumstance + {len(subject_counts)} subject + "
    f"{len(region_counts)} region + {len(closing_soon_counts)} closing-soon + "
    f"{len(highest_value_counts)} highest-value + hub + sitemap "
    f"({len(urls)} URLs total, {len(changed_urls)} changed this run)."
)
