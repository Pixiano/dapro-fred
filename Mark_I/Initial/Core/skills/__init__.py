class Unavailable(Exception):
    """Raised by a skill's bind(ctx) when what it needs is not there; the loader leaves that skill out (kept here, not in loader.py, so it is one class however loader.py is run)."""
