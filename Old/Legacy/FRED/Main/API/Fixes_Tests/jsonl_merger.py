import json
import os

# Paths
base_path = r"C:\Users\Admin\Project_FRED\FRED\Main\API"
old_file = os.path.join(base_path, "Memory.json")
new_file = os.path.join(base_path, "memory_22_9_25.json")
merged_file = os.path.join(base_path, "vatsal_merged.jsonl")

def safe_load_json_array(file_path):
    """Safely load a JSON array, skipping any invalid entries."""
    safe_list = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                for i, item in enumerate(data):
                    try:
                        # Convert dicts back to JSON to ensure they are valid
                        json_str = json.dumps(item)
                        safe_list.append(json.loads(json_str))
                    except Exception:
                        print(f"Skipped invalid item at index {i} in {file_path}")
            else:
                print(f"{file_path} is not a JSON array, skipping completely")
    except Exception as e:
        print(f"Failed to load {file_path}: {e}")
    return safe_list

# Load both files safely
old_memories = safe_load_json_array(old_file)
new_memories = safe_load_json_array(new_file)

# Merge
merged_memories = old_memories + new_memories

# Write JSONL
with open(merged_file, "w", encoding="utf-8") as f:
    for item in merged_memories:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")

print(f"Merged {len(old_memories)} old + {len(new_memories)} new safe memories into {merged_file}")