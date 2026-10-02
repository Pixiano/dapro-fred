#tts.py 

import asyncio
import edge_tts
import threading
from playsound import playsound
import os
import time

# Default male "Jarvis/Calm" neural voice
VOICE = "en-US-GuyNeural"

# Lock to make TTS thread-safe
tts_lock = threading.Lock()

def speak(text: str):
    """Speak text aloud without blocking main loop."""
    def _say():
        with tts_lock:
            output_file = "temp_voice.mp3"
            asyncio.run(_save_tts(text, output_file))
            playsound(output_file)
            # wait a bit before cleanup so playback completes
            time.sleep(1.0)
            if os.path.exists(output_file):
                os.remove(output_file)

    t = threading.Thread(target=_say)
    t.start()

async def _save_tts(text: str, filename: str):
    """Saves text-to-speech audio to a file."""
    # The Communicate function will now receive the plain text directly
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(filename)