#test_clean.py
import json
from pathlib import Path
from modules.cleaner import clean_text

input_file = Path("data/tests/test_1.jsonl")
output_file = Path("data/tests/cleaned/test_1_clean.jsonl")

with open(input_file, "r", encoding="utf-8") as f_in, open(output_file, "w", encoding="utf-8") as f_out:
    for line in f_in:
        entry = json.loads(line)
        raw = entry.get("raw_text", "")
        cleaned = clean_text(raw)
        entry["cleaned_text"] = cleaned
        json.dump(entry, f_out)
        f_out.write("\n")

print(f"Saved cleaned snippets to {output_file}")