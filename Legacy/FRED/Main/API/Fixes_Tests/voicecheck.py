import asyncio
import edge_tts
from playsound import playsound

async def main():
    voice = "en-US-GuyNeural"
    text = "Hello, I am F.R.E.D., your personal assistant. Ready to assist you."
    output_file = "jarvis_test.mp3"

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_file)

    playsound(output_file)
    print("Done speaking!")

asyncio.run(main())