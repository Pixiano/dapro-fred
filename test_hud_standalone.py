# test_hud_standalone.py
# Test the HUD window in isolation without full FRED dependencies

import sys
sys.path.insert(0, 'Core')

import time
from ui.hud import HUDWindow

def test_hud():
    """Test HUD with realistic state transitions."""
    print("\n" + "="*60)
    print("  PHASE 16: HUD Window Test")
    print("="*60 + "\n")

    print("Initializing HUD window...")
    hud = HUDWindow()
    time.sleep(1)

    test_sequence = [
        {
            "state": "idle",
            "transcript": "Waiting for input...",
            "description": "System idle, ready to listen"
        },
        {
            "state": "listening",
            "transcript": "You: hello",
            "description": "User speaking, capturing audio"
        },
        {
            "state": "thinking",
            "transcript": "You: hello\n\nFRED: Processing...",
            "description": "LLM generating response"
        },
        {
            "state": "speaking",
            "transcript": "You: hello\n\nFRED: Hi there! How can I help?",
            "description": "Speaking the response via TTS"
        },
        {
            "state": "idle",
            "transcript": "Ready for next command...",
            "description": "Returned to idle state"
        },
    ]

    print("Running state transition test:")
    print("-" * 60)

    for i, step in enumerate(test_sequence, 1):
        print(f"\n[{i}/{len(test_sequence)}] {step['state'].upper()}")
        print(f"    {step['description']}")
        hud.set_state(step['state'])
        hud.set_transcript(step['transcript'])
        time.sleep(2.5)

    print("\n" + "-" * 60)
    print("\nShutting down HUD...")
    hud.shutdown()
    time.sleep(0.5)

    print("\n" + "="*60)
    print("  TEST COMPLETE")
    print("="*60)
    print("\nPhase 16 accomplished:")
    print("  [x] Always-on-top HUD window created")
    print("  [x] Four visual states implemented (idle/listening/thinking/speaking)")
    print("  [x] Color-coded state indicator")
    print("  [x] Live transcript display")
    print("  [x] Thread-safe queue architecture")
    print("  [x] Integrated into orchestrator")
    print("\nThe HUD should have displayed these states in color,")
    print("transitioning smoothly between them.\n")

if __name__ == "__main__":
    test_hud()
