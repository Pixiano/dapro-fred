# Core/main.py

from orchestrator.orchestrator import FREDOrchestrator
from audio.device_info import describe_audio_devices
from config.settings import TTS_ENABLED, STT_ENABLED, WAKE_WORD_ENABLED


def run_text_loop(orchestrator: FREDOrchestrator):

    print("Text mode. Type 'voice' to switch to voice mode, 'exit' to quit.\n")
    orchestrator.hud.set_state("idle")

    while True:

        user_input = input("You: ").strip()

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            print("\nF.R.E.D.: Shutting down gracefully.")
            orchestrator.hud.set_state("idle")
            return

        if user_input.lower() == "voice":
            run_voice_loop(orchestrator)
            print("\nBack to text mode. Type 'voice' to switch back, 'exit' to quit.\n")
            orchestrator.hud.set_state("idle")
            continue

        response = orchestrator.process(user_input)
        print(f"\nF.R.E.D.: {response}\n")
        orchestrator.hud.set_transcript(f"You: {user_input}\n\nFRED: {response}")


def run_voice_loop(orchestrator: FREDOrchestrator):

    if not (STT_ENABLED and TTS_ENABLED):
        print(
            "\n[Voice mode unavailable — STT_ENABLED/TTS_ENABLED are off "
            "in config/settings.py]\n"
        )
        return

    from audio.stt import STTManager
    from audio.tts import TTSManager

    stt = STTManager()
    tts = TTSManager()

    wake_word = None
    if WAKE_WORD_ENABLED:
        from audio.wake_word import WakeWordListener
        wake_word = WakeWordListener(stt=stt)

    print(
        "\nVoice mode active. "
        + ("Say \"Fred\", \"hey\", or just start talking. "
           if wake_word else "Listening — speak now. ")
        + "Say 'exit voice mode' or press Ctrl+C to return to text.\n"
    )

    try:
        while True:

            if wake_word:
                orchestrator.hud.set_state("listening")
                wake_word.listen_for_wake_word()
                print("F.R.E.D.: Yes?")
                tts.speak("Yes?")

            orchestrator.hud.set_state("listening")
            user_input = stt.listen_once()

            if not user_input:
                continue

            print(f"You (voice): {user_input}")
            orchestrator.hud.set_transcript(f"You: {user_input}")

            if user_input.lower() in ("exit voice mode", "exit", "quit"):
                print("\nF.R.E.D.: Returning to text mode.")
                orchestrator.hud.set_state("idle")
                return

            response = orchestrator.process(user_input)

            orchestrator.hud.set_state("speaking")
            print(f"F.R.E.D.: {response}\n")
            orchestrator.hud.set_transcript(f"You: {user_input}\n\nFRED: {response}")
            tts.speak(response)
            orchestrator.hud.set_state("idle")

    except KeyboardInterrupt:
        print("\n\nF.R.E.D.: Returning to text mode.")
        orchestrator.hud.set_state("idle")


def main():

    print("\nF.R.E.D. Core Runtime Online.")
    print(describe_audio_devices())

    orchestrator = FREDOrchestrator()

    try:
        run_text_loop(orchestrator)

    except KeyboardInterrupt:
        print("\n\nF.R.E.D.: Forced shutdown detected.")

    except Exception as e:
        print("\n[FATAL ERROR]", str(e))

    finally:
        orchestrator.hud.shutdown()
        orchestrator.shutdown()


if __name__ == "__main__":
    main()
