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
    return _ICON_RE.sub('', _CHECKED_RE.sub('', html_text))

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
        attrs = f' data-l="{l}" data-i="{cap}" data-c="{circ}" data-x="{maybe}"'
    return (f'<div class="row"{attrs}>{name_html}{display_amount(row)}'
            f'<span class="m">{deadline_tag(row)}{prov}{esc(who_line(row))}</span></div>')

def fold_rows(rows_html, show=4, more_label="Show {n} more"):
    """First `show` rows visible, the rest inside a native <details> (still in
    the HTML, so search engines read them)."""
    head, tail = rows_html[:show], rows_html[show:]
    out = "".join(head)
    if tail:
        out += (f'<details class="more"><summary><span>{esc(more_label.format(n=len(tail)))}</span>'
                f'</summary>{"".join(tail)}</details>')
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
  --ink:#0B0C0C; --soft:#505A5F; --line:#B1B4B6; --hair:#E5E6E7; --paper:#FFFFFF;
  --panel:#F3F2F1; --teal:#00766F; --teal-dark:#00403C; --link:#00605A;
  --warn-bg:#FFF7BF; --warn:#594D00; --ok-bg:#CCE2D8; --ok:#005A30; --maxw:1020px;
}
html{-webkit-text-size-adjust:100%;}
body{margin:0; background:var(--paper); color:var(--ink);
  font-family:"Public Sans",Arial,system-ui,sans-serif; font-size:16px; line-height:1.45;}
a{color:var(--link); text-decoration:underline; text-underline-offset:3px; text-decoration-thickness:1px;}
a:hover{text-decoration-thickness:3px; color:var(--teal-dark);}
:focus-visible{outline:3px solid #FFDD00; outline-offset:0; box-shadow:0 4px 0 var(--ink);}

.hdr{background:var(--ink); color:#fff;}
.hdr .in{max-width:var(--maxw); margin:0 auto; display:flex; align-items:center; gap:10px 22px;
  padding:11px 16px; flex-wrap:wrap;}
.brand{display:flex; align-items:center; gap:8px; color:#fff; text-decoration:none;
  font-family:"Sora",Arial,sans-serif; font-weight:700; font-size:16px; letter-spacing:-.01em;}
.brand:hover{color:#fff;}
.brand img{width:26px; height:26px; border-radius:6px; display:block;}
.nav{display:flex; gap:6px 18px; flex-wrap:wrap;}
.nav a{color:#fff; font-size:14.5px; font-weight:600;}
.hdr .grow{flex:1;}
.hdr .get{color:#fff; font-weight:700; font-size:14.5px;}

.wrap{max-width:var(--maxw); margin:0 auto; padding:0 16px 56px;}
.crumb{font-size:13.5px; color:var(--soft); padding:12px 0 0;}
.crumb a{color:var(--ink);}
.crumb .sep{margin:0 6px; color:var(--soft);}
.yr{display:flex; align-items:center; gap:10px; margin:18px 0 6px; font-size:13.5px;
  font-weight:700; color:var(--soft);}
.yr img{height:32px; width:auto; max-width:160px; object-fit:contain;}
h1.page{font-weight:800; font-size:clamp(25px,4.4vw,38px); line-height:1.1;
  letter-spacing:-.015em; margin:0 0 16px; text-wrap:balance; max-width:24ch;}
.lede{font-size:17px; max-width:62ch; margin:0 0 18px;}
.wrap h2{font-weight:800; font-size:21px; letter-spacing:-.01em; margin:30px 0 4px;
  display:flex; justify-content:space-between; align-items:baseline; gap:12px;}
.wrap h2 small{font-size:14px; font-weight:600; color:var(--soft);}
.fresh{font-size:13px; color:var(--soft); margin:0 0 14px;}

.stats{display:grid; grid-template-columns:repeat(3,1fr); border-top:3px solid var(--ink);
  border-bottom:1px solid var(--line); margin:0 0 18px; max-width:620px;}
.stats div{padding:10px 8px 11px 0; min-width:0;}
.stats div + div{padding-left:12px; border-left:1px solid var(--line);}
.stats b{display:block; font-size:24px; font-weight:800; letter-spacing:-.02em; line-height:1.05;
  font-variant-numeric:tabular-nums; overflow-wrap:anywhere;}
.stats span{font-size:13px; color:var(--soft);}

.chk{background:var(--panel); padding:16px;}
.chk .hd{display:flex; justify-content:space-between; align-items:baseline; gap:10px; margin:0 0 10px;}
.chk .hd b{font-size:19px; font-weight:800;}
.chk .hd span{font-size:13.5px; color:var(--soft); font-variant-numeric:tabular-nums;}
.prog{display:flex; gap:4px; margin:0 0 14px;}
.prog i{flex:1; height:5px; background:#D1D3D4;} .prog i.on{background:var(--teal);}
.chk .qq{font-size:17px; font-weight:700; margin:0 0 10px;}
.opt{display:block; width:100%; text-align:left; background:#fff; color:var(--ink);
  border:2px solid var(--ink); padding:11px 14px; font:inherit; font-weight:700; font-size:16px;
  margin:0 0 8px; box-shadow:0 2px 0 var(--ink); cursor:pointer;}
.opt:hover{background:#F8F8F8;}
.opt:active{transform:translateY(2px); box-shadow:none;}
.chk .fine{font-size:13px; color:var(--soft); margin:6px 0 0;}
.chk .back{background:none; border:0; padding:0; font:inherit; font-size:14px; color:var(--link);
  text-decoration:underline; cursor:pointer; margin-top:6px;}
.res{background:var(--teal); color:#fff; padding:16px;}
.res .nums{display:grid; grid-template-columns:1fr 1fr; gap:12px; margin:0 0 14px;}
.res .nums b{display:block; font-size:42px; font-weight:800; letter-spacing:-.03em; line-height:1;}
.res .nums span{display:block; font-size:14px; line-height:1.25; margin-top:5px;}
.wbtn{display:block; text-align:center; background:#fff; color:var(--teal-dark) !important;
  text-decoration:none !important; font-weight:800; font-size:16px; padding:12px;
  box-shadow:0 3px 0 #002B28;}
.res .again{display:block; margin:12px auto 0; background:none; border:0; color:#fff;
  font:inherit; font-size:14px; text-decoration:underline; cursor:pointer;}
.res .small{font-size:12.5px; margin:10px 0 0; text-align:center; opacity:.9;}

.list .row{display:grid; grid-template-columns:1fr auto; gap:2px 14px; padding:11px 0;
  border-bottom:1px solid var(--line);}
.row .nm{font-weight:700; font-size:16.5px; line-height:1.25;}
.row .amt{font-weight:800; font-size:16.5px; text-align:right; white-space:nowrap;
  font-variant-numeric:tabular-nums;}
.row .amt.v{font-weight:600; font-size:14px; color:var(--soft);}
.row .m{grid-column:1/-1; font-size:14px; color:var(--soft);}
.row .m a{color:var(--soft);}
.row.no{opacity:.42;}
.t{display:inline-block; font-size:12px; font-weight:800; padding:1px 6px; margin-right:6px;
  letter-spacing:.02em; vertical-align:1px;}
.t.g{background:var(--ok-bg); color:var(--ok);} .t.y{background:var(--warn-bg); color:var(--warn);}
.t.k{background:var(--ink); color:#fff;}
details.more{border-bottom:1px solid var(--line);}
details.more > summary{list-style:none; cursor:pointer; padding:13px 0; display:flex;
  justify-content:space-between; gap:10px; font-weight:700; color:var(--link);}
details.more > summary::-webkit-details-marker{display:none;}
details.more > summary span:first-child{text-decoration:underline; text-underline-offset:3px;}
details.more > summary::after{content:"+"; font-size:22px; line-height:.9; color:var(--ink);}
details.more[open] > summary::after{content:"\\2212";}
details.more > summary small{margin-left:auto; color:var(--soft); font-weight:600;}
details.more .row:last-child{border-bottom:0;}
.none{padding:11px 0; color:var(--soft); border-bottom:1px solid var(--line);}

.faq details{border-bottom:1px solid var(--line);}
.faq summary{list-style:none; cursor:pointer; padding:13px 0; font-weight:700; color:var(--link);
  display:flex; justify-content:space-between; gap:12px;}
.faq summary::-webkit-details-marker{display:none;}
.faq summary span{text-decoration:underline; text-underline-offset:3px;}
.faq summary::after{content:"+"; font-size:22px; line-height:.9; color:var(--ink);}
.faq details[open] summary::after{content:"\\2212";}
.faq p{margin:0 0 14px; max-width:62ch;}

.cta{background:var(--panel); padding:18px 16px; margin:26px 0 0; max-width:620px;}
.cta b{display:block; font-size:19px; font-weight:800; margin:0 0 4px;}
.cta p{margin:0 0 12px; color:var(--soft); font-size:15px;}
.gbtn{display:inline-block; background:var(--teal); color:#fff !important; text-decoration:none !important;
  font-weight:700; font-size:16px; padding:10px 15px; box-shadow:0 3px 0 var(--teal-dark);}
.gbtn:hover{background:#005F59;}

.links{display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:4px 24px;
  margin:6px 0 0; padding:0; list-style:none;}
.links li{padding:7px 0; border-bottom:1px solid var(--hair); font-size:15.5px;}
.links li span{color:var(--soft); font-size:13.5px; margin-left:6px;}
.tiles{display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:4px 24px; margin:6px 0 0;}
.tiles a{display:block; padding:7px 0; border-bottom:1px solid var(--hair); font-size:15.5px;}
.tiles a b{font-weight:600;}
.tiles a span{color:var(--soft); font-size:13.5px; margin-left:6px; text-decoration:none; display:inline-block;}

h1.page + .sub{font-size:14.5px; color:var(--soft); margin:-8px 0 16px;}
.kv{display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); border-top:3px solid var(--ink);
  border-bottom:1px solid var(--line); margin:0 0 18px;}
.kv > div{padding:10px 12px 11px 0; border-bottom:1px solid var(--hair);}
.kv dt{font-size:13px; color:var(--soft);}
.kv dd{margin:0; font-weight:800; font-size:17px;}
.crit{margin:6px 0 0; padding:0; list-style:none;}
.crit li{display:flex; gap:10px; padding:7px 0; border-bottom:1px solid var(--hair);}
.crit li::before{content:"\\2713"; font-weight:800; color:var(--teal);}
.note{font-size:13.5px; color:var(--soft); margin-top:10px;}
.applybtn{margin:16px 0 0;}
.btn{display:inline-block; background:#fff; color:var(--ink) !important; text-decoration:none !important;
  border:2px solid var(--ink); font-weight:700; padding:10px 14px; box-shadow:0 2px 0 var(--ink);}

.ftr{background:var(--panel); border-top:4px solid var(--ink); margin-top:40px;}
.ftr .in{max-width:var(--maxw); margin:0 auto; padding:28px 16px 30px;}
.ftr .cols{display:flex; flex-wrap:wrap; gap:22px 48px;}
.ftr .col b{display:block; font-size:15px; margin-bottom:6px;}
.ftr .col a{display:block; font-size:14.5px; color:var(--ink); margin-bottom:6px;}
.ftr .fine{font-size:13.5px; color:var(--soft); margin:22px 0 0; padding-top:14px;
  border-top:1px solid var(--line); max-width:70ch;}

.ctabar{position:fixed; left:0; right:0; bottom:0; z-index:30; display:none; align-items:center; gap:10px;
  padding:9px 10px 9px 16px; padding-bottom:calc(9px + env(safe-area-inset-bottom,0px));
  background:var(--ink); color:#fff;}
.ctabar p{margin:0; flex:1; font-size:14px; font-weight:700; line-height:1.25;}
.ctabar a{background:#14B3AA; color:#06221E !important; text-decoration:none !important; font-weight:800;
  font-size:14px; padding:8px 12px; white-space:nowrap;}
.ctabar button{background:none; border:0; color:#fff; font-size:22px; line-height:1; padding:0 4px; cursor:pointer;}

.layout{display:block;}
.layout > .side{margin:0 0 6px;}
@media (max-width:760px){
  .nav{display:none;}
  .ctabar{display:flex;}
  body.has-bar{padding-bottom:64px;}
}
@media (min-width:900px){
  .layout{display:grid; grid-template-columns:minmax(0,1fr) 340px; column-gap:48px;
    grid-template-areas:"head side" "body side";}
  .layout > .head{grid-area:head;}
  .layout > .side{grid-area:side; align-self:start; position:sticky; top:16px; margin:18px 0 0;}
  .layout > .body{grid-area:body; min-width:0;}
}
@media (prefers-reduced-motion:reduce){.opt:active{transform:none;}}
"""

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Public+Sans:wght@400;600;700;800&family=Sora:wght@700&display=swap">'
)

APPLE_APP_ID = "6795890396"

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
        '<span>BursaSearch</span></a>'
        f'<nav class="nav">{nav}</nav>'
        '<span class="grow"></span>'
        f'<a class="get" href="/go/{go}/">Get the app</a>'
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
        '<a href="https://bursasearchp.github.io/bursasearch-legal/support.html">Support</a></div>'
        '</div>'
        '<p class="fine">Details come from official university and provider pages and can change. '
        'Always check the official page before applying. BursaSearch is not affiliated with any '
        'university; university names and logos belong to their owners. &copy; Bursa Group Ltd.</p>'
        '</div></footer>'
    )

def sticky_bar(go="seo_site", text="Find every bursary you qualify for"):
    return (
        '<div class="ctabar" id="ctabar">'
        f'<p id="ctatext">{esc(text)}</p>'
        f'<a href="/go/{go}/">Open app</a>'
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
        f'<script>var a={json.dumps(ios_url)},p={json.dumps(android_url)};'
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

def render_shell(*, title, description, canonical, body, hero="", sticky="", schema="", scripts="", go="seo_site"):
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
<meta name="apple-itunes-app" content="app-id={APPLE_APP_ID}">
<link rel="canonical" href="{canonical}">
{ICON_LINKS}
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
{header_html(go)}
{hero}
<main class="wrap">
{body}
</main>
{footer_html()}
{sticky}
{scripts}
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

def content_body(*, trail, h1, lede, context_phrase, count, rows_html, faq_html, related_html, go="seo_tag"):
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
        + f'<h2>Funds <small>{len(rows)}</small></h2>'
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
                         r"support fund|financial assistance", re.I)

def is_hardship(row):
    return bool(HARDSHIP_RE.search(clean(row.get("Bursary Name", ""))))

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
        if level_code(r) == "p" or is_hardship(r) or any(w in name for w in _NOT_FLAGSHIP):
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
    '<div class="hd"><b>Which could you get?</b><span>1 of 3</span></div>'
    '<div class="prog"><i class="on"></i><i></i><i></i></div>'
    '<p class="qq">What will you study?</p>'
    '<button type="button" class="opt" data-v="u">Undergraduate degree</button>'
    '<button type="button" class="opt" data-v="p">Master&#x27;s or PhD</button>'
    '<p class="fine">For UK students. Nothing is saved or sent.</p>'
)

def check_box(go, who):
    return (f'<div id="chk" class="chk" data-go="{go}" data-uni="{esc(who)}">{CHECK_Q1}</div>')

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

def render_page(uni_name, entries, slug):
    count = len(entries)
    amt_range = amount_range_text(entries)
    amt_bit = f" worth {amt_range}" if amt_range else ""
    alias = uni_alias(uni_name)
    alias_bit = f" ({alias})" if alias else ""
    top = max_amount_text(entries)
    # Titles + descriptions unchanged from the 30 Sep version (frozen while
    # that change is measured in Search Console).
    title = (f"{uni_name}{alias_bit} Bursaries {academic_year()}: {count} grants"
             + (f" up to {top}" if top else ""))
    description = (
        f"{count} bursaries and scholarships for {uni_name}{alias_bit} students in "
        f"{academic_year()}{amt_bit}. See who qualifies, deadlines and how to apply."
    )
    canonical = f"{SITE_URL}/bursaries/{slug}/"

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

    subj_pages = UNI_SUBJECT_PAGES.get(slug, [])
    subj_block = ""
    if subj_pages:
        subj_block = f'<h2>By subject at {esc(uni_name)}</h2>' + tiles_html([
            (f"/bursaries/{slug}/subject/{s}/", f"{SUBJECT_LABEL.get(s, s)} bursaries", f"{c}")
            for s, _, c in sorted(subj_pages, key=lambda t: -t[2])
        ])

    faq_items = uni_faq_items(uni_name, flag, ug, hard, pg, named)
    head = (
        crumb_html([("Home", "/"), ("Universities", "/bursaries/"), (uni_name, None)])
        + f'<p class="yr">{uni_logo(slug, uni_name)}<span>{academic_year()}</span></p>'
        + f'<h1 class="page">{esc(uni_name)} Bursaries &amp; Scholarships</h1>'
        + stats_html
    )
    body_html = (
        group("ug", "For undergraduates", ug, 4)
        + group("h", "Hardship funds", hard, 2)
        + group("pg", "Master's and PhD funding", pg, 2)
        + '<h2>Questions</h2>'
        + f'<div class="faq">{faq_html_from(faq_items)}</div>'
        + subj_block + related_links_html(entries)
        + stat_line()
    )
    body = (f'<div class="layout"><div class="head">{head}</div>'
            f'<div class="side">{check_box("seo_uni", uni_short(uni_name))}</div>'
            f'<div class="body">{body_html}</div></div>')
    schema = jsonld_script(faq_jsonld_from(faq_items)) + jsonld_script(breadcrumb_jsonld([
        ("BursaSearch", f"{SITE_URL}/"),
        ("Bursaries by university", f"{SITE_URL}/bursaries/"),
        (uni_name, canonical),
    ]))
    return render_shell(title=esc(title), description=esc(description), canonical=canonical,
                        body=body, sticky=sticky_bar("seo_uni"), schema=schema,
                        scripts=CHECK_SCRIPT, go="seo_uni")

CHECK_JS = r"""(function(){
var NAT=__NAT__;
var box=document.getElementById('chk'); if(!box) return;
var go=box.getAttribute('data-go')||'seo_site', uni=box.getAttribute('data-uni')||'University';
var Q=[
 {k:'l',q:'What will you study?',o:[['u','Undergraduate degree'],['p',"Master's or PhD"]]},
 {k:'i',q:'What is your household income?',o:[['0','Under £25,000'],['25000','£25,000 to £43,000'],['43000','Over £43,000'],['-1','Not sure']]},
 {k:'c',q:'Do any of these apply to you?',o:[['c','Care-experienced'],['e','Estranged from my family'],['r',"I'm a carer"],['d','Disabled or long-term condition'],['f','Refugee or asylum seeker'],['n','None of these']]}
];
var A={},step=0,groups=[].slice.call(document.querySelectorAll('.grp[data-g]')),orig=groups.map(function(g){return g.innerHTML});
var bar=document.getElementById('ctatext'),barOrig=bar?bar.textContent:'';
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function fits(l,cap,circ){if(l!=='a'&&l!==A.l)return false;var lo=+A.i;if(cap>0&&lo>=0&&lo>=cap)return false;if(circ&&circ.indexOf(A.c)<0)return false;return true}
function ask(){var q=Q[step],h='<div class="hd"><b>Which could you get?</b><span>'+(step+1)+' of '+Q.length+'</span></div><div class="prog">';
 for(var i=0;i<Q.length;i++)h+='<i'+(i<=step?' class="on"':'')+'></i>';
 h+='</div><p class="qq">'+esc(q.q)+'</p>';
 q.o.forEach(function(o){h+='<button type="button" class="opt" data-v="'+esc(o[0])+'">'+esc(o[1])+'</button>'});
 h+=step?'<button type="button" class="back">Back</button>':'<p class="fine">For UK students. Nothing is saved or sent.</p>';
 box.className='chk';box.innerHTML=h}
box.addEventListener('click',function(e){var t=e.target.closest('button');if(!t)return;
 if(t.classList.contains('opt')){A[Q[step].k]=t.getAttribute('data-v');step++;if(step<Q.length)ask();else result()}
 else if(t.classList.contains('back')){step=Math.max(0,step-1);ask()}
 else if(t.classList.contains('again')){A={};step=0;groups.forEach(function(g,i){g.innerHTML=orig[i]});if(bar)bar.textContent=barOrig;ask()}});
function result(){var here=0;
 groups.forEach(function(g,gi){if(g.getAttribute('data-g')==='h')return;g.innerHTML=orig[gi];
  var rows=[].slice.call(g.querySelectorAll('.row[data-l]')),yes=[],no=[];
  rows.forEach(function(r){var ok=fits(r.getAttribute('data-l'),+r.getAttribute('data-i'),r.getAttribute('data-c'));
   if(ok&&r.getAttribute('data-x')!=='1')here++;(ok?yes:no).push(r)});
  var list=g.querySelector('.list');list.innerHTML='';
  yes.forEach(function(r){list.appendChild(r)});
  if(!yes.length){var p=document.createElement('p');p.className='none';p.textContent='None of these match your answers.';list.appendChild(p)}
  if(no.length){var d=document.createElement('details');d.className='more';d.innerHTML='<summary><span>'+no.length+(no.length===1?' doesn\'t':' don\'t')+' match your answers</span></summary>';
   no.forEach(function(r){r.classList.add('no');d.appendChild(r)});list.appendChild(d)}
  var sm=g.querySelector('h2 small');if(sm)sm.textContent=yes.length+' of '+rows.length});
 var nat=0;NAT.forEach(function(n){if(fits(n[0],n[1],n[2]))nat++});
 box.className='res';
 box.innerHTML='<div class="nums"><div><b>'+here+'</b><span>'+esc(uni)+' funds you may get</span></div>'
  +(nat?'<div><b>+'+nat+'</b><span>national &amp; charity grants worth checking</span></div>':'')+'</div>'
  +'<a class="wbtn" href="/go/'+esc(go)+'/">See which fit you in the free app</a>'
  +'<p class="small">Based on the main rules we have on file. Always check the official page.</p>'
  +'<button type="button" class="again">Change answers</button>';
 if(bar)bar.textContent=here+' '+uni+' funds'+(nat?' + '+nat+' national grants':'')+' to check';
 if(window.innerWidth<900)box.scrollIntoView({behavior:'smooth',block:'start'})}
})();
"""

def write_check_js(rows_all):
    nat = [list(check_attrs(r)) for r in rows_all
           if clean(r.get("University", "")).lower().startswith("external")
           and clean(r.get("Bursary Name", ""))]
    os.makedirs("assets", exist_ok=True)
    with open(os.path.join("assets", "check.js"), "w", encoding="utf-8") as f:
        f.write(CHECK_JS.replace("__NAT__", json.dumps(nat, separators=(",", ":"))))
    return len(nat)


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
    title = f"{h1} ({academic_year()}) | BursaSearch"
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
        go="seo_tag",
    )
    schema = jsonld_script(faq_jsonld(noun_phrase, scope_phrase=noun_phrase)) + jsonld_script(
        breadcrumb_jsonld([
            ("BursaSearch", f"{SITE_URL}/"),
            (crumb_label, f"{SITE_URL}/bursaries/"),
            (h1, canonical),
        ])
    )
    return render_shell(title=esc(title), description=esc(description),
                        canonical=canonical, body=body, sticky=sticky_bar("seo_tag"), schema=schema,
                        go="seo_tag")

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
UNI_SUBJECT_MIN = 2

def render_uni_subject_page(uni_name, uni_slug, subj_slug, matched):
    label = SUBJECT_LABEL.get(subj_slug, "Subject")
    count = len(matched)
    h1 = f"{label} Bursaries at {uni_name}"
    canonical = f"{SITE_URL}/bursaries/{uni_slug}/subject/{subj_slug}/"
    title = f"{label} Bursaries at {uni_name} ({academic_year()}) | BursaSearch"
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

def fund_title(row, name, uni_name):
    alias = uni_alias(uni_name)
    who = alias or uni_name
    t = name if who.lower() in name.lower() else f"{name} – {who}"
    amount = format_amount(row.get("Amount", ""))
    if amount and len(amount) <= 18 and "£" in amount:
        t += f": {amount}"
    for cand in (f"{t} | Eligibility {academic_year()}", f"{t} ({academic_year()})"):
        if len(cand) <= 65:
            return cand
    return t

def fund_description(row, name, uni_name, ftype):
    amount = format_amount(row.get("Amount", ""))
    aud = eligibility_audience_phrase(row)
    bits = f"{name}: " + (f"{amount} " if amount and len(amount) <= 24 else "")
    aud = aud.replace("income of Under ", "income under ").replace("income of Over ", "income over ")
    bits += f"{ftype.lower()}" + (f" for {aud}" if aud else "") + f" at {uni_name}"
    alias = uni_alias(uni_name)
    if alias and alias.lower() not in name.lower():
        bits += f" ({alias})"
    bits += f". Who qualifies, the {academic_year()} deadline and how to apply."
    return bits if len(bits) <= 160 else bits[:157].rsplit(" ", 1)[0] + "…"

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
    description = fund_description(row, name, uni_name, ftype)
    title = fund_title(row, name, uni_name)

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
        + app_cta(f"the {name}", go="seo_fund")
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
                        canonical=canonical, body=body, sticky=sticky_bar("seo_fund"), schema=schema,
                        go="seo_fund")

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
        + stat_line()
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

n_nat = write_check_js(rows)

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
core_urls = list(urls)
urls += fund_urls

# Homepage: keep a plain link to every university page between markers in
# the hand-authored index.html, so the one page Google already trusts links
# straight to each uni page (the rest of the homepage is left untouched).
if os.path.exists("index.html"):
    with open("index.html", encoding="utf-8") as f:
        home = f.read()
    links = "".join(f'<a href="/bursaries/{slug}/">{html.escape(name)}</a>' for name, slug, _ in uni_list)
    block = ('<!-- UNI-LINKS:START -->\n    <div class="unis"><h3>Bursaries at your university</h3>'
             f'<p>{links}</p></div>\n    <!-- UNI-LINKS:END -->')
    new_home = re.sub(r"<!-- UNI-LINKS:START -->.*?<!-- UNI-LINKS:END -->", lambda m: block, home, flags=re.S)
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

submit_indexnow(core_urls + sitemap_fund_urls if os.environ.get("INDEXNOW_ALL") else changed_urls)

print(
    f"Built {len(uni_list)} university pages + {len(fund_urls)} fund pages + "
    f"{len(uni_subject_urls)} uni×subject + 1 rollup + "
    f"{len(circumstance_counts)} circumstance + {len(subject_counts)} subject + "
    f"{len(region_counts)} region + {len(closing_soon_counts)} closing-soon + "
    f"{len(highest_value_counts)} highest-value + hub + sitemap "
    f"({len(urls)} URLs total, {len(changed_urls)} changed this run)."
)
