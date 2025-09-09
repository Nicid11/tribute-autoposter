# poster.py — self-contained, no extra libs
import os, sys, time, random, re, csv, pathlib
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"; DATA.mkdir(exist_ok=True)
SHOTS = DATA / "shots"; SHOTS.mkdir(exist_ok=True)
LOG = DATA / "posts.csv"

TARGET_URL = os.getenv("TARGET_URL", "").strip()
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip()
CONTACT_PHONE = os.getenv("CONTACT_PHONE", "").strip()
BRAND_NAME = os.getenv("BRAND_NAME", "Simply Averie").strip()
if not TARGET_URL:
    print("Missing TARGET_URL secret.", file=sys.stderr); sys.exit(2)

def sanitize(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in s)[:80]

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
MAX_PER_RUN = 6

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

def append_log(row: dict):
    write_header = not LOG.exists()
    with LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ts","site","url","title","result","detail"])
        if write_header: w.writeheader()
        w.writerow(row)

BUTTON_TEXTS = ["Post","Submit","Publish","Continue","Create","Place Ad","Post Ad","Proceed","Next","Save","Send"]
TITLE_HINTS = ["title","subject","headline","adtitle","posttitle"]
BODY_HINTS  = ["body","content","message","description","text","post","story","details"]
EMAIL_HINTS = ["email","e-mail","contact"]
NAME_HINTS  = ["name","contactname","fullname"]
PHONE_HINTS = ["phone","telephone","mobile","contactnumber","contact"]

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
                    sel.select_option(value=val)
                    return True
    except Exception:
        pass
    return False

def click_submit(page):
    for t in BUTTON_TEXTS:
        btn = page.query_selector(f'button:has-text("{t}")') or page.query_selector(f'input[type="submit"][value*="{t}" i]')
        if btn: btn.click(); return True
    btn = page.query_selector("button") or page.query_selector('input[type="submit"]')
    if btn: btn.click(); return True
    page.keyboard.press("Enter"); return True

def looks_success(page):
    html = page.content().lower()
    if any(p in html for p in ["thank you","your ad","posted","success","submitted"]): return True
    if re.search(r"/(view|ads?|post|detail|success|thanks)/", page.url, re.I): return True
    return False

def screenshot(page, site_id, status):
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    fn = SHOTS / f"{ts}-{sanitize(site_id)}-{status}.png"
    try:
        page.screenshot(path=str(fn), full_page=True)
    except Exception:
        pass

def post_one(pw, site, ad):
    site_id = site["id"]; url = site["new_ad_url"]
    result = {"site":site_id, "url":"", "title":ad["title"], "result":"fail", "detail":""}

    browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        page.goto(url, timeout=60000)
        time.sleep(random.uniform(1.8,3.8))
        try:
            link = page.get_by_text(re.compile(r"(post|publish).{0,6}(ad|now)?", re.I))
            if link: link.first.click(timeout=2000); time.sleep(random.uniform(1.0,2.0))
        except Exception:
            pass

        utm = "?utm_source=classifieds&utm_medium=autoposter&utm_campaign=tribute"
        body = f"{ad['body'].replace('{{TARGET_URL}}', TARGET_URL)}\nMore: {TARGET_URL}{utm}"
        title = ad['title'].replace("{{TARGET_URL}}", TARGET_URL)

        guess_and_fill_fields(page, title, body)
        try_select_category(page, site.get("category_hints", []))
        click_submit(page)

        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PWTimeout:
            pass
        time.sleep(random.uniform(1.0,2.0))

        if looks_success(page):
            screenshot(page, site_id, "ok")
            result.update(result="ok", url=page.url, detail="posted")
        else:
            screenshot(page, site_id, "maybe")
            result.update(detail="no-success-marker")
    except Exception as e:
        screenshot(page, site_id, "error")
        result.update(detail=f"error:{type(e).__name__}:{str(e)[:140]}")
    finally:
        ctx.close(); browser.close()
    return result

def main():
    # cooldown using CSV log
    last = {}
    if LOG.exists():
        with LOG.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    ts = datetime.fromisoformat(row["ts"])
                    site = row.get("site","")
                    if site and (site not in last or ts > last[site]):
                        last[site] = ts
                except Exception:
                    pass

    eligible, now = [], datetime.utcnow()
    for s in SITES:
        cd = int(s.get("cooldown_days", 7))
        if s["id"] not in last or (now - last[s["id"]]) >= timedelta(days=cd):
            eligible.append(s)
    random.shuffle(eligible)
    todo = eligible[:MAX_PER_RUN]
    if not todo:
        print("No sites eligible today."); return

    with sync_playwright() as pw:
        for s in todo:
            ad = random.choice(ADS)
            print(f"[{datetime.utcnow().isoformat()}] Posting to {s['id']} ...")
            r = post_one(pw, s, ad)
            print(r)
            with LOG.open("a", newline="", encoding="utf-8") as f:
                write_header = f.tell() == 0
                w = csv.DictWriter(f, fieldnames=["ts","site","url","title","result","detail"])
                if write_header: w.writeheader()
                w.writerow({"ts": datetime.utcnow().isoformat(), **r})
            time.sleep(random.uniform(7,15))

if __name__ == "__main__":
    main()
