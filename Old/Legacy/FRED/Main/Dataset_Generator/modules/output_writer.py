import json
import os

OUTPUT_DIR = "data/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def save_jsonl(items, filename="dataset.jsonl"):
    """
    Save a list of dicts as a JSONL file.
    If the filename exists, automatically append a counter.
    """
    base_name, ext = os.path.splitext(filename)
    final_path = os.path.join(OUTPUT_DIR, filename)
    counter = 1

    # Avoid overwriting existing files
    while os.path.exists(final_path):
        final_path = os.path.join(OUTPUT_DIR, f"{base_name}_{counter}{ext}")
        counter += 1

    with open(final_path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"[INFO] Saved {len(items)} items to {final_path}")
    return final_path