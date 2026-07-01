# Core/ui/fred_orb.py - Python Controller for FRED Orb UI

import threading
import queue
import time
import json
from typing import Literal, Optional

import webview
import pystray
from PIL import Image, ImageDraw
import sounddevice as sd
import numpy as np

from orchestrator.orchestrator import FREDOrchestrator


class AudioMonitor:
    """Background thread capturing RMS audio level at ~10Hz."""

    def __init__(self, js_api, sample_rate: int = 16000, block_size: int = 1600):
        self.js_api = js_api
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.stream: Optional[sd.InputStream] = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)

    def _run(self):
        try:
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                channels=1,
                dtype="float32",
                callback=self._callback,
            )
            self.stream.start()
            while self.running:
                time.sleep(0.1)
        except Exception as e:
            print(f"[AudioMonitor] Error: {e}")
        finally:
            if self.stream:
                self.stream.stop()
                self.stream.close()

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"[AudioMonitor] {status}")
        if not self.running:
            return
        # RMS normalized 0-1
        rms = np.sqrt(np.mean(indata**2))
        level = min(1.0, rms * 10)  # scale factor, tune as needed
        try:
            self.js_api.set_audio_level(float(level))
        except Exception:
            pass


class SystemTrayManager:
    """System tray icon with Open/Exit menu."""

    def __init__(self, app):
        self.app = app
        self.icon: Optional[pystray.Icon] = None
        self.thread: Optional[threading.Thread] = None

    def create_icon_image(self) -> Image.Image:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Green "F" circle
        draw.ellipse([4, 4, 60, 60], fill=(0, 255, 136, 255))
        draw.text((18, 14), "F", fill=(8, 10, 14, 255), font_size=36)
        return img

    def run(self):
        menu = pystray.Menu(
            pystray.MenuItem("Open FRED", self.on_open, default=True),
            pystray.MenuItem("Exit", self.on_exit),
        )
        self.icon = pystray.Icon(
            "FRED",
            self.create_icon_image(),
            "FRED",
            menu,
        )
        self.icon.run()

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        if self.icon:
            self.icon.stop()

    def on_open(self, icon, item):
        self.app.show_window()

    def on_exit(self, icon, item):
        self.app.shutdown()


class JSAPI:
    """JavaScript API exposed to the webview frontend."""

    def __init__(self, app):
        self.app = app
        self.response_queue = queue.Queue()
        self.pending_voice = False

    def set_state(self, state: Literal["idle", "listening", "thinking", "speaking"]):
        self.app.window.evaluate_js(f"window.setState('{state}')")

    def set_transcript(self, user_text: str, fred_reply: str):
        self.app.window.evaluate_js(
            f"window.setTranscript({json.dumps(user_text)}, {json.dumps(fred_reply)})"
        )

    def set_audio_level(self, level: float):
        self.app.window.evaluate_js(f"window.setAudioLevel({level})")

    def on_submit(self, text: str):
        self.app.process_text(text)

    def on_voice_toggle(self):
        self.app.toggle_voice()

    def on_mode_toggle(self):
        self.app.toggle_mode()

    def minimize_to_tray(self):
        self.app.hide_window()

    def poll_responses(self):
        """Called from JS polling interval."""
        try:
            while True:
                msg_type, payload = self.response_queue.get_nowait()
                if msg_type == "state":
                    self.set_state(payload)
                elif msg_type == "transcript":
                    self.set_transcript(payload.get("user", ""), payload.get("fred", ""))
                elif msg_type == "voice_state":
                    self.pending_voice = payload
                    if not payload:
                        self.set_state("idle")
        except queue.Empty:
            pass


class FREDOrbApp:
    """Main application controller."""

    def __init__(self):
        self.orchestrator = FREDOrchestrator(show_hud=False)
        self.js_api = JSAPI(self)
        self.audio_monitor = AudioMonitor(self.js_api)
        self.tray = SystemTrayManager(self)
        self.window: Optional[webview.Window] = None
        self.running = False
        self.voice_active = False
        self.mode = "compact"  # compact | fullscreen

    def create_window(self):
        import os
        base_dir = os.path.dirname(os.path.abspath(__file__))
        index_path = os.path.join(base_dir, "orb_ui", "index.html")
        
        self.window = webview.create_window(
            "FRED",
            index_path,
            width=380,
            height=320,
            min_size=(380, 320),
            frameless=True,
            easy_drag=True,
            resizable=True,
            transparent=True,
            js_api=self.js_api,
            on_top=True,
        )

        # Window will be shown when webview.start() runs

    def run(self):
        self.running = True
        self.create_window()
        self.tray.start()
        webview.start(debug=False, private_mode=False)

    def show_window(self):
        if self.window:
            self.mode = "compact"
            self.window.resize(380, 320)
            self.window.show()
            self.window.restore()
            self.js_api.set_state("idle")

    def hide_window(self):
        if self.window:
            self.window.hide()
            self.audio_monitor.stop()
            self.voice_active = False

    def toggle_mode(self):
        if not self.window:
            return
        if self.mode == "compact":
            self.mode = "fullscreen"
            self.window.resize(600, 700)
            self.window.move(
                (self.window.screen.width - 600) // 2,
                (self.window.screen.height - 700) // 2,
            )
        else:
            self.mode = "compact"
            self.window.resize(380, 320)
            # Position at bottom-center
            self.window.move(
                (self.window.screen.width - 380) // 2,
                self.window.screen.height - 320 - 40,
            )

    def process_text(self, text: str):
        self.js_api.set_state("thinking")
        self.js_api.set_transcript(text, "")

        def worker():
            try:
                reply = self.orchestrator.process(text)
                self.js_api.response_queue.put(("transcript", {"user": text, "fred": reply}))
                self.js_api.response_queue.put(("state", "idle"))
            except Exception as e:
                self.js_api.response_queue.put(("transcript", {"user": text, "fred": f"Error: {e}"}))
                self.js_api.response_queue.put(("state", "idle"))

        threading.Thread(target=worker, daemon=True).start()

    def toggle_voice(self):
        if self.voice_active:
            self.stop_voice()
        else:
            self.start_voice()

    def start_voice(self):
        self.voice_active = True
        self.js_api.set_state("listening")
        self.audio_monitor.start()

        def worker():
            try:
                from audio.stt import STTManager
                from config.settings import STT_MODEL_PATH, STT_SAMPLE_RATE
                stt = STTManager(model_path=str(STT_MODEL_PATH), samplerate=STT_SAMPLE_RATE)
                text = stt.listen_once(timeout=10)
            except Exception as e:
                text = None
                self.js_api.response_queue.put(("transcript", {"user": "", "fred": f"Voice error: {e}"}))

            self.voice_active = False
            self.audio_monitor.stop()

            if text:
                self.js_api.response_queue.put(("transcript", {"user": text, "fred": ""}))
                self.process_text(text)
            else:
                self.js_api.response_queue.put(("voice_state", False))
                self.js_api.set_state("idle")

        threading.Thread(target=worker, daemon=True).start()

    def stop_voice(self):
        self.voice_active = False
        self.audio_monitor.stop()
        self.js_api.set_state("idle")

    def shutdown(self):
        self.running = False
        self.audio_monitor.stop()
        self.orchestrator.shutdown()
        self.tray.stop()
        if self.window:
            self.window.destroy()
        webview.windows[0].destroy() if webview.windows else None


def main():
    app = FREDOrbApp()
    app.run()


if __name__ == "__main__":
    main()