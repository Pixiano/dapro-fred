import os
import json

with open("memory.json", "w", encoding="utf-8") as f:
    json.dump([], f, indent=2)
