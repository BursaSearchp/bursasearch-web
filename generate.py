"""
Generates static, SEO-indexable university bursary pages from the scraped
dataset. Run from inside the bursasearch-web repo:

    python generate.py

Reads _source_data.csv, writes bursaries/<slug>/index.html per university
(>=2 real bursaries), one rollup page for single-entry universities, a hub
index, sitemap.xml and robots.txt.
"""
import csv
import html
import json
import os
import re
from collections import defaultdict
from datetime import date

SITE_URL = "https://findmyfund.co.uk"
APP_STORE_URL = "https://apps.apple.com/app/id6795890396"
PLAY_URL = "https://play.google.com/store/apps/details?id=fresherforgev2.com"
OUT_DIR = "bursaries"
TODAY = date.today().isoformat()

# ── Data load ────────────────────────────────────────────────────────────────
with open("_source_data.csv", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

def clean(v):
    v = (v or "").strip()
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

def bursary_card(row, tag=None):
    name = clean(row.get("Bursary Name", "")) or "Bursary"
    amount = clean(row.get("Amount", ""))
    deadline = clean(row.get("Deadline", ""))
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
    tag_html = f'<div class="uni-tag">{esc(tag)}</div>' if tag else ""

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
.uni-tag{font-size:10.5px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; color:var(--teal-br); margin-bottom:4px;}
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
<script type="application/ld+json">{jsonld}</script>
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

  <div class="foot">
    Bursary details are sourced from official university and provider pages and may change —
    always confirm on the official link before applying. <a href="/">FindMyFund</a> · Bursa Group Ltd
  </div>
</div>
</body>
</html>
"""

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
        jsonld=faq_jsonld(uni_name),
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
        jsonld=faq_jsonld("these universities"),
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

def render_tag_page(kind, slug, h1, noun_phrase, rows_matched, crumb_label, lede_tail):
    """Shared renderer for the cross-cutting tag pages (circumstance, region —
    same shape as a circumstance page, just a different filter axis and a
    different URL prefix)."""
    entries_sorted = sorted(
        rows_matched, key=lambda r: (clean(r.get("University", "")), clean(r.get("Bursary Name", "")))
    )
    cards = "".join(
        bursary_card(r, tag=clean(r.get("University", "")) or None) for r in entries_sorted
    )
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
    canonical = f"{SITE_URL}/bursaries/{kind}/{slug}/"
    return PAGE_TEMPLATE.format(
        title=esc(title),
        description=esc(description),
        canonical=canonical,
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
        jsonld=faq_jsonld(noun_phrase, scope_phrase=noun_phrase),
        css=PAGE_CSS,
    )

def render_circumstance_page(slug, h1, noun_phrase, rows_matched):
    return render_tag_page(
        "circumstance", slug, h1, noun_phrase, rows_matched,
        "Bursaries by circumstance", "your full circumstances",
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

def render_region_page(slug, h1, noun_phrase, rows_matched):
    return render_tag_page(
        "region", slug, h1, noun_phrase, rows_matched,
        "Bursaries by region", "your region and full circumstances",
    )

HUB_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>UK University Bursaries & Scholarships — Browse by University | FindMyFund</title>
<meta name="description" content="Browse verified bursaries and scholarships at {n_unis} UK universities, covering {n_total} funds in total. Free to search.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="{canonical}">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700;800&display=swap">
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
  <h2>Browse by circumstance</h2>
  <div class="ulist">{circumstance_links}
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

def render_hub(uni_list, singles_count, circumstance_counts, region_counts):
    links = "".join(
        f'<a href="/bursaries/{slug}/">{esc(name)}<div class="n">{count} bursar{"y" if count==1 else "ies"}</div></a>'
        for name, slug, count in uni_list
    )
    links += f'<a href="/bursaries/more-universities/">More universities<div class="n">{singles_count} bursaries</div></a>'
    circumstance_links = "".join(
        f'<a href="/bursaries/circumstance/{slug}/">{esc(h1)}<div class="n">{count} bursaries</div></a>'
        for slug, h1, count in circumstance_counts
    )
    region_links = "".join(
        f'<a href="/bursaries/region/{slug}/">{esc(h1)}<div class="n">{count} bursaries</div></a>'
        for slug, h1, count in region_counts
    )
    n_total = sum(c for _, _, c in uni_list) + singles_count
    return HUB_TEMPLATE.format(
        n_unis=len(uni_list) + singles_count,
        n_total=n_total,
        canonical=f"{SITE_URL}/bursaries/",
        css=PAGE_CSS,
        app_store=APP_STORE_URL,
        play=PLAY_URL,
        perks=PERKS_HTML,
        circumstance_links=circumstance_links,
        region_links=region_links,
        links=links,
    )

# ── Build ────────────────────────────────────────────────────────────────────
os.makedirs(OUT_DIR, exist_ok=True)
uni_list = []
for uni, entries in sorted(multi.items()):
    slug = slugify(uni)
    d = os.path.join(OUT_DIR, slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_page(uni, entries, slug))
    uni_list.append((uni, slug, len(entries)))

# rollup
d = os.path.join(OUT_DIR, "more-universities")
os.makedirs(d, exist_ok=True)
with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
    f.write(render_rollup(singles))

# circumstance pages
circumstance_counts = []
for slug, h1, noun_phrase, filt in CIRCUMSTANCES:
    matched = [r for r in rows if filt(r) and clean(r.get("Bursary Name", ""))]
    if not matched:
        continue
    d = os.path.join(OUT_DIR, "circumstance", slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_circumstance_page(slug, h1, noun_phrase, matched))
    circumstance_counts.append((slug, h1, len(matched)))

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
    with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_region_page(slug, h1, noun_phrase, matched))
    region_counts.append((slug, h1, len(matched)))

# hub
with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
    f.write(render_hub(uni_list, len(singles), circumstance_counts, region_counts))

# sitemap
urls = [f"{SITE_URL}/", f"{SITE_URL}/bursaries/", f"{SITE_URL}/bursaries/more-universities/"]
urls += [f"{SITE_URL}/bursaries/{slug}/" for _, slug, _ in uni_list]
urls += [f"{SITE_URL}/bursaries/circumstance/{slug}/" for slug, _, _ in circumstance_counts]
urls += [f"{SITE_URL}/bursaries/region/{slug}/" for slug, _, _ in region_counts]
sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
for u in urls:
    sitemap += f"  <url><loc>{u}</loc><lastmod>{TODAY}</lastmod></url>\n"
sitemap += "</urlset>\n"
with open("sitemap.xml", "w", encoding="utf-8") as f:
    f.write(sitemap)

with open("robots.txt", "w", encoding="utf-8") as f:
    f.write(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")

print(f"Built {len(uni_list)} university pages + 1 rollup + {len(circumstance_counts)} circumstance pages + {len(region_counts)} region pages + hub + sitemap ({len(urls)} URLs total).")
