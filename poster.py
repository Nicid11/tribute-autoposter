# poster.py — fresh final
import os, sys, time, random, re, pathlib
from datetime import datetime, timedelta

import pandas as pd
import yaml
from slugify import slugify
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ---------------- Paths / files ----------------
REPO_DIR = pathlib.Path(__file__).parent
DATA_DIR = REPO_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
LOG_CSV = DATA_DIR / "posts.csv"
SHOT_DIR = DATA_DIR / "shots"
SHOT_DIR.mkdir(exist_ok=True)

# ---------------- Secrets ----------------
TARGET_URL = os.getenv("TARGET_URL", "").strip()
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip()
CONTACT_PHONE = os.getenv("CONTACT_PHONE", "").strip()
BRAND_NAME = os.getenv("BRAND_NAME", "Simply Averie").strip()
if not TARGET_URL:
    print("Missing TARGET_URL secret.", file=sys.stderr); sys.exit(2)

# ---------------- Loaders ----------------
def load_ads():
    """
    Read ads.csv safely as strings only.
    Skip blank/invalid rows. Replace {{TARGET_URL}} tokens.
    """
    df = pd.read_csv(REPO_DIR / "ads.csv", dtype=str, keep_default_na=False)
    rows = []
    for r in df.to_dict("records"):
        title = (r.get("title") or "").strip()
        body  = (r.get("body")  or "").strip()
        if not title or not body:
            continue
        title = title.replace("{{TARGET_URL}}", TARGET_URL)
        body  = body.replace("{{TARGET_URL}}", TARGET_URL)
        rows.append({"title": title, "body": body})
    if not rows:
        raise RuntimeError("ads.csv has no valid rows")
    random.shuffle(rows)
    return rows

def load_sites():
    with open(REPO_DIR / "sites.yaml", "r", encoding="utf-8") as f:
        y = yaml.safe_load(f)
    defaults = y.get("defaults", {})
    for s in y["sites"]:
        s.setdefault("cooldown_days", defaults.get("cooldown_days", 7))
        s.setdefault("success_text", defaults.get("success_text", []))
    y.setdefault("defaults", {}).setdefault("max_per_run", 6)
    return y

def load_history():
    if LOG_CSV.exists():
        return pd.read_csv(LOG_CSV)
    return pd.DataFrame(columns=["ts","site","url","title","result","detail"])

def save_history(rows):
    old = load_history()
    new = pd.concat([old, pd.DataFrame(rows)], ignore_index=True)
    new.to_csv(LOG_CSV, index=False)

def too_soon(df_hist, site_id, cooldown_days):
    if df_hist.empty: return False
    df = df_hist[df_hist["site"] == site_id]
    if df.empty: return False
    last = pd.to_datetime(df["ts"]).max()
    return (datetime.utcnow() - last) < timedelta(days=cooldown_days)

# ---------------- Heuristics ----------------
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
            opts = sel.query_selector_all("option")
            choice = None
            for opt in opts:
                t = (opt.inner_text() or "").strip()
                if any(h.lower() in t.lower() for h in hints):
                    choice = opt; break
            if choice:
                val = choice.get_attribute("value") or choice.inner_text()
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

def looks_success(page, patterns):
    html = page.content().lower()
    if any(p.lower() in html for p in patterns): return True
    url = page.url
    if re.search(r"/(view|ads?|post|detail|success|thanks)/", url, re.I): return True
    return False

def screenshot(page, site_id, status):
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    fn = SHOT_DIR / f"{ts}-{slugify(site_id)}-{status}.png"
    try:
        page.screenshot(path=str(fn), full_page=True)
    except Exception:
        pass

# ---------------- Posting ----------------
def post_one(play, site, ad):
    site_id = site["id"]
    url = site["new_ad_url"]
    hints = site.get("category_hints", [])
    success_text = site.get("success_text", [])
    result = {"site": site_id, "url": "", "title": ad["title"], "result": "fail", "detail": ""}

    browser = play.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = browser.new_context(user_agent="Mozilla/5.0 autoposter")
    page = ctx.new_page()
    try:
        page.goto(url, timeout=60000)
        time.sleep(random.uniform(1.8, 3.8))

        # try obvious “post ad” links if on a landing page
        try:
            link = page.get_by_text(re.compile(r"(post|publish).{0,6}(ad|now)?", re.I))
            if link:
                link.first.click(timeout=2000)
                time.sleep(random.uniform(1.0, 2.0))
        except Exception:
            pass

        utm = "?utm_source=classifieds&utm_medium=autoposter&utm_campaign=tribute"
        body = f"{ad['body']}\nMore: {TARGET_URL}{utm}"

        guess_and_fill_fields(page, ad["title"], body)
        try_select_category(page, hints)
        click_submit(page)

        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PWTimeout:
            pass
        time.sleep(random.uniform(1.0, 2.0))

        if looks_success(page, success_text):
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

# ---------------- Main ----------------
def main():
    ads = load_ads()
    cfg = load_sites()
    hist = load_history()

    # pick sites not in cooldown and not requiring login
    todo = []
    for s in cfg["sites"]:
        if s.get("require_login", False):
            continue
        if not too_soon(hist, s["id"], s["cooldown_days"]):
            todo.append(s)
    random.shuffle(todo)
    todo = todo[: cfg["defaults"].get("max_per_run", 6)]

    if not todo:
        print("No sites to post today (cooldowns).")
        return

    results = []
    with sync_playwright() as p:
        for s in todo:
            ad = random.choice(ads)
            print(f"[{datetime.utcnow().isoformat()}] Posting to {s['id']} ...")
            r = post_one(p, s, ad)
            print(r)
            results.append({"ts": datetime.utcnow().isoformat(), **r})
            time.sleep(random.uniform(7, 15))  # human-like pacing

    save_history(results)

if __name__ == "__main__":
    main()
