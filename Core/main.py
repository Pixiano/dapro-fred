# Core/main.py

from orchestrator.orchestrator import FREDOrchestrator
from audio.device_info import describe_audio_devices
from config.settings import TTS_ENABLED, STT_ENABLED


def run_text_loop(orchestrator: FREDOrchestrator):

    print("Text mode. Type 'voice' to switch to voice mode, 'exit' to quit.\n")

    while True:

        user_input = input("You: ").strip()

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            print("\nF.R.E.D.: Shutting down gracefully.")
            return

        if user_input.lower() == "voice":
            run_voice_loop(orchestrator)
            print("\nBack to text mode. Type 'voice' to switch back, 'exit' to quit.\n")
            continue

        response = orchestrator.process(user_input)
        print(f"\nF.R.E.D.: {response}\n")


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

    # No wake word here any more — GUI mode is hold-to-talk (left
    # Ctrl+Alt, see Core/ui/pill_app.py) and the old always-on VAD+Vosk
    # wake-word listener retired with it (Attic/phase16-orb/audio/).
    # The CLI just listens on each turn.
    print(
        "\nVoice mode active. Listening — speak now. "
        "Say 'exit voice mode' or press Ctrl+C to return to text.\n"
    )

    try:
        while True:

            user_input = stt.listen_once()

            if not user_input:
                continue

            print(f"You (voice): {user_input}")

            if user_input.lower() in ("exit voice mode", "exit", "quit"):
                print("\nF.R.E.D.: Returning to text mode.")
                return

            response = orchestrator.process(user_input)

            print(f"F.R.E.D.: {response}\n")
            tts.speak(response)

    except KeyboardInterrupt:
        print("\n\nF.R.E.D.: Returning to text mode.")


def main():

    print("\nF.R.E.D. Core Runtime Online.")
    print(describe_audio_devices())

    orchestrator = FREDOrchestrator()
    # CLI mode never calls notifier.set_voice() (always SAPI), so there's
    # no voice-ordering reason to delay this — start right away.
    orchestrator.scheduler.start()

    try:
        run_text_loop(orchestrator)

    except KeyboardInterrupt:
        print("\n\nF.R.E.D.: Forced shutdown detected.")

    except Exception as e:
        print("\n[FATAL ERROR]", str(e))

    finally:
        orchestrator.shutdown()


if __name__ == "__main__":
    main()
