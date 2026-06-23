import time, random
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# -------------------
# Tech news sites with search box selectors
# -------------------
TECH_SITES = [
    {"name": "ZDNet", "url": "https://www.zdnet.com/", "search_selector": "input[name='q']"},
    {"name": "The Verge", "url": "https://www.theverge.com/", "search_selector": "input[type='search']"},
    {"name": "TechCrunch", "url": "https://techcrunch.com/", "search_selector": "input[name='s']"},
    {"name": "Wired", "url": "https://www.wired.com/", "search_selector": "input[type='search']"},
    {"name": "BlogtheTech", "url": "https://www.blogthetech.com/", "search_selector": "input[name='s']"},
    {"name": "Ars Technica", "url": "https://arstechnica.com/", "search_selector": "input[name='s']"},
]

# -------------------
# Chrome setup
# -------------------
def _setup_driver(headless=True):
    print("[DEBUG] Setting up Chrome WebDriver...")
    opts = Options()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    if headless:
        opts.add_argument("--headless=new")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    print("[DEBUG] Chrome WebDriver ready")
    return driver

# -------------------
# Type like a human
# -------------------
def _human_type(element, text):
    print(f"[DEBUG] Typing '{text}' like a human...")
    element.click()
    for ch in text:
        element.send_keys(ch)
        time.sleep(0.05 + random.random() * 0.05)
    print("[DEBUG] Finished typing")

# -------------------
# Search a keyword on a single site
# -------------------
def _search_site(driver, site_info, keyword, top_n=10):
    print(f"[DEBUG] Visiting {site_info['name']} homepage: {site_info['url']}")
    results = []
    driver.get(site_info["url"])
    try:
        # Wait for search box
        search_box = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, site_info["search_selector"]))
        )
        print(f"[DEBUG] Search box found on {site_info['name']}")
        _human_type(search_box, keyword)
        search_box.submit()
        print(f"[DEBUG] Submitted search for '{keyword}' on {site_info['name']}")

        # Wait for search results container to appear
        time.sleep(3 + random.random()*2)  # basic wait
        links = driver.find_elements(By.TAG_NAME, "a")
        print(f"[DEBUG] Found {len(links)} <a> elements on {site_info['name']} search page")

        seen = set()
        for link in links:
            href = link.get_attribute("href")
            if href and keyword.lower() in href.lower() and href not in seen:
                seen.add(href)
                results.append({"site": site_info["name"], "url": href})
                print(f"[DEBUG] Collected link: {href}")
            if len(results) >= top_n:
                break
        print(f"[INFO] {len(results)} links collected on {site_info['name']} for '{keyword}'")

    except Exception as e:
        print(f"[⚠️] Failed on {site_info['name']}: {e}")
    return results

# -------------------
# Main search function
# -------------------
def search(keywords, top_n=10, headless=True):
    print("[INFO] Starting search for keywords...")
    driver = _setup_driver(headless=headless)
    all_results = []

    for keyword in keywords:
        print(f"\n🔍 Searching for keyword: '{keyword}'")
        for site in TECH_SITES:
            print(f"[DEBUG] Processing site: {site['name']}")
            site_results = _search_site(driver, site, keyword, top_n=top_n)
            all_results.extend(site_results)
            print(f"[DEBUG] Total links collected so far: {len(all_results)}")
            time.sleep(1 + random.random()*2)  # human-like delay between sites

    driver.quit()
    print(f"\n✅ Total relevant links collected: {len(all_results)}")
    return all_results