# Core/ui/gui_app.py
# FRED GUI Application — a single reactive "blob" instead of a
# traditional chat-log window. No textbox you can see, no bubbles,
# no scrollback. One presence that changes color, shape, and motion
# depending on what FRED is doing.

import math
import time
import threading
import queue
import tkinter as tk
from tkinter import font as tkfont

from orchestrator.orchestrator import FREDOrchestrator

BG = "#0b0c10"
TEXT_DIM = "#5b6472"
TEXT_BRIGHT = "#d6dde6"

STATE_COLORS = {
    "idle":      (58, 64, 76),
    "typing":    (56, 120, 120),
    "listening": (47, 123, 209),
    "thinking":  (209, 163, 47),
    "speaking":  (47, 209, 123),
}

STATE_LABELS = {
    "idle": "idle",
    "typing": "typing",
    "listening": "listening",
    "thinking": "thinking",
    "speaking": "speaking",
}

# (amplitude, frequency) for the base breathing pulse, per state.
ANIM_PARAMS = {
    "idle":      (4, 0.5),
    "typing":    (5, 1.0),
    "listening": (9, 1.3),
    "thinking":  (3, 2.2),
    "speaking":  (6, 1.8),
}


def _lerp(a, b, t):
    return a + (b - a) * t


def _lerp_rgb(c1, c2, t):
    return tuple(_lerp(c1[i], c2[i], t) for i in range(3))


def _rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c))) for c in rgb)


class Blob:
    """
    The single reactive presence. Owns a canvas and animates a
    circle (plus state-specific decoration: orbiting dots while
    thinking, ripples while listening, bars while speaking) toward
    whatever state it's told to be in, smoothly.
    """

    CANVAS_W = 460
    CANVAS_H = 380
    CX, CY = 230, 190
    BASE_RADIUS = 78

    def __init__(self, parent):
        self.canvas = tk.Canvas(
            parent, width=self.CANVAS_W, height=self.CANVAS_H,
            bg=BG, highlightthickness=0, bd=0,
        )
        self.canvas.pack()

        self.state = "idle"
        self.cur_color = list(STATE_COLORS["idle"])
        self.target_color = list(STATE_COLORS["idle"])
        self.jitter = 0.0
        self.ripples = []
        self._running = True

        self.state_font = tkfont.Font(family="Segoe UI", size=11)
        self.circle_id = self.canvas.create_oval(0, 0, 0, 0, width=0)
        self.state_text_id = self.canvas.create_text(
            self.CX, self.CY + self.BASE_RADIUS + 38,
            text="idle", fill=TEXT_DIM, font=self.state_font,
        )

        self._decor_ids = []
        self._tick()

    def set_state(self, state: str):
        if state not in STATE_COLORS:
            return
        self.state = state
        self.target_color = list(STATE_COLORS[state])
        if state != "listening":
            self.ripples.clear()
        self.canvas.itemconfig(self.state_text_id, text=STATE_LABELS[state])

    def bump(self, amount: float = 8.0):
        """A one-off impulse — used for keystrokes and 'reply arrived'."""
        self.jitter = amount

    def shutdown(self):
        self._running = False

    def _clear_decor(self):
        for item_id in self._decor_ids:
            self.canvas.delete(item_id)
        self._decor_ids = []

    def _tick(self):
        if not self._running:
            return

        t = time.time()

        # Smooth color transition toward whatever state we're in.
        self.cur_color = list(_lerp_rgb(self.cur_color, self.target_color, 0.12))
        hex_color = _rgb_to_hex(self.cur_color)

        amp, freq = ANIM_PARAMS.get(self.state, (4, 0.5))
        pulse = math.sin(t * freq * 2 * math.pi) * amp

        self.jitter *= 0.82
        radius = self.BASE_RADIUS + pulse + self.jitter

        self.canvas.coords(
            self.circle_id,
            self.CX - radius, self.CY - radius,
            self.CX + radius, self.CY + radius,
        )
        self.canvas.itemconfig(self.circle_id, fill=hex_color, outline=hex_color)

        self._clear_decor()

        if self.state == "thinking":
            self._draw_orbit(t, radius)
        elif self.state == "listening":
            self._draw_ripples(t)
        elif self.state == "speaking":
            self._draw_bars(t)

        self.canvas.after(33, self._tick)

    def _draw_orbit(self, t, radius):
        accent = _rgb_to_hex(_lerp_rgb(self.cur_color, (255, 255, 255), 0.35))
        orbit_r = radius + 20
        for i in range(3):
            angle = math.radians(t * 160 + i * 120)
            x = self.CX + orbit_r * math.cos(angle)
            y = self.CY + orbit_r * math.sin(angle)
            dot = self.canvas.create_oval(
                x - 4, y - 4, x + 4, y + 4, fill=accent, outline=accent,
            )
            self._decor_ids.append(dot)

    def _draw_ripples(self, t):
        if not self.ripples or t - self.ripples[-1] > 0.9:
            self.ripples.append(t)
        self.ripples = [s for s in self.ripples if t - s < 1.3]

        bg_rgb = (11, 12, 16)  # matches BG = "#0b0c10"
        for spawn in self.ripples:
            age = t - spawn
            frac = age / 1.3
            r = self.BASE_RADIUS + age * 70
            faded = _lerp_rgb(self.target_color, bg_rgb, frac)
            width = max(1, int(3 * (1 - frac)))
            ring = self.canvas.create_oval(
                self.CX - r, self.CY - r, self.CX + r, self.CY + r,
                outline=_rgb_to_hex(faded), width=width,
            )
            self._decor_ids.append(ring)

    def _draw_bars(self, t):
        accent = _rgb_to_hex(_lerp_rgb(self.cur_color, (255, 255, 255), 0.3))
        n_bars = 5
        bar_w = 8
        gap = 8
        total_w = n_bars * bar_w + (n_bars - 1) * gap
        start_x = self.CX - total_w / 2
        base_y = self.CY + self.BASE_RADIUS + 14

        for i in range(n_bars):
            freq = 1.3 + 0.45 * i
            phase = i * 0.7
            height = 6 + 22 * abs(math.sin(t * freq + phase))
            x0 = start_x + i * (bar_w + gap)
            bar = self.canvas.create_rectangle(
                x0, base_y - height, x0 + bar_w, base_y + height,
                fill=accent, outline=accent,
            )
            self._decor_ids.append(bar)


def _resolve_emoji_font(root):
    """
    Try a couple of fonts known to render emoji on Windows; return the
    first that's actually available (tkinter silently substitutes a
    fallback font otherwise, which we can detect via .actual()).
    """
    for family in ("Segoe UI Emoji", "Segoe UI Symbol"):
        try:
            f = tkfont.Font(root=root, family=family, size=14)
            if f.actual("family") == family:
                return f
        except tk.TclError:
            continue
    return None


class FREDGUIApp:
    """Single-window, single-blob GUI for FRED."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("FRED")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self.orchestrator = FREDOrchestrator(show_hud=False)
        self.response_queue = queue.Queue()
        self.processing = False
        self._typing_revert_id = None

        self._build_ui()
        self._poll_responses()

    # ---------------------------------------------------------------
    # LAYOUT
    # ---------------------------------------------------------------

    def _build_ui(self):
        header = tk.Frame(self.root, bg=BG, height=54)
        header.pack(fill=tk.X)
        tk.Label(
            header, text="FRED", bg=BG, fg=TEXT_BRIGHT,
            font=("Segoe UI", 15, "bold"),
        ).pack(pady=14)

        self.blob = Blob(self.root)

        # The "textbox" — same background as the window, no border,
        # no highlight ring. It's there, you just can't see it as a
        # shape; only the text you type is visible, floating directly
        # on the background.
        entry_row = tk.Frame(self.root, bg=BG)
        entry_row.pack(fill=tk.X, padx=40, pady=(4, 18))

        self.input_var = tk.StringVar()
        self.entry = tk.Entry(
            entry_row,
            textvariable=self.input_var,
            bg=BG, fg=TEXT_BRIGHT,
            insertbackground=TEXT_BRIGHT,
            relief=tk.FLAT, bd=0, highlightthickness=0,
            font=("Segoe UI", 12),
            justify=tk.CENTER,
        )
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry.bind("<Return>", self._on_submit)
        self.entry.bind("<Key>", self._on_typing)
        self.entry.focus()

        emoji_font = _resolve_emoji_font(self.root)
        mic_text = "\U0001F3A4" if emoji_font else "Voice"
        mic_font = emoji_font if emoji_font else ("Segoe UI", 10, "bold")

        self.mic_btn = tk.Label(
            entry_row, text=mic_text, bg=BG, fg=TEXT_DIM,
            font=mic_font, cursor="hand2", padx=10,
        )
        self.mic_btn.pack(side=tk.RIGHT)
        self.mic_btn.bind("<Button-1>", self._on_voice_click)
        self.mic_btn.bind("<Enter>", lambda e: self.mic_btn.config(fg=TEXT_BRIGHT))
        self.mic_btn.bind("<Leave>", lambda e: self.mic_btn.config(fg=TEXT_DIM))

    # ---------------------------------------------------------------
    # TEXT INPUT
    # ---------------------------------------------------------------

    def _on_typing(self, event):
        if event.keysym in ("Return", "Tab"):
            return
        self.blob.bump(6)
        if self.blob.state in ("idle",):
            self.blob.set_state("typing")
        if self._typing_revert_id:
            self.root.after_cancel(self._typing_revert_id)
        self._typing_revert_id = self.root.after(900, self._revert_from_typing)

    def _revert_from_typing(self):
        self._typing_revert_id = None
        if self.blob.state == "typing":
            self.blob.set_state("idle")

    def _on_submit(self, event=None):
        user_input = self.input_var.get().strip()
        if not user_input or self.processing:
            return

        if user_input.lower() in ("exit", "quit"):
            self.root.quit()
            return

        self.input_var.set("")
        if self._typing_revert_id:
            self.root.after_cancel(self._typing_revert_id)

        self.processing = True
        self.blob.set_state("thinking")
        threading.Thread(target=self._process_input, args=(user_input,), daemon=True).start()

    # ---------------------------------------------------------------
    # VOICE INPUT
    # ---------------------------------------------------------------

    def _on_voice_click(self, event=None):
        if self.processing:
            return
        self.processing = True
        self.blob.set_state("listening")
        threading.Thread(target=self._capture_voice, daemon=True).start()

    def _capture_voice(self):
        try:
            from audio.stt import STTManager
            stt = STTManager()
            user_input = stt.listen_once()
        except Exception as e:
            self.response_queue.put(("error", f"Voice capture unavailable: {e}"))
            self.processing = False
            return

        if not user_input:
            self.response_queue.put(("revert_idle", None))
            self.processing = False
            return

        self.blob.set_state("thinking")
        self._process_input(user_input, speak=True)

    # ---------------------------------------------------------------
    # PROCESSING
    # ---------------------------------------------------------------

    def _process_input(self, user_input: str, speak: bool = False):
        try:
            response = self.orchestrator.process(user_input)
            self.blob.bump(14)  # little impact when the reply arrives
            if speak:
                self.response_queue.put(("speak", response))
            else:
                self.response_queue.put(("done", response))
        except Exception as e:
            self.response_queue.put(("error", str(e)))
        finally:
            self.processing = False

    def _speak(self, text: str):
        self.blob.set_state("speaking")
        try:
            from audio.tts import TTSManager
            tts = TTSManager()
            tts.speak(text)
        except Exception:
            pass
        self.response_queue.put(("revert_idle", None))

    # ---------------------------------------------------------------
    # QUEUE POLLING
    # ---------------------------------------------------------------

    def _poll_responses(self):
        try:
            while True:
                msg_type, payload = self.response_queue.get_nowait()

                if msg_type == "done":
                    self.blob.set_state("idle")
                elif msg_type == "speak":
                    threading.Thread(target=self._speak, args=(payload,), daemon=True).start()
                elif msg_type == "revert_idle":
                    self.blob.set_state("idle")
                elif msg_type == "error":
                    self.blob.set_state("idle")

        except queue.Empty:
            pass

        self.root.after(100, self._poll_responses)

    def shutdown(self):
        self.blob.shutdown()
        self.orchestrator.shutdown()


def run_gui():
    root = tk.Tk()
    app = FREDGUIApp(root)

    def on_close():
        app.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
