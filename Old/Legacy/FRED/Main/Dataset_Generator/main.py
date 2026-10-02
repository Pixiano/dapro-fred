# main.py
import os
import random
from modules.user_input import get_keywords
from modules.search import search
from modules.scraper import scrape
from modules.cleaner import clean
from modules.deduplicator import dedupe
from modules.instr_response import instruction_response
from modules.output_writer import save_jsonl

# ---------------- Config ----------------
RAW_HTML_DIR = "data/output/raw_html"
OUTPUT_DIR = "data/output"
os.makedirs(RAW_HTML_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

MIN_KEYWORDS = 3
TOP_N_ARTICLES = 10
HEADLESS = True  # set False to watch browser actions

# Optional: random instruction templates
INSTRUCTION_TEMPLATES = [
    "Explain '{}' in detail.",
    "Provide a comprehensive overview of '{}'.",
    "Share key insights about '{}'.",
    "Elaborate on the topic '{}'.",
    "Give an informative summary of '{}'.",
    "Discuss '{}' with examples.",
    "Provide useful information about '{}'.",
    "Teach me about '{}'."
]

def main():
    # 1️⃣ Get keywords from user
    keywords = get_keywords(MIN_KEYWORDS)
    print(f"[INFO] Keywords: {keywords}")

    # 2️⃣ Search sites and collect article links
    article_links = search(keywords, top_n=TOP_N_ARTICLES, headless=HEADLESS)
    if not article_links:
        print("[⚠️] No relevant links collected. Exiting.")
        return

    # 3️⃣ Scrape full content from collected links
    articles = scrape(article_links, headless=HEADLESS, raw_dir=RAW_HTML_DIR)
    if not articles:
        print("[⚠️] No articles scraped successfully. Exiting.")
        return

    # 4️⃣ Clean articles
    cleaned_articles = clean(articles)
    print(f"[INFO] Cleaned {len(cleaned_articles)} articles.")

    if not cleaned_articles:
        print("[⚠️] No articles remaining after cleaning. Exiting.")
        return

    # 5️⃣ Convert to instruction-response pairs with random instruction templates
    instruction_pairs = []
    for item in cleaned_articles:
        keyword = item.get("keyword", "")
        content = item.get("content", "")
        template = random.choice(INSTRUCTION_TEMPLATES)
        instruction_pairs.append(instruction_response(content, template.format(keyword)))

    # 6️⃣ Deduplicate
    unique_entries = dedupe(instruction_pairs)
    print(f"[INFO] Deduplicated: {len(unique_entries)} entries remain")

    # 7️⃣ Save final JSONL
    final_path = os.path.join(OUTPUT_DIR, "dataset.jsonl")
    save_jsonl(unique_entries, final_path)
    print(f"[SUCCESS] Dataset saved at: {final_path}")
    print(f"[SUMMARY] Total entries: {len(unique_entries)}")

if __name__ == "__main__":
    main()