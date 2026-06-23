import json

dims = set(len(m.get("embedding", [])) for m in memories)
print(f"All embedding dimensions in memory: {dims}")
