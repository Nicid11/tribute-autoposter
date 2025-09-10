# poster.py — speed/variation mode: ALL sites each run, spintax, city rotation, dedupe, HTML report
import os, sys, time, random, re, csv, json, pathlib, html
from datetime import datetime
from collections import defaultdict
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ----- paths -----
ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"; DATA.mkdir(exist_ok=True)
SHOTS = DATA / "shots"; SHOTS.mkdir(exist_ok=True)
LOG = DATA / "posts.csv"
REPORT = DATA / "report.html"
URLS = DATA / "urls.txt"
STATE = DATA / "state.json"   # keeps recent combos per site to avoid repeats

# ----- secrets -----
TARGET_URL = os.getenv("TARGET_URL","").strip()
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL","").strip()
CONTACT_PHONE = os.getenv("CONTACT_PHONE","").strip()
BRAND_NAME = os.getenv("BRAND_NAME","Simply Averie").strip()
if not TARGET_URL:
    print("Missing TARGET_URL.", file=sys.stderr); sys.exit(2)

# ----- config -----
BUTTON_TEXTS = ["Post","Submit","Publish","Continue","Create","Place Ad","Post Ad","Proceed","Next","Save","Send"]
TITLE_HINTS = ["title","subject","headline","adtitle","posttitle"]
BODY_HINTS  = ["body","content","message","description","text","post","story","details"]
EMAIL_HINTS = ["email","e-mail","contact"]
NAME_HINTS  = ["name","contactname","fullname"]
PHONE_HINTS = ["phone","telephone","mobile","contactnumber","contact"]

# cities to localize copy (edit if you want)
CITIES = [
    "Philadelphia, PA","Camden, NJ","Wilmington, DE","Cherry Hill, NJ","Upper Darby, PA",
    "Norristown, PA","Bensalem, PA","Trenton, NJ","King of Prussia, PA","Levittown, PA",
    "Lansdale, PA","Media, PA","Gloucester City, NJ","Pennsauken, NJ"
]

# -------- spintax + templating --------
SPIN_RX = re.compile(r"\{([^{}]+)\}")

def spin_once(s: str) -> str:
    # replace the innermost {...|...} once
    m = SPIN_RX.search(s)
    if not m: return s
    choices = m.group(1).split("|")
    pick = random.choice(choices).strip()
    return s[:m.start()] + pick + s[m.end():]

def spin(s: str) -> str:
    # expand nested spintax by iterating
    for _ in range(100):
        if "{" not in s: break
        s = spin_once(s)
    return s

def fill_vars(s: str, city: str) -> str:
    return (s.replace("{{CITY}}", city)
             .replace("{{BRAND}}", BRAND_NAME)
             .replace("{{PHONE}}", CONTACT_PHONE or "")
             .replace("{{TARGET_URL}}", TARGET_URL))

def sanitize(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in s)[:90]

# -------- creatives (spintax) --------
TITLES = [
    "{Create|Build|Make} an Online {Obituary|Memorial} in {{CITY}} {Today|Now}",
    "{Shareable|Modern|Dignified} {Memorial|Tribute} Page — {{CITY}}",
    "Skip {print|newspaper} fees — {Online|Modern} {Obituary|Tribute} {{CITY}}",
    "{Fast|Same-day} Online Tribute Pages in {{CITY}}",
    "Elegant {Memorial|Obituary} Pages {Families Share|Built for Sharing} — {{CITY}}",
]

BODIES = [
    "Honor a life with a {dignified|beautiful|modern} page {family can visit|family can share} anytime.\n"
    "Clear pricing. {Fast|Same-day} setup. Start here: {{TARGET_URL}}",

    "Philadelphia area service — create a {shareable|searchable} {memorial|tribute} page in minutes.\n"
    "Details: {{TARGET_URL}}",

    "Stop overpaying for {print|newspaper} obits. Go online with a {clean|elegant} design and {real|wide} visibility.\n"
    "Begin: {{TARGET_URL}}",

    "{Create|Launch} a permanent {memorial|tribute} link for programs and QR codes. {Simple|Quick}. {Respectful|Dignified}.\n"
    "Learn more: {{TARGET_URL}}",

    "{Modern|Contemporary} online {obituary|memorial} pages for {{CITY}} families. {Same-day|Fast} turnaround.\n"
    "Start: {{TARGET_URL}}",
]

# -------- sites (guest-posting first) --------
SITES = [
    {"id":"posteezy","new_ad_url":"https://posteezy.com/","category_hints":["Announcements","Community","Obituary","Services"]},
    {"id":"usnetads","new_ad_url":"https://www.usnetads.com/post/post-free-ads.php","category_hints":["Announcements","Community","Services"]},
    {"id":"adpost4u","new_ad_url":"https://www.adpost4u.com/","category_hints":["Services","Community","Announcements"]},
    {"id":"globalfreeads","new_ad_url":"https://www.global-free-classified-ads.com/","category_hints":["Announcements","Services","Community"]},
    {"id":"adpost","new_ad_url":"https://www.adpost.com/post-ad/","category_hints":["Community","Services","Announcements"]},
    {"id":"postcorn","new_ad_url":"https://www.postcorn.com/","category_hints":["Announcements","Community","Services"]},
    {"id":"adlandpro","new_ad_url":"https://www.adlandpro.com/","category_hints":["Community","Announcements","Services"]},
    {"id":"freeglobalclassifiedads","new_ad_url":"https://www.freeglobalclassifiedads.com/classifieds/postad.php","category_hints":["Announcements","Community","Services"]},
    {"id":"adzone","new_ad_url":"https://www.adzoneclassifieds.com/post-free-ads","category_hints":["Announcements","Community","Services"]},
    {"id":"postadverts","new_ad_url":"https://www.postadverts.com/","category_hints":["Announcements","Community","Services"]},
    {"id":"adcrazy","new_ad_url":"https://www.adcrazy.co.uk/","category_hints":["Announcements","Community","Services"]},
    {"id":"worldfreeads","new_ad_url":"https://www.worldfreeads.com/","category_hints":["Announcements","Community","Services"]},
]

# -------- state (avoid repeats) --------
def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_state(state):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def choose_creative(site_id: str):
    state = load_state()
    site_hist = state.get(site_id, {"recent_keys": []})
    # build a new combo key
    for _ in range(50):
        city = random.choice(CITIES)
        title_t = random.choice(TITLES)
        body_t  = random.choice(BODIES)
        title = fill_vars(spin(title_t), city)
        body  = fill_vars(spin(body_t), city)
        key = f"{city}|{title[:50]}"
        if key not in site_hist.get("recent_keys", []):
            # update history (keep last 6 to avoid repeats)
            rk = site_hist.get("recent_keys", [])
            rk = ([key] + rk)[:6]
            state[site_id] = {"recent_keys": rk}
            save_state(state)
            return title, body, city
    # fallback
    city = random.choice(CITIES)
    return fill_vars(spin(random.choice(TITLES)), city), fill_vars(spin(random.choice(BODIES)), city), city

# -------- helpers --------
def append_log(row):
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
    except Exception: pass
    return False

def click_submit(page):
    for t in BUTTON_TEXTS:
        btn = page.query_selector(f'button:has-text("{t}")') or page.query_selector(f'input[type="submit"][value*="{t}" i]')
        if btn:
            btn.click(); page.wait_for_timeout(900)
            try: btn.click()
            except Exception: pass
            return True
    btn = page.query_selector("button") or page.query_selector('input[type="submit"]')
    if btn: btn.click(); page.wait_for_timeout(900); return True
    page.keyboard.press("Enter"); page.wait_for_timeout(900); return True

def looks_success(page):
    html_l = page.content().lower()
    if any(p in html_l for p in ["thank you","your ad","posted","success","submitted","awaiting approval"]): return True
    if re.search(r"/(view|ads?|post|detail|success|thanks|submitted)/", page.url, re.I): return True
    return False

def take_shot(page, site_id, status):
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    fn = f"{ts}-{sanitize(site_id)}-{status}.png"; path = SHOTS / fn
    try: page.screenshot(path=str(path), full_page=True)
    except Exception: pass
    return f"shots/{fn}"

def post_one(pw, site):
    site_id = site["id"]; url = site["new_ad_url"]
    title, body, city = choose_creative(site_id)
    result = {"site":site_id,"url":"","title":title,"result":"fail","detail":"","shot":""}

    browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        page.goto(url, timeout=60000); page.wait_for_load_state("domcontentloaded", timeout=30000)
        page.wait_for_timeout(random.uniform(900,1500))
        try:
            link = page.get_by_text(re.compile(r"(post|publish).{0,6}(ad|now)?", re.I))
            if link: link.first.click(timeout=2500); page.wait_for_timeout(800)
        except Exception: pass

        utm = f"?utm_source=classifieds&utm_medium=autoposter&utm_campaign=tribute&utm_content={sanitize(site_id)}"
        body_final  = body + f"\nMore: {TARGET_URL}{utm}"
        title_final = title

        guess_and_fill_fields(page, title_final, body_final)
        try_select_category(page, site.get("category_hints", []))
        click_submit(page)

        for _ in range(3):
            try: page.wait_for_load_state("networkidle", timeout=8000)
            except Exception: pass
            page.wait_for_timeout(900)
            if looks_success(page): break

        if looks_success(page):
            result.update(result="ok", url=page.url, detail=f"posted ({city})", shot=take_shot(page, site_id, "ok"))
        else:
            result.update(detail=f"no-success-marker ({city})", shot=take_shot(page, site_id, "maybe"))
    except Exception as e:
        result.update(detail=f"error:{type(e).__name__}:{str(e)[:140]}", shot=take_shot(page, site_id, "error"))
    finally:
        ctx.close(); browser.close()
    return result

def build_report(rows):
    with URLS.open("w", encoding="utf-8") as f:
        for r in rows:
            if r["result"]=="ok" and r["url"]: f.write(r["url"]+"\n")
    head = """<!doctype html><meta charset="utf-8"><title>Autoposter Report</title>
<style>body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:20px}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;font-size:14px}
th{background:#f3f3f3} .ok{color:#0a7a2a;font-weight:600} .fail{color:#a00;font-weight:600}
img{max-width:420px;border:1px solid #ddd}</style>"""
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
    REPORT.write_text(head+"\n"+"\n".join(body), encoding="utf-8")

def main():
    rows = []
    with sync_playwright() as pw:
        for s in SITES:  # ALL sites each run
            print(f"[{datetime.utcnow().isoformat()}] Posting to {s['id']} ...")
            r = post_one(pw, s)
            print(r)
            append_log({"ts": datetime.utcnow().isoformat(), **r})
            rows.append(r)
            time.sleep(random.uniform(6,12))
    build_report(rows)

if __name__ == "__main__":
    main()

