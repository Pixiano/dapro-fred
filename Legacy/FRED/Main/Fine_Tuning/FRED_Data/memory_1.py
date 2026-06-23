import json
from pathlib import Path

# ---------------- Paths ----------------
DATASET_DIR = Path("Datasets")
DATASET_DIR.mkdir(exist_ok=True)
DATASET_PATH = DATASET_DIR / "dataset_1.jsonl"

# ---------------- Session cache ----------------
conversation = []         # pending instruction/response pairs
pending_instruction = None
SAVE_INTERVAL = 10        # flush every 10 pairs
counter = 0

# ---------------- Helpers ----------------
def is_valid_entry(entry):
    """
    Checks if the entry is safe and complete enough to save.
    Avoids logging errors, empty, or truncated messages.
    """
    if not entry:
        return False

    instr = entry.get("instruction", "").strip()
    resp = entry.get("response", "").strip()

    # reject if empty or None
    if not instr or not resp:
        return False

    # reject if too short (likely failed generations)
    if len(resp) < 3:
        return False

    # reject if looks truncated or like an error
    bad_patterns = [
        "Traceback", "Error", "Exception",
        "Incomplete response", "null",
        "…", "...", "⟨", "⟩"
    ]
    if any(bad in resp for bad in bad_patterns):
        return False

    return True


def append_entry(entry):
    """Write single instruction/response pair to JSONL."""
    if not is_valid_entry(entry):
        return  # skip invalid or incomplete pairs
    with open(DATASET_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------- Remember a chat turn ----------------
def remember(role, content):
    """
    role: "user" or "assistant"
    content: text
    """
    global pending_instruction, conversation, counter

    if role == "user":
        pending_instruction = content

    elif role == "assistant":
        if not pending_instruction:
            # assistant spoke without a user input — skip
            return

        entry = {
            "instruction": pending_instruction.strip(),
            "response": content.strip()
        }

        # only add if valid
        if is_valid_entry(entry):
            conversation.append(entry)
            counter += 1

        pending_instruction = None

        # Flush to disk every SAVE_INTERVAL pairs
        if counter >= SAVE_INTERVAL and conversation:
            for e in conversation:
                append_entry(e)
            conversation.clear()
            counter = 0
            print(f"[INFO] Saved {SAVE_INTERVAL} instruction/response pairs to dataset_1.jsonl")


# ---------------- Manual flush ----------------
def flush():
    """Force write any remaining pairs to disk."""
    global conversation, counter, pending_instruction
    if conversation:
        valid_entries = [e for e in conversation if is_valid_entry(e)]
        for e in valid_entries:
            append_entry(e)
        print(f"[INFO] Flushed {len(valid_entries)} valid instruction/response pairs.")
        conversation.clear()
        counter = 0
    pending_instruction = None


# ---------------- Forget all data ----------------
def forget_all():
    """Delete the dataset entirely."""
    if DATASET_PATH.exists():
        DATASET_PATH.unlink()
        print("[INFO] Cleared dataset_1.jsonl")
    else:
        print("[INFO] No dataset found to clear.")


# ---------------- Get last N entries ----------------
def get_context(max_chats=50):
    """
    Returns the last max_chats instruction/response pairs
    for context or LLM prompt building.
    """
    if not DATASET_PATH.exists():
        return []

    all_entries = []
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        for line in f:
            try:
                all_entries.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                continue

    return all_entries[-max_chats:] if all_entries else []