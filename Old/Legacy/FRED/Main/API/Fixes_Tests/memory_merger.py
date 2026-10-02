# merge_recover.py
import json
from pathlib import Path

# --- Configure paths ---
OLD_DIR = Path(r"C:\Users\Admin\ProjectJ\FRED\API\OldMemories")
OLD_NAMES = ["memory_19_9_25", "memory_22_9_25"]   # try with/without .json
NEW_FILE = Path(r"C:\Users\Admin\ProjectJ\FRED\API\Tests\memory.json")   # fresh empty
OUTPUT_FILE = Path(r"C:\Users\Admin\ProjectJ\FRED\API\Tests\memory_merged.json")

def resolve_path(dirpath: Path, name: str) -> Path:
    """Return an existing path for name (try with/without .json)."""
    cand = dirpath / name
    if cand.exists():
        return cand
    cand_json = dirpath / (name + ".json")
    if cand_json.exists():
        return cand_json
    raise FileNotFoundError(f"Could not find {name} (with or without .json) in {dirpath}")

def try_full_load(p: Path):
    """Try json.load; if it fails, return None to indicate fallback needed."""
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            else:
                print(f"[WARN] {p} loaded but is not a list; ignoring full-load result.")
                return None
    except json.JSONDecodeError as e:
        print(f"[INFO] Full json.load failed for {p}: {e}")
        return None
    except FileNotFoundError:
        print(f"[INFO] File not found: {p}")
        return None

def extract_objects_by_braces(text: str):
    """
    Extract top-level JSON objects by scanning braces.
    Returns list of parsed objects.
    """
    objs = []
    in_string = False
    brace_count = 0
    start_idx = None
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        # Handle string state with escapes
        if ch == '"':
            # Count preceding backslashes to know if quote is escaped
            j = i - 1
            backslashes = 0
            while j >= 0 and text[j] == "\\":
                backslashes += 1
                j -= 1
            if backslashes % 2 == 0:  # not escaped
                in_string = not in_string

        if not in_string:
            if ch == '{':
                if brace_count == 0:
                    start_idx = i
                brace_count += 1
            elif ch == '}':
                if brace_count > 0:
                    brace_count -= 1
                    if brace_count == 0 and start_idx is not None:
                        candidate = text[start_idx:i+1]
                        # attempt to parse this candidate
                        try:
                            obj = json.loads(candidate)
                            objs.append(obj)
                        except json.JSONDecodeError:
                            # If parsing fails, try a small cleanup: strip trailing commas/spaces
                            cleaned = candidate.strip()
                            # No more aggressive fixes here — skip if invalid
                            try:
                                obj = json.loads(cleaned)
                                objs.append(obj)
                            except json.JSONDecodeError:
                                # skip silently but log
                                pass
                        start_idx = None
        i += 1
    return objs

def safe_load(path: Path):
    """Load a list of JSON objects from path, using full load or fallback extraction."""
    if not path.exists():
        return []
    # try full load first
    full = try_full_load(path)
    if full is not None:
        return full

    # fallback: read whole text and extract objects
    print(f"[INFO] Falling back to brace-extraction for {path}")
    text = path.read_text(encoding="utf-8", errors="ignore")
    objs = extract_objects_by_braces(text)
    print(f"[INFO] Extracted {len(objs)} objects from {path} using brace scanner.")
    return objs

def merge_and_dedupe(list_of_lists):
    """Merge multiple lists of entries; dedupe using (role,content,timestamp)."""
    merged = []
    seen = set()
    for lst in list_of_lists:
        for entry in lst:
            # Only accept dict-like entries
            if not isinstance(entry, dict):
                continue
            key = (entry.get("role"), entry.get("content"), entry.get("timestamp"))
            if key not in seen:
                seen.add(key)
                merged.append(entry)
    return merged

def main():
    old_paths = []
    for name in OLD_NAMES:
        try:
            p = resolve_path(OLD_DIR, name)
            old_paths.append(p)
        except FileNotFoundError as e:
            print(f"[WARN] {e} (skipping)")

    # load each old file safely
    old_loaded = []
    for p in old_paths:
        objs = safe_load(p)
        print(f"[INFO] {p.name}: {len(objs)} valid entries recovered.")
        old_loaded.append(objs)

    # load the new fresh memory (should be small / empty)
    new_loaded = safe_load(NEW_FILE)
    print(f"[INFO] New file {NEW_FILE.name}: {len(new_loaded)} entries loaded.")

    # Merge order: old files first (in given order), then new memory (so new items not lost)
    all_lists = []
    for lst in old_loaded:
        all_lists.append(lst)
    all_lists.append(new_loaded)

    merged = merge_and_dedupe(all_lists)
    print(f"[INFO] Total merged entries (deduped): {len(merged)}")

    # Write merged output
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    print(f"[DONE] Merged memory written to: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()