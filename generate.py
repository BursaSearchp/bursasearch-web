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
# (shortest variant, with no leading "The" / trailing ", The" / slash suffix
# — reads cleanest as a page title).
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
for key, entries in _raw_by_key.items():
    names = _names_by_key[key]
    canonical = min(names, key=lambda n: (n.lower().startswith("the "), "/" in n, len(n)))
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

def bursary_card(row):
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

    return f"""
      <div class="card">
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
"""

def faq_block(uni_name, count):
    items = [
        ("Is this list free to use?",
         f"Yes. Every bursary listed here for {uni_name} is free to browse — no account or payment needed to see what's available."),
        ("Are these bursaries verified?",
         "Each entry links directly to the official university or provider page so you can confirm eligibility and deadlines yourself before applying."),
        ("How do I know which ones I actually qualify for?",
         "This page lists what's publicly available. FindMyFund's app checks your specific circumstances — income, region, fee status and more — against the full database and tells you exactly which ones you qualify for and why."),
    ]
    out = []
    for q, a in items:
        out.append(f"""
        <details>
          <summary>{esc(q)}</summary>
          <p>{esc(a)}</p>
        </details>""")
    return "".join(out)

def faq_jsonld(uni_name):
    items = [
        ("Is this list free to use?",
         f"Yes. Every bursary listed here for {uni_name} is free to browse — no account or payment needed to see what's available."),
        ("Are these bursaries verified?",
         "Each entry links directly to the official university or provider page so you can confirm eligibility and deadlines yourself before applying."),
        ("How do I know which ones I actually qualify for?",
         "This page lists what's publicly available. FindMyFund's app checks your specific circumstances against the full database and tells you exactly which ones you qualify for and why."),
    ]
    entities = ",".join(
        f'{{"@type":"Question","name":{q!r},"acceptedAnswer":{{"@type":"Answer","text":{a!r}}}}}'
        for q, a in items
    )
    return f'{{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[{entities}]}}'

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
  <div class="crumb"><a href="/">FindMyFund</a> / <a href="/bursaries/">Bursaries by university</a> / {uni_name_esc}</div>
  <h1>{h1}</h1>
  <p class="lede">{lede}</p>

  <div class="cta-row">
    <a class="cta" href="{app_store}" target="_blank" rel="noopener">Get matched — App Store</a>
    <a class="cta ghost" href="{play}" target="_blank" rel="noopener">Get matched — Google Play</a>
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
        f"{uni_name}. Every listing links to the official source — or let the app match you "
        f"automatically to the ones you actually qualify for."
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
        uni_name_esc=esc(uni_name),
        h1=esc(f"{uni_name} Bursaries & Scholarships"),
        lede=esc(lede),
        app_store=APP_STORE_URL,
        play=PLAY_URL,
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
    cards = ""
    for uni, r in all_rows:
        card = bursary_card(r).replace(
            '<h3>', f'<div class="uni-tag">{esc(uni)}</div><h3>', 1
        )
        cards += card
    count = len(all_rows)
    title = f"More UK University Bursaries ({date.today().year}) | FindMyFund"
    description = f"{count} additional verified UK university bursaries and scholarships, one per institution."
    canonical = f"{SITE_URL}/bursaries/more-universities/"
    lede = (
        f"{count} more verified bursaries, one each from smaller listings across UK universities. "
        f"Every listing links to the official source — or let the app match you automatically."
    )
    extra_css = ".uni-tag{font-size:10.5px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; color:var(--teal-br); margin-bottom:4px;}"
    return PAGE_TEMPLATE.format(
        title=esc(title),
        description=esc(description),
        canonical=canonical,
        uni_name_esc="More universities",
        h1=esc("More UK University Bursaries"),
        lede=esc(lede),
        app_store=APP_STORE_URL,
        play=PLAY_URL,
        list_heading=esc(f"{count} bursaries currently listed"),
        cards=cards,
        faq=faq_block("these universities", count),
        jsonld=faq_jsonld("these universities"),
        css=PAGE_CSS + extra_css,
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
  <div class="crumb"><a href="/">FindMyFund</a> / Bursaries by university</div>
  <h1>UK University Bursaries &amp; Scholarships</h1>
  <p class="lede">{n_total} verified bursaries and scholarships across {n_unis} UK universities. Pick your university, or let the app match you automatically to what you actually qualify for.</p>
  <div class="cta-row">
    <a class="cta" href="{app_store}" target="_blank" rel="noopener">Get matched — App Store</a>
    <a class="cta ghost" href="{play}" target="_blank" rel="noopener">Get matched — Google Play</a>
  </div>
  <h2>Browse by university</h2>
  <div class="ulist">{links}
  </div>
  <div class="foot">FindMyFund · Bursa Group Ltd</div>
</div>
</body>
</html>
"""

def render_hub(uni_list, singles_count):
    links = "".join(
        f'<a href="/bursaries/{slug}/">{esc(name)}<div class="n">{count} bursar{"y" if count==1 else "ies"}</div></a>'
        for name, slug, count in uni_list
    )
    links += f'<a href="/bursaries/more-universities/">More universities<div class="n">{singles_count} bursaries</div></a>'
    n_total = sum(c for _, _, c in uni_list) + singles_count
    return HUB_TEMPLATE.format(
        n_unis=len(uni_list) + singles_count,
        n_total=n_total,
        canonical=f"{SITE_URL}/bursaries/",
        css=PAGE_CSS,
        app_store=APP_STORE_URL,
        play=PLAY_URL,
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

# hub
with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
    f.write(render_hub(uni_list, len(singles)))

# sitemap
urls = [f"{SITE_URL}/", f"{SITE_URL}/bursaries/", f"{SITE_URL}/bursaries/more-universities/"]
urls += [f"{SITE_URL}/bursaries/{slug}/" for _, slug, _ in uni_list]
sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
for u in urls:
    sitemap += f"  <url><loc>{u}</loc><lastmod>{TODAY}</lastmod></url>\n"
sitemap += "</urlset>\n"
with open("sitemap.xml", "w", encoding="utf-8") as f:
    f.write(sitemap)

with open("robots.txt", "w", encoding="utf-8") as f:
    f.write(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")

print(f"Built {len(uni_list)} university pages + 1 rollup + hub + sitemap ({len(urls)} URLs total).")
