def dedupe(entries):
    """Remove duplicates based on 'output' content."""
    seen = set()
    unique = []
    for item in entries:
        key = item["output"]  # now safe, since item is dict
        if key not in seen:
            seen.add(key)
            unique.append(item)
    print(f"✅ Deduped {len(entries)} → {len(unique)} unique items.")
    return unique
