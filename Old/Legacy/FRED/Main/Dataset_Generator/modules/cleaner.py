from modules.scraper import scrape

def clean(raw_articles):
    """
    Takes raw scraped articles and cleans them for final dataset.
    Ensures content is not empty, strips unnecessary whitespace.
    """
    cleaned = []

    for entry in raw_articles:
        content = entry.get("content", "").strip()
        if not content:
            continue

        cleaned.append({
            "keyword": entry.get("keyword", ""),
            "source": entry.get("source", ""),
            "url": entry.get("url", ""),
            "content": content
        })

    print(f"[INFO] Cleaned {len(cleaned)} articles")
    return cleaned