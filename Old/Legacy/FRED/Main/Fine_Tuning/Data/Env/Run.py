import os
import time
import json
from cerebras.cloud.sdk import Cerebras

# ---------------- CONFIG ----------------
API_KEY = "csk-xe85jy5m5kjw6mk4m3ppkth9rk32fk23h69whpfmh5fjdycd"
OUTPUT_DIR = r"C:\Users\Admin\Project_FRED\FRED\Main\Fine_Tuning\Data\Datasets"
BATCH_SIZE = 10           # number of examples per API call
SAVE_EVERY = 10           # save after every batch
NEW_FILE_EVERY = 1000     # create new JSONL file every N examples
TOTAL_EXAMPLES = 20000
MAX_CONTEXT_WORDS = 250   # maximum words from previous context
DEBUG_LOG_EVERY = 100     # log progress every N examples
MODEL_NAME = "llama-3.3-70b"  # model name for output file prefix

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ---------------- INIT CLIENT ----------------
client = Cerebras(api_key=API_KEY)

# ---------------- HELPERS ----------------
def trim_context(context_text):
    words = context_text.split()
    return " ".join(words[-MAX_CONTEXT_WORDS:]) if len(words) > MAX_CONTEXT_WORDS else context_text

def generate_prompt(batch_number, num_examples, previous_context=""):
    trimmed_context = trim_context(previous_context)
    context_section = f"Context (last {MAX_CONTEXT_WORDS} words): {trimmed_context}" if trimmed_context else ""
    return f"""
{context_section}
Generate {num_examples} high-quality instruction-response pairs about technology from 2024–2025.
Ask questions starting with What, Why, How, When, Where and Who.
Each example must be a JSON object with two fields:
{{"instruction": "...", "response": "..."}}
Output only valid JSON objects, one per line.
Batch {batch_number}.
"""

def call_cerebras(prompt, retries=3, backoff=5):
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=MODEL_NAME,
                max_tokens=1024,
                temperature=0.7,
                n=1
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"[WARN] API call failed (attempt {attempt+1}): {e}")
            time.sleep(backoff)
    return None

def parse_generated_text(text):
    """Parses model output and separates valid vs invalid examples."""
    valid, invalid = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
                if "instruction" in obj and "response" in obj:
                    valid.append(obj)
                else:
                    invalid.append(line)
            except Exception:
                invalid.append(line)
        else:
            invalid.append(line)
    return valid, invalid

# ---------------- MAIN ----------------
def main():
    file_count = 1
    generated_count = 0
    batch_number = 1
    previous_context = ""

    # Open dataset + discarded file
    start_idx = 1
    end_idx = NEW_FILE_EVERY
    dataset_path = os.path.join(OUTPUT_DIR, f"{MODEL_NAME}_dataset_{start_idx}_{end_idx}.jsonl")
    current_file = open(dataset_path, "a", encoding="utf-8")

    discarded_path = os.path.join(OUTPUT_DIR, f"{MODEL_NAME}_discarded.jsonl")
    discarded_file = open(discarded_path, "a", encoding="utf-8")

    print(f"[INIT] Writing to {dataset_path}")

    while generated_count < TOTAL_EXAMPLES:
        prompt = generate_prompt(batch_number, BATCH_SIZE, previous_context)
        time.sleep(3)

        raw_text = call_cerebras(prompt)
        if not raw_text:
            print("[ERROR] Skipping batch due to API failure.")
            continue

        valid_examples, invalid_lines = parse_generated_text(raw_text)

        # Write valid examples
        for ex in valid_examples:
            json_line = json.dumps(ex, ensure_ascii=False)
            current_file.write(json_line + "\n")
            generated_count += 1

            # Rotate file every 1K examples
            if generated_count % NEW_FILE_EVERY == 0:
                current_file.close()
                file_count += 1
                start_idx = (file_count - 1) * NEW_FILE_EVERY + 1
                end_idx = file_count * NEW_FILE_EVERY
                dataset_path = os.path.join(OUTPUT_DIR, f"{MODEL_NAME}_dataset_{start_idx}_{end_idx}.jsonl")
                current_file = open(dataset_path, "a", encoding="utf-8")
                print(f"[INFO] Started new dataset file: {dataset_path}")

        # Write invalid examples to discarded file
        for bad in invalid_lines:
            discarded_file.write(bad + "\n")

        # Append everything (valid only) to current progress file
        temp_filename = os.path.join(OUTPUT_DIR, f"{MODEL_NAME}_current.jsonl")
        with open(temp_filename, "a", encoding="utf-8") as temp_f:
            for ex in valid_examples:
                temp_f.write(json.dumps(ex, ensure_ascii=False) + "\n")

        # Update previous context
        previous_context += " " + raw_text

        if generated_count % DEBUG_LOG_EVERY == 0:
            print(f"[DEBUG] {generated_count}/{TOTAL_EXAMPLES} examples done. Last batch: {len(valid_examples)} valid, {len(invalid_lines)} discarded.")

        batch_number += 1

    # Close open files
    current_file.close()
    discarded_file.close()

    print(f"[DONE] Finished generating {generated_count} examples.")
    print(f"[DONE] Valid data saved in: {OUTPUT_DIR}")
    print(f"[DONE] Discarded samples saved to: {discarded_path}")

if __name__ == "__main__":
    main()