# poster.py — single-file suite: posts, logs, screenshots, HTML report with links
import os, sys, time, random, re, csv, pathlib, html
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ---- paths ----
ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"; DATA.mkdir(exist_ok=True)
SHOTS = DATA / "shots"; SHOTS.mkdir(exist_ok=True)
LOG = DATA / "posts.csv"
REPORT = DATA / "report.html"
URLS = DATA / "urls.txt"

# ---- secrets ----
TARGET_URL = os.getenv("TARGET_URL", "").strip()
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip()
CONTACT_PHONE = os.getenv("CONTACT_PHONE", "").strip()
BRAND_NAME = os.getenv("BRAND_NAME", "Simply Averie").strip()
if not TARGET_URL:
    print("Missing TARGET_URL secret.", file=sys.stderr); sys.exit(2)

# ---- config ----
MAX_PER_RUN = 10  # post to up to 10 sites per run
BUTTON_TEXTS = ["Post","Submit","Publish","Continue","Create","Place Ad","Post Ad","Proceed","Next","Save","Send"]
TITLE_HINTS = ["title","subject","headline","adtitle","posttitle"]
BODY_HINTS  = ["body","content","message","description","text","post","story","details"]
EMAIL_HINTS = ["email","e-mail","contact"]
NAME_HINTS  = ["name","contactname","fullname"]
PHONE_HINTS = ["phone","telephone","mobile","contactnumber","contact"]

ADS = [
    {"title":"Skip $2,000 newspaper fees—modern obituary service","body":"Honor your loved one with a dignified online tribute and real visibility. Start today: {{TARGET_URL}}"},
    {"title":"Philadelphia families: professional online memorials","body":"Create once, share everywhere. Elegant, permanent, easy. Details: {{TARGET_URL}}"},
    {"title":"A better obituary—online, searchable, shareable","body":"Stop paying print premiums. Modern alternative that looks beautiful. Learn more: {{TARGET_URL}}"},
    {"title":"Create a respectful tribute in minutes","body":"Premium designs and serious reach options. Begin here: {{TARGET_URL}}"},
    {"title":"Permanent memorial link for family and friends","body":"One page they can visit forever. Set up now: {{TARGET_URL}}"},
    {"title":"The people’s obituary platform—fast and dignified","body":"Handled with care. Built for sharing. Start here: {{TARGET_URL}}"},
    {"title":"Online tribute with optional visibility blast","body":"Your loved one deserves to be seen. Explore options: {{TARGET_URL}}"},
    {"title":"Philadelphia obituary alternative—same day","body":"No runaround. Clear pricing. See packages: {{TARGET_URL}}"},
    {"title":"Make the memory visible, not expensive","body":"Modern tribute + optional syndication. Info: {{TARGET_URL}}"},
    {"title":"Create once. Share everywhere. Keep forever.","body":"A link that travels with the family. Details: {{TARGET_URL}}"},
    {"title":"Elegant online memorial pages","body":"Professional look. Quick turnaround. Begin: {{TARGET_URL}}"},
    {"title":"Obituary without the print cost","body":"Set up a lasting page you control. Learn more: {{TARGET_URL}}"},
    {"title":"Make a tribute that people actually see","body":"Built for phones, social, and search. Start now: {{TARGET_URL}}"},
    {"title":"From grief to remembrance—done right","body":"Clarity, dignity, permanence. Options here: {{TARGET_URL}}"},
    {"title":"A modern home for a life well lived","body":"Clean design, shareable link, clear upgrades. Visit: {{TARGET_URL}}"},
    {"title":"Respectful, permanent, and easy to share","body":"Philadelphia-ready service. Begin here: {{TARGET_URL}}"},
    {"title":"Your link for the funeral program & QR","body":"Point guests to photos and details online. See how: {{TARGET_URL}}"},
    {"title":"Stop overpaying for print obits","body":"Choose a smarter, modern option. Details: {{TARGET_URL}}"},
    {"title":"Create a tribute page families will use","body":"Clear, fast, professional. Explore: {{TARGET_URL}}"},
    {"title":"Online memorials with serious reach","body":"Upgrade options when you’re ready. Start: {{TARGET_URL}}"},
]

SITES = [
    {"id":"posteezy","new_ad_url":"https://posteezy.com/","category_hints":["Announcements","Community","Obituary","Services"],"cooldown_days":7},
    {"id":"usnetads","new_ad_url":"https://www.usnetads.com/post/post-free-ads.php","category_hints":["Announcements","Community","Services"],"cooldown_days":7},
    {"id":"adpost4u","new_ad_url":"https://www.adpost4u.com/","category_hints":["Services","Community","Announcements"],"cooldown_days":7},
    {"id":"globalfreeads","new_ad_url":"https://www.global-free-classified-ads.com/","category_hints":["Announcements","Services","Community"],"cooldown_days":7},
    {"id":"adpost","new_ad_url":"https://www.adpost.com/post-ad/","category_hints":["Community","Services","Announcements"],"cooldown_days":7},
    {"id":"postcorn","new_ad_url":"https://www.postcorn.com/","category_hints":["Announcements","Community","Services"],"cooldown_days":7},
    {"id":"adlandpro","new_ad_url":"https://www.adlandpro.com/","category_hints":["Community","Announcements","Services"],"cooldown_days":7},
    {"id":"freeglobalclassifiedads","new_ad_url":"https://www.freeglobalclassifiedads.com/classifieds/postad.php","category_hints":["Announcements","Community","Services"],"cooldown_days":7},
    {"id":"adzone","new_ad_url":"https://www.adzoneclassifieds.com/post-free-ads","category_hints":["Announcements","Community","Services"],"cooldown_days":7},
    {"id":"postadverts","new_ad_url":"https://www.postadverts.com/","category_hints":["Announcements","Community","Services"],"cooldown_days":7},
    {"id":"adcrazy","new_ad_url":"https://www.adcrazy.co.uk/","category_hints":["Announcements","Community","Services"],"cooldown_days":7},
    {"id":"worldfreeads","new_ad_url":"https://www.worldfreeads.com/","category_hints":["Announcements","Community","Services"],"cooldown_days":7},
]

# ---- utils ----
def sanitize(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in s)[:80]

def read_last_by_site():
    m = {}
    if LOG.exists():
        with LOG.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    ts = datetime.fromisoformat(row["ts"])
                    site = row.get("site","")
                    if site and (site not in m or ts > m[site]):
                        m[site] = ts
                except Exception:
                    continue
    return m

def append_log_row(row):
    write_header = not LOG.exists()
    with LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ts","site","url","title","result","detail","shot"])
        if write_header: w.writeheader()
        w.writerow(row)

def fill_by_hints(page, hints, value):
    for h in hints:
        q = page.query_selector(f'input[name*="{h}" i]')
        if q: q.fill(value); return True
    for h in hints:
        q = page.query_selector(f'textarea[name*="{h}" i]')
        if q: q.fill(value); return True
    for h in hints:
        q = page.query_selector(f'input[placeholder*="{h}" i]')
        if q: q.fill(value); return True
    q = page.query_selector("input[type='text']") or page.query_selector("textarea")
    if q: q.fill(value); return True
    return False

def guess_and_fill_fields(page, title, body):
    ok1 = fill_by_hints(page, TITLE_HINTS, title[:120])
    ok2 = fill_by_hints(page, BODY_HINTS,  body[:4000])
    if CONTACT_EMAIL: fill_by_hints(page, EMAIL_HINTS, CONTACT_EMAIL)
    if BRAND_NAME:    fill_by_hints(page, NAME_HINTS,  BRAND_NAME)
    if CONTACT_PHONE: fill_by_hints(page, PHONE_HINTS, CONTACT_PHONE)
    return ok1 or ok2

def try_select_category(page, hints):
    try:
        selects = page.query_selector_all("select")
        for sel in selects:
            for opt in sel.query_selector_all("option"):
                t = (opt.inner_text() or "").strip()
                if any(h.lower() in t.lower() for h in hints):
                    val = opt.get_attribute("value") or opt.inner_text()
                    sel.select_option(value=val); return True
    except Exception:
        pass
    return False

def click_submit(page):
    for t in BUTTON_TEXTS:
        btn = page.query_selector(f'button:has-text("{t}")') or page.query_selector(f'input[type="submit"][value*="{t}" i]')
        if btn:
            btn.click(); page.wait_for_timeout(1200)
            try: btn.click()
            except Exception: pass
            return True
    btn = page.query_selector("button") or page.query_selector('input[type="submit"]')
    if btn: btn.click(); page.wait_for_timeout(1200); return True
    page.keyboard.press("Enter"); page.wait_for_timeout(1200); return True

def looks_success(page):
    html_l = page.content().lower()
    patterns = ["thank you","your ad","posted","success","submitted","awaiting approval","processing"]
    if any(p in html_l for p in patterns): return True
    if re.search(r"/(view|ads?|post|detail|success|thanks|submitted)/", page.url, re.I): return True
    return False

def take_shot(page, site_id, status):
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    fn = f"{ts}-{sanitize(site_id)}-{status}.png"
    path = SHOTS / fn
    try: page.screenshot(path=str(path), full_page=True)
    except Exception: pass
    return f"shots/{fn}"

def post_one(pw, site, ad):
    site_id = site["id"]; url = site["new_ad_url"]
    result = {"site":site_id,"url":"","title":ad["title"],"result":"fail","detail":"","shot":""}

    browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        page.goto(url, timeout=60000)
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        page.wait_for_timeout(random.uniform(1200,2000))

        try:
            link = page.get_by_text(re.compile(r"(post|publish).{0,6}(ad|now)?", re.I))
            if link: link.first.click(timeout=2500); page.wait_for_timeout(1000)
        except Exception:
            pass

        utm = "?utm_source=classifieds&utm_medium=autoposter&utm_campaign=tribute"
        title = ad["title"].replace("{{TARGET_URL}}", TARGET_URL)
        body  = ad["body"].replace("{{TARGET_URL}}", TARGET_URL) + f"\nMore: {TARGET_URL}{utm}"

        guess_and_fill_fields(page, title, body)
        try_select_category(page, site.get("category_hints", []))
        click_submit(page)

        for _ in range(3):
            try: page.wait_for_load_state("networkidle", timeout=10000)
            except Exception: pass
            page.wait_for_timeout(1200)
            if looks_success(page): break

        if looks_success(page):
            result.update(result="ok", url=page.url, detail="posted", shot=take_shot(page, site_id, "ok"))
        else:
            result.update(detail="no-success-marker", shot=take_shot(page, site_id, "maybe"))
    except Exception as e:
        result.update(detail=f"error:{type(e).__name__}:{str(e)[:140]}", shot=take_shot(page, site_id, "error"))
    finally:
        ctx.close(); browser.close()
    return result

def build_report(rows):
    # write urls.txt
    with URLS.open("w", encoding="utf-8") as f:
        for r in rows:
            if r["result"]=="ok" and r["url"]:
                f.write(r["url"]+"\n")

    # write HTML
    head = """<!doctype html><meta charset="utf-8">
<title>Autoposter Report</title>
<style>
body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:20px}
h1{margin:0 0 6px 0}
small{color:#666}
table{border-collapse:collapse;width:100%;margin-top:12px}
td,th{border:1px solid #ddd;padding:8px;font-size:14px;vertical-align:top}
th{background:#f3f3f3;text-align:left}
.ok{color:#0a7a2a;font-weight:600}
.fail{color:#a00;font-weight:600}
code{background:#f5f5f5;padding:2px 4px;border-radius:4px}
img{max-width:420px;border:1px solid #ddd}
</style>"""
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    body = [f"<h1>Autoposter Report</h1><small>Generated {ts}</small>",
            "<table><tr><th>Site</th><th>Result</th><th>Title</th><th>URL</th><th>Screenshot</th><th>Detail</th></tr>"]
    for r in rows:
        url_html = f'<a href="{html.escape(r["url"])}" target="_blank">{html.escape(r["url"])}</a>' if r["url"] else ""
        shot_html = f'<a href="{html.escape(r["shot"])}" target="_blank"><img src="{html.escape(r["shot"])}"></a>' if r["shot"] else ""
        cls = "ok" if r["result"]=="ok" else "fail"
        body.append(f"<tr><td>{html.escape(r['site'])}</td><td class='{cls}'>{html.escape(r['result'])}</td>"
                    f"<td>{html.escape(r['title'])}</td><td>{url_html}</td><td>{shot_html}</td><td><code>{html.escape(r['detail'])}</code></td></tr>")
    body.append("</table>")
    REPORT.write_text(head + "\n" + "\n".join(body), encoding="utf-8")

def main():
    # cooldown by CSV history
    last = read_last_by_site()
    now = datetime.utcnow()
    eligible = [s for s in SITES if s["id"] not in last or (now - last[s["id"]]) >= timedelta(days=int(s.get("cooldown_days",7)))]
    random.shuffle(eligible)
    todo = eligible[:MAX_PER_RUN]
    if not todo:
        print("No sites eligible today."); return

    results = []
    with sync_playwright() as pw:
        for s in todo:
            ad = random.choice(ADS)
            print(f"[{datetime.utcnow().isoformat()}] Posting to {s['id']} ...")
            r = post_one(pw, s, ad)
            print(r)
            row = {"ts": datetime.utcnow().isoformat(), **r}
            append_log_row(row)
            results.append(r)
            time.sleep(random.uniform(7,15))

    build_report(results)

if __name__ == "__main__":
    main()
