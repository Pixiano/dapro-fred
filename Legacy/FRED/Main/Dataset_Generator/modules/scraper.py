# modules/scraper.py
import time
import random
import os
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
}

MIN_CONTENT_CHARS = 400  # require at least this many characters to consider real article

def _setup_driver(headless=True):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1200,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"--user-agent={HEADERS['User-Agent']}")
    try:
        driver = webdriver.Chrome(ChromeDriverManager().install(), options=opts)
    except WebDriverException:
        driver = webdriver.Chrome(options=opts)
    return driver

def _human_delay(a=0.5, b=1.8):
    time.sleep(random.uniform(a, b))

def _extract_text_from_driver(driver):
    """
    Prefer <article> text; fallback to main, then body.
    Return large cleaned text or empty string.
    """
    try:
        # try article tag
        article_el = driver.find_elements(By.TAG_NAME, "article")
        if article_el:
            text = article_el[0].text
        else:
            # fallback: main tag
            mains = driver.find_elements(By.TAG_NAME, "main")
            if mains:
                text = mains[0].text
            else:
                text = driver.find_element(By.TAG_NAME, "body").text
        # basic cleanup: collapse repeated whitespace, remove nav lines
        lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) > 30]
        return "\n".join(lines)
    except Exception:
        return ""

def scrape(article_entries, headless=True, raw_dir="data/output/raw_html"):
    """
    article_entries: list of {"keyword","source","url"}
    Returns list of dicts: {"keyword","source","url","content"}
    Saves a simple text file copy in raw_dir for debugging.
    """
    os.makedirs(raw_dir, exist_ok=True)
    driver = _setup_driver(headless=headless)
    results = []
    try:
        for item in article_entries:
            url = item.get("url")
            source = item.get("source")
            keyword = item.get("keyword")
            print(f"[INFO] Opening article: {url}")
            try:
                driver.get(url)
            except Exception as e:
                print(f"[WARN] driver.get failed for {url}: {e}")
                continue
            # Wait some random time for JS content to load
            _human_delay(1.2, 3.0)

            content = _extract_text_from_driver(driver)
            if not content or len(content) < MIN_CONTENT_CHARS:
                print(f"[WARN] Content too short or empty for {url} (len={len(content)})")
                continue

            # Save to file for reference
            safe_name = f"{source}_{keyword}_{abs(hash(url))}.txt"
            safe_name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in safe_name)
            file_path = os.path.join(raw_dir, safe_name)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            results.append({
                "keyword": keyword,
                "source": source,
                "url": url,
                "content": content
            })

            # polite pause
            _human_delay(0.8, 2.0)

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print(f"\n[INFO] Total scraped and saved articles: {len(results)}")
    return results