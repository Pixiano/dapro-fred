# Core/test_hud.py
# Simple test to verify the HUD window works

import sys
import time
import threading
from ui.hud import HUDWindow

def test_hud():
    """Test the HUD with state transitions."""
    print("Starting HUD test...")
    hud = HUDWindow()

    # Give the window a moment to appear
    time.sleep(1)

    # Test state transitions
    states = [
        ("idle", "Idle state test"),
        ("listening", "Listening... waiting for input"),
        ("thinking", "Processing: hello\n\nFRED: Hi there!"),
        ("speaking", "Speaking the response..."),
        ("idle", "Done"),
    ]

    for state, transcript in states:
        print(f"  → {state.upper()}: {transcript[:40]}...")
        hud.set_state(state)
        hud.set_transcript(transcript)
        time.sleep(2)

    print("Shutting down HUD...")
    hud.shutdown()
    print("✓ HUD test complete!")

if __name__ == "__main__":
    test_hud()
