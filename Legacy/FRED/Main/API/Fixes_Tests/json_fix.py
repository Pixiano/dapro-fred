import json

with open("memory.json", "r", encoding="utf-8") as f:
    lines = f.readlines()

fixed_lines = []
for line in lines:
    try:
        json.loads(line)
        fixed_lines.append(line)
    except:
        continue

with open("memory_fixed.json", "w", encoding="utf-8") as f:
    f.write("[" + ",".join([l.strip() for l in fixed_lines]) + "]")