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
# Both stores get a campaign tag on every link we control, so App Store
# Connect (Acquisition -> Campaigns, by ct=) and Play Console (acquisition
# reports, by utm_source) can say which channel each install came from.
# pt = Bursa Group's App Store provider token (App Analytics -> Campaigns ->
# "Generate a Campaign Link"); ct = campaign name, max 30 chars.
APPLE_PROVIDER_TOKEN = "129237255"


def app_store_url(campaign):
    return (f"https://apps.apple.com/app/apple-store/id6795890396"
            f"?pt={APPLE_PROVIDER_TOKEN}&ct={campaign}&mt=8")


def play_url(source, medium="referral", campaign=None):
    return ("https://play.google.com/store/apps/details?id=fresherforgev2.com"
            f"&referrer=utm_source%3D{source}%26utm_medium%3D{medium}"
            f"%26utm_campaign%3D{campaign or source}")


APP_STORE_URL = app_store_url("seo_site")
PLAY_URL = play_url("bursasearch_web", "referral", "seo_site")

# -- 5 page versions (14 Nov 2026, Isaac: "we dont need all the versions most are bad, make 5 different good types
# using same format"). Each visitor gets ONE version (html class pv1..pv5, kept in localStorage bs_v1), picked by a
# tiny inline <head> script before first paint. Same format in all five: the app look, the same ranked content
# (headings, fund lists, FAQs), and a quick check that never shows results and ends on "get your results in the app".
# They differ only in what leads the page. Every /go/ link carries ?a=v3 so the store tag becomes ct=seo_uni-v3:
# Apple/Play downloads by campaign = downloads per version. Events go to Firestore preAuthEvents (platform=web,
# ab=v1..v5). Read with tools/ab_report.py.
# Earlier the same day: 15 independent coin-flip tests (bs_ab1). Their weak parts are gone everywhere (per-row
# 'Check if I qualify', no sticky bar, header copy test); the better bar copy and 'See all N in the app' stay on.
# 14 Nov evening, Isaac: "iterate, make it good, optimize, maybe drop down from 5". ~130 search visitors a week can't
# split five ways (26 each a week), so the five merge into three clear ideas, on university AND fund pages:
AB_VARIANTS = [
    ("quiz", "the quiz straight under the heading, nothing else"),
    ("money", "'What <uni> could pay you' card, then 'How much could you get?' opening on household income"),
    ("preview", "the quiz, then a real screenshot of the app + the deadline reminder it gives you"),
]
AB_N = len(AB_VARIANTS)
AB_HEAD = ("<script>(function(){var N=%d,k='bs_v2',v='',id;try{v=localStorage.getItem(k)||''}catch(e){}"
           "if(!/^[1-9]$/.test(v)||+v>N){v=String(1+Math.floor(Math.random()*N));try{localStorage.setItem(k,v)}catch(e){}}"
           "document.documentElement.className+=' pv'+v;try{id=localStorage.getItem('bs_wid')}catch(e){}"
           "if(!id){id='w'+Math.random().toString(36).slice(2,12);try{localStorage.setItem('bs_wid',id)}catch(e){}}"
           "window.BSAB={a:'v'+v,id:id,hex:'v'+v}})();</script>") % AB_N
# The page markup still carries the old per-block classes (.v1b hero, .v3a/.v3b quiz title, .v7a/.v7b bar copy,
# .v9b row link, .v10a/.v10b fold, .v11b screenshot, .v12b proof line, .v13b deadline, .v14a/.v14b first question,
# .v15a/.v15b header). This maps them onto the five versions.
AB_CSS = """
.v1b,.v3b,.v7a,.v9b,.v10a,.v11b,.v12b,.v13b,.v14b,.v15b{display:none!important}
html.pv2 .v1b{display:block!important}
html.pv2 .v3b{display:inline!important} html.pv2 .v3a{display:none!important}
html.pv2 .v14b{display:block!important} html.pv2 .v14a{display:none!important}
html.pv3 .v11b,html.pv3 .v13b{display:block!important}
.v7b{display:inline!important} .v10b{display:inline} .v10b.fapp{display:block}
.side,.fside{display:flex; flex-direction:column;}
.side .dl .rm,.fside .dl .rm{display:block; margin-top:4px; color:var(--sec); font-size:13px;}
.ans{background:var(--hero); border:1px solid rgba(255,255,255,.06); border-radius:22px 22px 0 0; padding:18px 18px 6px;
  margin:14px 0 0; border-bottom:0;}
html.pv2 .ans + .stats{display:none;}
html.pv2 .side .ans + .chk{border-radius:0 0 22px 22px; border-top:0; padding-top:6px;}
.ans .al{margin:0; font-size:12px; font-weight:600; color:var(--sec); text-transform:uppercase; letter-spacing:.08em;}
.ans .av{margin:4px 0 4px; font-size:38px; font-weight:700; letter-spacing:-.03em; line-height:1.1; color:var(--white);}
.ans .as{margin:0 0 8px; font-size:14px; color:var(--body);}
.dl{font-size:14px; margin:12px 0 0; padding:13px 15px; background:rgba(232,160,32,.10); border:1px solid rgba(232,160,32,.25);
  border-radius:16px; color:var(--body);}
.dl b{color:var(--white);} .dl .dd{font-weight:700; color:var(--amber);}
.shot{margin:16px 0 0; text-decoration:none !important; color:var(--white) !important;}
.shot img{display:block; width:100%; max-width:360px; height:auto; border-radius:22px; border:1px solid var(--line);}
.shot span{display:block; font-weight:600; font-size:14px; margin-top:8px; color:var(--teal);}
.fapp{margin:12px 0 4px; font-weight:600; color:var(--teal) !important;}
.ftr .abo{font-size:12px; color:var(--sec); margin:10px 0 0;}
.chk .hd b{font-size:19px; line-height:1.25;}
.chk .hd b span{font-size:inherit; color:inherit; font-weight:inherit;}
.chk .hd > span{white-space:nowrap; flex:none;}
.stats b{font-size:clamp(17px,4.8vw,22px); overflow-wrap:normal; word-break:normal; hyphens:none;}
.fside{margin:4px 0 22px;}
"""
AB_SCRIPT = '<script src="/assets/ab.js" defer></script>'

# /go/<channel> - one tagged smart link per marketing channel (bio links,
# emails to schools, etc.). Each sends phones to the right store with that
# channel's tag. Add a channel here and it exists after the next rebuild.
GO_CHANNELS = {
    "tiktok": "social",
    "tiktok_promote": "paid",
    "instagram": "social",
    "youtube": "social",
    "reddit": "community",
    "tsr": "community",
    "school": "outreach",
    "email": "outreach",
    # Organic search, split by page type so the stores show which pages install.
    "seo_site": "organic",
    "seo_uni": "organic",
    "seo_fund": "organic",
    "seo_tag": "organic",
}
OG_IMAGE = f"{SITE_URL}/og-image.png"
ICON_LINKS = """<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" type="image/png" sizes="48x48" href="/favicon-48.png">
<link rel="icon" type="image/png" sizes="96x96" href="/favicon-96.png">
<link rel="icon" type="image/png" sizes="192x192" href="/favicon-192.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">"""
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

# Site-wide totals for the stat line under every page's H1. Deliberately NOT
# per-category: the app matches a student across income / region / subject /
# national funds at once, so a single category's count understates the reach.
SITE_FUND_COUNT = sum(len(v) for v in by_uni.values())
SITE_UNI_COUNT = len(by_uni)

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

_CHECKED_RE = re.compile(r'Last checked <b>[^<]*</b>|<b>[^<]*</b>last checked')

_ICON_RE = re.compile(r'<link rel="(?:icon|apple-touch-icon)"[^>]*>\n?')

def _strip_checked(html_text):
    # Favicon tags are site chrome, not page content: adding them must not
    # bump every page's lastmod at once.
    # Same for the stylesheet and the check scripts: a styling change isn't new content.
    return _CHROME_RE.sub('', _ICON_RE.sub('', _CHECKED_RE.sub('', html_text)))

_CHROME_RE = re.compile(r'<style>.*?</style>|<script src="/assets/[a-z]+\.js" defer></script>'
                        r'|<script defer src="https://static\.cloudflareinsights\.com/[^>]*></script>', re.S)

WRITTEN = set()   # every page path written this run: anything else under bursaries/ is a leftover (see sweep below)


def write_page(url, path, content, lastmod_map, changed_urls):
    """Writes a page, records its sitemap lastmod — bumped to TODAY only when
    the content actually changed from the last run, not on every
    regeneration (a sitemap where every URL's lastmod reads 'today'
    regardless of real changes is a known anti-pattern Google's own guidance
    warns can get the whole sitemap's lastmod signal discounted) — and
    appends to changed_urls when it did, for the IndexNow submission below."""
    WRITTEN.add(os.path.normpath(path))
    existing = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    # stat_line()'s "last checked" date changes every run by design; it is
    # not a content change, so it must not bump lastmod (it was faking a
    # daily update on ~400 pages and teaching Google to ignore our lastmod).
    if existing is None or _strip_checked(existing) != _strip_checked(content) or url not in lastmod_map:
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
    if 0 < n < 1:
        # The sheet stores a percentage fee discount as a fraction (0.1 = 10%).
        return f"{n * 100:.0f}% off fees"
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
    nums = [int(n.replace(",", "")) for n in re.findall(r"£?\s?([\d,]{2,7})", clean(v))
            if n.replace(",", "").isdigit()]
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

def _blob(row, *cols):
    return " ".join(clean(row.get(c, "")) for c in cols).lower()

def is_automatic(row):
    """Paid without a separate application (usually via the Student Finance
    income assessment) — only when the data actually says so."""
    b = _blob(row, "Deadline", "Extra Requirement", "AI Notes")
    return "automatic" in b or "no application" in b or "no need to apply" in b

def deadline_line(row):
    """Kept for any caller that still wants the old one-line deadline text."""
    tag = deadline_tag(row)
    return tag

def deadline_tag(row):
    """A small tag when the deadline changes what the student should do:
    'AUTOMATIC', a real future closing date, or 'ROLLING'. Otherwise nothing."""
    if is_automatic(row):
        return '<span class="t g">AUTOMATIC</span>'
    raw = clean(row.get("Deadline", ""))
    if not raw:
        return ""
    d = parse_deadline_date(raw)
    if d:
        if d < date.today():
            return ""
        return f'<span class="t y">CLOSES {d.day} {d.strftime("%b").upper()}</span>'
    if any(h in raw.lower() for h in _ROLLING_HINTS):
        return '<span class="t g">ROLLING</span>'
    return ""

# ── Who-it's-for, in a handful of words, from the data ─────────────────────
CIRC_CODES = [  # (code, short label, needles in the Vulnerabilities cell)
    ("c", "Care-experienced", ("care leaver", "care experienced", "care-experienced")),
    ("e", "Estranged", ("estranged",)),
    ("r", "Carer", ("carer",)),
    ("d", "Disabled", ("disab",)),
    ("f", "Refugee or asylum seeker", ("refugee", "asylum")),
]
# Circumstances the web check doesn't ask about but which are broad enough
# not to rule anyone out (low-participation postcodes, low income, etc.).
_LENIENT_VULN = ("polar", "low participation", "low income", "fsm", "free school meal",
                 "means", "widening", "first gen", "first in", "mature", "pupil premium")

def _vuln_parts(row):
    return [p.strip().lower() for p in clean(row.get("Vulnerabilities (multi-select)", "")).split(",")
            if p.strip()]

def income_cap(row):
    """Household-income ceiling in £ (0 = none stated)."""
    hh = clean(row.get("Household income", ""))
    nums = [int(n.replace(",", "")) for n in re.findall(r"([\d,]{4,7})", hh) if n.replace(",", "").isdigit()]
    if nums:
        return max(nums)
    if hh or any(k in p for p in _vuln_parts(row) for k in ("low income", "fsm", "free school meal")):
        return 25000 if ("low" in hh.lower() or not hh) else 0
    return 0

def _k(n):
    return f"£{n // 1000}k" if n % 1000 == 0 else f"£{n / 1000:.1f}k".replace(".0k", "k")

def level_code(row):
    lvl = clean(row.get("Study level", "")).lower()
    ug, pg = ("undergrad" in lvl or "foundation" in lvl), "postgrad" in lvl
    return "a" if (ug and pg) or not (ug or pg) else ("p" if pg else "u")

def check_attrs(row):
    """(level, income cap, circumstance codes, maybe-flag) for the on-page
    eligibility check. maybe=1 marks funds that depend on something the
    check doesn't ask (course, sport, ethnicity, international fee status):
    they're never greyed out but aren't counted as a match either."""
    parts = _vuln_parts(row)
    circ = "".join(code for code, _, needles in CIRC_CODES if any(n in p for p in parts for n in needles))
    unknown = [p for p in parts
               if not any(n in p for _, _, needles in CIRC_CODES for n in needles)
               and not any(k in p for k in _LENIENT_VULN)]
    fs = clean(row.get("Fee status", "")).lower()
    intl_only = ("overseas" in fs or "international" in fs) and "home" not in fs and "uk" not in fs
    b = _blob(row, "Extra Requirement", "Bursary Name")
    maybe = bool(
        (unknown and not circ) or intl_only or clean(row.get("Study subject", ""))
        or "sport" in b or "athlet" in b or clean(row.get("Ethnicity", "")) or clean(row.get("Gender", ""))
    )
    return level_code(row), income_cap(row), circ, 1 if maybe else 0

def who_line(row):
    """At most two short pieces: 'Income under £43k · first-years'."""
    bits = []
    labels = [lab for code, lab, needles in CIRC_CODES
              if any(n in p for p in _vuln_parts(row) for n in needles)]
    if labels:
        bits.append(" or ".join(labels[:2]) if len(labels) <= 2 else labels[0] + " and others")
    cap = income_cap(row)
    if clean(row.get("Household income", "")) and cap:
        bits.append(f"Income under {_k(cap)}")
    b = _blob(row, "Extra Requirement", "Bursary Name")
    if "sport" in b or "athlet" in b:
        bits.append("Sport")
    subj = clean(row.get("Study subject", ""))
    if subj and len(subj) <= 24:
        bits.append(subj)
    yr = clean(row.get("Course Year", ""))
    if yr in ("1", "1st", "First", "Year 1"):
        bits.append("First-years")
    fs = clean(row.get("Fee status", "")).lower()
    if ("overseas" in fs or "international" in fs) and "home" not in fs:
        bits.append("International students")
    if not bits:
        lc = level_code(row)
        if lc != "a":
            bits.append({"u": "Undergraduates", "p": "Postgraduates"}[lc])
        elif is_automatic(row):
            bits.append("No form needed")
    return " · ".join(bits[:2])

def display_amount(row):
    a = format_amount(row.get("Amount", ""))
    if a:
        return f'<span class="amt">{esc(a if len(a) <= 22 else a[:21] + "…")}</span>'
    if clean(row.get("Household income", "")) or is_automatic(row):
        return '<span class="amt v">Income-based</span>'
    return '<span class="amt v">Varies</span>'

def short_fund_name(name, uni_name=None):
    """'Accommodation Bursary (Manchester)' -> 'Accommodation Bursary' on that
    university's own page, where the bracket is noise."""
    if not uni_name:
        return name
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", name)
    if m and (m.group(2).lower() in uni_name.lower() or m.group(2) == uni_alias(uni_name)):
        return m.group(1)
    return name

def bursary_row(row, uni=None, uni_href=None, fund_href=None, own_uni=None, check=False):
    """One fund as a single compact row: name + amount, then one short line
    (provider on cross-university pages, tags, who it's for). `own_uni` = the
    university whose page this is (shortens the name). `check` = add the data
    attributes the on-page eligibility check reads."""
    name = clean(row.get("Bursary Name", "")) or "Bursary"
    shown = short_fund_name(name, own_uni)
    link = clean(row.get("Application URL", "")) or clean(row.get("Link", ""))
    if fund_href:
        name_html = f'<a class="nm" href="{esc(fund_href)}">{esc(shown)}</a>'
    elif link:
        name_html = f'<a class="nm" href="{esc(link)}" rel="noopener" target="_blank">{esc(shown)}</a>'
    else:
        name_html = f'<span class="nm">{esc(shown)}</span>'
    prov = ""
    if uni:
        prov = (f'<a href="{esc(uni_href)}">{esc(uni)}</a> · ' if uni_href else f'{esc(uni)} · ')
    attrs = ""
    if check:
        l, cap, circ, maybe = check_attrs(row)
        attrs = (f' data-l="{l}" data-i="{cap}" data-c="{circ}" data-x="{maybe}"'
                 f' data-a="{max_amount_value(row.get("Amount", ""))}"')
    rq = ('<a class="rq v9b" href="/go/seo_uni/" rel="nofollow" data-p="row">Check if I qualify &rarr;</a>'
          if check else "")
    return (f'<div class="row"{attrs}>{name_html}{display_amount(row)}'
            f'<span class="m">{deadline_tag(row)}{prov}{esc(who_line(row))}</span>{rq}</div>')

def fold_rows(rows_html, show=4, more_label="Show {n} more"):
    """First `show` rows visible, the rest inside a native <details> (still in
    the HTML, so search engines read them)."""
    head, tail = rows_html[:show], rows_html[show:]
    out = "".join(head)
    if tail:
        lab = esc(more_label.format(n=len(tail)))
        out += (f'<details class="more"><summary><span><span class="v10a">{lab}</span><span class="v10b">{lab} here</span></span>'
                f'</summary>{"".join(tail)}</details>'
                f'<a class="fapp v10b" href="/go/seo_site/" data-p="fold" rel="nofollow">See all {len(rows_html)} in the free app, '
                f'matched to you &rarr;</a>')
    return out


RANGE_AMOUNT_FLOOR = 100  # see amount_range_text

def amount_range_text(entries):
    """Best-effort human summary like '£500–£3,000' from the raw Amount
    strings — floored at RANGE_AMOUNT_FLOOR. A handful of source rows carry a
    stray sub-£100 figure (a per-item top-up, a misparsed cell — genuinely
    live examples: '£20', '£0.90'), and a headline range built from the raw
    min ('worth £20–£21,805') reads as broken data rather than a real offer.
    This range only feeds the meta description / lede headline; the fund's
    own row still shows its real amount untouched."""
    nums = []
    for r in entries:
        for n in re.findall(r"£?\s?([\d,]{2,7})", clean(r.get("Amount", ""))):
            try:
                nums.append(int(n.replace(",", "")))
            except ValueError:
                pass
    nums = [n for n in nums if n >= RANGE_AMOUNT_FLOOR] or nums
    if not nums:
        return None
    lo, hi = min(nums), max(nums)
    if lo == hi:
        return f"£{lo:,}"
    return f"£{lo:,}–£{hi:,}"

# ── Presentation layer ──────────────────────────────────────────────────────
# Light, plain, high-contrast — modelled on GOV.UK / Citizens Advice, because a
# student arriving from Google's white results page should land on something
# that reads like a trustworthy reference, not an app ad. Black top bar with the
# white BursaSearch mark; teal only for actions and links. Numbers over
# sentences, one question at a time, fold anything past the first few rows.
# Type: Public Sans throughout; Sora for the wordmark only.
SITE_CSS = """
*{box-sizing:border-box;}
:root{
  /* the app's own tokens (AppCode/widgets/grants_home_widget.dart) */
  --bg:#091723; --card:#0F2135; --raise:#16293F; --line:#1A3050; --teal:#4EC4C4; --teal-ink:#032621;
  --white:#FFFFFF; --body:#C9D6E0; --sec:#7A9BB5; --mut:#3D5A75; --green:#4ADE80; --amber:#E8A020;
  --hero:linear-gradient(135deg,#0D4B56 0%,#10273D 100%); --maxw:1020px; --r:18px;
  /* old names still used by a few inline bits */
  --ink:#FFFFFF; --soft:#7A9BB5; --hair:#1A3050; --paper:#091723; --panel:#0F2135; --link:#4EC4C4;
}
html{-webkit-text-size-adjust:100%; background:var(--bg);}
body{margin:0; background:var(--bg); color:var(--body);
  font-family:"Sora",system-ui,-apple-system,"Segoe UI",sans-serif; font-size:15.5px; line-height:1.55;}
a{color:var(--teal); text-decoration:none;}
a:hover{text-decoration:underline; text-underline-offset:3px;}
:focus-visible{outline:2px solid var(--teal); outline-offset:2px; border-radius:8px;}
b,strong{color:var(--white);}

.hdr{background:rgba(9,23,35,.92); backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px);
  border-bottom:1px solid var(--line); position:sticky; top:0; z-index:20;}
.hdr .in{max-width:var(--maxw); margin:0 auto; display:flex; align-items:center; gap:10px 22px; padding:12px 16px; flex-wrap:wrap;}
.brand{display:flex; align-items:center; gap:9px; color:var(--white); text-decoration:none !important;
  font-weight:700; font-size:18px; letter-spacing:-.02em;}
.brand span::after{content:"";}
.brand img{width:28px; height:28px; border-radius:8px; display:block;}
.nav{display:flex; gap:6px 18px; flex-wrap:wrap;}
.nav a{color:var(--body); font-size:14px; font-weight:600;}
.hdr .grow{flex:1;}
.hdr .get{background:var(--teal); color:var(--teal-ink) !important; font-weight:700; font-size:14px;
  padding:8px 14px; border-radius:999px; text-decoration:none !important;}

.wrap{max-width:var(--maxw); margin:0 auto; padding:0 16px 64px;}
.crumb{font-size:13px; color:var(--sec); padding:14px 0 0;}
.crumb a{color:var(--sec);}
.crumb .sep{margin:0 6px; color:var(--mut);}
.yr{display:flex; align-items:center; gap:10px; margin:18px 0 8px; font-size:13px; font-weight:600; color:var(--sec);}
.yr img{height:34px; width:auto; max-width:150px; object-fit:contain; background:#fff; border-radius:10px; padding:4px 6px;}
h1.page{color:var(--white); font-weight:700; font-size:clamp(26px,5vw,40px); line-height:1.12;
  letter-spacing:-.025em; margin:0 0 14px; text-wrap:balance; max-width:22ch;}
.lede{font-size:16px; max-width:62ch; margin:0 0 18px; color:var(--body);}
.wrap h2{color:var(--white); font-weight:700; font-size:20px; letter-spacing:-.02em; margin:34px 0 10px;
  display:flex; justify-content:space-between; align-items:baseline; gap:12px;}
.wrap h2 small{font-size:13px; font-weight:600; color:var(--sec);}
.fresh{font-size:12.5px; color:var(--sec); margin:0 0 16px;}

.stats{display:grid; grid-template-columns:repeat(3,1fr); background:var(--card); border:1px solid var(--line);
  border-radius:var(--r); margin:0 0 18px; max-width:620px; overflow:hidden;}
.stats div{padding:14px 12px; min-width:0;}
.stats div + div{border-left:1px solid var(--line);}
.stats b{display:block; font-size:22px; font-weight:700; letter-spacing:-.02em; line-height:1.1; color:var(--white);
  font-variant-numeric:tabular-nums; overflow-wrap:anywhere;}
.stats span{font-size:12.5px; color:var(--sec);}

.chk,.res,.finder{background:var(--hero); border:1px solid rgba(255,255,255,.06); border-radius:22px; padding:18px;
  color:var(--white);}
.chk .hd{display:flex; justify-content:space-between; align-items:baseline; gap:10px; margin:0 0 12px;}
.chk .hd b{font-size:19px; font-weight:700; letter-spacing:-.02em;}
.chk .hd span{font-size:12.5px; color:var(--sec); font-variant-numeric:tabular-nums;}
.prog{display:flex; gap:5px; margin:0 0 16px;}
.prog i{flex:1; height:5px; border-radius:99px; background:rgba(255,255,255,.12);} .prog i.on{background:var(--teal);}
.chk .qq{font-size:16.5px; font-weight:600; margin:0 0 12px; color:var(--white);}
.opt{display:block; width:100%; text-align:left; background:rgba(9,23,35,.55); color:var(--white);
  border:1px solid rgba(255,255,255,.10); border-radius:14px; padding:13px 16px; font:inherit; font-weight:600;
  font-size:15.5px; margin:0 0 8px; cursor:pointer; transition:background .15s, border-color .15s;}
.opt:hover{border-color:var(--teal); background:rgba(78,196,196,.10);}
.opt:active{transform:scale(.99);}
.chk .fine{font-size:12.5px; color:var(--sec); margin:8px 0 0;}
.chk .back{background:none; border:0; padding:0; font:inherit; font-size:14px; color:var(--teal); cursor:pointer; margin-top:6px;}
.res .nums{display:grid; grid-template-columns:1fr 1fr; gap:12px; margin:0 0 14px;}
.res .nums b{display:block; font-size:40px; font-weight:700; letter-spacing:-.03em; line-height:1;}
.res .nums span{display:block; font-size:13.5px; line-height:1.3; margin-top:6px; color:var(--body);}
.wbtn,.gbtn,.abtn{display:block; text-align:center; background:var(--teal); color:var(--teal-ink) !important;
  text-decoration:none !important; font-weight:700; font-size:16px; padding:14px 18px; border-radius:999px; border:0;}
.gbtn,.abtn{display:inline-block;}
.wbtn:hover,.gbtn:hover,.abtn:hover{filter:brightness(1.08); text-decoration:none;}
.res .again{display:block; margin:14px auto 0; background:none; border:0; color:var(--sec); font:inherit; font-size:14px; cursor:pointer;}
.res .small{font-size:12.5px; margin:12px 0 0; text-align:center; color:var(--sec);}
.res .big{font-size:22px; font-weight:700; letter-spacing:-.02em; margin:0 0 8px; line-height:1.2; color:var(--white);}
.res p{margin:0 0 14px; color:var(--body);}
.res .top{margin:0 0 14px; font-size:16px;}
.res .alt{display:block; text-align:center; color:var(--white); font-weight:600; margin:14px 0 0;}

.list,.faq,.tblw,.links,.tiles,.crit,.kv,.fres:not(:empty){background:var(--card); border:1px solid var(--line); border-radius:var(--r);}
.list{padding:2px 16px;}
.list .row{display:grid; grid-template-columns:1fr auto; gap:3px 14px; padding:14px 0; border-bottom:1px solid var(--line);}
.list > .row:last-child, .list > details.more:last-child{border-bottom:0;}
.row .nm{font-weight:600; font-size:15.5px; line-height:1.3; color:var(--white);}
a.nm:hover{color:var(--teal); text-decoration:none;}
.row .amt{font-weight:700; font-size:16px; text-align:right; white-space:nowrap; color:var(--white);
  font-variant-numeric:tabular-nums;}
.row .amt.v{font-weight:500; font-size:13.5px; color:var(--sec);}
.row .m{grid-column:1/-1; font-size:13px; color:var(--sec);}
.row .m a{color:var(--sec);}
.row.no{opacity:.4;}
.t{display:inline-block; font-size:10.5px; font-weight:700; padding:2px 8px; margin-right:6px; border-radius:99px;
  letter-spacing:.04em; vertical-align:1px;}
.t.g{background:rgba(74,222,128,.14); color:var(--green);} .t.y{background:rgba(232,160,32,.16); color:var(--amber);}
.t.k{background:var(--raise); color:var(--white);}
details.more{border-bottom:1px solid var(--line);}
.cmps{margin:30px 0 10px;}
.cmps > h2{margin:0 0 6px;}
.cmps > .ci{margin:0 0 12px; color:var(--sec); font-size:14px;}
details.cmp{background:var(--card); border:1px solid var(--line); border-radius:16px; margin:0 0 10px;}
details.cmp > summary{list-style:none; cursor:pointer; padding:14px 16px; display:flex; align-items:center; gap:10px;}
details.cmp > summary::-webkit-details-marker{display:none;}
details.cmp > summary h3{margin:0; flex:1; font-size:15.5px; font-weight:600; line-height:1.35; color:var(--white);}
details.cmp > summary small{color:var(--sec); font-size:13px; font-weight:500;}
details.cmp > summary::after{content:"+"; font-size:20px; line-height:.9; color:var(--teal);}
details.cmp[open] > summary::after{content:"\\2212";}
details.cmp .cb{padding:0 16px 14px; font-size:14.5px; line-height:1.55;}
details.cmp .cb p{margin:0 0 10px;}
details.cmp .cb ul{margin:0 0 10px; padding-left:18px;}
details.cmp .cb li{margin:0 0 6px;}
details.cmp .cb li span{color:var(--sec);}
details.cmp .cb .go{display:inline-block; margin-top:2px; font-weight:600;}
details.more > summary{list-style:none; cursor:pointer; padding:14px 0; display:flex; justify-content:space-between;
  gap:10px; font-weight:600; color:var(--teal);}
details.more > summary::-webkit-details-marker{display:none;}
details.more > summary::after{content:"+"; font-size:20px; line-height:.9; color:var(--teal);}
details.more[open] > summary::after{content:"\\2212";}
details.more > summary small{margin-left:auto; color:var(--sec); font-weight:500;}
details.more .row:last-child{border-bottom:0;}
.none{padding:12px 0; color:var(--sec); border-bottom:1px solid var(--line);}

.faq{padding:2px 16px;}
.faq details{border-bottom:1px solid var(--line);}
.faq details:last-child{border-bottom:0;}
.faq summary{list-style:none; cursor:pointer; padding:15px 0; font-weight:600; color:var(--white);
  display:flex; justify-content:space-between; gap:12px;}
.faq summary::-webkit-details-marker{display:none;}
.faq summary::after{content:"+"; font-size:20px; line-height:.9; color:var(--teal);}
.faq details[open] summary::after{content:"\\2212";}
.faq p{margin:0 0 16px; max-width:62ch; color:var(--body);}

.cta{background:var(--hero); border:1px solid rgba(255,255,255,.06); border-radius:22px; padding:20px 18px;
  margin:28px 0 0; max-width:620px;}
.cta b{display:block; font-size:19px; font-weight:700; margin:0 0 6px; letter-spacing:-.02em;}
.cta p{margin:0 0 14px; color:var(--body); font-size:14.5px;}

.links,.tiles{display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:0 20px; margin:6px 0 0;
  padding:6px 16px; list-style:none;}
.links li{padding:11px 0; border-bottom:1px solid var(--line); font-size:15px;}
.links li span{color:var(--sec); font-size:13px; margin-left:6px;}
.tiles a{display:block; padding:11px 0; border-bottom:1px solid var(--line); font-size:15px; color:var(--white);}
.tiles a:hover{color:var(--teal); text-decoration:none;}
.tiles a b{font-weight:600;}
.tiles a span{color:var(--sec); font-size:13px; margin-left:6px; display:inline-block;}

h1.page + .sub{font-size:14px; color:var(--sec); margin:-6px 0 16px;}
.kv{display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); margin:0 0 18px; overflow:hidden;}
.kv > div{padding:13px 14px; border-bottom:1px solid var(--line); border-right:1px solid var(--line);}
.kv dt{font-size:12.5px; color:var(--sec);}
.kv dd{margin:2px 0 0; font-weight:700; font-size:16.5px; color:var(--white);}
.crit{margin:6px 0 0; padding:4px 16px; list-style:none;}
.crit li{display:flex; gap:10px; padding:11px 0; border-bottom:1px solid var(--line); color:var(--body);}
.crit li:last-child{border-bottom:0;}
.crit li::before{content:"\\2713"; font-weight:700; color:var(--green);}
.note{font-size:13px; color:var(--sec); margin-top:10px;}
.applybtn{margin:16px 0 0;}
.btn{display:inline-block; background:transparent; color:var(--white) !important; text-decoration:none !important;
  border:1px solid rgba(255,255,255,.2); font-weight:600; padding:11px 18px; border-radius:999px;}
.btn:hover{border-color:var(--teal);}

.ftr{background:#071320; border-top:1px solid var(--line); margin-top:48px;}
.ftr .in{max-width:var(--maxw); margin:0 auto; padding:30px 16px 32px;}
.ftr .cols{display:flex; flex-wrap:wrap; gap:22px 48px;}
.ftr .col b{display:block; font-size:14px; margin-bottom:8px; color:var(--white);}
.ftr .col a{display:block; font-size:14px; color:var(--body); margin-bottom:7px;}
.ftr .fine{font-size:12.5px; color:var(--sec); margin:22px 0 0; padding-top:16px; border-top:1px solid var(--line); max-width:70ch;}

.ctabar{position:fixed; left:10px; right:10px; bottom:10px; z-index:30; display:none; align-items:center; gap:10px;
  padding:10px 10px 10px 16px; margin-bottom:env(safe-area-inset-bottom,0px); background:rgba(15,33,53,.96);
  backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px); border:1px solid var(--line); border-radius:18px;
  box-shadow:0 10px 30px rgba(0,0,0,.4); color:var(--white);}
.ctabar p{margin:0; flex:1; font-size:13.5px; font-weight:600; line-height:1.3;}
.ctabar a{background:var(--teal); color:var(--teal-ink) !important; text-decoration:none !important; font-weight:700;
  font-size:14px; padding:9px 14px; white-space:nowrap; border-radius:999px;}
.ctabar button{background:none; border:0; color:var(--sec); font-size:22px; line-height:1; padding:0 4px; cursor:pointer;}

.finder{margin:4px 0 16px; max-width:620px; position:relative;}
.finder label{display:block; font-weight:700; font-size:17px; margin:0 0 10px; color:var(--white);}
.finder input,.mchk input{width:100%; font:inherit; font-size:16px; padding:13px 16px; border:1px solid rgba(255,255,255,.12);
  border-radius:14px; background:rgba(9,23,35,.6); color:var(--white); -webkit-appearance:none; margin:0 0 8px;}
.finder input::placeholder,.mchk input::placeholder{color:var(--sec);}
.finder input:focus,.mchk input:focus{outline:none; border-color:var(--teal);}
.fres{list-style:none; margin:0; padding:0 14px;}
.fres li{padding:11px 2px; border-bottom:1px solid var(--line); font-size:15px;}
.fres li:last-child{border-bottom:0;}
.fres li a{font-weight:600;}
.fres li span{display:block; font-size:13px; color:var(--sec);}
.fres li.nil{color:var(--sec);}
.tblw{overflow-x:auto; margin:6px 0 4px; padding:4px 14px;}
.tbl{width:100%; border-collapse:collapse; font-size:14.5px;}
.tbl th{text-align:left; font-size:12px; color:var(--sec); font-weight:600; padding:10px 8px 8px 0; border-bottom:1px solid var(--line);
  text-transform:uppercase; letter-spacing:.04em;}
.tbl td{padding:12px 8px 12px 0; border-bottom:1px solid var(--line); vertical-align:top; color:var(--body);}
.tbl tr:last-child td{border-bottom:0;}
.tbl .r{text-align:right; white-space:nowrap; font-weight:700; color:var(--white); font-variant-numeric:tabular-nums; padding-right:0;}
.tbl td.n{width:28px; font-weight:700; color:var(--sec); font-variant-numeric:tabular-nums;}
.tbl td a{font-weight:600; color:var(--white);}
.tbl td a:hover{color:var(--teal);}
.tbl small{display:block; font-size:12.5px; color:var(--sec); margin-top:2px;}
.tbl small a{font-weight:400; color:var(--sec);}
.yn{display:grid; grid-template-columns:1fr 1fr; gap:8px;}
.yn .opt{text-align:center; margin:0 0 8px;}
.fside{max-width:620px; margin:22px 0 0;}
.layout{display:block;}
.layout > .side{margin:0 0 6px;}
.mchk{max-width:620px; margin:0 0 18px;}
.mchk label.qq{display:block;}
.mchk .fres{margin:0 0 10px; background:rgba(9,23,35,.5); border:0;}
.mchk .fres li{padding:0;}
.mchk .fres li.nil{padding:11px 2px;}
.pick{display:block; width:100%; text-align:left; background:none; border:0; padding:12px 2px; font:inherit;
  font-weight:600; color:var(--white); cursor:pointer;}
.pick:hover{color:var(--teal);}
#mchk.res{max-width:620px; margin:0 0 18px;}
.hlink{margin:12px 0 0;}
@media (max-width:760px){
  .nav{display:none;}
  .ctabar{display:flex;}
  body.has-bar{padding-bottom:84px;}
}
@media (min-width:900px){
  .layout{display:grid; grid-template-columns:minmax(0,1fr) 360px; column-gap:44px;
    grid-template-areas:"head side" "body side";}
  .layout > .head{grid-area:head;}
  .layout > .side{grid-area:side; align-self:start; position:sticky; top:76px; margin:18px 0 0;}
  .layout > .body{grid-area:body; min-width:0;}
}
@media (prefers-reduced-motion:reduce){.opt:active{transform:none;} .opt{transition:none;}}
"""

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Sora:wght@400;500;600;700&display=swap">'
)

APPLE_APP_ID = "6795890396"

# Cloudflare Web Analytics (free, no cookies). Paste the site token from
# dash.cloudflare.com -> Analytics & Logs -> Web Analytics -> bursasearch.com.
# Empty = no analytics script on any page.
CF_ANALYTICS_TOKEN = "425457cc8fd34e67a2a3bffa5b92aff0"

def analytics_html():
    if not CF_ANALYTICS_TOKEN:
        return ""
    return ('<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
            f"data-cf-beacon='{{\"token\": \"{CF_ANALYTICS_TOKEN}\"}}'></script>")

NAV_LINKS = [
    ("/bursaries/", "Universities"),
    ("/bursaries/circumstance/care-leavers/", "Care leavers"),
    ("/bursaries/#subject", "Subjects"),
    ("/bursaries/closing-soon/", "Closing soon"),
]

def header_html(go="seo_site"):
    nav = "".join(f'<a href="{h}">{esc(t)}</a>' for h, t in NAV_LINKS)
    return (
        '<header class="hdr"><div class="in">'
        '<a class="brand" href="/"><img src="/app-icon.png" alt="" width="26" height="26">'
        '<span>Bursa<span style="color:#4EC4C4">Search</span></span></a>'
        f'<nav class="nav">{nav}</nav>'
        '<span class="grow"></span>'
        f'<a class="get" href="/go/{go}/" data-p="hdr"><span class="v15a">Get the app</span><span class="v15b">See my grants</span></a>'
        '</div></header>'
    )

def footer_html():
    return (
        '<footer class="ftr"><div class="in"><div class="cols">'
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
        '<div class="col"><b>BursaSearch</b>'
        f'<a href="{APP_STORE_URL}">iPhone app</a>'
        f'<a href="{PLAY_URL}">Android app</a>'
        '<a href="/about/">How we check our data</a>'
        '<a href="https://bursasearchp.github.io/bursasearch-legal/support.html">Support</a></div>'
        '</div>'
        '<p class="fine">Details come from official university and provider pages and can change. '
        'Always check the official page before applying. BursaSearch is not affiliated with any '
        'university; university names and logos belong to their owners. &copy; Bursa Group Ltd.</p>'
        '<p class="abo">We count anonymous page actions (no cookies, no names) to make this site more useful. '
        '<a href="#" id="abopt">Turn this off</a></p>'
        '</div></footer>'
    )

def sticky_bar(go="seo_site", text="Find every bursary you qualify for", alt="See the grants you could get, free"):
    return (
        '<div class="ctabar" id="ctabar">'
        f'<p id="ctatext"><span class="v7a">{esc(text)}</span><span class="v7b">{esc(alt)}</span></p>'
        f'<a href="/go/{go}/" data-p="bar">Open app</a>'
        '<button type="button" aria-label="Close" onclick="try{localStorage.setItem(\'bs_cta_x\',\'1\')}catch(e){}'
        'document.getElementById(\'ctabar\').style.display=\'none\';document.body.classList.remove(\'has-bar\')">&times;</button>'
        '<script>try{if(localStorage.getItem(\'bs_cta_x\')){document.getElementById(\'ctabar\').style.display=\'none\';'
        'document.body.classList.remove(\'has-bar\')}}catch(e){}</script>'
        '</div>'
    )

STICKY_BAR = sticky_bar()

def redirect_html(ios_url, android_url):
    return (
        '<!doctype html><html lang="en"><head><meta charset="UTF-8">'
        '<meta name="robots" content="noindex"><title>Get the BursaSearch app</title>'
        f'<meta http-equiv="refresh" content="0;url={ios_url}">'
        f'<script>var a={json.dumps(ios_url)},p={json.dumps(android_url)},'
        'q=(location.search.match(/[?&]a=([0-9a-f]{1,4}|v[1-9])(&|$)/)||[])[1];'
        'if(q){a=a.replace(/ct=([^&]+)/,function(m,c){return "ct="+c+"-"+q});'
        'p=p.replace(/utm_campaign%3D([^&%]+)/,function(m,c){return "utm_campaign%3D"+c+"-"+q})}'
        'location.replace(/android/i.test(navigator.userAgent||"")?p:a);</script>'
        f'</head><body>Opening the app&hellip; <a href="{ios_url}">App Store</a> '
        f'&middot; <a href="{android_url}">Google Play</a></body></html>'
    )


GET_REDIRECT_HTML = redirect_html(APP_STORE_URL, PLAY_URL)

def jsonld_script(obj_json):
    return f'<script type="application/ld+json">{obj_json}</script>'

def stat_line():
    """One quiet freshness line. The date changes every run by design and is
    stripped before the lastmod comparison (see _strip_checked)."""
    d = date.today()
    return (f'<p class="fresh">{SITE_FUND_COUNT:,} UK funds tracked &middot; '
            f'Last checked <b>{d.day} {d.strftime("%b %Y")}</b></p>')

def app_cta(lead=None, go="seo_site"):
    """The one app panel on pages without the eligibility check."""
    what = f"Check if you qualify for {lead}" if lead else "See which of these you qualify for"
    return (
        '<aside class="cta">'
        f'<b>{esc(what)}</b>'
        '<p>The free app matches you to every UK bursary, including national and charity grants, '
        'and reminds you before deadlines.</p>'
        f'<a class="gbtn" href="/go/{go}/">Get the free app</a>'
        '</aside>'
    )

def render_shell(*, title, description, canonical, body, hero="", sticky="", schema="", scripts="", go="seo_site",
                 og_image=None):
    """The one page template for the whole site. `title`/`description` arrive
    already escaped by the caller (same as the old PAGE_TEMPLATE contract)."""
    if 'id="mchk"' in body and "match.js" not in scripts:
        scripts += MATCH_SCRIPT
    if ('id="chk"' in body or 'id="fchk"' in body) and "check.js" not in scripts:
        scripts += CHECK_SCRIPT
    return f"""<!doctype html>
<html lang="en" data-go="{go}">
<head>
<meta charset="UTF-8">
{AB_HEAD}
<title>{title}</title>
<meta name="description" content="{description}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#091723">
<meta name="apple-itunes-app" content="app-id={APPLE_APP_ID}">
<link rel="canonical" href="{canonical}">
{ICON_LINKS}
{FONTS}
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="website">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{og_image or OG_IMAGE}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{og_image or OG_IMAGE}">
{schema}
<style>{SITE_CSS}{AB_CSS}</style>
</head>
<body class="{'has-bar' if sticky else ''}">
{header_html(go)}
{hero}
<main class="wrap">
{body}
</main>
{footer_html()}
{sticky}
{scripts}
{AB_SCRIPT}
{analytics_html()}
</body>
</html>
"""


def faq_items_generic(uni_name, scope_phrase="this university"):
    return [
        ("Is this list free to use?",
         f"Yes. Every bursary listed here for {uni_name} is free to browse — no account or payment needed to see what's available."),
        ("Do I apply through BursaSearch?",
         "No — every listing links directly to the official university or provider page, and you apply there."),
        ("How do I know which ones I actually qualify for?",
         f"This page lists what's publicly available for {scope_phrase}. The free BursaSearch app checks your circumstances against every UK bursary, including national and independent funds, and tells you which ones you qualify for and why."),
    ]

def faq_html_from(items):
    return "".join(
        f"<details><summary><span>{esc(q)}</span></summary><p>{esc(a)}</p></details>"
        for q, a in items
    )

def faq_jsonld_from(items):
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in items
        ],
    })

def faq_block(uni_name, count, scope_phrase="this university"):
    return faq_html_from(faq_items_generic(uni_name, scope_phrase))

def faq_jsonld(uni_name, scope_phrase="this university"):
    return faq_jsonld_from(faq_items_generic(uni_name, scope_phrase))

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
    return '<nav class="crumb">' + '<span class="sep">›</span>'.join(parts) + '</nav>'

_ROW_RE = re.compile(r'<div class="row".*?</span></div>', re.S)

def content_body(*, trail, h1, lede, context_phrase, count, rows_html, faq_html, related_html, go="seo_tag",
                 pre_html="", list_title="Funds"):
    """Shared body of the listing pages (rollup / circumstance / subject /
    region / closing-soon / highest-value / uni×subject)."""
    rows = _ROW_RE.findall(rows_html)
    lede = lede.split(" — each linking")[0].rstrip(". ") + "."
    return (
        crumb_html(trail)
        + f'<p class="yr">{academic_year()}</p>'
        + f'<h1 class="page">{esc(h1)}</h1>'
        + f'<p class="lede">{esc(lede)}</p>'
        + stat_line()
        + ("" if ('id="mchk"' in pre_html or 'id="chk"' in pre_html) else match_box(go, title="Which grants could you get?"))
        + pre_html
        + f'<h2>{esc(list_title)} <small>{len(rows)}</small></h2>'
        + f'<div class="list">{fold_rows(rows, show=8)}</div>'
        + app_cta(go=go)
        + '<h2>Questions</h2>'
        + f'<div class="faq">{faq_html}</div>'
        + related_html
    )

def related_links_html(entries, exclude=None):
    """Cross-links a page out to the circumstance/subject/region pages its
    own bursaries actually qualify for — used on university pages (nothing
    to exclude) and on the tag pages themselves (excluding their own family
    member), so these clusters connect to each other instead of only being
    reachable from the hub."""
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

# ── University page ─────────────────────────────────────────────────────────
HARDSHIP_RE = re.compile(r"hardship|emergency|crisis|cost of living|access to learning|"
                         r"support fund|financial assistance|success fund|discretionary fund", re.I)
_NHS_LSF_RE = re.compile(r"learning support fund", re.I)   # NHS course grant, not a hardship fund

def is_hardship(row):
    name = clean(row.get("Bursary Name", ""))
    return bool(HARDSHIP_RE.search(name)) and not _NHS_LSF_RE.search(name)

def uni_short(uni_name):
    """'University of Manchester' -> 'Manchester'; alias wins when we have one."""
    alias = uni_alias(uni_name)
    if alias:
        return alias
    s = re.sub(r"^(the\s+)?university\s+of\s+(the\s+)?", "", uni_name, flags=re.I)
    s = re.sub(r"\s+university.*$", "", s, flags=re.I)
    s = re.sub(r",.*$", "", s)
    return s.strip() or uni_name

_NOT_FLAGSHIP = ("accommodation", "sport", "abroad", "placement", "alumni", "care", "estranged",
                 "refugee", "sanctuary", "music", "master", "phd", "foundation year", "nhs")

def flagship_fund(entries, uni_name):
    """The university's main income-based undergraduate bursary, when the data
    makes it identifiable ('The Manchester Bursary'). None otherwise."""
    short = re.sub(r"^(the\s+)?university\s+of\s+(the\s+)?", "", uni_name, flags=re.I)
    short = re.sub(r"\s+university.*$", "", short, flags=re.I).lower()
    best, best_score = None, 0
    for r in entries:
        name = clean(r.get("Bursary Name", "")).lower()
        if level_code(r) == "p" or is_hardship(r) or any(w in name for w in _NOT_FLAGSHIP) or re.search(r"society|trust|foundation|charit|association", name):
            continue
        score = 0
        if "bursary" in name and short.split(" ")[0] in name:
            score += 3
        if is_automatic(r):
            score += 2
        if clean(r.get("Household income", "")):
            score += 1
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= 3 else None

_NOT_UNI_OWN = re.compile(r"allowance|dsa|nhs|childcare grant|dependants|learning support fund|"
                          r"social work bursar|trust|foundation|society|commission|daad|lpdp|"
                          r"leverhulme|postgrad solutions", re.I)

def counts_for_top_award(row):
    """The headline 'top award' should be the university's own money that a UK
    student could get — not a government allowance (DSA, Childcare Grant), an
    outside charity listed on the uni's page, or an international-only award."""
    if _NOT_UNI_OWN.search(clean(row.get("Bursary Name", ""))):
        return False
    fs = clean(row.get("Fee status", "")).lower()
    return not (("overseas" in fs or "international" in fs) and "home" not in fs and "uk" not in fs)

def uni_logo(slug, uni_name):
    if os.path.exists(os.path.join("logos", f"{slug}.png")):
        return f'<img src="/logos/{slug}.png" alt="{esc(uni_name)} logo" height="32">'
    return ""

CHECK_Q1 = (
    '<div class="hd"><b><span class="v3a">Which __WHO__ grants could you get?</span><span class="v3b">How much could you get?</span></b>'
    '<span>1 of 3</span></div>'
    '<div class="prog"><i class="on"></i><i></i><i></i></div>'
    '<div class="v14a"><p class="qq">What will you study?</p>'
    '<button type="button" class="opt" data-v="u">Undergraduate degree</button>'
    '<button type="button" class="opt" data-v="p">Master&#x27;s or PhD</button></div>'
    '<div class="v14b"><p class="qq">What is your household income?</p>'
    '<button type="button" class="opt" data-v="0">Under £25,000</button>'
    '<button type="button" class="opt" data-v="25000">£25,000 to £43,000</button>'
    '<button type="button" class="opt" data-v="43000">Over £43,000</button>'
    '<button type="button" class="opt" data-v="-1">Not sure</button></div>'
    '<p class="fine">3 quick questions. Your matches open in the free app.</p>'
)

def check_box(go, who):
    return (f'<div id="chk" class="chk" data-go="{go}" data-uni="{esc(who)}">{CHECK_Q1.replace("__WHO__", esc(who))}</div>')

CHECK_SCRIPT = '<script src="/assets/check.js" defer></script>'

def uni_faq_items(uni_name, flag, ug, hard, pg, entries):
    items = []
    if flag is not None:
        fname = clean(flag.get("Bursary Name", ""))
        if is_automatic(flag):
            items.append((f"Do I need to apply for the {fname}?",
                          f"No. The {fname} is paid automatically, based on the household income "
                          "you give Student Finance. Check the official page for the income bands."))
    priced = [r for r in ug if max_amount_value(r.get("Amount", "")) >= 100]
    if priced:
        top = max(priced, key=lambda r: max_amount_value(r.get("Amount", "")))
        who = who_line(top)
        items.append((f"What is the biggest undergraduate bursary at {uni_name}?",
                      f"The {clean(top.get('Bursary Name', ''))}, worth {format_amount(top.get('Amount', ''))}"
                      + (f" ({who.lower()})." if who and who != "Undergraduates" else ".")))
    if hard:
        names = [clean(r.get("Bursary Name", "")) for r in hard[:3]]
        items.append((f"Does {uni_name} have a hardship fund?",
                      f"Yes. {uni_name} lists " + (names[0] if len(names) == 1 else
                                                   ", ".join(names[:-1]) + " and " + names[-1])
                      + ". These help if you run short of money during your course."))
    care = [r for r in entries if any(c in check_attrs(r)[2] for c in "ce")]
    if care:
        bits = []
        for r in care[:3]:
            a = format_amount(r.get("Amount", ""))
            bits.append(clean(r.get("Bursary Name", "")) + (f" ({a})" if a else ""))
        items.append((f"What does {uni_name} offer care leavers and estranged students?",
                      "; ".join(bits) + "."))
    if pg:
        names = [clean(r.get("Bursary Name", "")) for r in pg[:2]]
        items.append((f"Is there funding for master's and PhD students at {uni_name}?",
                      f"Yes, {len(pg)} postgraduate funds, including " + " and ".join(names) + "."))
    if not items:
        items = faq_items_generic(uni_name)[2:]
    return items

def uni_title(uni_name, alias_bit, flag, ug, top_ug, count, slug_for_title=""):
    """Searchers name the main bursary ('uon core bursary'), so lead with it when
    we can identify it. The 'up to' figure is the biggest undergraduate award -
    a PhD studentship figure misleads someone searching '<uni> bursary'. Long
    names fall back to the short form so the £ figure is never the part cut."""
    yr = academic_year()
    short = re.sub(r"^(the\s+)?university\s+of\s+(the\s+)?", "", uni_name, flags=re.I).strip()
    bases = [f"{uni_name}{alias_bit} Bursaries {yr}", f"{uni_name} Bursaries {yr}", f"{short}{alias_bit} Bursaries {yr}",
             f"{short} Bursaries {yr}"]
    upto = f" up to £{top_ug:,}" if top_ug >= 100 else ""
    tails = []
    if flag is not None:
        fname = short_fund_name(clean(flag.get("Bursary Name", "")), uni_name)
        famt = format_amount(flag.get("Amount", ""))
        more = f" + {count - 1} more" if count > 1 else ""
        if famt and "£" in famt and len(famt) <= 14:
            tails.append(f": {fname} {famt}{more}")
        elif upto:
            tails.append(f": {fname} +{count - 1} more,{upto}")
    tails.append(f": {count} grant{'s' if count != 1 else ''}{upto}")
    # 15 Nov split test (title_arm): "a" = the all-in-one-place promise the descriptions make,
    # "b" = the LJMU style (6.2% CTR on 14 Nov vs 0.3-0.6% for '+ N More' titles). No '+ N More'.
    sh = (uni_alias(uni_name) or short) if len(short) > 14 else short
    if is_external(uni_name):
        return f"UK Charity & National Grants {yr}: {count:,} You Can Apply For"
    if count > 1:
        arm = title_arm(f"/bursaries/{slug_for_title}/") if slug_for_title else "a"
        if arm == "a":
            cands = [f"{sh} Bursary {yr}: All {count} Grants, See What You Get",
                     f"{sh} Bursary {yr}: All {count} Grants, See Yours",
                     f"{sh} Bursary: All {count} Grants, See What You Get"]
        else:
            cands = ([f"{sh} Bursary & Grants {yr}: {count} Grants{upto}, Check Yours",
                      f"{sh} Bursary {yr}: {count} Grants{upto}, Check Yours"] if upto else []) +                     [f"{sh} Bursary {yr}: {count} Grants, Check Yours"]
        for t in cands:
            if len(t) <= 66:
                return t
    for tail in tails:
        for base in bases:
            if len(base + tail) <= 75:
                return base + tail
    return bases[-1] + tails[-1]


# ── Folded info sections (14 Nov 2026, Isaac: "add the large amount of info in compartments to get position and
#    range, and keep it out of the way"). Native <details>: closed by default, fully in the HTML, so Google reads
#    and counts it. Every section is built from that page's own data (or checked official facts), never the same
#    paragraph pasted across pages. Headings carry the search combinations: '<uni> scholarships',
#    '<uni> hardship fund', '<uni> grants for care leavers', 'scholarships for disabled students'...
def cmp_block(heading, inner, count=None):
    c = f"<small>{count}</small>" if count is not None else ""
    return (f'<details class="cmp"><summary><h3>{esc(heading)}</h3>{c}</summary>'
            f'<div class="cb">{inner}</div></details>')


def cmp_section(title, blocks, intro=""):
    blocks = [b for b in blocks if b]
    if not blocks:
        return ""
    return (f'<section class="cmps"><h2>{esc(title)}</h2>'
            + (f'<p class="ci">{esc(intro)}</p>' if intro else "") + "".join(blocks) + "</section>")


def by_amount(rs):
    return sorted(rs, key=lambda r: (-max_amount_value(r.get("Amount", "")), clean(r.get("Bursary Name", ""))))


def fund_li(r, own_uni=None, canon_by_key=None):
    name = clean(r.get("Bursary Name", "")) or "Bursary"
    shown = short_fund_name(name, own_uni)
    href = fund_href_for(r, own_uni)
    link = clean(r.get("Application URL", "")) or clean(r.get("Link", ""))
    if href:
        a = f'<a href="{esc(href)}">{esc(shown)}</a>'
    elif link:
        a = f'<a href="{esc(link)}" rel="noopener" target="_blank">{esc(shown)}</a>'
    else:
        a = esc(shown)
    bits = []
    amt = format_amount(r.get("Amount", ""))
    if amt and len(amt) <= 26:
        bits.append(amt)
    if canon_by_key is not None:
        res_ = canon_by_key.get(norm_uni_key(clean(r.get("University", ""))))
        if res_:
            bits.append(res_[0])
    w = who_line(r)
    if w:
        bits.append(w)
    return f'<li>{a}' + (f' <span>&middot; {esc(" · ".join(bits))}</span>' if bits else "") + "</li>"


def fund_ul(rs, own_uni=None, canon_by_key=None, limit=10):
    rs = list(rs)
    if canon_by_key is not None:      # cross-university lists: one row per fund ('X (UCL)' = 'X (City)')
        seen, keep = set(), []
        for r in rs:
            k = re.sub(r"\s*\([^)]*\)\s*$", "", clean(r.get("Bursary Name", ""))).lower()
            if k not in seen:
                seen.add(k)
                keep.append(r)
        rs = keep
    out = "<ul>" + "".join(fund_li(r, own_uni, canon_by_key) for r in rs[:limit]) + "</ul>"
    if len(rs) > limit:
        out += f"<p>Plus {len(rs) - limit} more.</p>"
    return out


def _plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


_SPORT_RE = re.compile(r"sport|athlet", re.I)
_ARTS_RE = re.compile(r"music|choral|choir|organ|drama|dance|art\b|arts\b|perform|theatre|creative", re.I)
_EXCEL_RE = re.compile(r"excellence|merit|academic|achiev|vice[- ]chancellor|chancellor's|principal's|high[- ]flyer|"
                       r"outstanding|elite|talent", re.I)
_STEM_RE = re.compile(r"engineer|science|scientist|math|physic|chemi|computing|computer|stem\b|technolog|data", re.I)

# short labels for 'grants for X' headings (CIRCUMSTANCES slug -> phrase)
CIRC_SHORT = {
    "low-income-students": "low-income students", "care-leavers": "care leavers",
    "international-students": "international students", "estranged-students": "estranged students",
    "disabled-students": "disabled students", "refugees-and-asylum-seekers": "refugees and asylum seekers",
    "student-carers": "student carers", "young-carers": "young carers",
    "low-participation-areas": "students from low-participation areas", "part-time-students": "part-time students",
    "ethnic-minority-students": "Black and minority ethnic students", "women-scholarships": "women",
}

# Official help, checked against GOV.UK / NHSBSA / SAAS / SFW / SFNI on 14 Nov 2026. No amounts: they change every
# year and depend on income. key -> (name, where, who, what + how, url)
GOV_INFO = {
    "childcare": ("Childcare Grant", "England",
                  "Full-time higher education students with children under 15, or under 17 with special educational "
                  "needs, in registered or approved childcare.",
                  "Pays up to 85% of your childcare costs, up to a weekly cap that depends on household income and how "
                  "many children you have. Paid on top of your student finance and never paid back. Apply through "
                  "Student Finance England.", "https://www.gov.uk/childcare-grant"),
    "pla": ("Parents' Learning Allowance", "England", "Full-time students with dependent children.",
            "Helps with course costs like books and travel. The amount depends on household income. It isn't paid "
            "back, comes on top of your student finance and doesn't affect your benefits. Apply through Student "
            "Finance England.", "https://www.gov.uk/parents-learning-allowance"),
    "adg": ("Adult Dependants' Grant", "England",
            "Full-time students with an adult, usually a partner, who depends on them financially.",
            "Not paid back and paid on top of your student finance, though it can affect income-related benefits. "
            "Apply through Student Finance England.", "https://www.gov.uk/adult-dependants-grant"),
    "dsa": ("Disabled Students' Allowance", "UK",
            "Undergraduates and postgraduates with a disability, long-term illness, mental health condition or a "
            "specific learning difficulty such as dyslexia.",
            "Pays the study costs your condition causes: specialist equipment, non-medical helpers like note takers, "
            "and taxis if you can't use public transport. Based on your needs, not household income, and never paid "
            "back. Apply through your student finance body; a needs assessment decides what you get.",
            "https://www.gov.uk/disabled-students-allowance-dsa"),
    "nhs": ("NHS Learning Support Fund", "England",
            "Students on eligible nursing, midwifery and allied health courses, such as physiotherapy, radiography "
            "and paramedic science.",
            "A yearly training grant you don't pay back, extra money if you have children, help with placement "
            "travel and accommodation, and an exceptional support fund if you hit hardship. Apply through the NHS "
            "Business Services Authority.", "https://www.nhsbsa.nhs.uk/nhs-learning-support-fund-lsf"),
    "swb": ("Social Work Bursary", "England", "Social work students in England, undergraduate and postgraduate.",
            "A bursary you don't pay back. Undergraduate places are limited and your university nominates who gets "
            "one. Run by the NHS Business Services Authority.", "https://www.nhsbsa.nhs.uk/social-work-bursary-swb"),
    "teach": ("Teacher training bursaries and scholarships", "England",
              "Trainee teachers on postgraduate teacher training in shortage subjects such as maths, physics, "
              "chemistry and computing.",
              "Tax-free money you don't pay back, set each year by subject. The current subject list is on Get Into "
              "Teaching.", "https://getintoteaching.education.gov.uk/funding-and-support/bursaries"),
    "wales": ("Welsh Government Learning Grant", "Wales",
              "Students from Wales on full-time or part-time undergraduate courses.",
              "A grant towards living costs that you don't pay back. The amount depends on household income. Apply "
              "through Student Finance Wales.", "https://www.studentfinancewales.co.uk/"),
    "scot": ("Young Students' Bursary", "Scotland",
             "Scottish students under 25 when their course starts, from lower-income households.",
             "Not paid back; the amount depends on household income. Students 25 or over, or who support themselves, "
             "can get the Independent Students' Bursary instead. Apply through SAAS.",
             "https://www.saas.gov.uk/full-time/support-for-scottish-students"),
    "scotcare": ("Care Experienced Students' Bursary", "Scotland", "Care-experienced Scottish students.",
                 "A bursary for living costs in place of a student loan, so you don't build up debt for them. Apply "
                 "through SAAS.", "https://www.saas.gov.uk/full-time/care-experienced-students"),
    "ni": ("Maintenance Grant", "Northern Ireland", "Students from Northern Ireland from lower-income households.",
           "Not paid back; the amount depends on household income. Apply through Student Finance NI.",
           "https://www.studentfinanceni.co.uk/"),
    "careleaver": ("Higher education bursary for care leavers", "England", "Care leavers in England starting a degree.",
                   "Your local council pays a £2,000 bursary on top of anything your university gives. Ask your "
                   "personal adviser. Propel lists what every university offers care leavers.",
                   "https://becomecharity.org.uk/get-support/propel/"),
    "estranged": ("Independent student status", "UK",
                  "Students estranged from their parents.",
                  "Student finance can assess you as independent, so your parents' income isn't counted. That usually "
                  "means the biggest loan and the biggest income-based bursaries. Stand Alone explains the evidence "
                  "you need.", "https://www.standalone.org.uk/"),
}
GOV_FOR_CIRC = {
    "low-income-students": ["wales", "scot", "ni"],
    "care-leavers": ["careleaver", "scotcare"],
    "estranged-students": ["estranged"],
    "disabled-students": ["dsa"],
    "part-time-students": ["wales", "dsa"],
}


def gov_block(key):
    n, where, who, what, url = GOV_INFO[key]
    return cmp_block(f"{n} ({where})", f"<p><b>Who:</b> {esc(who)}</p><p>{esc(what)}</p>"
                     f'<p><a class="go" href="{esc(url)}" rel="noopener" target="_blank">Official page &rarr;</a></p>')


def uni_compartments(uni_name, slug, named, hard, pg):
    """Folded sections for a university page, all from that uni's own funds."""
    sh = uni_short(uni_name)
    yr = academic_year()
    nonh = [r for r in named if not is_hardship(r)]
    sch = by_amount([r for r in nonh if infer_fund_type(r) == "Scholarship" and level_code(r) != "p"])
    grants = by_amount([r for r in nonh if infer_fund_type(r) != "Scholarship" and level_code(r) != "p"])
    blocks = []
    if grants:
        auto = [r for r in grants if is_automatic(r)]
        caps = [income_cap(r) for r in grants if income_cap(r)]
        t = f"{uni_name} has {_plural(len(grants), 'grant or bursary', 'grants and bursaries')} for undergraduates in {yr}."
        if auto:
            t += (f" {_plural(len(auto), 'is', 'are')} paid automatically, with no form to fill in: "
                  + ", ".join(short_fund_name(clean(r.get('Bursary Name', '')), uni_name) for r in auto[:3]) + ".")
            t = t.replace(f" {len(auto)} is ", f" {len(auto)} is ", 1)
        if caps:
            t += f" {_plural(len(caps), 'depends', 'depend')} on household income, for families earning up to £{max(caps):,}."
        blocks.append(cmp_block(f"{sh} grants and bursaries {yr}", f"<p>{esc(t)}</p>" + fund_ul(grants, uni_name),
                                len(grants)))
    if sch:
        sport = [r for r in sch if _SPORT_RE.search(_blob(r, "Bursary Name", "Extra Requirement"))]
        arts = [r for r in sch if _ARTS_RE.search(clean(r.get("Bursary Name", "")))]
        extra = []
        if sport:
            extra.append(f"{len(sport)} for sport")
        if arts:
            extra.append(f"{len(arts)} for music or the arts")
        t = (f"{uni_name} offers {_plural(len(sch), 'scholarship', 'scholarships')} for undergraduates"
             + (f", including {' and '.join(extra)}" if extra else "") + ".")
        blocks.append(cmp_block(f"{sh} scholarships", f"<p>{esc(t)}</p>" + fund_ul(sch, uni_name), len(sch)))
    if hard:
        t = (f"{uni_name} has {_plural(len(hard), 'hardship fund', 'hardship funds')} for students who run short of "
             f"money during their course. You apply to the university's student support or money advice team, and "
             f"they decide based on your circumstances.")
        more = (f'<p><a class="go" href="/bursaries/{slug}/hardship-funds/">{esc(sh)} hardship funds: who can apply '
                f'and how &rarr;</a></p>' if slug in HARDSHIP_PAGES else "")
        blocks.append(cmp_block(f"{sh} hardship fund", f"<p>{esc(t)}</p>" + fund_ul(by_amount(hard), uni_name) + more,
                                len(hard)))
    for cslug, _h1, _noun, fn in CIRCUMSTANCES:
        m = by_amount([r for r in named if fn(r)])
        if not m:
            continue
        lab = CIRC_SHORT.get(cslug, _noun)
        noun = "scholarships" if cslug == "women-scholarships" else "grants and scholarships"
        t = f"{_plural(len(m), 'fund', 'funds')} at {uni_name} {'is' if len(m) == 1 else 'are'} open to {lab}."
        blocks.append(cmp_block(f"{sh} {noun} for {lab}",
                                f"<p>{esc(t)}</p>" + fund_ul(m, uni_name, limit=8)
                                + f'<p><a class="go" href="/bursaries/circumstance/{cslug}/">{esc(noun.capitalize())} for '
                                  f'{esc(lab)} at every UK university &rarr;</a></p>', len(m)))
    dated = sorted([(d, r) for r in named for d in [parse_deadline_date(r.get("Deadline", ""))]
                    if d and d >= date.today()], key=lambda t: t[0])
    if dated:
        lis = "".join(f'<li><b>{d.day} {d.strftime("%b %Y")}</b> <span>&middot; </span>'
                      f'{esc(short_fund_name(clean(r.get("Bursary Name", "")), uni_name))}</li>' for d, r in dated[:12])
        blocks.append(cmp_block(f"{sh} bursary and scholarship deadlines {yr}",
                                f"<p>The next {sh} closing dates, soonest first. The free BursaSearch app reminds you "
                                f"before each one.</p><ul>{lis}</ul>", len(dated)))
    if pg:
        blocks.append(cmp_block(f"{sh} postgraduate scholarships and funding",
                                f"<p>{esc(_plural(len(pg), 'fund', 'funds'))} at {esc(uni_name)} for master's and PhD "
                                f"students.</p>" + fund_ul(by_amount(pg), uni_name), len(pg)))
    return cmp_section(f"{sh} grants, scholarships and hardship funds, by type", blocks,
                       "Tap a section to open it.")


def circ_compartments(cslug, rows_matched, canon_by_key):
    lab = CIRC_SHORT.get(cslug, cslug.replace("-", " "))
    own = [r for r in rows_matched if not clean(r.get("University", "")).lower().startswith("external")]
    nonh = [r for r in own if not is_hardship(r)]
    sch = by_amount([r for r in nonh if infer_fund_type(r) == "Scholarship" and level_code(r) != "p"])
    gr = by_amount([r for r in nonh if infer_fund_type(r) != "Scholarship" and level_code(r) != "p"])
    hard = by_amount([r for r in own if is_hardship(r)])
    pg = by_amount([r for r in nonh if level_code(r) == "p"])
    blocks = [gov_block(k) for k in GOV_FOR_CIRC.get(cslug, [])]
    for heading, rs in ((f"University grants and bursaries for {lab}", gr), (f"Scholarships for {lab}", sch),
                        (f"Hardship funds for {lab}", hard), (f"Postgraduate funding for {lab}", pg)):
        if rs:
            n_u = len({clean(r.get("University", "")) for r in rs})
            blocks.append(cmp_block(heading, f"<p>{esc(_plural(len(rs), 'fund', 'funds'))} at "
                                             f"{esc(_plural(n_u, 'university', 'universities'))}, biggest first.</p>"
                                    + fund_ul(rs, canon_by_key=canon_by_key, limit=10), len(rs)))
    return cmp_section(f"Grants, scholarships and hardship funds for {lab}", blocks,
                       "Government help and university money, by type. Tap a section to open it.")


def scholarship_groups(rs):
    ug = [r for r in rs if level_code(r) != "p" and not _is_international(r)]
    return [
        ("Biggest undergraduate scholarships", ug),
        ("Sports scholarships", [r for r in rs if _SPORT_RE.search(_blob(r, "Bursary Name", "Extra Requirement"))]),
        ("Music, art and drama scholarships", [r for r in rs if _ARTS_RE.search(clean(r.get("Bursary Name", "")))]),
        ("Academic excellence scholarships", [r for r in rs if _EXCEL_RE.search(clean(r.get("Bursary Name", "")))]),
        ("STEM scholarships: science, engineering, maths and computing",
         [r for r in rs if _STEM_RE.search(_blob(r, "Bursary Name", "Study subject"))]),
        ("Scholarships for women", [r for r in rs if _is_women_scholarship(r)]),
        ("Scholarships for care leavers and estranged students",
         [r for r in rs if vuln_has(r, "care leaver", "care experienced", "care-experienced", "estranged")]),
        ("Scholarships for disabled students", [r for r in rs if vuln_has(r, "disab")]),
        ("Scholarships for international students", [r for r in rs if _is_international(r)]),
        ("Master's and PhD scholarships", [r for r in rs if level_code(r) == "p"]),
    ]


def render_scholarships(canon_by_key):
    """'university scholarships uk', 'sports scholarships uk', 'scholarships for care leavers'...: every scholarship
    in the data, folded by kind, with the quick check on top."""
    yr = academic_year()
    own = [r for r in rows if clean(r.get("Bursary Name", "")) and not is_hardship(r)
           and not clean(r.get("University", "")).lower().startswith("external")
           and infer_fund_type(r) == "Scholarship"]
    own = by_amount(own)
    n_unis = len({clean(r.get("University", "")) for r in own})
    blocks = []
    for heading, rs in scholarship_groups(own):
        if rs:
            n_u = len({clean(r.get("University", "")) for r in rs})
            blocks.append(cmp_block(heading, f"<p>{esc(_plural(len(rs), 'scholarship', 'scholarships'))} at "
                                             f"{esc(_plural(n_u, 'university', 'universities'))}, biggest first.</p>"
                                    + fund_ul(rs, canon_by_key=canon_by_key, limit=12), len(rs)))
    lede = (f"{len(own):,} scholarships at {n_unis} UK universities for {yr}, money you never pay back. Scholarships "
            f"usually reward grades, a talent like sport or music, or a subject. Most need an application, so the ones "
            f"you don't apply for, you don't get.")
    faq = [
        ("What's the difference between a scholarship and a bursary?",
         "A bursary is usually based on need, like household income, and is often paid automatically. A scholarship "
         "usually rewards grades, a talent or a subject and you normally apply for it. Neither is paid back."),
        ("Do you have to pay a scholarship back?", "No. Scholarships are not loans."),
        ("Can you get a scholarship and a bursary at the same time?",
         "Often yes. Many universities let you hold an income-based bursary and a scholarship together. Each fund's "
         "own rules say whether it can be combined."),
        ("How do I find which scholarships I can get?",
         "Answer the quick check on this page, then the free BursaSearch app matches you to the scholarships and "
         "grants you qualify for and reminds you before they close."),
    ]
    h1 = f"University scholarships UK {yr}"
    body = (crumb_html([("Home", "/"), ("Bursaries", "/bursaries/"), ("Scholarships", None)])
            + f'<p class="yr">{yr}</p>'
            + f'<h1 class="page">{esc(h1)}</h1>'
            + f'<p class="lede">{esc(lede)}</p>'
            + match_box("seo_tag", title="Which scholarships could you get?")
            + cmp_section("Scholarships by type", blocks, "Tap a section to open it.")
            + '<h2>More ways to get money for uni</h2>'
            + tiles_html([("/bursaries/grants/", "University grants: every kind, and who gets them", None),
                          ("/bursaries/biggest-bursaries/", "Which universities give the biggest bursaries", None),
                          ("/bursaries/hardship-funds/", "Hardship funds at every university", None),
                          ("/bursaries/", "Grants and scholarships at every university", None)])
            + '<h2>Questions</h2>' + f'<div class="faq">{faq_html_from(faq)}</div>')
    canonical = f"{SITE_URL}/bursaries/scholarships/"
    schema = (jsonld_script(faq_jsonld_from(faq)) + jsonld_script(breadcrumb_jsonld(
        [("BursaSearch", f"{SITE_URL}/"), ("Bursaries", f"{SITE_URL}/bursaries/"), ("Scholarships", canonical)])))
    top = max_amount_value(own[0].get("Amount", "")) if own else 0
    title = f"University Scholarships UK {yr}: {len(own):,} You Can Apply For"
    description = clip_desc(f"All {len(own):,} UK university scholarships for {yr} in one place" + (f", up to £{top:,}" if top >= 1000 else "")
                            + ": sport, music, grades, STEM. " + ELIG_CTA)
    if description.endswith("…"):
        description = clip_desc(f"All {len(own):,} UK university scholarships for {yr} in one place"
                                + (f", up to £{top:,}" if top >= 1000 else "") + ". " + ELIG_CTA)
    return canonical, render_shell(title=esc(title), description=esc(description), canonical=canonical, body=body,
                                   sticky=sticky_bar("seo_tag"), schema=schema, go="seo_tag")


import hashlib as _hl

# 15 Nov: CTR split test. Each page gets title style "a" or "b" from a fixed hash of
# its URL path, so the same page always keeps its style and half the site runs each.
# Compare the two groups in Search Console (tools/title_test_report.py). Written to
# title_test.json on every build.
TITLE_TEST = {}


def title_arm(path):
    arm = "a" if int(_hl.md5(path.encode()).hexdigest(), 16) % 2 == 0 else "b"
    TITLE_TEST[path] = arm
    return arm


def is_external(uni_name):
    u = uni_name.lower()
    return "external" in u or "national bodies" in u


def first_fit(*cands, n=158):
    """First candidate that fits a snippet; else the last one clipped."""
    for c in cands:
        if c and len(c) <= n:
            return c
    return clip_desc(cands[-1], n)


def all_n(n, noun):
    """'All 47 UCL grants' - but '2 medicine grants', never 'All 2'."""
    return f"All {n:,} {noun}" if n >= 3 else f"{n} {noun}"


ELIG_CTA = "See exactly what you're eligible for, instantly. Free."
FUND_CTA = " See instantly if you're eligible. Free."


def _the(n):
    return n if n.lower().startswith("the ") else f"The {n}"


def clip_desc(t, n=158):
    return t if len(t) <= n else t[:n - 1].rsplit(" ", 1)[0].rstrip(",.:;") + "…"


def render_page(uni_name, entries, slug):
    count = len(entries)
    amt_range = amount_range_text(entries)
    amt_bit = f" worth {amt_range}" if amt_range else ""
    alias = uni_alias(uni_name)
    alias_bit = f" ({alias})" if alias and alias.lower() != uni_name.lower() else ""
    canonical = f"{SITE_URL}/bursaries/{slug}/"
    _flag0 = flagship_fund([r for r in entries if clean(r.get("Bursary Name", ""))], uni_name)
    _fn = short_fund_name(clean(_flag0.get("Bursary Name", "")), uni_name) if _flag0 is not None else ""
    _famt0 = format_amount(_flag0.get("Amount", "")) if _flag0 is not None else ""
    _famt0 = _famt0 if "\u00a3" in _famt0 and len(_famt0) <= 18 else ""
    _famt0 = re.sub(r"^Up to ", "up to ", _famt0)
    _us = uni_short(uni_name)
    if _us.lower().startswith("the "):          # 'The Open' -> 'Open University'
        _us = re.sub(r"^the\s+", "", uni_name, flags=re.I)
    _top = max((max_amount_value(r.get("Amount", "")) for r in entries if counts_for_top_award(r)), default=0)
    _up = f", up to £{_top:,}" if _top >= 100 else ""
    if is_external(uni_name):
        description = first_fit(
            f"{all_n(count, 'grants from UK charities and national bodies')} in one place{_up}, never paid back. " + ELIG_CTA,
            f"{all_n(count, 'UK charity grants')} in one place{_up}. " + ELIG_CTA)
    elif count >= 3:
        description = first_fit(
            f"The {_us} bursary plus all {count - 1} other {_us} grants and scholarships in one place{_up}. " + ELIG_CTA,
            f"The {_us} bursary plus all {count - 1} other {_us} grants in one place{_up}, never paid back. " + ELIG_CTA,
            f"The {_us} bursary plus all {count - 1} other {_us} grants in one place{_up}. " + ELIG_CTA,
            f"{_us} bursary plus all {count - 1} other grants in one place. " + ELIG_CTA)
    else:
        description = first_fit(
            f"{_us} bursaries and grants: {count} in one place{_up}, never paid back. " + ELIG_CTA,
            f"{_us} bursaries and grants: {count} in one place. " + ELIG_CTA)

    named = [r for r in entries if clean(r.get("Bursary Name", ""))]
    flag = flagship_fund(named, uni_name)
    hard = sorted([r for r in named if is_hardship(r)], key=lambda r: clean(r.get("Bursary Name", "")))
    pg = sorted([r for r in named if not is_hardship(r) and level_code(r) == "p"],
                key=lambda r: (-max_amount_value(r.get("Amount", "")), clean(r.get("Bursary Name", ""))))
    ug = sorted([r for r in named if not is_hardship(r) and level_code(r) != "p"],
                key=lambda r: (r is not flag, not is_automatic(r), check_attrs(r)[3],
                               -max_amount_value(r.get("Amount", "")), clean(r.get("Bursary Name", ""))))

    def rows(rs):
        return [bursary_row(r, fund_href=fund_href_for(r, uni_name), own_uni=uni_name, check=True) for r in rs]

    def group(key, heading, rs, show):
        if not rs:
            return ""
        return (f'<section class="grp" data-g="{key}"><h2>{esc(heading)} <small>{len(rs)}</small></h2>'
                f'<div class="list">{fold_rows(rows(rs), show=show)}</div></section>')

    # Three numbers instead of a paragraph.
    top_ug = max((max_amount_value(r.get("Amount", "")) for r in ug if counts_for_top_award(r)), default=0)
    title = uni_title(uni_name, alias_bit, flag, ug, top_ug, count, slug_for_title=slug)
    stats = []
    stats.append((f"£{top_ug:,}", "top award") if top_ug >= 100 else (f"{count}", "funds listed"))
    stats.append((f"{len(ug)}", "for undergrads") if ug else (f"{len(pg)}", "postgrad funds"))
    if flag is not None and is_automatic(flag):
        stats.append(("Auto", "main bursary"))
    elif hard:
        stats.append((f"{len(hard)}", "hardship fund" + ("" if len(hard) == 1 else "s")))
    elif pg and ug:
        stats.append((f"{len(pg)}", "postgrad funds"))
    stats_html = '<div class="stats">' + "".join(
        f"<div><b>{esc(v)}</b><span>{esc(l)}</span></div>" for v, l in stats) + "</div>"
    og_url = None
    if HAVE_PIL:
        headline = f"Up to £{top_ug:,}" if top_ug >= 100 else f"{count} bursaries & scholarships"
        sub = (f"{len(ug)} funds for undergraduates · {academic_year()}" if ug
               else f"{count} funds · {academic_year()}")
        OG_JOBS[slug] = (uni_name, headline, sub)
        og_url = f"{SITE_URL}/og/{slug}.png"

    subj_pages = UNI_SUBJECT_PAGES.get(slug, [])
    subj_block = ""
    if subj_pages:
        subj_block = f'<h2>By subject at {esc(uni_name)}</h2>' + tiles_html([
            (f"/bursaries/{slug}/subject/{s}/", f"{SUBJECT_LABEL.get(s, s)} bursaries", f"{c}")
            for s, _, c in sorted(subj_pages, key=lambda t: -t[2])
        ])

    faq_items = uni_faq_items(uni_name, flag, ug, hard, pg, named)
    sh = uni_short(uni_name)
    # t1: answer the search in money first, like the TikTok winners ("how much does YOUR uni give you")
    hero = ""
    famt = format_amount(flag.get("Amount", "")) if flag is not None else ""
    if flag is not None and famt and "\u00a3" in famt:
        fname = short_fund_name(clean(flag.get("Bursary Name", "")), uni_name)
        bits = [fname]
        if is_automatic(flag):
            bits.append("paid automatically")
        cap = income_cap(flag)
        if cap:
            bits.append(f"household income up to \u00a3{cap:,}")
        more = f", plus {len(ug) - 1} more {esc(sh)} funds" if len(ug) > 1 else ""
        hero = f'<p class="av">{esc(famt)}</p><p class="as">{esc(" · ".join(bits))}{more}</p>'
    elif top_ug >= 100:
        hero = (f'<p class="av">Up to \u00a3{top_ug:,}</p><p class="as">{len(ug)} {esc(sh)} funds for undergraduates, '
                f'plus national grants</p>')
    hero_html = (f'<div class="ans v1b"><p class="al">What {esc(sh)} could pay you</p>{hero}'
                 f'<p class="as" style="color:#4EC4C4;margin:0">Answer 3 questions to see what you\u2019d get &darr;</p></div>'
                 if hero else "")
    nxt = []
    for r in ug + hard:
        d = parse_deadline_date(r.get("Deadline", ""))
        if d and d >= date.today():
            nxt.append((d, r))
    nxt.sort(key=lambda t: t[0])
    dline = ""
    if nxt:
        d, r = nxt[0]
        dline = (f'<p class="dl v13b" data-d="{d.isoformat()}">Next {esc(sh)} deadline: '
                 f'<b>{esc(short_fund_name(clean(r.get("Bursary Name", "")), uni_name))}</b>, {d.day} {d.strftime("%b %Y")}'
                 f'<span class="rm">The free app reminds you before it closes.</span></p>')
    if not dline:   # version 4 on a page with no dated deadline: the reminder promise without inventing a date
        dline = (f'<p class="dl v13b"><b>Don’t miss a {esc(sh)} deadline</b>'
                 f'<span class="rm">The free app tracks every grant you match and reminds you before each one closes.</span></p>')
    side_extra = ('<a class="shot v11b" href="/go/seo_uni/" data-p="shot"><img src="/assets/app-track.webp" '
                    'width="320" height="274" loading="lazy" alt="The BursaSearch app tracking a grant: to start, applied, claimed, with a deadline reminder">'
                    '<span>In the app: every grant you match, tracked from apply to claimed &rarr;</span></a>' + dline)
    head = (
        crumb_html([("Home", "/"), ("Universities", "/bursaries/"), (uni_name, None)])
        + f'<p class="yr">{uni_logo(slug, uni_name)}<span>{academic_year()}</span></p>'
        + f'<h1 class="page">{esc(uni_name)} Bursaries, Grants &amp; Scholarships</h1>'
    )
    body_html = (
        stats_html
        + group("ug", "For undergraduates", ug, 4)
        + group("h", "Hardship funds", hard, 2)
        + (f'<p class="hlink"><a href="/bursaries/{slug}/hardship-funds/">Hardship funds at '
           f'{esc(uni_short(uni_name))}: who can apply and how →</a></p>' if slug in HARDSHIP_PAGES else "")
        + group("pg", "Master's and PhD funding", pg, 2)
        + uni_compartments(uni_name, slug, named, hard, pg)
        + '<h2>Questions</h2>'
        + f'<div class="faq">{faq_html_from(faq_items)}</div>'
        + subj_block + related_links_html(entries)
        + stat_line()
    )
    body = (f'<div class="layout"><div class="head">{head}</div>'
            f'<div class="side">{hero_html}{check_box("seo_uni", uni_short(uni_name))}{side_extra}</div>'
            f'<div class="body">{body_html}</div></div>')
    schema = jsonld_script(faq_jsonld_from(faq_items)) + jsonld_script(breadcrumb_jsonld([
        ("BursaSearch", f"{SITE_URL}/"),
        ("Bursaries by university", f"{SITE_URL}/bursaries/"),
        (uni_name, canonical),
    ]))
    return render_shell(title=esc(title), description=esc(description), canonical=canonical,
                        body=body, sticky=sticky_bar("seo_uni", alt=f"See which {sh} grants you could get, free"),
                        schema=schema, scripts=CHECK_SCRIPT, go="seo_uni", og_image=og_url)

CHECK_JS = r"""(function(){
var NAT=__NAT__;
function gateBox(el,uni){var go=el.getAttribute('data-go')||'seo_site';el.className='res';
 el.innerHTML='<p class="big">Done. Get your grant results in the app</p>'
  +'<p>The free BursaSearch app checks your answers against all '+(window.BS_FUNDS||'4,000+')+' UK funds: '+(uni?esc(uni)+'’s own, ':'your uni’s, ')
  +'national grants and charities. It shows exactly which you’d get, how much, and when each one closes.</p>'
  +'<a class="wbtn" href="/go/'+esc(go)+'/'+(window.BSAB?'?a='+window.BSAB.hex:'')+'" data-p="gate">Get my results in the free app</a>'
  +'<p class="small">Takes about 2 minutes. The app asks a few more questions so the matches are exact.</p>'
  +'<button type="button" class="again">Change answers</button>';
 var bar=document.getElementById('ctatext');if(bar)bar.textContent='Get your results free in the app';
 try{window.bsLog&&window.bsLog('web_check_done',{gate:1})}catch(_){}}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
var fb=document.getElementById('fchk');
if(fb){var FQ=JSON.parse(fb.getAttribute('data-q')),fs=0,fok=true;
 var fask=function(){var q=FQ[fs],h='<div class="hd"><b>'+esc(fb.getAttribute(V(2)?'data-tb':'data-ta')||'Could you get this?')+'</b><span>'+(fs+1)+' of '+FQ.length+'</span></div><div class="prog">';
  for(var i=0;i<FQ.length;i++)h+='<i'+(i<=fs?' class="on"':'')+'></i>';
  h+='</div><p class="qq">'+esc(q[0])+'</p><div class="yn"><button type="button" class="opt" data-v="y">Yes</button><button type="button" class="opt" data-v="n">No</button></div>';
  if(q[1])h+='<button type="button" class="opt" data-v="u">Not sure</button>';
  fb.className='chk';fb.innerHTML=h};
 var fres=function(){gateBox(fb,fb.getAttribute('data-uni'));return;var go=fb.getAttribute('data-go'),off=fb.getAttribute('data-off'),n=+fb.getAttribute('data-n'),u=fb.getAttribute('data-uni');fb.className='res';
  fb.innerHTML=(fok?'<p class="big">You may be eligible</p><p>Check the full rules on the official page, then apply there. People who fit this usually qualify for other funds too.</p>'
    +'<a class="wbtn" href="/go/'+esc(go)+'/">Find my other funds in the free app</a>'
   :'<p class="big">Probably not this one</p><p>'+(n?n+' other funds at '+esc(u)+', plus national grants, may still fit you.':'National and charity grants may still fit you.')+'</p>'
    +'<a class="wbtn" href="/go/'+esc(go)+'/">See which fit in the free app</a>')
   +'<p class="small">A quick estimate only. The app checks your course, region, fee status and more, so its matches are far more accurate.</p><button type="button" class="again">Change answers</button>'};
 fb.addEventListener('click',function(e){var t=e.target.closest('button');if(!t)return;
  if(t.classList.contains('opt')){if(t.getAttribute('data-v')==='n')fok=false;fs++;if(fs<FQ.length)fask();else fres()}
  else if(t.classList.contains('again')){fs=0;fok=true;fask()}});
}
var box=document.getElementById('chk'); if(!box) return;
var go=box.getAttribute('data-go')||'seo_site', uni=box.getAttribute('data-uni')||'University';
var Q=[
 {k:'l',q:'What will you study?',o:[['u','Undergraduate degree'],['p',"Master's or PhD"]]},
 {k:'i',q:'What is your household income?',o:[['0','Under £25,000'],['25000','£25,000 to £43,000'],['43000','Over £43,000'],['-1','Not sure']]},
 {k:'c',q:'Do any of these apply to you?',o:[['c','Care-experienced'],['e','Estranged from my family'],['r',"I'm a carer"],['d','Disabled or long-term condition'],['f','Refugee or asylum seeker'],['n','None of these']]}
];
function T(n){return document.documentElement.classList.contains('t'+n)}
function V(n){return document.documentElement.classList.contains('pv'+n)}
if(V(5))Q=[Q[1],Q[0],Q[2]];
var started=false;function L(e,x){try{window.bsLog&&window.bsLog(e,x)}catch(_){}}
var A={},step=0,groups=[].slice.call(document.querySelectorAll('.grp[data-g]')),orig=groups.map(function(g){return g.innerHTML});
var bar=document.getElementById('ctatext'),barOrig=bar?bar.textContent:'';
function fits(l,cap,circ){if(l!=='a'&&l!==A.l)return false;var lo=+A.i;if(cap>0&&lo>=0&&lo>=cap)return false;if(circ&&circ.indexOf(A.c)<0)return false;return true}
function ask(){var q=Q[step],h='<div class="hd"><b>'+(V(5)?'How much could you get?':'Which could you get?')+'</b><span>'+(step+1)+' of '+Q.length+'</span></div><div class="prog">';
 for(var i=0;i<Q.length;i++)h+='<i'+(i<=step?' class="on"':'')+'></i>';
 h+='</div><p class="qq">'+esc(q.q)+'</p>';
 q.o.forEach(function(o){h+='<button type="button" class="opt" data-v="'+esc(o[0])+'">'+esc(o[1])+'</button>'});
 h+=step?'<button type="button" class="back">Back</button>':'<p class="fine">3 quick questions. Your matches open in the free app.</p>';
 box.className='chk';box.innerHTML=h}
box.addEventListener('click',function(e){var t=e.target.closest('button');if(!t)return;
 if(t.classList.contains('opt')){if(!started){started=true;L('web_check_start')}A[Q[step].k]=t.getAttribute('data-v');step++;if(step<Q.length)ask();else result()}
 else if(t.classList.contains('back')){step=Math.max(0,step-1);ask()}
 else if(t.classList.contains('again')){A={};step=0;started=false;groups.forEach(function(g,i){g.innerHTML=orig[i]});if(bar)bar.textContent=barOrig;ask()}});
function result(){gateBox(box,uni);if(window.innerWidth<900)box.scrollIntoView({behavior:'smooth',block:'start'});return;
 var here=0,money=0,names=[];
 groups.forEach(function(g,gi){if(g.getAttribute('data-g')==='h')return;g.innerHTML=orig[gi];
  var rows=[].slice.call(g.querySelectorAll('.row[data-l]')),yes=[],no=[];
  rows.forEach(function(r){var ok=fits(r.getAttribute('data-l'),+r.getAttribute('data-i'),r.getAttribute('data-c'));
   if(ok&&r.getAttribute('data-x')!=='1'){here++;money+=+(r.getAttribute('data-a')||0);
    var nm=r.querySelector('.nm'),am=r.querySelector('.amt');names.push([nm?nm.textContent:'',am?am.textContent:''])}
   (ok?yes:no).push(r)});
  var list=g.querySelector('.list');list.innerHTML='';
  yes.forEach(function(r){list.appendChild(r)});
  if(!yes.length){var p=document.createElement('p');p.className='none';p.textContent='None of these match your answers.';list.appendChild(p)}
  if(no.length){var d=document.createElement('details');d.className='more';d.innerHTML='<summary><span>'+no.length+(no.length===1?' doesn\'t':' don\'t')+' match your answers</span></summary>';
   no.forEach(function(r){r.classList.add('no');d.appendChild(r)});list.appendChild(d)}
  var sm=g.querySelector('h2 small');if(sm)sm.textContent=yes.length+' of '+rows.length});
 var nat=0;NAT.forEach(function(n){if(fits(n[0],n[1],n[2]))nat++});
 // the new arms count only this uni's funds: the web data can't narrow national charity funds the way the app's
 // matcher does, so a national number here would overpromise ("80k total is scammy", Isaac on the app)
 var tot=here,ms=money>=10000?'£10,000+':'£'+money.toLocaleString('en-GB');
 var h='';
 if(T(4)&&money>=100)h+='<p class="mbig">'+ms+'</p><p class="mlab">in '+esc(uni)+' grants you could apply for ('+here+' fund'+(here===1?'':'s')+'), plus national grants the app checks</p>';
 else h+='<div class="nums"><div><b>'+here+'</b><span>'+esc(uni)+' funds you may get</span></div>'
  +(nat?'<div><b>+'+nat+'</b><span>national &amp; charity grants worth checking</span></div>':'')+'</div>';
 if(T(5)&&tot>=2){h+='<ul class="lk">';names.slice(0,2).forEach(function(x){h+='<li><b>'+esc(x[0])+'</b><b>'+esc(x[1])+'</b></li>'});
  var lockN=tot-Math.min(2,names.length);for(var i=0;i<Math.min(3,lockN);i++)h+='<li class="z"><span>Locked grant name here</span><span>£X,XXX</span></li>';
  h+='<li style="border:0"><a class="more" href="/go/'+esc(go)+'/" data-p="lock">+'+lockN+' more '+esc(uni)+' match'+(lockN===1?'':'es')+' and your national grants: free in the app</a></li></ul>'}
 h+='<a class="wbtn" href="/go/'+esc(go)+'/" data-p="res">'+(T(6)?(tot?'See my '+tot+' '+esc(uni)+' match'+(tot===1?'':'es')+' + national grants free':'See my national grants free'):'Get accurate matches in the app')+'</a>'
  +'<p class="small">A quick estimate only. The app checks your course, region, fee status and more, so its matches are far more accurate.</p>'
  +'<button type="button" class="again">Change answers</button>';
 box.className='res';box.innerHTML=h;
 [].forEach.call(box.querySelectorAll('a[href^="/go/"]'),function(a){if(window.BSAB)a.setAttribute('href',a.getAttribute('href')+'?a='+window.BSAB.hex)});
 L('web_check_done',{n:here,nat:nat,m:money});
 if(bar)bar.textContent=T(7)?(tot?'Your '+tot+' '+uni+' match'+(tot===1?' is':'es are')+' waiting in the app':'Your national grant matches are in the app'):(here+' '+uni+' funds'+(nat?' + '+nat+' national grants':'')+' to check');
 if(window.innerWidth<900)box.scrollIntoView({behavior:'smooth',block:'start'})}
})();
"""

AB_JS = r"""(function(){
var B=window.BSAB||{a:'',id:'w0',hex:'0000'},go=document.documentElement.getAttribute('data-go')||'seo_site';
var off=false;try{off=localStorage.getItem('bs_noab')==='1'}catch(e){}
if(navigator.globalPrivacyControl||navigator.doNotTrack==='1')off=true;
if(/bot|crawl|spider|slurp|lighthouse|headless|preview|facebookexternalhit/i.test(navigator.userAgent||''))off=true;
var t0=Date.now(),maxs=0,r=document.referrer||'';
var ref=!r?'direct':/google\./.test(r)?'google':/bing\.|duckduckgo|yahoo|ecosia/.test(r)?'search':/bursasearch\.com/.test(r)?'site':'other';
var U='https://firestore.googleapis.com/v1/projects/fresher-forge-v2-cmw4ev/databases/(default)/documents/preAuthEvents';
function log(ev,x){if(off)return;var f={event:{stringValue:ev},device:{stringValue:B.id},platform:{stringValue:'web'},
 ts:{timestampValue:new Date().toISOString()},ab:{stringValue:B.a},pg:{stringValue:go},path:{stringValue:location.pathname.slice(0,120)},ref:{stringValue:ref}};
 if(x)for(var k in x){var v=x[k];f[k]=typeof v==='number'?{integerValue:String(Math.round(v))}:{stringValue:String(v).slice(0,80)}}
 try{fetch(U,{method:'POST',keepalive:true,headers:{'Content-Type':'application/json'},body:JSON.stringify({fields:f})})}catch(e){}}
window.bsLog=log;
function tag(a){var h=a.getAttribute('href')||'';if(h.indexOf('/go/')!==0||h.indexOf('?a=')>0)return;
 var m=h.match(/^\/go\/([^\/?#]+)/),ch=m?m[1]:go;if(ch==='seo_site'&&a.hasAttribute('data-p'))ch=go;a.setAttribute('href','/go/'+ch+'/?a='+B.hex)}
[].forEach.call(document.querySelectorAll('a[href^="/go/"]'),tag);
document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a');if(!a)return;var h=a.getAttribute('href')||'';
 if(h.indexOf('/go/')===0){tag(a);log('web_tap',{place:a.getAttribute('data-p')||'link',ms:Date.now()-t0})}
 else if(/apps\.apple|play\.google/.test(a.href))log('web_tap',{place:'store_direct',ms:Date.now()-t0})},true);
[].forEach.call(document.querySelectorAll('[data-d]'),function(el){var n=Math.ceil((new Date(el.getAttribute('data-d')+'T23:59:00')-Date.now())/864e5);
 if(n>=0){var s=document.createElement('span');s.className='dd';s.textContent=' · '+n+' day'+(n===1?'':'s')+' left';var rm=el.querySelector('.rm');rm?el.insertBefore(s,rm):el.appendChild(s)}});
addEventListener('scroll',function(){var h=document.documentElement,p=(h.scrollTop+innerHeight)/h.scrollHeight;if(p>maxs)maxs=p},{passive:true});
log('web_view');
var gone=false;function bye(){if(gone)return;gone=true;log('web_leave',{ms:Date.now()-t0,scroll:Math.round(Math.min(maxs,1)*100)})}
document.addEventListener('visibilitychange',function(){if(document.visibilityState==='hidden')bye()});addEventListener('pagehide',bye);
var o=document.getElementById('abopt');if(o)o.addEventListener('click',function(e){e.preventDefault();try{localStorage.setItem('bs_noab','1')}catch(x){}off=true;o.textContent='Turned off'});
})();
"""


def write_ab_assets():
    os.makedirs("assets", exist_ok=True)
    with open(os.path.join("assets", "ab.js"), "w", encoding="utf-8") as f:
        f.write(AB_JS)


def write_check_js(rows_all):
    nat = [list(check_attrs(r)) + [max_amount_value(r.get("Amount", ""))] for r in rows_all
           if clean(r.get("University", "")).lower().startswith("external")
           and clean(r.get("Bursary Name", ""))]
    os.makedirs("assets", exist_ok=True)
    with open(os.path.join("assets", "check.js"), "w", encoding="utf-8") as f:
        f.write(CHECK_JS.replace("__NAT__", json.dumps(nat, separators=(",", ":"))))
    write_ab_assets()
    return len(nat)

# ── "Pick your uni" check (homepage, hub, circumstance pages) ───────────────
# Same rules as the per-university check, but the first question is the
# university, so it works on pages that aren't about one university. Data is
# /assets/match.json (fetched on first use), code is /assets/match.js.
MATCH_SCRIPT = '<script src="/assets/match.js" defer></script>'

def match_box(go, preset="", title="Which grants could you get?"):
    """Server-rendered first question; match.js takes over from there.
    `preset` = a circumstance code the page already answers (care leavers -> c)."""
    n = 3 if preset else 4
    return (
        f'<div id="mchk" class="chk mchk" data-go="{go}" data-c="{preset}" data-t="{esc(title)}">'
        f'<div class="hd"><b>{esc(title)}</b><span>1 of {n}</span></div>'
        '<div class="prog"><i class="on"></i>' + '<i></i>' * (n - 1) + '</div>'
        '<label class="qq" for="mq">Where are you studying, or hoping to?</label>'
        '<input id="mq" type="search" autocomplete="off" placeholder="Type your university">'
        '<ul class="fres" id="mr"></ul>'
        '<button type="button" class="opt" data-v="none">Not decided yet</button>'
        '<p class="fine">3 quick questions. Your matches open in the free app.</p></div>'
    )

MATCH_JS = r"""(function(){
var box=document.getElementById('mchk');if(!box)return;
var go=box.getAttribute('data-go')||'seo_site',pre=box.getAttribute('data-c')||'',title=box.getAttribute('data-t')||'Which grants could you get?';
var D=null,wait=[],busy=0;
function load(cb){if(D){cb();return}wait.push(cb);if(busy)return;busy=1;
 fetch('/assets/match.json').then(function(r){return r.json()}).then(function(j){D=j;var w=wait;wait=[];w.forEach(function(f){f()})})
 .catch(function(){busy=0})}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function nm(s){return s.toLowerCase().replace(/[^a-z0-9 ]/g,'')}
var Q=[{k:'u'},
 {k:'l',q:'What will you study?',o:[['u','Undergraduate degree'],['p',"Master's or PhD"]]},
 {k:'i',q:'What is your household income?',o:[['0','Under £25,000'],['25000','£25,000 to £43,000'],['43000','Over £43,000'],['-1','Not sure']]},
 {k:'c',q:'Do any of these apply to you?',o:[['c','Care-experienced'],['e','Estranged from my family'],['r',"I'm a carer"],['d','Disabled or long-term condition'],['f','Refugee or asylum seeker'],['n','None of these']]}];
if(pre)Q.pop();
var A={},U=null,step=0,bar=document.getElementById('ctatext'),barOrig=bar?bar.textContent:'';
function head(){var h='<div class="hd"><b>'+esc(title)+'</b><span>'+(step+1)+' of '+Q.length+'</span></div><div class="prog">';
 for(var i=0;i<Q.length;i++)h+='<i'+(i<=step?' class="on"':'')+'></i>';return h+'</div>'}
function ask(){var q=Q[step],h=head();
 if(q.k==='u')h+='<label class="qq" for="mq">Where are you studying, or hoping to?</label><input id="mq" type="search" autocomplete="off" placeholder="Type your university"><ul class="fres" id="mr"></ul>'
  +'<button type="button" class="opt" data-v="none">Not decided yet</button><p class="fine">3 quick questions. Your matches open in the free app.</p>';
 else{h+='<p class="qq">'+esc(q.q)+'</p>';q.o.forEach(function(o){h+='<button type="button" class="opt" data-v="'+esc(o[0])+'">'+esc(o[1])+'</button>'});
  h+='<button type="button" class="back">Back</button>'}
 box.className='chk mchk';box.innerHTML=h;if(q.k==='u')bind()}
function bind(){var inp=document.getElementById('mq');if(!inp)return;
 inp.addEventListener('focus',function(){load(function(){})});
 inp.addEventListener('input',suggest);
 inp.addEventListener('keydown',function(e){if(e.key==='Enter'){e.preventDefault();var b=box.querySelector('#mr .pick');if(b)b.click()}})}
function suggest(){var inp=document.getElementById('mq'),o=document.getElementById('mr');if(!inp)return;var q=nm(inp.value).trim();
 if(q.length<2){o.innerHTML='';return}
 load(function(){if(nm(inp.value).trim()!==q)return;var m=[];
  D.u.forEach(function(x,i){if((' '+nm(x[0]+' '+x[2])).indexOf(' '+q)>=0)m.push(i)});
  m.sort(function(a,b){return D.u[a][0].length-D.u[b][0].length});m=m.slice(0,6);
  o.innerHTML=m.length?m.map(function(i){return '<li><button type="button" class="pick" data-i="'+i+'">'+esc(D.u[i][0])+'</button></li>'}).join('')
   :'<li class="nil">Not in our list yet. Choose “Not decided yet” to see national grants.</li>'})}
function gateM(){var u=U?U[0]:'';box.className='res';
 box.innerHTML='<p class="big">Done. Get your grant results in the app</p>'
  +'<p>The free BursaSearch app checks your answers against every UK fund we track: '+(u?esc(u)+'’s own grants and bursaries, ':'university grants and bursaries, ')
  +'national grants and charities. It shows exactly which you’d get, how much, and when each one closes.</p>'
  +'<a class="wbtn" href="/go/'+esc(go)+'/'+(window.BSAB?'?a='+window.BSAB.hex:'')+'" data-p="gate">Get my results in the free app</a>'
  +'<p class="small">Takes about 2 minutes. The app asks a few more questions so the matches are exact.</p>'
  +'<button type="button" class="again">Change answers</button>';
 if(bar)bar.textContent='Get your results free in the app';
 try{window.bsLog&&window.bsLog('web_check_done',{gate:1})}catch(_){}
 if(window.innerWidth<900)box.scrollIntoView({behavior:'smooth',block:'start'})}
function next(){if(step===0)try{window.bsLog&&window.bsLog('web_check_start')}catch(_){}step++;if(step<Q.length)ask();else gateM()}
box.addEventListener('click',function(e){var t=e.target.closest('button');if(!t)return;
 if(t.classList.contains('pick')){U=D.u[+t.getAttribute('data-i')];next()}
 else if(t.classList.contains('opt')){if(Q[step].k==='u')U=0;else A[Q[step].k]=t.getAttribute('data-v');next()}
 else if(t.classList.contains('back')){step=Math.max(0,step-1);ask()}
 else if(t.classList.contains('again')){A={};U=null;step=0;if(bar)bar.textContent=barOrig;ask()}});
function href(){return box.getAttribute('data-href')||'/go/'+go+'/'}
function result(){var c=pre||A.c,here=0,top=0,nat=0;
 function fits(r){if(r[0]!=='a'&&r[0]!==A.l)return false;var lo=+A.i;if(r[1]>0&&lo>=0&&lo>=r[1])return false;if(r[2]&&r[2].indexOf(c)<0)return false;return true}
 if(U)U[4].forEach(function(r){if(fits(r)&&!r[3]){here++;if(r[4]>top)top=r[4]}});
 D.n.forEach(function(r){if(fits(r))nat++});
 var s=U?U[3]:'',h='<div class="nums">';
 if(U&&here)h+='<div><b>'+here+'</b><span>'+esc(s)+' funds you may get</span></div>';
 h+='<div><b>'+(U&&here?'+':'')+nat+'</b><span>national &amp; charity grants worth checking</span></div></div>';
 if(U&&!here)h+='<p class="top">None of '+esc(s)+'’s own funds match these answers, but these grants might.</p>';
 if(top>=100)h+='<p class="top">Biggest '+esc(s)+' award you may get: <b>£'+top.toLocaleString('en-GB')+'</b></p>';
 h+='<a class="wbtn" href="'+esc(href())+'">Get accurate matches in the app</a>';
 if(U&&location.pathname!=='/bursaries/'+U[1]+'/')h+='<a class="alt" href="/bursaries/'+U[1]+'/">See every '+esc(s)+' fund</a>';
 h+='<p class="small">'+(U?'':'University funds depend on where you study. The app checks all 144. ')+'A quick estimate only. The app checks your course, region, fee status and more, so its matches are far more accurate.</p>'
  +'<button type="button" class="again">Change answers</button>';
 box.className='res';box.innerHTML=h;
 if(bar)bar.textContent=(U?here+' '+s+' funds + ':'')+nat+' national grants to check';
 if(window.innerWidth<900)box.scrollIntoView({behavior:'smooth',block:'start'})}
bind();
})();
"""

def write_match_assets(uni_list, by_name, rows_all):
    """/assets/match.json: per-university check data + national funds;
    /assets/site.css: SITE_CSS for the hand-authored homepage."""
    def rec(r, with_amt):
        l, cap, circ, x = check_attrs(r)
        amt = max_amount_value(r.get("Amount", "")) if with_amt and counts_for_top_award(r) else 0
        return [l, cap, circ, x, amt] if with_amt else [l, cap, circ, x]
    unis = [[name, slug, uni_alias(name) or "", uni_short(name),
             [rec(r, True) for r in by_name[name] if clean(r.get("Bursary Name", ""))]]
            for name, slug, _ in uni_list if not name.lower().startswith("external")]
    nat = [rec(r, False) for r in rows_all
           if clean(r.get("University", "")).lower().startswith("external") and clean(r.get("Bursary Name", ""))]
    os.makedirs("assets", exist_ok=True)
    for name, text in (("match.json", json.dumps({"u": unis, "n": nat}, separators=(",", ":"))),
                       ("match.js", MATCH_JS), ("site.css", SITE_CSS)):
        p = os.path.join("assets", name)
        if not os.path.exists(p) or open(p, encoding="utf-8").read() != text:
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)


# ── Rollup page for single-entry universities ───────────────────────────────
def render_rollup(singles):
    all_rows = []
    for uni, rows_ in singles.items():
        for r in rows_:
            all_rows.append((uni, r))
    all_rows.sort(key=lambda t: t[0])
    rows_html = "".join(bursary_row(r, uni=uni) for uni, r in all_rows)
    count = len(all_rows)
    title = f"More UK University Bursaries ({academic_year()}) | BursaSearch"
    description = f"All {count} more UK university bursaries in one place, never paid back. " + ELIG_CTA
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

def _time_has(row, needle):
    return needle in clean(row.get("Time spent on course", "")).lower()

def _ethnicity_has(row, *needles):
    e = clean(row.get("Ethnicity", "")).lower()
    return any(n in e for n in needles)

def _is_women_scholarship(row):
    return "female" in clean(row.get("Gender", "")).lower()

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
    # Expanded 2026-09-04 — real, well-spread tags profiled from the live
    # dataset (see the seo-improvement-plan memory): each of these clears
    # 30+ funds across 20+ universities, same bar the original six meet.
    ("student-carers", "Bursaries for Student Carers",
     "student carers", lambda r: vuln_has(r, "carer (adult)")),
    ("young-carers", "Bursaries for Young Carers",
     "young carers", lambda r: vuln_has(r, "young carer")),
    ("low-participation-areas", "Bursaries for Students from Low-Participation Areas",
     "students from areas with low progression to higher education",
     lambda r: vuln_has(r, "polar", "low participation")),
    ("part-time-students", "Bursaries for Part-Time Students",
     "part-time students", lambda r: _time_has(r, "part-time")),
    ("ethnic-minority-students", "Bursaries for Black and Minority Ethnic Students",
     "Black and minority ethnic students",
     lambda r: _ethnicity_has(r, "ethnic minority", "black", "gypsy", "south asian")),
    ("women-scholarships", "Scholarships for Women",
     "women", _is_women_scholarship),
]

# ── Wave 2: shared pieces for tag / fund / hub / about pages ────────────────
def _json_for_script(obj):
    return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")

FINDER_JS = (
    "(function(){var d=JSON.parse(document.getElementById('uqd').textContent),"
    "i=document.getElementById('uq'),o=document.getElementById('uqr');"
    "function n(s){return s.toLowerCase().replace(/[^a-z0-9 ]/g,'')}"
    "i.addEventListener('input',function(){var q=n(i.value).trim();o.innerHTML='';if(q.length<2)return;"
    "var m=d.filter(function(x){return (' '+n(x[0]+' '+x[2])).indexOf(' '+q)>=0}).slice(0,6);"
    "if(!m.length){o.innerHTML='<li class=\"nil\">No match. Try the full list below.</li>';return}"
    "m.forEach(function(x){var li=document.createElement('li'),a=document.createElement('a');"
    "a.href=x[1];a.textContent=x[0];li.appendChild(a);if(x[3]){var s=document.createElement('span');"
    "s.textContent=x[3];li.appendChild(s)}o.appendChild(li)})});"
    "i.addEventListener('keydown',function(e){if(e.key==='Enter'){var a=o.querySelector('a');"
    "if(a){e.preventDefault();location.href=a.href}}})})();"
)

def uni_finder_html(items, label="Find your university", placeholder="Start typing, e.g. Leeds"):
    """items = [(name, href, alias, note)] — a type-ahead that jumps to the page."""
    return (
        '<div class="finder">'
        f'<label for="uq">{esc(label)}</label>'
        f'<input id="uq" type="search" placeholder="{esc(placeholder)}" autocomplete="off">'
        '<ul class="fres" id="uqr"></ul>'
        f'<script type="application/json" id="uqd">{_json_for_script(items)}</script>'
        f'<script>{FINDER_JS}</script>'
        '</div>'
    )

_CIRC_NAME_HINT = {"c": ("care",), "e": ("estranged",), "r": ("carer",), "d": ("disab",),
                   "f": ("sanctuary", "refugee", "asylum")}

def dedicated_to(row, code):
    """True when a fund is really aimed at this group: its name says so, or the
    group is one of at most two it targets (not one of eight on a wide scheme)."""
    name = clean(row.get("Bursary Name", "")).lower()
    if any(h in name for h in _CIRC_NAME_HINT.get(code, ())):
        return True
    _, _, circ, _ = check_attrs(row)
    return code in circ and len(_vuln_parts(row)) <= 2

RANK_CODE = {"care-leavers": "c", "estranged-students": "e", "student-carers": "r", "young-carers": "r",
             "disabled-students": "d", "refugees-and-asylum-seekers": "f"}

def best_per_university(rows, canon_by_key, limit=10, keep=None):
    """One row per university (its biggest qualifying award), biggest first —
    the ranked table on tag pages."""
    best = {}
    for r in rows:
        raw = clean(r.get("University", ""))
        if not raw or raw.lower().startswith("external"):
            continue
        val = max_amount_value(r.get("Amount", ""))
        if val < 100 or not counts_for_top_award(r) or level_code(r) == "p" or (keep and not keep(r)):
            continue
        key = norm_uni_key(raw)
        if key not in best or val > best[key][0]:
            best[key] = (val, r)
    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])[:limit]
    out = []
    for key, (val, r) in ranked:
        resolved = canon_by_key.get(key)
        uni = resolved[0] if resolved else clean(r.get("University", ""))
        href = f"/bursaries/{resolved[1]}/" if resolved else None
        out.append((uni, href, r, val))
    return out

def ranked_table_html(ranked, noun_title):
    if len(ranked) < 3:
        return "", ""
    trs = []
    for i, (uni, href, r, val) in enumerate(ranked, 1):
        name = clean(r.get("Bursary Name", ""))
        fh = fund_href_for(r)
        uni_cell = f'<a href="{href}">{esc(uni)}</a>' if href else esc(uni)
        fund_cell = f'<a href="{fh}">{esc(name)}</a>' if fh else esc(name)
        trs.append(f'<tr><td class="n">{i}</td><td>{uni_cell}<small>{fund_cell}</small></td>'
                   f'<td class="r">{esc(format_amount(r.get("Amount", "")) or f"£{val:,}")}</td></tr>')
    html_ = (f'<h2>Biggest {esc(noun_title)} by university</h2>'
             '<div class="tblw"><table class="tbl"><thead><tr><th></th><th>University</th>'
             '<th class="r">Award</th></tr></thead><tbody>' + "".join(trs) + '</tbody></table></div>')
    itemlist = json.dumps({
        "@context": "https://schema.org", "@type": "ItemList",
        "name": f"Biggest {noun_title} by university",
        "itemListElement": [
            {"@type": "ListItem", "position": i,
             "name": f"{uni}: {clean(r.get('Bursary Name', ''))}",
             **({"url": SITE_URL + href} if href else {})}
            for i, (uni, href, r, val) in enumerate(ranked, 1)
        ],
    })
    return html_, jsonld_script(itemlist)

def list_faq_items(scope, rows, ranked=None, nat=None):
    """Data-driven questions for a listing page (no sentences about us)."""
    items = []
    n_unis = len({norm_uni_key(clean(r.get("University", ""))) for r in rows
                  if clean(r.get("University", "")) and not clean(r.get("University", "")).lower().startswith("external")})
    items.append((f"How many bursaries are there for {scope}?",
                  f"We list {len(rows)} for {scope}, across {n_unis} UK universities"
                  + (" and national funders." if nat else ".")))
    if ranked:
        uni, href, r, val = ranked[0]
        items.append((f"Which university gives the most to {scope}?",
                      f"{uni}: the {clean(r.get('Bursary Name', ''))} is worth "
                      f"{format_amount(r.get('Amount', '')) or f'£{val:,}'}."))
    if nat:
        names = [clean(r.get("Bursary Name", "")) for r in nat[:3]]
        items.append((f"Are there grants for {scope} outside universities?",
                      "Yes, including " + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]) + "."))
    soon = sorted(((parse_deadline_date(r.get("Deadline", "")), r) for r in rows
                   if parse_deadline_date(r.get("Deadline", "")) and parse_deadline_date(r.get("Deadline", "")) >= date.today()),
                  key=lambda t: t[0])
    if soon:
        d, r = soon[0]
        items.append(("Which deadline is next?",
                      f"The {clean(r.get('Bursary Name', ''))} ({clean(r.get('University', ''))}) closes on {d.day} {d.strftime('%B %Y')}."))
    return items

# ── Fund page check ──────────────────────────────────────────────────────────
_CIRC_Q = {"c": "care-experienced", "e": "estranged from your family", "r": "a carer",
           "d": "disabled or living with a long-term condition", "f": "a refugee or asylum seeker"}

def _join_or(parts):
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " or " + parts[-1]

def fund_questions(row, uni_name):
    """2–3 yes/no questions built from the fund's own criteria. Each is
    (question, allows_not_sure)."""
    l, cap, circ, _ = check_attrs(row)
    where = "a UK university" if uni_name.lower().startswith("external") else uni_name
    qs = []
    if l == "u":
        qs.append((f"Are you an undergraduate at {where}, or starting soon?", False))
    elif l == "p":
        qs.append((f"Are you a master's or PhD student at {where}, or starting soon?", False))
    else:
        qs.append((f"Are you studying at {where}, or starting soon?", False))
    if cap and clean(row.get("Household income", "")):
        qs.append((f"Is your household income under £{cap:,}?", True))
    if circ:
        qs.append((f"Are you {_join_or([_CIRC_Q[c] for c in circ])}?", False))
    yr = clean(row.get("Course Year", ""))
    if yr in ("1", "1st", "First", "Year 1") and len(qs) < 3:
        qs.append(("Is this your first year?", False))
    return qs[:3]

def fund_check_html(qs, go, official, n_other, uni_short_name, title_a="Could you get this?", title_b="Could you get this?"):
    q, ns = qs[0]
    prog = "".join(f'<i{" class=\"on\"" if i == 0 else ""}></i>' for i in range(len(qs)))
    first = (f'<div class="hd"><b><span class="v3a">{esc(title_a)}</span><span class="v3b">{esc(title_b)}</span></b>'
             f'<span>1 of {len(qs)}</span></div>'
             f'<div class="prog">{prog}</div><p class="qq">{esc(q)}</p>'
             '<div class="yn"><button type="button" class="opt" data-v="y">Yes</button>'
             '<button type="button" class="opt" data-v="n">No</button></div>'
             + ('<button type="button" class="opt" data-v="u">Not sure</button>' if ns else ""))
    data_q = esc(json.dumps([[a, 1 if b else 0] for a, b in qs]))
    return (f'<div id="fchk" class="chk" data-q="{data_q}" data-go="{go}" data-off="{esc(official)}" '
            f'data-ta="{esc(title_a)}" data-tb="{esc(title_b)}" '
            f'data-n="{n_other}" data-uni="{esc(uni_short_name)}">{first}</div>')

SIMILAR_BY_CIRC = {}   # circ code -> [(value, uni_name, row, href)] — filled in phase 1

def similar_elsewhere(row, uni_name, n=3):
    _, _, circ, _ = check_attrs(row)
    if not circ:
        return []
    pool = SIMILAR_BY_CIRC.get(circ[0], [])
    lvl = level_code(row)
    out, seen = [], {norm_uni_key(uni_name)}
    for val, u, r, href in pool:
        if norm_uni_key(u) in seen or (lvl != "a" and level_code(r) not in (lvl, "a")):
            continue
        seen.add(norm_uni_key(u))
        out.append((u, r, href))
        if len(out) == n:
            break
    return out

def short_deadline(row):
    if is_automatic(row):
        return ("Automatic", "no application")
    raw = clean(row.get("Deadline", ""))
    d = parse_deadline_date(raw)
    if d and d >= date.today():
        return (f"{d.day} {d.strftime('%b')}", f"deadline {d.year}")
    if raw and any(h in raw.lower() for h in _ROLLING_HINTS):
        return ("Rolling", "no fixed deadline")
    return ("Yearly", "deadline varies")

# ── Share images (1200×630) per university ──────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont
    HAVE_PIL = os.path.exists(os.path.join("fonts", "PublicSans.ttf"))
except ImportError:
    HAVE_PIL = False

OG_JOBS = {}   # slug -> (uni_name, headline, subline)

def _font(size, weight):
    f = ImageFont.truetype(os.path.join("fonts", "PublicSans.ttf"), size)
    f.set_variation_by_axes([weight])
    return f

def _fit(draw, text, weight, start, max_w):
    size = start
    while size > 30:
        f = _font(size, weight)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 4
    return _font(30, weight)

def make_og_image(slug, uni_name, headline, subline):
    W, H, PAD = 1200, 630, 72
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 96], fill="#0B0C0C")
    try:
        icon = Image.open("app-icon.png").convert("RGBA").resize((56, 56), Image.LANCZOS)
        im.paste(icon, (PAD, 20), icon)
    except OSError:
        pass
    d.text((PAD + 74, 48), "BursaSearch", font=_font(34, 700), fill="white", anchor="lm")
    d.text((W - PAD, 48), "bursasearch.com", font=_font(26, 500), fill="#C9CCCD", anchor="rm")
    y = 140
    lp = os.path.join("logos", f"{slug}.png")
    if os.path.exists(lp):
        lg = Image.open(lp).convert("RGBA")
        h = 84
        w = min(int(lg.size[0] * h / lg.size[1]), 420)
        lg = lg.resize((w, int(lg.size[1] * w / lg.size[0])), Image.LANCZOS)
        im.paste(lg, (PAD, y), lg)
        y += lg.size[1] + 26
    d.text((PAD, y), uni_name, font=_fit(d, uni_name, 700, 44, W - 2 * PAD), fill="#0B0C0C")
    y += 70
    d.rectangle([PAD, y, W - PAD, y + 6], fill="#0B0C0C")
    y += 30
    d.text((PAD, y), headline, font=_fit(d, headline, 800, 108, W - 2 * PAD), fill="#00766F")
    d.text((PAD, H - 64), subline, font=_font(32, 500), fill="#505A5F", anchor="ls")
    _save_png(im, os.path.join("og", f"{slug}.png"))

def make_site_og_image(n_funds, n_unis):
    # Default share image (og-image.png) for every page without its own.
    W, H, PAD = 1200, 630, 72
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 96], fill="#0B0C0C")
    try:
        icon = Image.open("app-icon.png").convert("RGBA").resize((56, 56), Image.LANCZOS)
        im.paste(icon, (PAD, 20), icon)
    except OSError:
        pass
    d.text((PAD + 74, 48), "BursaSearch", font=_font(34, 700), fill="white", anchor="lm")
    d.text((W - PAD, 48), "bursasearch.com", font=_font(26, 500), fill="#C9CCCD", anchor="rm")
    head = _font(84, 800)
    d.text((PAD, 150), "Find every UK bursary", font=head, fill="#0B0C0C")
    d.text((PAD, 250), "you qualify for", font=head, fill="#0B0C0C")
    d.rectangle([PAD, 372, W - PAD, 378], fill="#0B0C0C")
    d.text((PAD, 412), f"{n_funds // 500 * 500:,}+ verified funds · {n_unis} universities",
           font=_font(44, 700), fill="#00766F")
    d.text((PAD, H - 64), "University, national and charity grants in one search",
           font=_font(32, 500), fill="#505A5F", anchor="ls")
    _save_png(im, "og-image.png")

def _save_png(im, out):
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    buf = __import__("io").BytesIO()
    im.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    if not os.path.exists(out) or open(out, "rb").read() != data:
        with open(out, "wb") as f:
            f.write(data)

# ── About / methodology page ────────────────────────────────────────────────
def render_about(n_funds, n_unis, n_nat):
    canonical = f"{SITE_URL}/about/"
    body = (
        crumb_html([("Home", "/"), ("How we check our data", None)])
        + '<h1 class="page">How we check our data</h1>'
        + '<div class="stats">'
          f'<div><b>{n_funds:,}</b><span>funds tracked</span></div>'
          f'<div><b>{n_unis}</b><span>universities</span></div>'
          f'<div><b>{n_nat}</b><span>national funds</span></div></div>'
        + '<h2>Where the information comes from</h2>'
        '<p class="lede">Every fund comes from an official page: the university\'s own funding '
        'pages, or the charity, trust or public body that runs it. Each listing links to that page.</p>'
        + '<h2>How we keep it accurate</h2>'
        '<ul class="crit">'
        '<li>Details are taken from the official page and checked against it.</li>'
        '<li>An automatic check revisits official pages and flags any change in name, amount or income limit, which we then review and correct.</li>'
        '<li>This site rebuilds every day from that data.</li>'
        '</ul>'
        + '<h2>What we don\'t do</h2>'
        '<ul class="crit">'
        '<li>We don\'t take applications. You always apply directly with the university or funder.</li>'
        '<li>We aren\'t paid by universities or funders to list them, and we aren\'t affiliated with any of them.</li>'
        '</ul>'
        + '<h2>Spotted something wrong?</h2>'
        '<p>Amounts and rules change. If a detail here doesn\'t match the official page, the official '
        'page is right. Tell us through <a href="https://bursasearchp.github.io/bursasearch-legal/support.html">'
        'support</a> and we\'ll correct it.</p>'
        + '<p class="fresh">Run by Bursa Group Ltd.</p>'
    )
    schema = jsonld_script(json.dumps({
        "@context": "https://schema.org", "@type": "AboutPage", "url": canonical,
        "name": "How BursaSearch checks its bursary data",
        "publisher": {"@type": "Organization", "name": "BursaSearch", "legalName": "Bursa Group Ltd",
                      "url": f"{SITE_URL}/", "logo": f"{SITE_URL}/app-icon.png"},
    }))
    return render_shell(title="How we check our data | BursaSearch",
                        description=esc(f"Where BursaSearch's {n_funds:,} UK bursary listings come from, how they're reviewed and how often they're updated."),
                        canonical=canonical, body=body, schema=schema)


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
        f"{count} verified bursaries, grants and scholarships{amt_bit} for {noun_phrase}, across {n_unis} "
        f"UK universities — each linking straight to the official source, no forms with us. Our app "
        f"also matches you to additional grants beyond this list, based on {lede_tail}."
    )
    if h1.startswith("Bursaries for "):
        h1 = "Bursaries & Grants for " + h1[len("Bursaries for "):]
    title = f"{h1} {academic_year()}"
    _top = re.findall(r"£[\d,]+", amt_range or "")
    _up = f", up to {_top[-1]}" if _top else ""
    description = first_fit(
        f"{all_n(count, 'grants for ' + noun_phrase)} in one place, from {n_unis} UK universities{_up}. " + ELIG_CTA,
        f"{all_n(count, 'grants for ' + noun_phrase)} in one place{_up}. " + ELIG_CTA,
        f"{all_n(count, 'grants for ' + noun_phrase)} in one place. " + ELIG_CTA
    )
    path_suffix = f"{kind}/{slug}/" if slug else f"{kind}/"
    canonical = f"{SITE_URL}/bursaries/{path_suffix}"
    pre, extra_schema, ranked, nat = "", "", None, None
    if slug:
        code_ = RANK_CODE.get(slug) if kind == "circumstance" else None
        ranked = best_per_university(rows_matched, canon_by_key,
                                     keep=(lambda r: dedicated_to(r, code_)) if code_ else None)
        table, extra_schema = ranked_table_html(ranked, RANK_NOUN.get(slug, "bursaries"))
        nat = [r for r in rows_matched if clean(r.get("University", "")).lower().startswith("external")]
        by_u = {}
        for r in rows_matched:
            raw = clean(r.get("University", ""))
            res_ = canon_by_key.get(norm_uni_key(raw)) if raw else None
            if res_ and not raw.lower().startswith("external"):
                v = by_u.setdefault(res_[1], [res_[0], 0, 0])
                v[1] += 1
                if counts_for_top_award(r):
                    v[2] = max(v[2], max_amount_value(r.get("Amount", "")))
        finder_items = [[n, f"/bursaries/{s}/", uni_alias(n),
                         f"{c} fund{'s' if c != 1 else ''}" + (f", up to £{m:,}" if m >= 100 else "")]
                        for s, (n, c, m) in sorted(by_u.items(), key=lambda kv: kv[1][0])]
        check_here = kind == "circumstance" and slug != "international-students"
        pre = (match_box("seo_tag", preset=code_ or "") if check_here else "") + table
        if finder_items and not check_here:
            pre += uni_finder_html(finder_items, label="Where are you studying?",
                                   placeholder="Type your university")
        if nat:
            nat_rows = [bursary_row(r, fund_href=fund_href_for(r)) for r in
                        sorted(nat, key=lambda r: -max_amount_value(r.get("Amount", "")))]
            pre += (f'<h2>From outside your university <small>{len(nat)}</small></h2>'
                    f'<div class="list">{fold_rows(nat_rows, show=3)}</div>')
    faq_items = list_faq_items(noun_phrase, rows_matched, ranked, nat)
    body = content_body(
        trail=[("Home", "/"), (crumb_label, "/bursaries/"), (h1, None)],
        h1=h1,
        lede=lede,
        context_phrase=f"these {count} funds",
        count=count,
        rows_html=rows_html,
        faq_html=faq_html_from(faq_items),
        related_html=extra_html + related_links_html(rows_matched, exclude=(kind, slug)),
        go="seo_tag",
        pre_html=pre,
        list_title="All funds" if slug else "Funds",
    )
    schema = jsonld_script(faq_jsonld_from(faq_items)) + extra_schema + jsonld_script(
        breadcrumb_jsonld([
            ("BursaSearch", f"{SITE_URL}/"),
            (crumb_label, f"{SITE_URL}/bursaries/"),
            (h1, canonical),
        ])
    )
    return render_shell(title=esc(title), description=esc(description),
                        canonical=canonical, body=body, sticky=sticky_bar("seo_tag"), schema=schema,
                        go="seo_tag",
                        scripts=MATCH_SCRIPT if 'id="mchk"' in body else "")

def render_circumstance_page(slug, h1, noun_phrase, rows_matched, canon_by_key):
    return render_tag_page(
        "circumstance", slug, h1, noun_phrase, rows_matched,
        "Bursaries by circumstance", "your full circumstances", canon_by_key,
        extra_html=circ_compartments(slug, [r for r in rows_matched if clean(r.get("Bursary Name", ""))], canon_by_key),
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
    # Expanded 2026-09-04 — the live "Study subject" field is far cleaner than
    # it was when only 5 subjects cleared the bar (labels are already mostly
    # normalised, e.g. "Engineering", "Social Sciences"); every one of these
    # clears 10+ funds across 5+ universities (see seo-improvement-plan memory
    # for the full profiling). Long-tail subject pages get 5–10x the
    # click-through of the head-term university pages — they don't compete
    # against the university's own official page the way "[uni] bursary" does.
    ("humanities", "Bursaries for Humanities Students", "humanities students",
     _subj_match(r"\bhumanities\b")),
    ("social-sciences", "Bursaries for Social Sciences Students", "social sciences students",
     _subj_match(r"\bsocial sciences\b")),
    ("education-teaching", "Bursaries for Education & Teaching Students",
     "education and teaching students", _subj_match(r"\beducation\b|\bteaching\b")),
    ("media-communications", "Bursaries for Media & Communications Students",
     "media and communications students", _subj_match(r"\bmedia\b|\bcommunications\b")),
    ("environmental-science", "Bursaries for Environmental Science Students",
     "environmental science students", _subj_match(r"\benvironmental science\b")),
    ("biology", "Bursaries for Biology Students", "biology students",
     _subj_match(r"\bbiology\b|\bbiomedical\b")),
    ("architecture", "Bursaries for Architecture Students", "architecture students",
     _subj_match(r"\barchitecture\b")),
    ("finance", "Bursaries for Finance Students", "finance students",
     _subj_match(r"\bfinance\b")),
    ("mathematics", "Bursaries for Mathematics Students", "mathematics students",
     _subj_match(r"\bmathematics\b")),
    ("agriculture", "Bursaries for Agriculture Students", "agriculture students",
     _subj_match(r"\bagriculture\b")),
    ("physics", "Bursaries for Physics Students", "physics students",
     _subj_match(r"\bphysics\b")),
    ("history", "Bursaries for History Students", "history students",
     _subj_match(r"\bhistory\b")),
    ("psychology", "Bursaries for Psychology Students", "psychology students",
     _subj_match(r"\bpsychology\b")),
    ("social-work", "Bursaries for Social Work Students", "social work students",
     _subj_match(r"\bsocial work\b")),
    ("chemistry", "Bursaries for Chemistry Students", "chemistry students",
     _subj_match(r"\bchemistry\b")),
]
# Short label per subject, for the "<Label> bursaries at <University>" pages.
SUBJECT_LABEL = {
    "law": "Law", "business": "Business", "medicine": "Medicine", "nursing": "Nursing",
    "engineering": "Engineering", "computer-science": "Computer Science",
    "art-and-design": "Art & Design", "music": "Music", "veterinary-studies": "Veterinary",
    "humanities": "Humanities", "social-sciences": "Social Sciences",
    "education-teaching": "Education & Teaching", "media-communications": "Media & Communications",
    "environmental-science": "Environmental Science", "biology": "Biology",
    "architecture": "Architecture", "finance": "Finance", "mathematics": "Mathematics",
    "agriculture": "Agriculture", "physics": "Physics", "history": "History",
    "psychology": "Psychology", "social-work": "Social Work", "chemistry": "Chemistry",
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
# ── Hardship funds: a page per university with 2+ (a university with one
#    already has that fund's own page, so a second page would compete with it)
#    plus one national page. Hardship searches peak Oct-Jan. ────────────────
HARDSHIP_MIN = 2
HARDSHIP_PAGES = {}   # uni slug -> hardship rows (filled before university pages render)

def _alias_bit(uni_name):
    a = uni_alias(uni_name)
    return f" ({a})" if a and a.lower() != uni_name.lower() else ""

def hardship_faq_items(hard, where):
    items = [
        ("What is a university hardship fund?",
         "Money a university sets aside for students who run into money problems during their course, "
         "for example with rent, bills or an unexpected cost. Most give grants you don't pay back; a few "
         "also offer short-term loans, so check the official page."),
        ("Who can apply?",
         "Usually students who are already enrolled and have taken up their full student finance but still "
         "can't cover essential costs. Each fund sets its own rules, so check the official page before applying."),
    ]
    valued = sorted(((max_amount_value(r.get("Amount", "")), r) for r in hard), key=lambda t: -t[0])
    if valued and valued[0][0] >= 100:
        v, r = valued[0]
        items.append((f"How much can you get {where}?",
                      f"The largest listed {where} is the {clean(r.get('Bursary Name', ''))}"
                      + (f" ({clean(r.get('University', ''))})" if where.startswith("across") else "")
                      + f", worth {format_amount(r.get('Amount', '')) or f'£{v:,}'}. Most awards depend on your situation."))
    soon = sorted(((d, r) for d, r in ((parse_deadline_date(r.get("Deadline", "")), r) for r in hard)
                   if d and d >= date.today()), key=lambda t: t[0])
    if soon:
        d, r = soon[0]
        items.append(("When can I apply?",
                      f"The next fixed deadline is the {clean(r.get('Bursary Name', ''))} on {d.day} {d.strftime('%B %Y')}. "
                      "Hardship money can run out before the end of the year, so apply as soon as you need it."))
    else:
        items.append(("When can I apply?",
                      f"None of the hardship funds we list {where} has a fixed closing date in our data. "
                      "Hardship money can run out before the end of the year, so apply as soon as you need it."))
    return items

def render_uni_hardship_page(uni_name, uni_slug, hard, entries):
    yr = academic_year()
    ab = _alias_bit(uni_name)
    n = len(hard)
    top = max_amount_text(hard)
    canonical = f"{SITE_URL}/bursaries/{uni_slug}/hardship-funds/"
    h1 = f"Hardship Funds & Grants at {uni_name}"
    niche = re.compile(r"international|childcare|nursing|digital|medic|dental|postgrad|phd|care leaver|sport|"
                       r"laptop|travel|placement|parent|disab", re.I)
    general = [r for r in hard if not niche.search(clean(r.get("Bursary Name", "")))]
    main_h = sorted(general or hard, key=lambda r: ("hardship" in clean(r.get("Bursary Name", "")).lower(),  # a named
                                                    -max_amount_value(r.get("Amount", ""))))[0]       # fund is searched
    mname = short_fund_name(clean(main_h.get("Bursary Name", "")), uni_name).replace("&", "and")
    sh = uni_alias(uni_name) or uni_short(uni_name)
    stop = {"university", "college", "the", "school", "institute", "of", "and"}
    if any(w.lower() in mname.lower() for w in re.findall(r"[A-Za-z]{4,}", f"{sh} {uni_short(uni_name)} {uni_name}")
           if w.lower() not in stop) or sh.lower() in mname.lower():
        sh = ""                                       # 'Manchester Met Success Fund' already says whose it is
    pre = f"{sh} " if sh else ""
    if re.search(r"20\d\d", mname):
        yr_t = ""
    else:
        yr_t = f" {yr}"
    named_title = None if not general else next((t for t in (f"{pre}{mname}{yr_t}: How Much + How to Apply", f"{pre}{mname}: How Much + How to Apply") if len(t) <= 68
                        and "hardship" not in mname.lower()), None)
    title = named_title or next((t for t in (f"{uni_name}{ab} Hardship Funds & Grants {yr}: How to Apply",
                                             f"{uni_short(uni_name)} Hardship Funds & Grants {yr}: How to Apply",
                              f"{uni_short(uni_name)} Hardship Funds {yr}: How to Apply")
                  if len(t) <= 70), f"{uni_short(uni_name)} Hardship Funds {yr}")
    # 15 Nov: 950 impressions/28d for 'keele access and success fund', 0 clicks - the snippet
    # never said the name people typed. A named general fund leads (Google bolds it).
    _upt = f", up to {top}" if top else ""
    _mn = f"{pre}{mname}"
    _ma = format_amount(main_h.get("Amount", ""))
    _mupt = (" (" + re.sub(r"^Up to ", "up to ", _ma) + ")") if _ma and "£" in _ma and len(_ma) <= 20 else ""
    description = first_fit(
        *([f"The {_mn}{_mupt}: who can apply, how much and how to apply. Not a loan. " + ELIG_CTA,
           f"The {_mn}{_mupt}: who can apply and how. Not a loan. " + ELIG_CTA,
           f"The {_mn}{_mupt}, not a loan. " + ELIG_CTA] if named_title else []),
        f"Short of money at {uni_short(uni_name)}? {all_n(n, 'hardship and emergency funds')} in one place{_upt}, not loans. " + ELIG_CTA,
        f"Short of money at {uni_short(uni_name)}? {all_n(n, 'hardship funds')} in one place{_upt}. " + ELIG_CTA)
    lede = (f"{n} hardship and emergency funds at {uni_name} for students who hit money problems "
            f"during the year" + (f", worth up to {top}" if top else "") + ".")
    rows_html = "".join(bursary_row(r, fund_href=fund_href_for(r, uni_name))
                        for r in sorted(hard, key=lambda r: (-max_amount_value(r.get("Amount", "")),
                                                             clean(r.get("Bursary Name", "")))))
    named = [r for r in entries if clean(r.get("Bursary Name", ""))]
    flag = flagship_fund(named, uni_name)
    tiles = [(f"/bursaries/{uni_slug}/", f"All {len(named)} {uni_short(uni_name)} bursaries", None)]
    if flag is not None and fund_href_for(flag, uni_name):
        tiles.append((fund_href_for(flag, uni_name), short_fund_name(clean(flag.get("Bursary Name", "")), uni_name),
                      format_amount(flag.get("Amount", "")) or None))
    tiles.append(("/bursaries/hardship-funds/", "Hardship funds at every UK university", None))
    faq_items = hardship_faq_items(hard, f"at {uni_short(uni_name)}")
    body = content_body(
        trail=[("Home", "/"), ("Universities", "/bursaries/"), (uni_name, f"/bursaries/{uni_slug}/"),
               ("Hardship funds", None)],
        h1=h1, lede=lede, context_phrase=f"these {n} hardship funds", count=n, rows_html=rows_html,
        faq_html=faq_html_from(faq_items),
        related_html="<h2>Also see</h2>" + tiles_html(tiles),
        list_title="Hardship and emergency funds",
    )
    schema = jsonld_script(faq_jsonld_from(faq_items)) + jsonld_script(breadcrumb_jsonld([
        ("BursaSearch", f"{SITE_URL}/"),
        ("Bursaries by university", f"{SITE_URL}/bursaries/"),
        (uni_name, f"{SITE_URL}/bursaries/{uni_slug}/"),
        (h1, canonical),
    ]))
    return render_shell(title=esc(title), description=esc(description), canonical=canonical,
                        body=body, sticky=sticky_bar("seo_tag"), schema=schema, go="seo_tag")

def render_hardship_hub(all_rows, canon_by_key):
    yr = academic_year()
    named = [r for r in all_rows if clean(r.get("Bursary Name", "")) and is_hardship(r)]
    nat = [r for r in named if clean(r.get("University", "")).lower().startswith("external")]
    hard = [r for r in named if clean(r.get("University", "")) and r not in nat]
    by_u = {}
    for r in hard:
        raw = clean(r.get("University", ""))
        res = canon_by_key.get(norm_uni_key(raw))
        key = res[1] if res else raw
        by_u.setdefault(key, [res[0] if res else raw, res[1] if res else None, 0])[2] += 1
    n_unis = len(by_u)
    finder_items = [[name, (f"/bursaries/{slug}/hardship-funds/" if slug in HARDSHIP_PAGES else f"/bursaries/{slug}/"),
                     uni_alias(name), f"{c} hardship fund{'s' if c != 1 else ''}"]
                    for name, slug, c in sorted(by_u.values(), key=lambda v: v[0]) if slug]
    ranked = best_per_university(hard, canon_by_key)
    table, extra_schema = ranked_table_html(ranked, "hardship funds")
    pre = uni_finder_html(finder_items, label="Where are you studying?", placeholder="Type your university") + table
    if nat:
        nat_rows = [bursary_row(r, fund_href=fund_href_for(r)) for r in
                    sorted(nat, key=lambda r: -max_amount_value(r.get("Amount", "")))]
        pre += (f'<h2>From outside your university <small>{len(nat)}</small></h2>'
                f'<div class="list">{fold_rows(nat_rows, show=3)}</div>')
    row_parts = []
    for r in sorted(hard, key=lambda r: (clean(r.get("University", "")), clean(r.get("Bursary Name", "")))):
        raw = clean(r.get("University", ""))
        res = canon_by_key.get(norm_uni_key(raw))
        row_parts.append(bursary_row(r, uni=raw, uni_href=f"/bursaries/{res[1]}/" if res else None,
                                     fund_href=fund_href_for(r)))
    canonical = f"{SITE_URL}/bursaries/hardship-funds/"
    title = f"University Hardship Funds & Grants {yr}: {n_unis} UK Unis Compared"
    description = (f"Run out of money at uni? All {len(hard)} hardship funds at {n_unis} UK universities in one place, not loans. "
                   + ELIG_CTA)
    lede = (f"{len(hard)} hardship and emergency funds at {n_unis} UK universities, for students who run "
            f"into money problems during their course.")
    faq_items = hardship_faq_items(hard, f"across {n_unis} universities")
    body = content_body(
        trail=[("Home", "/"), ("Bursaries", "/bursaries/"), ("Hardship funds", None)],
        h1="University Hardship Funds", lede=lede, context_phrase=f"these {len(hard)} funds",
        count=len(hard), rows_html="".join(row_parts), faq_html=faq_html_from(faq_items),
        related_html="", pre_html=pre, list_title="All hardship funds",
    )
    schema = jsonld_script(faq_jsonld_from(faq_items)) + extra_schema + jsonld_script(breadcrumb_jsonld([
        ("BursaSearch", f"{SITE_URL}/"), ("Bursaries", f"{SITE_URL}/bursaries/"),
        ("University Hardship Funds", canonical),
    ]))
    return render_shell(title=esc(title), description=esc(description), canonical=canonical,
                        body=body, sticky=sticky_bar("seo_tag"), schema=schema, go="seo_tag")

UNI_SUBJECT_MIN = 2

def render_uni_subject_page(uni_name, uni_slug, subj_slug, matched):
    label = SUBJECT_LABEL.get(subj_slug, "Subject")
    count = len(matched)
    h1 = f"{label} Bursaries & Grants at {uni_name}"
    canonical = f"{SITE_URL}/bursaries/{uni_slug}/subject/{subj_slug}/"
    title = f"{label} Bursaries & Grants at {uni_name} {academic_year()}"
    if is_external(uni_name):
        h1 = f"{label} Grants from UK Charities and National Bodies"
        title = f"{label} Grants from UK Charities {academic_year()}"
    amt = amount_range_text(matched)
    amt_bit = f" worth {amt}" if amt else ""
    lede = (
        f"{count} verified {label.lower()} bursaries and scholarships{amt_bit} for "
        f"{uni_name} students — each linking straight to the official page. Our app also "
        f"matches you to funds beyond this list, based on your subject and circumstances."
    )
    _t = re.findall(r"£[\d,]+", amt or "")
    amt_bit = f", up to {_t[-1]}" if _t else ""
    description = clip_desc(
        (f"{all_n(count, label.lower() + ' grants from UK charities and national bodies')} in one place{amt_bit}. "
         if is_external(uni_name) else
         f"{all_n(count, label.lower() + ' grants for ' + uni_short(uni_name) + ' students')} in one place{amt_bit}. ") + ELIG_CTA
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
        faq_html=faq_html_from(list_faq_items(f"{label.lower()} students at {uni_name}", matched)),
        related_html=f"<h2>Also see</h2>{also_tiles}",
    )
    schema = jsonld_script(faq_jsonld_from(list_faq_items(f"{label.lower()} students at {uni_name}", matched))) + \
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

def fund_in_sitemap(row):
    """Fund pages still render (and are linked from their university page)
    either way, but only the ones with a concrete amount or deadline go in
    the sitemap - on a young domain Google rations crawling, so point it at
    the pages with the most to offer a searcher."""
    return bool(clean(row.get("Amount", "")) or clean(row.get("Deadline", "")))

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
        return "Set yearly"
    if raw.lower().startswith("automatic"):
        return "None — paid automatically"
    d = parse_deadline_date(raw)
    if d:
        return format_deadline(raw) if d >= date.today() else "Set yearly"
    if any(h in raw.lower() for h in _ROLLING_HINTS):
        return "Rolling — no fixed date"
    return raw

# Short names students actually search ("mmu success fund", "uon bursary").
# Only unambiguous ones: "UoB" could be Bath, Bristol or Birmingham, so it's left out.
UNI_ALIASES = {
    "manchester metropolitan university": "MMU",
    "university of nottingham": "UoN",
    "university of manchester": "UoM",
    "ucl": "UCL",
    "university college london": "UCL",
    "king's college london": "KCL",
    "kings college london": "KCL",
    "queen mary university of london": "QMUL",
    "royal holloway, university of london": "RHUL",
    "royal holloway university of london": "RHUL",
    "university of the west of england": "UWE",
    "university of east anglia": "UEA",
    "liverpool john moores university": "LJMU",
    "nottingham trent university": "NTU",
    "university of lancashire": "UCLan",
    "university of central lancashire": "UCLan",
    "london south bank university": "LSBU",
    "birmingham city university": "BCU",
    "university of the arts london": "UAL",
    "london metropolitan university": "London Met",
    "university of west london": "UWL",
    "university of wales trinity saint david": "UWTSD",
    "sheffield hallam university": "SHU",
    "oxford brookes university": "Brookes",
    "anglia ruskin university": "ARU",
    "university of east london": "UEL",
    "goldsmiths, university of london": "Goldsmiths",
    "goldsmiths university of london": "Goldsmiths",
    "imperial college london": "Imperial",
}

def uni_alias(uni_name):
    return UNI_ALIASES.get(uni_name.strip().lower(), "")

def academic_year():
    """'2026/27' from August onwards, else the year that's running."""
    t = date.today()
    y = t.year if t.month >= 8 else t.year - 1
    return f"{y}/{str(y + 1)[2:]}"

def max_amount_text(entries):
    top = max((max_amount_value(r.get("Amount", "")) for r in entries), default=0)
    return f"£{top:,.0f}" if top >= 100 else ""

def fund_title(row, name, uni_name, fund_path=""):
    alias = uni_alias(uni_name)
    who = alias or uni_name
    t = name if who.lower() in name.lower() else f"{name} – {who}"
    if is_hardship(row):   # 'mmu success fund' searchers are students asking how to apply
        short_who = alias or uni_short(uni_name)
        t2 = name if short_who.lower() in name.lower() else f"{name} – {short_who}"
        for cand in (f"{t}: How to Apply {academic_year()}", f"{t}: How to Apply",
                     f"{t2}: How to Apply {academic_year()}", f"{t2}: How to Apply"):
            if len(cand) <= 72:
                return cand
        return f"{t2}: How to Apply"
    amount = format_amount(row.get("Amount", ""))
    amt = f": {amount}" if amount and len(amount) <= 18 and "£" in amount else ""
    # 15 Nov split test: "a" = old '| Eligibility' style, "b" = a question the searcher has.
    arm = title_arm(fund_path) if fund_path else "a"
    if arm == "b":
        for cand in (f"{t}{amt}. Are You Eligible?", f"{name}{amt}. Are You Eligible?",
                     f"{t}: Are You Eligible?"):
            if len(cand) <= 65:
                return cand
    t += amt
    for cand in (f"{t} | Eligibility {academic_year()}", f"{t} ({academic_year()})"):
        if len(cand) <= 65:
            return cand
    return t

def fund_description(row, name, uni_name, ftype):
    """Search snippet for a fund page. People search the fund's name, so it leads
    (Google bolds it); then how much, who for, when, never paid back, instant check.
    Each longer version drops a part until it fits."""
    amount = format_amount(row.get("Amount", ""))
    amt = amount if amount and "£" in amount and len(amount) <= 24 else ""
    amt = re.sub(r"^Up to ", "up to ", amt)
    external = is_external(uni_name)
    us = uni_alias(uni_name) or (uni_name if len(uni_name) <= 32 else uni_short(uni_name))
    nm = _the(name) if len(name) <= 60 else ""
    cta = FUND_CTA
    if is_hardship(row):
        q = "Short of money? " if external else f"Short of money at {us}? "
        give = (f" gives {amt}, and it isn't a loan." if amt else " can help, and it isn't a loan.")
        return first_fit(q + (nm or "This fund") + give + " Who can apply and how." + cta,
                         q + (nm or "This fund") + give + cta,
                         q + "This fund" + give + cta)
    aud = eligibility_audience_phrase(row)
    aud = aud.replace("income of Under ", "income under ").replace("income of Over ", "income over ")
    kind = ftype.lower()
    at = "" if external or us.lower() in name.lower() else f" at {us}"
    forw = f" for {aud}" if aud else ""
    dl = clean(row.get("Deadline", ""))
    d = parse_deadline_date(dl) if dl else None
    when = ""
    if dl.lower().startswith("automatic"):
        when = " Paid automatically."
    elif d and d >= date.today():
        when = f" Apply by {d.day} {d.strftime('%b')}."
    what = f"{amt} {kind}" if amt else f"a {kind}"
    c = []
    if nm:
        c += [f"{nm}: {what}{forw}{at}, never paid back.{when}{cta}",
              f"{nm}: {what}{forw}{at}.{when}{cta}",
              f"{nm}: {what}{forw}{at}.{cta}",
              f"{nm}: {what}{at}, never paid back.{cta}",
              f"{nm}: {what}{at}.{cta}"]
    c += [f"Get {amt} from this {kind}{forw}{at}, never paid back.{when}{cta}" if amt else
          f"A {kind}{forw}{at}, never paid back.{when}{cta}",
          f"Get {amt} from this {kind}{at}.{cta}" if amt else f"A {kind}{at}, never paid back.{cta}"]
    return first_fit(*c)

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
    return f"The {name} is a {ftype.lower()}{amt}{aud_bit} at {uni_name}.{dl}"

def with_article(label):
    """'care leaver' -> 'a care leaver'; adjectives ('estranged') stay bare."""
    nouns = ("leaver", "carer", "seeker", "parent", "refugee", "veteran", "student")
    first = label.split("/")[0].strip()
    return f"a {label}" if first.split(" ")[-1].rstrip("s") in nouns else label

def eligibility_lines(row):
    out = []
    hh = clean(row.get("Household income", ""))
    if hh:
        out.append(f"Your household income is {hh}.")
    v = clean(row.get("Vulnerabilities (multi-select)", ""))
    if v:
        parts = [p.strip().lower() for p in v.split(",") if p.strip()]
        if len(parts) == 1:
            out.append(f"You are {with_article(parts[0])}.")
        else:
            out.append("You are any one of: " + ", ".join(parts[:-1]) + " or " + parts[-1] + ".")
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
    a_amt = (f"The {name} is worth {amount}." if amount else
             f"{uni_name} doesn't publish one fixed figure for the {name}. The official page has the current amount.")
    d = parse_deadline_date(row.get("Deadline", ""))
    raw = clean(row.get("Deadline", ""))
    if is_automatic(row):
        a_dl = f"There's no deadline. The {name} is paid automatically."
    elif d and d >= date.today():
        a_dl = f"Applications for the {name} close on {format_deadline(raw)}."
    elif raw and any(h in raw.lower() for h in _ROLLING_HINTS):
        a_dl = f"The {name} has no fixed deadline, so you can apply at any point in the year."
    else:
        a_dl = f"{uni_name} sets the deadline for the {name} each year. The official page has the current date."
    items = [(f"How much is the {name}?", a_amt), (f"What is the deadline for the {name}?", a_dl)]
    crit = eligibility_lines(row)
    if crit:
        items.append((f"Who can apply for the {name}?", " ".join(crit)))
    return items

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
    description = fund_description(row, name, uni_name, ftype)
    title = fund_title(row, name, uni_name, fund_path=f"/bursaries/{uni_slug}/{fund_slug}/")
    national = uni_name.lower().startswith("external")
    ushort = "National funders" if national else uni_short(uni_name)

    amt = format_amount(row.get("Amount", ""))
    if not amt:
        amt = "Income-based" if (clean(row.get("Household income", "")) or is_automatic(row)) else "Varies"
    dl_v, dl_l = short_deadline(row)
    stats_html = ('<div class="stats">'
                  f'<div><b>{esc(amt if len(amt) <= 22 else amt[:21] + "…")}</b><span>amount</span></div>'
                  f'<div><b>{esc(dl_v)}</b><span>{esc(dl_l)}</span></div>'
                  f'<div><b>{esc(ftype.replace(" fund", ""))}</b><span>not repaid</span></div></div>')

    crit = eligibility_lines(row)
    crit_html = ('<ul class="crit">' + "".join(f"<li>{esc(c)}</li>" for c in crit) + '</ul>') if crit else (
        f'<p>The official page has the full rules for the {esc(name)}.</p>')

    siblings = [s for s in sibling_specs if s[3] != fund_slug]
    qs = fund_questions(row, uni_name)
    short_nm = short_fund_name(name, uni_name)
    t_a = f"Could you get the {short_nm}?" if len(short_nm) <= 38 else "Could you get this one?"
    t_b = f"Could you get {amt}?" if "\u00a3" in amt and len(amt) <= 16 else t_a
    if len(qs) >= 2:
        check = fund_check_html(qs, "seo_fund", official, len(siblings), ushort, t_a, t_b)
    else:
        check = check_box("seo_fund", ushort)   # 14 Nov: every fund page has the quick check (gated), not a bare button

    apply_html = ""
    if official:
        who = "the funder" if national else uni_name
        apply_html = (f'<p class="applybtn"><a class="btn" href="{esc(official)}" target="_blank" rel="noopener">'
                      f'Apply on the official page ↗</a></p><p class="note">You apply directly with {esc(who)}.</p>')

    sim = similar_elsewhere(row, uni_name)
    sim_html = ""
    if sim:
        sim_html = ('<h2>Similar at other universities</h2><div class="list">'
                    + "".join(bursary_row(r, uni=u, fund_href=h) for u, r, h in sim) + '</div>')

    sib_links = "".join(f'<li><a href="/bursaries/{uni_slug}/{s[3]}/">{esc(short_fund_name(s[0], uni_name))}</a></li>'
                        for s in siblings[:5])
    sib_html = (f'<h2>More from {esc(ushort)}</h2><ul class="links">{sib_links}'
                f'<li><a href="/bursaries/{uni_slug}/">'
                + ("All UK charity grants" if is_external(uni_name) else f"{esc(ushort)} bursary: all {len(siblings) + 1} grants")
                + '</a></li></ul>')
    # 15 Nov: 'ntu bursary' (187 imp/28d) ranked the part-time fund page, 0 clicks. The
    # back-link now says '<uni> bursary' so the uni page is the one that ranks for it.

    faq_items = fund_faq_items(row, uni_name)
    logo = uni_logo(uni_slug, uni_name)
    # version 3 on fund pages: the app screenshot, then the reminder for THIS fund's deadline (or the general promise)
    fd = parse_deadline_date(row.get("Deadline", ""))
    if fd and fd >= date.today():
        f_dl = (f'<p class="dl v13b" data-d="{fd.isoformat()}">Closes <b>{fd.day} {fd.strftime("%b %Y")}</b>'
                f'<span class="rm">The free app reminds you before it closes.</span></p>')
    else:
        f_dl = ('<p class="dl v13b"><b>Never miss a deadline</b><span class="rm">The free app tracks every grant you '
                'match and reminds you before each one closes.</span></p>')
    fund_extra = ('<a class="shot v11b" href="/go/seo_fund/" data-p="shot"><img src="/assets/app-track.webp" width="320" '
                  'height="274" loading="lazy" alt="The BursaSearch app tracking a grant: to start, applied, claimed, with a deadline reminder">'
                  '<span>In the app: every grant you match, tracked from apply to claimed &rarr;</span></a>' + f_dl)
    body = (
        crumb_html([("Home", "/"), ("Universities", "/bursaries/"),
                    (ushort, f"/bursaries/{uni_slug}/"), (name, None)])
        + f'<p class="yr">{logo}<span>{esc(uni_name)} · {academic_year()}</span></p>'
        + f'<h1 class="page">{esc(name)}</h1>'
        + stats_html
        + f'<div class="fside">{check}{fund_extra}</div>'
        + f'<p class="lede">{esc(lede)}</p>'
        + '<h2>Who can apply</h2>' + crit_html
        + apply_html
        + sim_html
        + sib_html
        + '<h2>Questions</h2>'
        + f'<div class="faq">{faq_html_from(faq_items)}</div>'
        + related_links_html([row])
    )
    schema = (
        jsonld_script(monetary_grant_jsonld(row, uni_name, canonical, description))
        + jsonld_script(faq_jsonld_from(faq_items))
        + jsonld_script(breadcrumb_jsonld([
            ("BursaSearch", f"{SITE_URL}/"),
            ("Bursaries by university", f"{SITE_URL}/bursaries/"),
            (uni_name, f"{SITE_URL}/bursaries/{uni_slug}/"),
            (name, canonical),
        ]))
    )
    return render_shell(title=esc(title), description=esc(description),
                        canonical=canonical, body=body, sticky=sticky_bar("seo_fund"), schema=schema,
                        scripts=CHECK_SCRIPT if len(qs) >= 2 else "", go="seo_fund")

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
    title = f"UK University Bursaries & Grants {academic_year()}: Browse by University"
    description = (
        f"All {n_total:,} grants, bursaries and scholarships at {n_unis} UK universities in one place, never paid back. "
        + ELIG_CTA
    )
    lede = f"{n_total:,} verified bursaries and scholarships across {n_unis} UK universities."
    uni_items = [(f"/bursaries/{slug}/", name,
                  f"{c} bursar{'y' if c == 1 else 'ies'}") for name, slug, c in uni_list]
    uni_items.append(("/bursaries/more-universities/", "More universities",
                      f"{singles_count} bursaries"))
    body = (
        crumb_html([("Home", "/"), ("Bursaries", None)])
        + '<h1 class="page">UK University Bursaries, Grants &amp; Scholarships</h1>'
        + match_box("seo_site")
        + stat_line()
        + f'<p class="lede">{esc(lede)}</p>'
        + '<h2>Quick links</h2>'
        + tiles_html([
            ("/bursaries/closing-soon/", "Bursaries closing soon", None),
            ("/bursaries/hardship-funds/", "University hardship funds", None),
            ("/bursaries/highest-value/", "Highest-value bursaries", None),
        ] + [(f"/bursaries/{s_}/", label, None) for s_, label in ANSWER_PAGES])
        + '<h2 id="circumstance">Browse by circumstance</h2>'
        + tiles_html([(f"/bursaries/circumstance/{s}/", h1.replace("Bursaries for ", "Bursaries & Grants for ", 1), f"{c} funds")
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
                        canonical=canonical, body=body, schema=schema,
                        sticky=sticky_bar("seo_site"), scripts=MATCH_SCRIPT)

# ── Answer pages (2026-10-16) ───────────────────────────────────────────────
# Cross-university questions no single university page answers. GSC 16 Oct: our cross-uni pages get 3.9–5.5% CTR
# vs 0.7–1.2% on uni/fund pages (where the uni's own site holds #1–7). Data = verified, committed JSON in data/
# (same datasets as the BursaSearch TikTok charts), never the raw sheet.
ANSWER_PAGES = [  # slug, short label for cross-links
    ("biggest-bursaries", "Which universities give the biggest bursaries"),
    ("household-income-under-25k", "Family earns under £25k: what each uni pays"),
    ("cost-of-living", "What uni costs a month, by university"),
    ("grants", "University grants: every kind, and who gets them"),
    ("scholarships", "University scholarships, by type"),
]

def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def _cap_from_what(what):
    """'Leeds Bursary, household <=£25k' -> (25000, 'up to £25,000'); unknown -> (None, '')."""
    m = re.search(r"household\s*(<=|<|£0-)?\s*£?\s?([\d,.]+)\s*(k)?", what or "")
    if not m:
        return None, ""
    v = float(m.group(2).replace(",", ""))
    v = int(v * 1000) if m.group(3) else int(v)
    word = "under" if m.group(1) == "<" else "up to"
    return v, f"{word} £{v:,}"

def _bursary_name(what):
    name = re.split(r",\s*household|\s*\(", what or "")[0].strip()
    return re.sub(r"\s+yr1$", " (year 1)", name)

_ANSWER_UNI_ALIAS = {"london school of economics and political science": "lse", "university college london": "ucl",
                     "royal holloway, university of london": "royal holloway university of london"}

def _uni_href(uni, canon_by_key):
    k = norm_uni_key(uni)
    res = canon_by_key.get(k) or canon_by_key.get(_ANSWER_UNI_ALIAS.get(k, ""))
    return (res[0], f"/bursaries/{res[1]}/") if res else (uni, None)

def _answer_related(exclude):
    tiles = [(f"/bursaries/{s_}/", label, None) for s_, label in ANSWER_PAGES if s_ != exclude]
    tiles += [("/bursaries/circumstance/low-income/", "Bursaries for low-income students", None),
              ("/bursaries/hardship-funds/", "University hardship funds", None)]
    return '<h2>Also see</h2>' + tiles_html(tiles)

def _answer_page(*, slug, title, description, h1, lede, stats, table, note, faq_items, itemlist):
    canonical = f"{SITE_URL}/bursaries/{slug}/"
    stats_html = '<div class="stats">' + "".join(f'<div><b>{esc(b)}</b><span>{esc(t)}</span></div>' for b, t in stats) + '</div>'
    body = (crumb_html([("Home", "/"), ("Bursaries", "/bursaries/"), (h1, None)])
            + f'<p class="yr">{academic_year()}</p>'
            + f'<h1 class="page">{esc(h1)}</h1>'
            + f'<p class="lede">{esc(lede)}</p>'
            + stats_html + match_box("seo_tag", title="Which grants could you get?") + table
            + f'<p class="fresh">{note}</p>'
            + app_cta(go="seo_tag")
            + '<h2>Questions</h2>' + f'<div class="faq">{faq_html_from(faq_items)}</div>'
            + _answer_related(slug))
    schema = (jsonld_script(faq_jsonld_from(faq_items)) + jsonld_script(json.dumps(itemlist))
              + jsonld_script(breadcrumb_jsonld([("BursaSearch", f"{SITE_URL}/"), ("Bursaries", f"{SITE_URL}/bursaries/"),
                                                 (h1, canonical)])))
    return canonical, render_shell(title=esc(title), description=esc(description), canonical=canonical, body=body,
                                   sticky=sticky_bar("seo_tag"), schema=schema, go="seo_tag")

def _itemlist(name, items):
    return {"@context": "https://schema.org", "@type": "ItemList", "name": name,
            "itemListElement": [{"@type": "ListItem", "position": i, "name": n, **({"url": SITE_URL + h} if h else {})}
                                for i, (n, h) in enumerate(items, 1)]}

def _money_rows(rows, canon_by_key):
    trs, items = [], []
    for i, d in enumerate(rows, 1):
        name, href = _uni_href(d["uni"], canon_by_key)
        cap = _cap_from_what(d["what"])[1]
        cell = f'<a href="{href}">{esc(name)}</a>' if href else esc(name)
        trs.append(f'<tr><td class="n">{i}</td><td>{cell}<small>{esc(_bursary_name(d["what"]))}</small></td>'
                   f'<td>{esc(cap.capitalize() if cap else "Means-tested")}</td>'
                   f'<td class="r">£{d["amount"]:,}</td></tr>')
        items.append((f'{name}: £{d["amount"]:,} a year', href))
    table = ('<div class="tblw"><table class="tbl"><thead><tr><th></th><th>University</th><th>Household income</th>'
             '<th class="r">Up to, a year</th></tr></thead><tbody>' + "".join(trs) + '</tbody></table></div>')
    return table, items

def render_biggest_bursaries(canon_by_key):
    data = _load_json("data/free_money_2026.json")
    rows = sorted(data["bursaries"], key=lambda d: -d["amount"])
    yr = academic_year()
    a, b, c = rows[0], rows[1], rows[2]
    an = _uni_href(a["uni"], canon_by_key)[0]
    table, items = _money_rows(rows, canon_by_key)
    amounts = sorted(d["amount"] for d in rows)
    median = amounts[len(amounts) // 2]
    lede = (f"{an} gives the biggest: up to £{a['amount']:,} a year ({_bursary_name(a['what'])}, household income "
            f"{_cap_from_what(a['what'])[1]}), then {_uni_href(b['uni'], canon_by_key)[0]} (£{b['amount']:,}) and "
            f"{_uni_href(c['uni'], canon_by_key)[0]} (£{c['amount']:,}). These are each university's main income-based "
            f"bursary for {yr}: money you don't pay back.")
    faq = [
        ("Which UK university gives the biggest bursary?",
         f"Of the {len(rows)} universities here, {an} gives the most: up to £{a['amount']:,} a year through the "
         f"{_bursary_name(a['what'])} for household incomes {_cap_from_what(a['what'])[1]}."),
        ("Do you have to pay a bursary back?",
         "No. A university bursary is money you don't repay, unlike the student loan."),
        ("What household income do I need?",
         "Each university sets its own limit. Here they range from £10,000 to £35,000 a year, and many pay a smaller "
         "amount as income rises. The table shows each limit."),
        ("How do I get one?",
         "Most of these bursaries use the household income that Student Finance assesses, so you usually need to apply "
         "for student finance and agree to share your income details with your university. Check each university's "
         "page for its exact rules."),
    ]
    note = (f"Amounts are the most each bursary pays, for {yr} entry, taken from each university's official bursary page "
            f"(checked {data['checked']}). Bath's figure includes campus credit and accommodation support; Sussex's "
            f"is year one. Amounts and limits can change, so check the university's page before you apply.")
    return _answer_page(
        slug="biggest-bursaries",
        title=f"Which UK Universities Give the Biggest Bursaries? {yr} Ranking",
        description=(f"{len(rows)} UK universities ranked by bursary size for {yr}: {an} up to £{a['amount']:,} a year. "
                     + ELIG_CTA),
        h1="Which UK universities give the biggest bursaries?", lede=lede,
        stats=[(f"£{a['amount']:,}", f"biggest ({an})"), (f"£{median:,}", "middle of the table"), (str(len(rows)), "universities")],
        table=table, note=note, faq_items=faq, itemlist=_itemlist(f"Biggest university bursaries {yr}", items))

GOV_GRANTS = [   # names only, linked to the official page: amounts change yearly and depend on circumstances
    ("Childcare Grant", "England, full-time students with children in registered childcare",
     "https://www.gov.uk/childcare-grant"),
    ("Parents' Learning Allowance", "England, full-time students with dependent children",
     "https://www.gov.uk/parents-learning-allowance"),
    ("Adult Dependants' Grant", "England, full-time students with an adult who depends on them financially",
     "https://www.gov.uk/adult-dependants-grant"),
    ("Disabled Students' Allowance", "UK, students with a disability, long-term health or mental health condition, "
     "or a specific learning difficulty", "https://www.gov.uk/disabled-students-allowance-dsa"),
    ("NHS Learning Support Fund", "England, nursing, midwifery and many allied health students",
     "https://www.nhsbsa.nhs.uk/nhs-learning-support-fund-lsf"),
    ("Welsh Government Learning Grant", "Wales, students from lower-income households",
     "https://www.studentfinancewales.co.uk/"),
    ("Young Students' Bursary", "Scotland, students from lower-income households (SAAS)", "https://www.saas.gov.uk/"),
    ("Maintenance Grant", "Northern Ireland, students from lower-income households", "https://www.studentfinanceni.co.uk/"),
]


def render_grants(canon_by_key):
    """'grants for university students', 'student grants uk', '<uni> grants': one page that sorts every kind of
    grant a student doesn't pay back, with the quick check on top. Counts come from the same live data."""
    yr = academic_year()
    own = [r for r in rows if clean(r.get("Bursary Name", "")) and not clean(r.get("University", "")).lower().startswith("external")]
    nat = [r for r in rows if clean(r.get("Bursary Name", "")) and clean(r.get("University", "")).lower().startswith("external")]
    hard = [r for r in own if is_hardship(r)]
    n_unis = len({clean(r.get("University", "")) for r in own})
    lede = (f"A grant is money for university you never pay back. UK students can get them from four places: their "
            f"university ({len(own) - len(hard):,} grants, bursaries and scholarships across {n_unis} unis), "
            f"hardship funds ({len(hard)}), charities and trusts ({len(nat):,}), and the government. "
            f"Most students only ever hear about the student loan.")
    best = []
    for r in own:
        if (counts_for_top_award(r) and not is_hardship(r) and level_code(r) != "p" and not _is_international(r)
                and not check_attrs(r)[3] and not check_attrs(r)[2]):   # open to most students: no course/sport/
                                                                          # circumstance/international-only rules
            v = max_amount_value(r.get("Amount", ""))
            if v >= 1000:
                best.append((v, r))
    best.sort(key=lambda t: -t[0])
    seen, items = set(), []
    for v, r in best:
        res_ = canon_by_key.get(norm_uni_key(clean(r.get("University", ""))))
        nm_ = re.sub(r"\s*\([^)]*\)\s*$", "", clean(r.get("Bursary Name", ""))).lower()   # 'X (UCL)' = 'X (City)'
        if not res_ or res_[0] in seen or nm_ in seen:
            continue
        seen.update((res_[0], nm_))
        items.append((res_[0], res_[1], r, v))
        if len(items) == 12:
            break
    rows_html = "".join(
        f'<div class="row"><a class="nm" href="{esc(fund_href_for(r, u) or "/bursaries/" + sl + "/")}">'
        f'{esc(short_fund_name(clean(r.get("Bursary Name", "")), u))}</a>{display_amount(r)}'
        f'<span class="m"><a href="/bursaries/{sl}/">{esc(u)}</a></span></div>'
        for u, sl, r, v in items)
    gov = "".join(f'<div class="row"><a class="nm" href="{esc(h)}" rel="noopener" target="_blank">{esc(n)}</a>'
                  f'<span class="amt v">Government</span><span class="m">{esc(w)}</span></div>' for n, w, h in GOV_GRANTS)
    faq = [
        ("What grants can university students get in the UK?",
         f"Grants and bursaries from your own university ({n_unis} UK universities run them), hardship funds for when "
         f"money runs short, grants from charities and trusts, and government grants such as the Childcare Grant, "
         f"Parents' Learning Allowance, Adult Dependants' Grant and Disabled Students' Allowance."),
        ("Do I have to pay a grant back?", "No. Grants, bursaries and scholarships are not loans. You don't repay them."),
        ("Are there grants for students from low-income families?",
         "Yes. Most universities pay an income-based bursary to students from lower-income households, often "
         "automatically through your Student Finance assessment, and many charity grants are aimed at low income too."),
        ("How do I find which grants I can get?",
         "Answer the quick check on this page, then the free BursaSearch app matches you to the grants you qualify "
         "for and reminds you before they close."),
    ]
    h1 = "University grants UK: money you never pay back"
    body = (crumb_html([("Home", "/"), ("Bursaries", "/bursaries/"), ("University grants", None)])
            + f'<p class="yr">{yr}</p>'
            + f'<h1 class="page">{esc(h1)}</h1>'
            + f'<p class="lede">{esc(lede)}</p>'
            + match_box("seo_tag", title="Which grants could you get?")
            + '<h2>Biggest university grants for undergraduates</h2>'
            + f'<div class="list">{rows_html}</div>'
            + '<p class="hlink"><a href="/bursaries/biggest-bursaries/">Every university\u2019s main bursary, ranked &rarr;</a></p>'
            + cmp_section("Government grants and allowances", [gov_block(k) for k in GOV_INFO],
                          "Official help on top of your student loan. Tap one to see who gets it and how to apply.")
            + cmp_section("Grants by situation", [
                cmp_block(f"Grants for {CIRC_SHORT[cs]}",
                          f"<p>{esc(_plural(len(m_), 'university fund', 'university funds'))}, biggest first.</p>"
                          + fund_ul(m_, canon_by_key=canon_by_key, limit=8)
                          + f'<p><a class="go" href="/bursaries/circumstance/{cs}/">All grants for {esc(CIRC_SHORT[cs])} '
                            f'&rarr;</a></p>', len(m_))
                for cs, _h, _n, fn_ in CIRCUMSTANCES
                for m_ in [by_amount([r for r in own if fn_(r) and not is_hardship(r)])] if m_])
            + '<h2>More grants by situation</h2>'
            + tiles_html([("/bursaries/household-income-under-25k/", "Grants if your family earns under £25k", None),
                          ("/bursaries/hardship-funds/", "Hardship funds and grants at every university", None),
                          ("/bursaries/scholarships/", "Scholarships at every university, by type", None),
                          ("/bursaries/circumstance/care-leavers/", "Grants for care leavers", None),
                          ("/bursaries/circumstance/estranged-students/", "Grants for estranged students", None),
                          ("/bursaries/circumstance/disabled-students/", "Grants for disabled students", None),
                          ("/bursaries/", "Grants at every university", None)])
            + '<h2>Questions</h2>' + f'<div class="faq">{faq_html_from(faq)}</div>')
    canonical = f"{SITE_URL}/bursaries/grants/"
    schema = (jsonld_script(faq_jsonld_from(faq)) + jsonld_script(breadcrumb_jsonld(
        [("BursaSearch", f"{SITE_URL}/"), ("Bursaries", f"{SITE_URL}/bursaries/"), ("University grants", canonical)])))
    title = f"University Grants UK {yr}: Every Grant You Don't Pay Back"
    description = clip_desc(f"All {len(own) + len(nat):,} UK student grants for {yr} in one place: universities, charities and "
                            f"the government. " + ELIG_CTA)
    return canonical, render_shell(title=esc(title), description=esc(description), canonical=canonical, body=body,
                                   sticky=sticky_bar("seo_tag"), schema=schema, go="seo_tag")


def render_under_25k(canon_by_key):
    data = _load_json("data/free_money_2026.json")
    yr = academic_year()
    rows = sorted(data["bursaries"], key=lambda d: -d["amount"])
    ok = [d for d in rows if (_cap_from_what(d["what"])[0] or 0) >= 25000]
    lower = [d for d in rows if 0 < (_cap_from_what(d["what"])[0] or 0) < 25000]
    t1, items = _money_rows(ok, canon_by_key)
    t2, _ = _money_rows(lower, canon_by_key)
    top = ok[0]
    tn = _uni_href(top["uni"], canon_by_key)[0]
    table = t1 + (f'<h2>Only if your family earns less <small>{len(lower)}</small></h2>'
                  '<p class="lede">These have a lower income limit, so check it against your household income.</p>' + t2 if lower else "")
    lede = (f"{len(ok)} of the {len(rows)} universities here pay a bursary if your household earns under £25,000. "
            f"The biggest is {tn}: up to £{top['amount']:,} a year. It's money you don't pay back, usually paid on top "
            f"of your student loan.")
    faq = [
        ("Can I get a bursary if my family earns under £25,000?",
         f"At most universities, yes. {len(ok)} of the {len(rows)} universities on this page pay their main bursary "
         f"to students from households earning under £25,000."),
        ("How much could I get?",
         f"Up to £{top['amount']:,} a year at {tn}. Most pay £1,000 to £3,500 a year."),
        ("Is household income before or after tax?",
         "Student Finance looks at your household's income before tax, with some allowances such as pension "
         "contributions, and universities usually use that same figure. Check the university's page for its exact rules."),
        ("Do I need to apply separately?",
         "Often not: many universities pay their bursary automatically using your Student Finance income assessment, "
         "as long as you agree to share it. Some need a separate form, so check each page."),
    ]
    note = (f"Main income-based bursaries for {yr} entry, from each university's official page (checked {data['checked']}). "
            f"Amounts shown are the most each pays; some pay less as income rises.")
    return _answer_page(
        slug="household-income-under-25k",
        title=f"Grants & Bursaries If Your Family Earns Under £25k ({yr})",
        description=(f"Household income under £25,000? {len(ok)} UK universities pay you a bursary for {yr}, up to "
                     f"£{top['amount']:,} a year at {tn}. " + ELIG_CTA),
        h1="Family earns under £25k? The grants each uni pays you", lede=lede,
        stats=[(str(len(ok)), "universities that pay you"), (f"£{top['amount']:,}", f"most ({tn})"), ("£0", "to pay back")],
        table=table, note=note, faq_items=faq, itemlist=_itemlist(f"Bursaries for household income under £25,000 {yr}", items))

def render_cost_of_living(canon_by_key):
    data = _load_json("data/uni_costs_2025.json")
    other, loan = data["everyday_spend"], data["avg_loan_month"]
    rows = sorted(({**u, "total": u["rent_bills"] + other} for u in data["unis"]), key=lambda u: -u["total"])
    trs, items = [], []
    for i, u in enumerate(rows, 1):
        name, href = _uni_href(u["uni"], canon_by_key)
        cell = f'<a href="{href}">{esc(name)}</a>' if href else esc(name)
        gap = u["total"] - loan
        trs.append(f'<tr><td class="n">{i}</td><td>{cell}<small>£{u["rent_bills"]:,} rent + bills</small></td>'
                   f'<td>£{gap:,} more than the loan</td><td class="r">£{u["total"]:,}</td></tr>')
        items.append((f'{name}: £{u["total"]:,} a month', href))
    table = ('<div class="tblw"><table class="tbl"><thead><tr><th></th><th>University</th><th>Gap</th>'
             '<th class="r">A month</th></tr></thead><tbody>' + "".join(trs) + '</tbody></table></div>')
    hi, lo = rows[0], rows[-1]
    hin, lon = _uni_href(hi["uni"], canon_by_key)[0], _uni_href(lo["uni"], canon_by_key)[0]
    ties = [_uni_href(u["uni"], canon_by_key)[0] for u in rows if u["total"] == hi["total"]]
    lede = (f"{' and '.join(ties)} {'are' if len(ties) > 1 else 'is'} the most expensive at £{hi['total']:,} a month "
            f"(£{hi['rent_bills']:,} rent and bills plus £{other} everyday spending). The cheapest here is {lon} at "
            f"£{lo['total']:,}. The average maintenance loan is £{loan} a month, so every one of these costs more than "
            f"the loan covers.")
    faq = [
        ("How much does it cost to live at university a month?",
         f"The average student spends £1,142 a month ({data['spend_source']}). By university it ranges from "
         f"£{lo['total']:,} at {lon} to £{hi['total']:,} at {hin}, using local rent and bills."),
        ("Does the student loan cover living costs?",
         f"Usually not on its own. The average maintenance loan is £{loan} a month, which is less than the monthly cost "
         f"at every university here. Bursaries, grants and part-time work fill the gap."),
        ("What's included in these figures?",
         f"Average rent and bills for a student room near each university ({data['rent_source']}) plus £{other} a month "
         f"for food, travel, going out and everything else ({data['spend_source']})."),
        ("How can I cover the gap?",
         "Check the bursaries and grants your university and others offer: many are paid automatically if your household "
         "income is below a limit, and you don't pay them back."),
    ]
    note = (f"Rent + bills: {esc(data['rent_source'])}. Everyday spending £{other}/month and the £{loan}/month average loan: "
            f"{esc(data['spend_source'])}. Real costs depend on where you live and how you spend.")
    return _answer_page(
        slug="cost-of-living",
        title="How Much Does Uni Cost a Month? Every UK Uni Compared (2026)",
        description=(f"What a month at uni costs at {len(rows)} UK universities: £{lo['total']:,} at {lon} to "
                     f"£{hi['total']:,} at {hin}, against a £{loan} average loan."),
        h1="How much does uni cost a month?", lede=lede,
        stats=[(f"£{hi['total']:,}", f"most ({hin})"), (f"£{lo['total']:,}", f"least ({lon})"), (f"£{loan}", "average loan a month")],
        table=table, note=note, faq_items=faq, itemlist=_itemlist("Monthly cost of living by UK university", items))

def submit_indexnow(urls):
    """Tells Bing/Yandex/Seznam about changed URLs immediately instead of
    waiting for their crawler to notice — free, no auth beyond the public
    key file already hosted at the site root. Only runs against live data
    (SEO_DATA_URL set); a local dev run shouldn't spam this on every tweak."""
    if not urls or not SEO_DATA_URL or os.environ.get("SKIP_INDEXNOW"):
        return
    for i in range(0, len(urls), 1000):
        _indexnow_post(urls[i:i + 1000])

def _indexnow_post(urls):
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

# hardship pages: only where a university has 2+ hardship funds and the URL is free
for uni, uslug, _ in uni_list:
    h_ = [r for r in multi[uni] if clean(r.get("Bursary Name", "")) and is_hardship(r)]
    if (len(h_) >= HARDSHIP_MIN and not uni.lower().startswith("external")
            and f"/bursaries/{uslug}/hardship-funds/" not in FUND_URLS.values()):
        HARDSHIP_PAGES[uslug] = h_

n_nat = write_check_js(rows)
write_match_assets(uni_list, multi, rows)

RANK_NOUN = {
    "low-income-students": "low-income bursaries", "care-leavers": "care-leaver bursaries",
    "international-students": "international scholarships", "estranged-students": "estranged-student bursaries",
    "disabled-students": "bursaries for disabled students", "refugees-and-asylum-seekers": "sanctuary scholarships",
    "student-carers": "bursaries for carers", "young-carers": "young-carer bursaries",
    "low-participation-areas": "widening-participation bursaries", "part-time-students": "part-time bursaries",
    "ethnic-minority-students": "scholarships for Black and minority ethnic students",
    "women-scholarships": "scholarships for women",
}
RANK_NOUN.update({s: f"{SUBJECT_LABEL.get(s, s)} bursaries" for s, *_ in SUBJECTS})
RANK_NOUN.update({s: f"bursaries ({h1.split(' in ')[-1]})" for s, h1, _ in REGIONS})

HUB_FINDER_ITEMS = [[name, f"/bursaries/{slug}/", uni_alias(name), f"{c} funds"] for name, slug, c in uni_list]

for uslug_, specs_ in fund_specs_by_uni.items():
    for fname_, r_, uname_, fslug_ in specs_:
        val_ = max_amount_value(r_.get("Amount", ""))
        _, _, circ_, _ = check_attrs(r_)
        if circ_ and val_ >= 100 and counts_for_top_award(r_) and not uname_.lower().startswith("external"):
            for c_ in circ_:
                if dedicated_to(r_, c_):
                    SIMILAR_BY_CIRC.setdefault(c_, []).append((val_, uname_, r_, f"/bursaries/{uslug_}/{fslug_}/"))
for v_ in SIMILAR_BY_CIRC.values():
    v_.sort(key=lambda t: -t[0])

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

# hardship pages (per university + national)
hardship_urls = []
_uni_by_slug = {s: u for u, s, _ in uni_list}
for uslug, h_ in HARDSHIP_PAGES.items():
    hd = os.path.join(OUT_DIR, uslug, "hardship-funds")
    os.makedirs(hd, exist_ok=True)
    url = f"{SITE_URL}/bursaries/{uslug}/hardship-funds/"
    write_page(url, os.path.join(hd, "index.html"),
               render_uni_hardship_page(_uni_by_slug[uslug], uslug, h_, multi[_uni_by_slug[uslug]]),
               lastmod_map, changed_urls)
    hardship_urls.append(url)
hd = os.path.join(OUT_DIR, "hardship-funds")
os.makedirs(hd, exist_ok=True)
url = f"{SITE_URL}/bursaries/hardship-funds/"
write_page(url, os.path.join(hd, "index.html"), render_hardship_hub(rows, canon_by_key), lastmod_map, changed_urls)
hardship_urls.insert(0, url)

# answer pages (cross-university questions)
answer_urls = []
for _slug, _render in (("biggest-bursaries", render_biggest_bursaries),
                       ("household-income-under-25k", render_under_25k),
                       ("cost-of-living", render_cost_of_living),
                       ("grants", render_grants),
                       ("scholarships", render_scholarships)):
    _url, _html = _render(canon_by_key)
    os.makedirs(os.path.join(OUT_DIR, _slug), exist_ok=True)
    write_page(_url, os.path.join(OUT_DIR, _slug, "index.html"), _html, lastmod_map, changed_urls)
    answer_urls.append(_url)

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
sitemap_fund_urls = []
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
        if fund_in_sitemap(r):
            sitemap_fund_urls.append(url)

# Persist the fund slug map so a light rename in the sheet doesn't churn URLs.
with open(FUND_SLUGS_FILE, "w", encoding="utf-8") as f:
    json.dump(fund_slugs, f, indent=0, sort_keys=True)

# hub (/bursaries/) — generated from the shared template
hub_url = f"{SITE_URL}/bursaries/"
write_page(hub_url, os.path.join(OUT_DIR, "index.html"),
           render_hub(uni_list, len(singles), circumstance_counts, subject_counts, region_counts),
           lastmod_map, changed_urls)

# /about/ — how the data is checked
about_url = f"{SITE_URL}/about/"
os.makedirs("about", exist_ok=True)
write_page(about_url, os.path.join("about", "index.html"),
           render_about(SITE_FUND_COUNT, len(uni_list), n_nat), lastmod_map, changed_urls)

# share images for university pages (only rewritten when they change)
if HAVE_PIL:
    for s_, (n_, h_, sub_) in OG_JOBS.items():
        make_og_image(s_, n_, h_, sub_)
    make_site_og_image(SITE_FUND_COUNT, len(uni_list))

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

# /go/<channel> - same redirect, tagged per channel (also noindex, not in sitemap).
for channel, medium in GO_CHANNELS.items():
    os.makedirs(os.path.join("go", channel), exist_ok=True)
    with open(os.path.join("go", channel, "index.html"), "w", encoding="utf-8") as f:
        f.write(redirect_html(app_store_url(channel), play_url(channel, medium)))

# Leftover pages (14 Nov): a fund that drops out of the data used to leave its old page live forever (322 of them,
# old look, stale facts, ~1,150 Google impressions a month). Each becomes an instant redirect to the university's
# current page (national funds -> /bursaries/grants/), which Google treats as a permanent move. Not in the sitemap.
def _stub(target):
    return ('<!doctype html><html lang="en"><head><meta charset="UTF-8"><title>Moved | BursaSearch</title>'
            f'<link rel="canonical" href="{SITE_URL}{target}"><meta http-equiv="refresh" content="0;url={target}">'
            f'<script>location.replace({json.dumps(target)})</script></head><body><a href="{target}">This page has moved</a>'
            '</body></html>')
_stubbed = 0
for _root, _dirs, _files in os.walk(OUT_DIR):
    if "index.html" not in _files:
        continue
    _p = os.path.normpath(os.path.join(_root, "index.html"))
    if _p in WRITTEN:
        continue
    _rel = os.path.relpath(_root, OUT_DIR).replace(os.sep, "/").split("/")
    if _rel == ["."]:
        continue
    _uni = _rel[0]
    _live = lambda d: os.path.normpath(os.path.join(OUT_DIR, d, "index.html")) in WRITTEN
    if len(_rel) > 1:                                   # an old fund or uni x subject page -> that uni's page
        _target = f"/bursaries/{_uni}/" if _live(_uni) else "/bursaries/grants/"
    else:                                               # an old university page: renamed slug, else the rollup
        _new = next((d for d in os.listdir(OUT_DIR) if _uni.endswith("-" + d) and _live(d)), None)
        _target = f"/bursaries/{_new}/" if _new else "/bursaries/more-universities/"
    with open(_p, encoding="utf-8", errors="ignore") as _f:
        _old = _f.read()
    if 'http-equiv="refresh"' in _old and _target in _old:
        continue
    with open(_p, "w", encoding="utf-8") as _f:
        _f.write(_stub(_target))
    _stubbed += 1
print(f"leftover pages redirected this run: {_stubbed}")

# sitemap — every URL's lastmod comes from lastmod_map (only bumped above
# when that page's content actually changed), not blindly stamped TODAY.
urls = [home_url, hub_url, rollup_url, about_url]
urls += [f"{SITE_URL}/bursaries/{slug}/" for _, slug, _ in uni_list]
urls += [f"{SITE_URL}/bursaries/circumstance/{slug}/" for slug, _, _ in circumstance_counts]
urls += [f"{SITE_URL}/bursaries/subject/{slug}/" for slug, _, _ in subject_counts]
urls += [f"{SITE_URL}/bursaries/region/{slug}/" for slug, _, _ in region_counts]
urls += [f"{SITE_URL}/bursaries/closing-soon/" for _ in closing_soon_counts]
urls += [f"{SITE_URL}/bursaries/highest-value/" for _ in highest_value_counts]
urls += uni_subject_urls
urls += hardship_urls
urls += answer_urls
core_urls = list(urls)
urls += fund_urls

# Homepage: the hand-authored index.html keeps its own layout; generate.py
# only fills the marked blocks (stats, real example funds, a plain
# link to every university page, footer) so they stay in step with the data.
def home_examples(n=4):
    """Real funds for the homepage: the biggest main university bursaries
    (open to UK undergrads, no extra conditions), one per university."""
    picks = []
    for name, slug, _ in uni_list:
        named = [r for r in multi[name] if clean(r.get("Bursary Name", ""))]
        f = flagship_fund(named, name)
        if f and counts_for_top_award(f) and not check_attrs(f)[3]:
            v = max_amount_value(f.get("Amount", ""))
            if v >= 1000:
                picks.append((-v, name, slug, f))
    picks.sort(key=lambda t: (t[0], t[1]))
    return [(name, slug, f) for _, name, slug, f in picks[:n]]

def fill_block(page, marker, inner):
    return re.sub(rf"<!-- {marker}:START -->.*?<!-- {marker}:END -->",
                  lambda m: f"<!-- {marker}:START -->{inner}<!-- {marker}:END -->", page, flags=re.S)

if os.path.exists("index.html"):
    with open("index.html", encoding="utf-8") as f:
        home = f.read()
    ex_rows = "".join(bursary_row(r, uni=name, uni_href=f"/bursaries/{slug}/", fund_href=fund_href_for(r, name))
                      for name, slug, r in home_examples())
    real_unis = [(n, s_) for n, s_, _ in uni_list if not n.lower().startswith("external")]
    links = "".join(f'<li><a href="/bursaries/{s_}/">{html.escape(n)}</a></li>' for n, s_ in real_unis)
    new_home = home
    for marker, inner in (
        ("STATS", '<div class="stats">'
                  f'<div><b>{SITE_FUND_COUNT // 500 * 500:,}+</b><span>funds tracked</span></div>'
                  f'<div><b>{len(uni_list)}</b><span>universities</span></div>'
                  f'<div><b>{n_nat // 10 * 10}+</b><span>national &amp; charity funds</span></div></div>'),
        ("EXAMPLES", ('<h2>Real bursaries from our list</h2>'
                      f'<div class="list">{ex_rows}</div>') if ex_rows else ""),
        ("UNI-LINKS", '<h2>Bursaries at your university</h2>'
                      f'<details class="more"><summary><span>Show all {len(real_unis)} universities</span></summary>'
                      f'<ul class="unilinks">{links}</ul></details>'),
        ("FOOTER", footer_html()),
        ("ANALYTICS", analytics_html()),
    ):
        new_home = fill_block(new_home, marker, inner)
    if new_home != home:
        with open("index.html", "w", encoding="utf-8") as f:
            f.write(new_home)
        lastmod_map[home_url] = TODAY

# Split sitemaps (core pages vs individual funds) so Search Console reports
# indexing for each separately; sitemap.xml is the index pointing at both.
def write_urlset(fname, url_list):
    out = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for u in url_list:
        out += f"  <url><loc>{u}</loc><lastmod>{lastmod_map.get(u, TODAY)}</lastmod></url>\n"
    out += "</urlset>\n"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(out)
    return max((lastmod_map.get(u, TODAY) for u in url_list), default=TODAY)

parts = [("sitemap-core.xml", write_urlset("sitemap-core.xml", core_urls)),
         ("sitemap-funds.xml", write_urlset("sitemap-funds.xml", sitemap_fund_urls))]
sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
for fname, lm in parts:
    sitemap += f"  <sitemap><loc>{SITE_URL}/{fname}</loc><lastmod>{lm}</lastmod></sitemap>\n"
sitemap += "</sitemapindex>\n"
with open("sitemap.xml", "w", encoding="utf-8") as f:
    f.write(sitemap)

# Persist lastmod_map for next run, pruned to only URLs still in this build
# (so a retired page's stale date doesn't linger forever).
with open(LASTMOD_FILE, "w", encoding="utf-8") as f:
    json.dump({u: lastmod_map[u] for u in urls if u in lastmod_map}, f, indent=0, sort_keys=True)

with open("robots.txt", "w", encoding="utf-8") as f:
    f.write(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")

with open("title_test.json", "w", encoding="utf-8") as _f:
    json.dump({"started": "2026-11-15", "note": "a/b title style per page, see title_arm() in generate.py",
               "pages": dict(sorted(TITLE_TEST.items()))}, _f, indent=0)

submit_indexnow(core_urls + sitemap_fund_urls if os.environ.get("INDEXNOW_ALL") else changed_urls)

print(
    f"Built {len(uni_list)} university pages + {len(fund_urls)} fund pages + "
    f"{len(uni_subject_urls)} uni×subject + 1 rollup + "
    f"{len(circumstance_counts)} circumstance + {len(subject_counts)} subject + "
    f"{len(region_counts)} region + {len(closing_soon_counts)} closing-soon + "
    f"{len(highest_value_counts)} highest-value + hub + sitemap "
    f"({len(urls)} URLs total, {len(changed_urls)} changed this run)."
)
