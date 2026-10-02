import requests
from bs4 import BeautifulSoup
import json
import time

NUM_KEYWORDS_MIN = 3
NUM_HN_POSTS = 20

def get_keywords():
    keywords = input(f"Enter at least {NUM_KEYWORDS_MIN} keywords, separated by commas: ").split(",")
    keywords = [k.strip() for k in keywords if k.strip()]
    if len(keywords) < NUM_KEYWORDS_MIN:
        raise ValueError(f"Need at least {NUM_KEYWORDS_MIN} keywords")
    return keywords

def fetch_wikipedia_content(keyword):
    url = f"https://en.wikipedia.org/wiki/{keyword.replace(' ', '_')}"
    try:
        print(f"[DEBUG] Fetching Wikipedia page for '{keyword}' -> {url}")
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        paragraphs = soup.select("div.mw-parser-output > p")
        content = "\n".join(p.get_text().strip() for p in paragraphs if p.get_text().strip())
        return content
    except Exception as e:
        print(f"[WARN] Failed to fetch Wikipedia page for '{keyword}': {e}")
        return ""

def fetch_hn_posts(keyword, num_posts=NUM_HN_POSTS):
    hn_url = f"https://hn.algolia.com/api/v1/search?query={keyword}&tags=story&hitsPerPage={num_posts}"
    try:
        print(f"[DEBUG] Fetching Hacker News posts for '{keyword}'")
        r = requests.get(hn_url, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data.get("hits", [])
    except Exception as e:
        print(f"[WARN] Failed to fetch HN posts for '{keyword}': {e}")
        return []

def fetch_url_content(url):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        paragraphs = soup.find_all("p")
        content = "\n".join(p.get_text().strip() for p in paragraphs if p.get_text().strip())
        return content
    except Exception as e:
        print(f"[WARN] Failed to fetch content from {url}: {e}")
        return ""

def main():
    keywords = get_keywords()
    results = []

    for keyword in keywords:
        # Wikipedia
        wiki_content = fetch_wikipedia_content(keyword)
        if wiki_content:
            results.append({
                "source": "Wikipedia",
                "keyword": keyword,
                "title": f"Wikipedia: {keyword}",
                "url": f"https://en.wikipedia.org/wiki/{keyword.replace(' ', '_')}",
                "content": wiki_content
            })
            print(f"[DEBUG] Wikipedia content length for '{keyword}': {len(wiki_content)} chars")

        # Hacker News
        hn_posts = fetch_hn_posts(keyword)
        print(f"[DEBUG] Found {len(hn_posts)} HN posts for '{keyword}'")
        for post in hn_posts:
            url = post.get("url")
            title = post.get("title")
            if url:
                content = fetch_url_content(url)
                if content:
                    results.append({
                        "source": "HackerNews",
                        "keyword": keyword,
                        "title": title,
                        "url": url,
                        "content": content
                    })
                    print(f"[DEBUG] Fetched content from HN post '{title}' -> {len(content)} chars")
                else:
                    print(f"[WARN] No content found at {url}")
            time.sleep(0.5)  # polite delay

    # Save JSONL
    out_file = "data/outputs/wiki_hn_content.jsonl"
    with open(out_file, "w", encoding="utf-8") as f:
        for item in results:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"[INFO] Saved {len(results)} items to {out_file}")
    if results:
        print("[INFO] Sample snippet:")
        print(results[0]["content"][:500] + "...\n")

if __name__ == "__main__":
    main()