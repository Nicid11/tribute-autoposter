import os, sys, time, random, re, csv, json, pathlib
from datetime import datetime, timedelta
import yaml
import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

REPO_DIR = pathlib.Path(__file__).parent
DATA_DIR = REPO_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
LOG_CSV = DATA_DIR / "posts.csv"

TARGET_URL = os.getenv("TARGET_URL", "").strip()
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip()
CONTACT_PHONE = os.getenv("CONTACT_PHONE", "").strip()
BRAND_NAME = os.getenv("BRAND_NAME", "Simply Averie").strip()

if not TARGET_URL:
    print("Missing TARGET_URL secret.", file=sys.stderr)
    sys.exit(2)

def load_ads():
    df = pd.read_csv(REPO_DIR / "ads.csv")
    # simple rotate
    records = df.to_dict("records")
    random.shuffle(records)
    # replace tokens
    for r in records:
        for k in ["title","body"]:
            r[k] = r[k].replace("{{TARGET_URL}}", TARGET_URL)
    return records

def load_sites():
    with open(REPO_DIR / "sites.yaml","r",encoding="utf-8") as f:
        y = yaml.safe_load(f)
    defaults = y.get("defaults",{})
    for s in y["sites"]:
        s.setdefault("cooldown_days", defaults.get("cooldown_days", 7))
        s.setdefault("success_text", defaults.get("success_text", []))
    return y

def load_history():
    if LOG_CSV.exists():
        return pd.read_csv(LOG_CSV)
    return pd.DataFrame(columns=["ts","site","url","title","result","detail"])

def save_history(rows):
    df_old = load_history()
    df_new = pd.concat([df_old, pd.DataFrame(rows)], ignore_index=True)
    df_new.to_csv(LOG_CSV, index=False)

def too_soon(df_hist, site_id, cooldown_days):
    if df_hist.empty: return False
    df = df_hist[df_hist["site"]==site_id]
    if df.empty: return False
    last = pd.to_datetime(df["ts"]).max()
    return (datetime.utcnow() - last) < timedelta(days=cooldown_days)

BUTTON_TEXTS = [
    "Post","Submit","Publish","Continue","Create","Place Ad","Post Ad","Proceed","Next","Save"
]
TITLE_HINTS = ["title","subject","headline","adtitle","posttitle"]
BODY_HINTS  = ["body","content","message","description","text","post","story"]
EMAIL_HINTS = ["email","e-mail","contact"]
NAME_HINTS  = ["name","contactname","fullname"]
PHONE_HINTS = ["phone","telephone","mobile","contactnumber"]

def find_first_field(page, selectors):
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el: return el
        except PWTimeout:
            pass
    return None

def guess_and_fill_fields(page, title, body):
    # try inputs by name/placeholder/label text
    def fill_by_hint(hints, value):
        # by input[name*]
        for h in hints:
            el = page.query_selector(f'input[name*="{h}" i]')
            if el:
                el.fill(value); return True
        # by textarea[name*]
        for h in hints:
            el = page.query_selector(f'textarea[name*="{h}" i]')
            if el:
                el.fill(value); return True
        # by placeholder
        for h in hints:
            el = page.query_selector(f'input[placeholder*="{h}" i]')
            if el:
                el.fill(value); return True
        # generic fallbacks
        el = page.query_selector("input[type='text']") or page.query_selector("textarea")
        if el:
            el.fill(value); return True
        return False

    ok1 = fill_by_hint(TITLE_HINTS, title[:120])
    ok2 = fill_by_hint(BODY_HINTS, body[:4000])
    # optional contact
    if CONTACT_EMAIL:
        fill_by_hint(EMAIL_HINTS, CONTACT_EMAIL)
    if BRAND_NAME:
        fill_by_hint(NAME_HINTS, BRAND_NAME)
    if CONTACT_PHONE:
        fill_by_hint(PHONE_HINTS, CONTACT_PHONE)
    return ok1 or ok2

def try_select_category(page, hints):
    # choose first select and pick option matching hints
    try:
        selects = page.query_selector_all("select")
        for sel in selects:
            opts = sel.query_selector_all("option")
            chosen = None
            for opt in opts:
                t = (opt.inner_text() or "").strip()
                if any(h.lower() in t.lower() for h in hints):
                    chosen = opt
                    break
            if chosen:
                val = chosen.get_attribute("value")
                sel.select_option(value=val)
                return True
    except Exception:
        pass
    return False

def click_submit(page):
    # buttons by text
    for t in BUTTON_TEXTS:
        btn = page.query_selector(f'button:has-text("{t}")') or page.query_selector(f'input[type="submit"][value*="{t}" i]')
        if btn:
            btn.click()
            return True
    # generic submit
    btn = page.query_selector("button") or page.query_selector('input[type="submit"]')
    if btn:
        btn.click()
        return True
    # press Enter as last resort
    page.keyboard.press("Enter")
    return True

def looks_success(page, patterns):
    html = page.content().lower()
    if any(p.lower() in html for p in patterns):
        return True
    # if redirected to a view page with an id-like path
    url = page.url
    if re.search(r"/(view|ads?|post|detail)/", url, re.I):
        return True
    return False

def post_one(play, site, ad):
    site_id = site["id"]
    url = site["new_ad_url"]
    hints = site.get("category_hints",[])
    success_text = site.get("success_text",[])
    result = {"site":site_id, "url":"", "title":ad["title"], "result":"fail", "detail":""}

    browser = play.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        page.goto(url, timeout=45000)
        time.sleep(random.uniform(1.5,3.5))

        # try to click obvious "Post Ad" links if on a home page
        links = page.get_by_text(re.compile(r"(post|publish).{0,6}(ad|now)?", re.I))
        try:
            if links:
                links.first.click(timeout=2000)
                time.sleep(random.uniform(1.0,2.0))
        except Exception:
            pass

        # fill fields
        ok = guess_and_fill_fields(page, ad["title"], f'{ad["body"]}\nMore: {TARGET_URL}')
        try_select_category(page, hints)
        click_submit(page)

        # wait for navigation or success text
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PWTimeout:
            pass
        time.sleep(random.uniform(1.0,2.0))

        if looks_success(page, success_text):
            result["result"] = "ok"
            result["url"] = page.url
            result["detail"] = "posted"
        else:
            result["detail"] = "no-success-marker"
    except Exception as e:
        result["detail"] = f"error:{type(e).__name__}:{str(e)[:140]}"
    finally:
        ctx.close(); browser.close()
    return result

def main():
    ads = load_ads()
    cfg = load_sites()
    df_hist = load_history()
    to_post = []
    for site in cfg["sites"]:
        if too_soon(df_hist, site["id"], site["cooldown_days"]):
            continue
        to_post.append(site)
    random.shuffle(to_post)
    max_per = cfg["defaults"].get("max_per_run",5)
    to_post = to_post[:max_per]

    results = []
    with sync_playwright() as p:
        for site in to_post:
            ad = random.choice(ads)
            print(f"Posting to {site['id']} ...")
            r = post_one(p, site, ad)
            print(r)
            results.append({
                "ts": datetime.utcnow().isoformat(),
                **r
            })
            # gentle pacing
            time.sleep(random.uniform(6,12))

    if results:
        save_history(results)

if __name__ == "__main__":
    main()
