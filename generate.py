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

SITE_URL = "https://findmyfund.co.uk"
# Apple has no equivalent of Play's &referrer= campaign tracking from a plain
# URL — attributing App Store installs to this site needs a provider/campaign
# token from App Analytics in App Store Connect (one-time setup only the
# account owner can do), then the link becomes
# https://apps.apple.com/app/id6795890396?pt=<providerID>&ct=<campaignToken>&mt=8
APP_STORE_URL = "https://apps.apple.com/app/id6795890396"
# &referrer= is Google Play's documented custom-campaign format — installs
# that came through this link now show up in Play Console's Acquisition
# reports under source "findmyfund_web" / campaign "seo_site", instead of
# being invisible in the "organic" bucket like every other install.
PLAY_URL = (
    "https://play.google.com/store/apps/details?id=fresherforgev2.com"
    "&referrer=utm_source%3Dfindmyfund_web%26utm_medium%3Dreferral%26utm_campaign%3Dseo_site"
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

def bursary_card(row, tag=None, tag_href=None):
    name = clean(row.get("Bursary Name", "")) or "Bursary"
    amount = format_amount(row.get("Amount", ""))
    deadline = format_deadline(row.get("Deadline", ""))
    link = clean(row.get("Link", "")) or clean(row.get("Application URL", ""))
    subject = clean(row.get("Study subject", ""))
    badges = eligibility_badges(row)
    if subject:
        badges = [f"Subject: {subject}"] + badges
    badges = badges[:4]

    meta_bits = []
    if amount:
        meta_bits.append(f'<span class="amt">{esc(amount)}</span>')
    if deadline:
        meta_bits.append(f'<span class="dl">Deadline: {esc(deadline)}</span>')
    meta_html = " · ".join(meta_bits)

    badge_html = "".join(f'<span class="badge">{esc(b)}</span>' for b in badges)
    link_html = (
        f'<a class="src" href="{esc(link)}" target="_blank" rel="noopener">View official details →</a>'
        if link else ""
    )
    if tag and tag_href:
        # Links a card's university tag back to that university's own page —
        # the main path (besides the hub) between the circumstance/region
        # pages and the university pages.
        tag_html = f'<a class="uni-tag" href="{esc(tag_href)}">{esc(tag)}</a>'
    elif tag:
        tag_html = f'<div class="uni-tag">{esc(tag)}</div>'
    else:
        tag_html = ""

    return f"""
      <div class="card">
        {tag_html}
        <h3>{esc(name)}</h3>
        {f'<div class="meta">{meta_html}</div>' if meta_html else ''}
        {f'<div class="badges">{badge_html}</div>' if badge_html else ''}
        {link_html}
      </div>"""

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

PAGE_CSS = """
:root{
  --bg:#091723; --card:#0f2135; --border:#1a3050;
  --teal:#0f8a8a; --teal-br:#1cc4bc;
  --text:#ffffff; --text-sec:#7a9bb5; --text-mut:#3d5a75;
}
*{box-sizing:border-box;}
body{
  margin:0; background:var(--bg); color:var(--text);
  font-family:'Sora',system-ui,-apple-system,'Segoe UI',sans-serif;
  line-height:1.5;
}
.wrap{max-width:720px; margin:0 auto; padding:28px 20px 64px;}
a{color:var(--teal-br);}
.crumb{font-size:12.5px; color:var(--text-sec); margin-bottom:18px;}
.crumb a{color:var(--text-sec); text-decoration:none;}
.crumb a:hover{color:var(--teal-br);}
h1{font-size:26px; font-weight:800; line-height:1.2; margin:0 0 10px;}
.lede{font-size:14.5px; color:var(--text-sec); margin:0 0 26px; max-width:60ch;}
.cta-row{display:flex; gap:10px; flex-wrap:wrap; margin:0 0 32px;}
.cta{
  display:inline-flex; align-items:center; gap:8px; text-decoration:none;
  background:var(--teal); color:#06110e; font-weight:700; font-size:13.5px;
  padding:11px 16px; border-radius:10px;
}
.cta.ghost{background:transparent; color:var(--teal-br); border:1px solid var(--border);}
.perks{display:flex; flex-wrap:wrap; gap:12px; margin:0 0 34px;}
.perks .perk{
  flex:1 1 220px; display:flex; gap:10px; align-items:flex-start;
  background:var(--card); border:1px solid var(--border); border-radius:12px;
  padding:13px 14px;
}
.perks .perk .ic{font-size:16px; line-height:1; flex-shrink:0; margin-top:1px;}
.perks .perk b{display:block; font-size:12.5px; font-weight:700; color:var(--text); margin-bottom:2px;}
.perks .perk span{font-size:11.5px; color:var(--text-sec); line-height:1.4;}
h2{font-size:16px; font-weight:700; margin:34px 0 14px; color:var(--text);}
.grid{display:flex; flex-direction:column; gap:12px;}
.card{
  background:var(--card); border:1px solid var(--border); border-radius:14px;
  padding:16px 18px;
}
.card h3{font-size:15px; font-weight:700; margin:0 0 6px; color:var(--text);}
.card .meta{font-size:12.5px; color:var(--teal-br); font-weight:600; margin-bottom:8px;}
.card .badges{display:flex; flex-wrap:wrap; gap:6px; margin-bottom:10px;}
.card .badge{
  font-size:10.5px; font-weight:600; color:var(--text-sec);
  background:rgba(255,255,255,.05); border:1px solid var(--border);
  padding:3px 8px; border-radius:999px;
}
.card .src{font-size:12.5px; font-weight:600; text-decoration:none;}
.card .src:hover{text-decoration:underline;}
.faq{margin-top:8px;}
.faq details{
  border-bottom:1px solid var(--border); padding:14px 0;
}
.faq summary{
  font-size:14px; font-weight:700; cursor:pointer; list-style:none;
}
.faq summary::-webkit-details-marker{display:none;}
.faq p{font-size:13px; color:var(--text-sec); margin:8px 0 0;}
.foot{
  margin-top:48px; padding-top:20px; border-top:1px solid var(--border);
  font-size:11.5px; color:var(--text-mut); text-align:center;
}
.foot a{color:var(--text-mut);}
.uni-tag{display:inline-block; font-size:10.5px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; color:var(--teal-br); margin-bottom:4px; text-decoration:none;}
a.uni-tag:hover{text-decoration:underline;}
.ulist{display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:8px; margin:0 0 8px;}
.ulist a{
  display:block; background:var(--card); border:1px solid var(--border); border-radius:10px;
  padding:10px 13px; font-size:12.5px; font-weight:600; color:var(--text); text-decoration:none;
}
.ulist a:hover{border-color:var(--teal);}
"""

PERKS_HTML = """
    <div class="perk">
      <span class="ic">⚡</span>
      <div><b>Direct to source</b><span>Every link goes straight to the official university or provider page — you apply with them, not through us.</span></div>
    </div>
    <div class="perk">
      <span class="ic">🎯</span>
      <div><b>Beyond this list</b><span>Our app also matches you to national and independent grants that aren't tied to any one university.</span></div>
    </div>"""

def faq_block(uni_name, count, scope_phrase="this university"):
    items = [
        ("Is this list free to use?",
         f"Yes. Every bursary listed here for {uni_name} is free to browse — no account or payment needed to see what's available."),
        ("Do I apply through FindMyFund?",
         "No — every listing links directly to the official university or provider page, and you apply there. We connect you straight to the source, not through a form with us."),
        ("How do I know which ones I actually qualify for?",
         f"This page lists what's publicly available for {scope_phrase}. FindMyFund's app checks your specific circumstances — income, region, fee status and more — against our full database, which also includes national and independent grants beyond this list, and tells you exactly which ones you qualify for and why."),
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
        ("Do I apply through FindMyFund?",
         "No — every listing links directly to the official university or provider page, and you apply there. We connect you straight to the source, not through a form with us."),
        ("How do I know which ones I actually qualify for?",
         f"This page lists what's publicly available for {scope_phrase}. FindMyFund's app checks your specific circumstances against our full database, which also includes national and independent grants beyond this list, and tells you exactly which ones you qualify for and why."),
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

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<meta name="description" content="{description}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="{canonical}">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700;800&display=swap">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="website">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{og_image}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{og_image}">
<script type="application/ld+json">{jsonld}</script>
<script type="application/ld+json">{breadcrumb}</script>
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <div class="crumb"><a href="/">FindMyFund</a> / <a href="/bursaries/">{crumb_label}</a> / {uni_name_esc}</div>
  <h1>{h1}</h1>
  <p class="lede">{lede}</p>

  <div class="cta-row">
    <a class="cta" href="{app_store}" target="_blank" rel="noopener">Get matched — App Store</a>
    <a class="cta ghost" href="{play}" target="_blank" rel="noopener">Get matched — Google Play</a>
  </div>

  <div class="perks">{perks}
  </div>

  <h2>{list_heading}</h2>
  <div class="grid">{cards}
  </div>

  <h2>Common questions</h2>
  <div class="faq">{faq}
  </div>
{related}
  <div class="foot">
    Bursary details are sourced from official university and provider pages and may change —
    always confirm on the official link before applying. <a href="/">FindMyFund</a> · Bursa Group Ltd
  </div>
</div>
</body>
</html>
"""

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
    items = "".join(f'<a href="{esc(href)}">{esc(label)}</a>' for href, label in links)
    return f"""
  <h2>Also see</h2>
  <div class="ulist">{items}
  </div>"""

def render_page(uni_name, entries, slug):
    entries_sorted = sorted(entries, key=lambda r: clean(r.get("Bursary Name", "")))
    cards = "".join(bursary_card(r) for r in entries_sorted)
    count = len(entries)
    amt_range = amount_range_text(entries)
    amt_bit = f" worth {amt_range}" if amt_range else ""
    lede = (
        f"{count} verified bursaries and scholarships{amt_bit} currently listed for students at "
        f"{uni_name} — each linking straight to the official source, no forms with us. Our app also "
        f"matches you to additional grants beyond this list, based on your specific circumstances."
    )
    title = f"{uni_name} Bursaries & Scholarships ({date.today().year}) | FindMyFund"
    description = (
        f"{count} verified bursaries and scholarships for {uni_name} students{amt_bit}. "
        f"See eligibility, deadlines and official application links."
    )
    canonical = f"{SITE_URL}/bursaries/{slug}/"
    return PAGE_TEMPLATE.format(
        title=esc(title),
        description=esc(description),
        canonical=canonical,
        og_image=OG_IMAGE,
        crumb_label="Bursaries by university",
        uni_name_esc=esc(uni_name),
        h1=esc(f"{uni_name} Bursaries & Scholarships"),
        lede=esc(lede),
        app_store=APP_STORE_URL,
        play=PLAY_URL,
        perks=PERKS_HTML,
        list_heading=esc(f"{count} bursaries currently listed"),
        cards=cards,
        faq=faq_block(uni_name, count),
        related=related_links_html(entries),
        jsonld=faq_jsonld(uni_name),
        breadcrumb=breadcrumb_jsonld([
            ("FindMyFund", f"{SITE_URL}/"),
            ("Bursaries by university", f"{SITE_URL}/bursaries/"),
            (uni_name, canonical),
        ]),
        css=PAGE_CSS,
    )

# ── Rollup page for single-entry universities ───────────────────────────────
def render_rollup(singles):
    all_rows = []
    for uni, rows_ in singles.items():
        for r in rows_:
            all_rows.append((uni, r))
    all_rows.sort(key=lambda t: t[0])
    cards = "".join(bursary_card(r, tag=uni) for uni, r in all_rows)
    count = len(all_rows)
    title = f"More UK University Bursaries ({date.today().year}) | FindMyFund"
    description = f"{count} additional verified UK university bursaries and scholarships, one per institution."
    canonical = f"{SITE_URL}/bursaries/more-universities/"
    lede = (
        f"{count} more verified bursaries, one each from smaller listings across UK universities — "
        f"each linking straight to the official source, no forms with us. Our app also matches you "
        f"to additional grants beyond this list, based on your specific circumstances."
    )
    return PAGE_TEMPLATE.format(
        title=esc(title),
        description=esc(description),
        canonical=canonical,
        og_image=OG_IMAGE,
        crumb_label="Bursaries by university",
        uni_name_esc="More universities",
        h1=esc("More UK University Bursaries"),
        lede=esc(lede),
        app_store=APP_STORE_URL,
        play=PLAY_URL,
        perks=PERKS_HTML,
        list_heading=esc(f"{count} bursaries currently listed"),
        cards=cards,
        faq=faq_block("these universities", count),
        related="",
        jsonld=faq_jsonld("these universities"),
        breadcrumb=breadcrumb_jsonld([
            ("FindMyFund", f"{SITE_URL}/"),
            ("Bursaries by university", f"{SITE_URL}/bursaries/"),
            ("More universities", canonical),
        ]),
        css=PAGE_CSS,
    )

# ── Circumstance-based pages (cut across universities, tag-based not
#    partition-based — the same bursary can legitimately appear on more
#    than one of these, e.g. a low-income care-leaver bursary). ─────────────
def has_vulnerability(row, name):
    v = clean(row.get("Vulnerabilities (multi-select)", ""))
    return name.lower() in [p.strip().lower() for p in v.split(",")]

CIRCUMSTANCES = [
    # (slug, h1, plural noun phrase used in copy, filter fn)
    ("low-income-students", "Bursaries for Low-Income Students",
     "low-income students", lambda r: has_vulnerability(r, "Low income")),
    ("care-leavers", "Bursaries for Care Leavers",
     "care leavers", lambda r: has_vulnerability(r, "Care leaver")),
    ("international-students", "Bursaries for International Students in the UK",
     "international students", lambda r: clean(r.get("Fee status", "")) == "Overseas"),
    ("estranged-students", "Bursaries for Estranged Students",
     "estranged students", lambda r: has_vulnerability(r, "Estranged from family")),
    ("disabled-students", "Bursaries for Disabled Students",
     "disabled students", lambda r: has_vulnerability(r, "Disability")),
    ("refugees-and-asylum-seekers", "Scholarships for Refugees and Asylum Seekers",
     "refugees and asylum seekers", lambda r: has_vulnerability(r, "Refugee or asylum seeker")),
]

def render_tag_page(kind, slug, h1, noun_phrase, rows_matched, crumb_label, lede_tail, canon_by_key,
                     sort_key=None, limit=None):
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
    card_parts = []
    for r in entries_sorted:
        raw_uni = clean(r.get("University", ""))
        resolved = canon_by_key.get(norm_uni_key(raw_uni)) if raw_uni else None
        tag_href = f"/bursaries/{resolved[1]}/" if resolved else None
        card_parts.append(bursary_card(r, tag=raw_uni or None, tag_href=tag_href))
    cards = "".join(card_parts)
    count = len(entries_sorted)
    amt_range = amount_range_text(entries_sorted)
    amt_bit = f" worth {amt_range}" if amt_range else ""
    n_unis = len({clean(r.get("University", "")) for r in entries_sorted if clean(r.get("University", ""))})
    lede = (
        f"{count} verified bursaries and scholarships{amt_bit} for {noun_phrase}, across {n_unis} "
        f"UK universities — each linking straight to the official source, no forms with us. Our app "
        f"also matches you to additional grants beyond this list, based on {lede_tail}."
    )
    title = f"{h1} ({date.today().year}) | FindMyFund"
    description = (
        f"{count} verified bursaries and scholarships for {noun_phrase} at {n_unis} UK universities{amt_bit}. "
        f"See eligibility, deadlines and official application links."
    )
    path_suffix = f"{kind}/{slug}/" if slug else f"{kind}/"
    canonical = f"{SITE_URL}/bursaries/{path_suffix}"
    return PAGE_TEMPLATE.format(
        title=esc(title),
        description=esc(description),
        canonical=canonical,
        og_image=OG_IMAGE,
        crumb_label=crumb_label,
        uni_name_esc=esc(h1),
        h1=esc(h1),
        lede=esc(lede),
        app_store=APP_STORE_URL,
        play=PLAY_URL,
        perks=PERKS_HTML,
        list_heading=esc(f"{count} bursaries currently listed"),
        cards=cards,
        faq=faq_block(noun_phrase, count, scope_phrase=noun_phrase),
        related=related_links_html(rows_matched, exclude=(kind, slug)),
        jsonld=faq_jsonld(noun_phrase, scope_phrase=noun_phrase),
        breadcrumb=breadcrumb_jsonld([
            ("FindMyFund", f"{SITE_URL}/"),
            (crumb_label, f"{SITE_URL}/bursaries/"),
            (h1, canonical),
        ]),
        css=PAGE_CSS,
    )

def render_circumstance_page(slug, h1, noun_phrase, rows_matched, canon_by_key):
    return render_tag_page(
        "circumstance", slug, h1, noun_phrase, rows_matched,
        "Bursaries by circumstance", "your full circumstances", canon_by_key,
    )

# ── Subject-based pages — only the handful of subjects with enough real
#    cross-university volume to be worth a page. The raw "Study subject"
#    field is otherwise messy free-text (course-list dumps, inconsistent
#    naming), so unlike circumstance/region this isn't "every category",
#    just the ones with a genuine, non-thin audience. ──────────────────────
def _subj(row):
    return clean(row.get("Study subject", "")).lower()

def _is_medicine(row):
    # "Veterinary Medicine" contains "medicine" too — keep the two subjects
    # distinct rather than one page silently absorbing the other's rows.
    s = _subj(row)
    return bool(re.search(r"\bmedicine\b", s)) and "veterinary" not in s

SUBJECTS = [
    # (slug, h1, plural noun phrase used in copy, filter fn operating on the row)
    ("law", "Bursaries for Law Students", "law students",
     lambda r: bool(re.search(r"\blaw\b", _subj(r)))),
    ("business", "Bursaries for Business Students", "business students",
     lambda r: bool(re.search(r"\bbusiness\b", _subj(r)))),
    ("medicine", "Bursaries for Medicine Students", "medicine students", _is_medicine),
    ("veterinary-studies", "Bursaries for Veterinary Students", "veterinary students",
     lambda r: "veterinary" in _subj(r)),
    ("music", "Bursaries for Music Students", "music students",
     lambda r: bool(re.search(r"\bmusic\b", _subj(r)))),
]

def render_subject_page(slug, h1, noun_phrase, rows_matched, canon_by_key):
    return render_tag_page(
        "subject", slug, h1, noun_phrase, rows_matched,
        "Bursaries by subject", "your subject and full circumstances", canon_by_key,
    )

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

HUB_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<meta name="description" content="{description}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="{canonical}">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700;800&display=swap">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="website">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{og_image}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{og_image}">
<script type="application/ld+json">{breadcrumb}</script>
<style>{css}
.ulist{{display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:8px;}}
.ulist a{{
  display:block; background:var(--card); border:1px solid var(--border); border-radius:10px;
  padding:12px 14px; font-size:13px; font-weight:600; color:var(--text); text-decoration:none;
}}
.ulist a:hover{{border-color:var(--teal);}}
.ulist .n{{color:var(--text-sec); font-weight:500; font-size:11.5px;}}
</style>
</head>
<body>
<div class="wrap">
  <div class="crumb"><a href="/">FindMyFund</a> / Bursaries</div>
  <h1>UK University Bursaries &amp; Scholarships</h1>
  <p class="lede">{n_total} verified bursaries and scholarships across {n_unis} UK universities — each linking straight to the official source, no forms with us. Pick your university, or let the app match you to these plus additional national and independent grants.</p>
  <div class="cta-row">
    <a class="cta" href="{app_store}" target="_blank" rel="noopener">Get matched — App Store</a>
    <a class="cta ghost" href="{play}" target="_blank" rel="noopener">Get matched — Google Play</a>
  </div>
  <div class="perks">{perks}
  </div>
  <h2>Quick links</h2>
  <div class="ulist">
    <a href="/bursaries/closing-soon/">Bursaries Closing Soon</a>
    <a href="/bursaries/highest-value/">Highest-Value Bursaries</a>
  </div>
  <h2>Browse by circumstance</h2>
  <div class="ulist">{circumstance_links}
  </div>
  <h2>Browse by subject</h2>
  <div class="ulist">{subject_links}
  </div>
  <h2>Browse by region</h2>
  <div class="ulist">{region_links}
  </div>
  <h2>Browse by university</h2>
  <div class="ulist">{links}
  </div>
  <div class="foot">FindMyFund · Bursa Group Ltd</div>
</div>
</body>
</html>
"""

def render_hub(uni_list, singles_count, circumstance_counts, subject_counts, region_counts):
    links = "".join(
        f'<a href="/bursaries/{slug}/">{esc(name)}<div class="n">{count} bursar{"y" if count==1 else "ies"}</div></a>'
        for name, slug, count in uni_list
    )
    links += f'<a href="/bursaries/more-universities/">More universities<div class="n">{singles_count} bursaries</div></a>'
    circumstance_links = "".join(
        f'<a href="/bursaries/circumstance/{slug}/">{esc(h1)}<div class="n">{count} bursaries</div></a>'
        for slug, h1, count in circumstance_counts
    )
    subject_links = "".join(
        f'<a href="/bursaries/subject/{slug}/">{esc(h1)}<div class="n">{count} bursaries</div></a>'
        for slug, h1, count in subject_counts
    )
    region_links = "".join(
        f'<a href="/bursaries/region/{slug}/">{esc(h1)}<div class="n">{count} bursaries</div></a>'
        for slug, h1, count in region_counts
    )
    n_total = sum(c for _, _, c in uni_list) + singles_count
    n_unis = len(uni_list) + singles_count
    canonical = f"{SITE_URL}/bursaries/"
    title = "UK University Bursaries & Scholarships — Browse by University | FindMyFund"
    description = f"Browse verified bursaries and scholarships at {n_unis} UK universities, covering {n_total} funds in total. Free to search."
    return HUB_TEMPLATE.format(
        title=esc(title),
        description=esc(description),
        n_unis=n_unis,
        n_total=n_total,
        canonical=canonical,
        og_image=OG_IMAGE,
        css=PAGE_CSS,
        app_store=APP_STORE_URL,
        play=PLAY_URL,
        perks=PERKS_HTML,
        circumstance_links=circumstance_links,
        subject_links=subject_links,
        region_links=region_links,
        links=links,
        breadcrumb=breadcrumb_jsonld([
            ("FindMyFund", f"{SITE_URL}/"),
            ("Bursaries", canonical),
        ]),
    )

def submit_indexnow(urls):
    """Tells Bing/Yandex/Seznam about changed URLs immediately instead of
    waiting for their crawler to notice — free, no auth beyond the public
    key file already hosted at the site root. Only runs against live data
    (SEO_DATA_URL set); a local dev run shouldn't spam this on every tweak."""
    if not urls or not SEO_DATA_URL:
        return
    payload = json.dumps({
        "host": "findmyfund.co.uk",
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

uni_list = []
for uni, entries in sorted(multi.items()):
    slug = slugify(uni)
    d = os.path.join(OUT_DIR, slug)
    os.makedirs(d, exist_ok=True)
    url = f"{SITE_URL}/bursaries/{slug}/"
    write_page(url, os.path.join(d, "index.html"), render_page(uni, entries, slug), lastmod_map, changed_urls)
    uni_list.append((uni, slug, len(entries)))

# Maps a normalised university key -> (canonical display name, slug), for
# the tag pages to link a bursary card's university tag back to that
# university's own page (only universities with >=2 bursaries get one).
canon_by_key = {norm_uni_key(name): (name, slug) for name, slug, _ in uni_list}

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

# hub
hub_url = f"{SITE_URL}/bursaries/"
write_page(hub_url, os.path.join(OUT_DIR, "index.html"), render_hub(uni_list, len(singles), circumstance_counts, subject_counts, region_counts), lastmod_map, changed_urls)

# home page — not generated here (index.html at repo root is hand-authored),
# but it's in the sitemap, so give it a lastmod based on its own file mtime
# rather than silently omitting it or always stamping it "today".
home_url = f"{SITE_URL}/"
if os.path.exists("index.html"):
    lastmod_map.setdefault(home_url, date.fromtimestamp(os.path.getmtime("index.html")).isoformat())

# sitemap — every URL's lastmod comes from lastmod_map (only bumped above
# when that page's content actually changed), not blindly stamped TODAY.
urls = [home_url, hub_url, rollup_url]
urls += [f"{SITE_URL}/bursaries/{slug}/" for _, slug, _ in uni_list]
urls += [f"{SITE_URL}/bursaries/circumstance/{slug}/" for slug, _, _ in circumstance_counts]
urls += [f"{SITE_URL}/bursaries/subject/{slug}/" for slug, _, _ in subject_counts]
urls += [f"{SITE_URL}/bursaries/region/{slug}/" for slug, _, _ in region_counts]
urls += [f"{SITE_URL}/bursaries/closing-soon/" for _ in closing_soon_counts]
urls += [f"{SITE_URL}/bursaries/highest-value/" for _ in highest_value_counts]
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
    f"Built {len(uni_list)} university pages + 1 rollup + {len(circumstance_counts)} circumstance pages + "
    f"{len(subject_counts)} subject pages + {len(region_counts)} region pages + "
    f"{len(closing_soon_counts)} closing-soon + {len(highest_value_counts)} highest-value + hub + sitemap "
    f"({len(urls)} URLs total, {len(changed_urls)} changed this run)."
)
