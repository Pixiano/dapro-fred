# Core/ui/gui_app.py
# FRED GUI Application — a single reactive "blob" instead of a
# traditional chat-log window. No textbox you can see, no bubbles,
# no scrollback. One presence that changes color, shape, and motion
# depending on what FRED is doing.

import colorsys
import math
import time
import threading
import queue
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from orchestrator.orchestrator import FREDOrchestrator

BG = "#0b0c10"
BG_RGB = (11, 12, 16)
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

# (swirl speed, swirl alpha) — how fast the rainbow band drifts across
# the sphere and how vivid it is, per state. Idle barely moves; active
# states swirl faster and brighter, like the iOS Siri orb waking up.
SWIRL_PARAMS = {
    "idle":      (0.06, 80),
    "typing":    (0.18, 110),
    "listening": (0.30, 150),
    "thinking":  (0.45, 170),
    "speaking":  (0.55, 180),
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
    CANVAS_H = 440
    CX, CY = 230, 220
    BASE_RADIUS = 78

    GLOW_PAD = 90  # extra canvas margin so the soft outer glow isn't clipped

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
        self._sphere_photo = None  # keep a reference or Tk garbage-collects it

        self.state_font = tkfont.Font(family="Segoe UI", size=11)
        self.sphere_id = self.canvas.create_image(self.CX, self.CY, image=None)
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

    def _render_sphere(self, t: float, radius: float):
        """
        Render one frame of a glassy, translucent sphere — a dark
        radial-shaded glass body, a soft outer glow, a colorful
        diagonal swirl band drifting across the middle (the iOS-Siri
        signature), and a specular highlight. Returns a PhotoImage
        ready to hand to the canvas.
        """
        r = max(1, int(radius))
        size = r * 2 + self.GLOW_PAD * 2
        cx = cy = size / 2
        color = tuple(int(c) for c in self.cur_color)

        composite = Image.new("RGBA", (size, size), (0, 0, 0, 0))

        # --- soft outer glow (aura) — intensity and color follow state -------
        glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        # A bright inner core plus a wider, softer outer wash so the aura
        # reads as a real ambient glow instead of a single blurred ring.
        core_r = r * 1.15
        wash_r = r * 1.75
        gd.ellipse([cx - wash_r, cy - wash_r, cx + wash_r, cy + wash_r],
                   fill=color + (90,))
        gd.ellipse([cx - core_r, cy - core_r, cx + core_r, cy + core_r],
                   fill=color + (150,))
        glow = glow.filter(ImageFilter.GaussianBlur(self.GLOW_PAD * 0.55))
        composite = Image.alpha_composite(composite, glow)

        # --- circular mask used to clip the body/band/highlight to the sphere
        mask = Image.new("L", (size, size), 0)
        md = ImageDraw.Draw(mask)
        md.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)

        # --- glass body: radial shading, lighter toward upper-left ----------
        body = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        bd = ImageDraw.Draw(body)
        for i in range(r, 0, -2):
            f = i / r  # 1 at the rim, 0 at the center
            light = 1 - f
            shade = tuple(
                int(min(255, c * (0.45 + 0.55 * light) + 255 * 0.12 * light))
                for c in color
            )
            alpha = int(150 + 50 * light)
            bd.ellipse([cx - i, cy - i, cx + i, cy + i], fill=shade + (alpha,))
        body.putalpha(Image.composite(body.split()[-1], Image.new("L", (size, size), 0), mask))
        composite = Image.alpha_composite(composite, body)

        # --- rainbow swirl band, masked to the sphere ------------------------
        swirl_speed, swirl_alpha = SWIRL_PARAMS.get(self.state, (0.1, 100))
        band = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        bnd = ImageDraw.Draw(band)
        n_segments = 48
        for i in range(n_segments):
            xf = i / n_segments
            x = i * (size / n_segments)
            wave = math.sin(xf * 3 * math.pi + t * swirl_speed * 2 * math.pi) * r * 0.22
            y = cy + wave
            hue = (xf * 0.85 + t * swirl_speed * 0.5) % 1.0
            rr, gg, bb = colorsys.hsv_to_rgb(hue, 0.75, 1.0)
            seg_h = r * 0.55
            bnd.ellipse(
                [x - 5, y - seg_h / 2, x + 5, y + seg_h / 2],
                fill=(int(rr * 255), int(gg * 255), int(bb * 255), swirl_alpha),
            )
        band = band.filter(ImageFilter.GaussianBlur(5))
        band.putalpha(Image.composite(band.split()[-1], Image.new("L", (size, size), 0), mask))
        composite = Image.alpha_composite(composite, band)

        # --- specular highlight, upper-left ----------------------------------
        hl_r = r * 0.32
        hl_x = cx - r * 0.35
        hl_y = cy - r * 0.42
        hl = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        hld = ImageDraw.Draw(hl)
        hld.ellipse([hl_x - hl_r, hl_y - hl_r, hl_x + hl_r, hl_y + hl_r],
                    fill=(255, 255, 255, 100))
        hl = hl.filter(ImageFilter.GaussianBlur(r * 0.18))
        composite = Image.alpha_composite(composite, hl)

        return ImageTk.PhotoImage(composite)

    def _tick(self):
        if not self._running:
            return

        t = time.time()

        # Smooth color transition toward whatever state we're in.
        self.cur_color = list(_lerp_rgb(self.cur_color, self.target_color, 0.12))

        amp, freq = ANIM_PARAMS.get(self.state, (4, 0.5))
        pulse = math.sin(t * freq * 2 * math.pi) * amp

        self.jitter *= 0.82
        radius = self.BASE_RADIUS + pulse + self.jitter

        self._sphere_photo = self._render_sphere(t, radius)
        self.canvas.itemconfig(self.sphere_id, image=self._sphere_photo)

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

        for spawn in self.ripples:
            age = t - spawn
            frac = age / 1.3
            r = self.BASE_RADIUS + age * 70
            faded = _lerp_rgb(self.target_color, BG_RGB, frac)
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

    SIDEBAR_W = 320
    HANDLE_W = 14

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("FRED")
        self.root.configure(bg=BG)
        self.root.attributes("-fullscreen", True)
        # Fullscreen has no title bar to close from — Escape is the
        # one keyboard-level escape hatch, separate from typing "exit".
        self.root.bind("<Escape>", lambda e: self.root.quit())

        self.orchestrator = FREDOrchestrator(show_hud=False)
        self.response_queue = queue.Queue()
        self.processing = False
        self._typing_revert_id = None
        self.history = []  # list of ("you"/"fred", text) — full transcript
        self.sidebar_open = False

        self._build_ui()
        self._poll_responses()

    # ---------------------------------------------------------------
    # LAYOUT
    # ---------------------------------------------------------------

    def _build_ui(self):
        # Centered content column — fullscreen window, fixed-width
        # content floating in the middle of it.
        main = tk.Frame(self.root, bg=BG, width=480)
        main.place(relx=0.5, rely=0.5, anchor="center")

        header = tk.Frame(main, bg=BG, height=54)
        header.pack(fill=tk.X)
        tk.Label(
            header, text="FRED", bg=BG, fg=TEXT_BRIGHT,
            font=("Segoe UI", 15, "bold"),
        ).pack(pady=14)

        self.blob = Blob(main)

        # Output — FRED's last exchange, shown plainly below the orb.
        # No bubble, no box, just text on the background.
        self.output_var = tk.StringVar(value="")
        self.output_label = tk.Label(
            main, textvariable=self.output_var,
            bg=BG, fg=TEXT_BRIGHT, font=("Segoe UI", 10),
            wraplength=440, justify=tk.CENTER,
        )
        self.output_label.pack(pady=(2, 10))

        # The "textbox" — same background as the window, no border,
        # no highlight ring. It's there, you just can't see it as a
        # shape; only the text you type is visible, floating directly
        # on the background.
        entry_row = tk.Frame(main, bg=BG)
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

        self._build_sidebar()

    def _build_sidebar(self):
        # Thin grey handle, always on top and always at the very left
        # edge — clicking it slides the chat-history sidebar in/out.
        self.handle = tk.Frame(self.root, bg="#2a2d33", width=self.HANDLE_W, cursor="hand2")
        self.handle.place(x=0, y=0, relheight=1)
        self.handle.bind("<Button-1>", self._toggle_sidebar)

        line = tk.Label(self.handle, text="│", bg="#2a2d33", fg=TEXT_DIM, font=("Segoe UI", 12))
        line.place(relx=0.5, rely=0.5, anchor="center")
        line.bind("<Button-1>", self._toggle_sidebar)

        self.sidebar = tk.Frame(self.root, bg="#13151a", width=self.SIDEBAR_W)

        tk.Label(
            self.sidebar, text="History", bg="#13151a", fg=TEXT_BRIGHT,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor=tk.W, padx=16, pady=(18, 10))

        self.history_text = tk.Text(
            self.sidebar, bg="#13151a", fg=TEXT_DIM,
            font=("Segoe UI", 9), wrap=tk.WORD,
            relief=tk.FLAT, bd=0, highlightthickness=0,
            padx=16, pady=4,
        )
        self.history_text.pack(fill=tk.BOTH, expand=True)
        self.history_text.tag_config("you", foreground=TEXT_BRIGHT)
        self.history_text.tag_config("fred", foreground="#7fd1a3")
        self.history_text.config(state=tk.DISABLED)

    def _toggle_sidebar(self, event=None):
        self.sidebar_open = not self.sidebar_open
        if self.sidebar_open:
            self.sidebar.place(x=self.HANDLE_W, y=0, relheight=1)
            self.sidebar.lift()
        else:
            self.sidebar.place_forget()
        self.handle.lift()

    def _append_history(self, role: str, text: str):
        self.history.append((role, text))
        self.history_text.config(state=tk.NORMAL)
        label = "You" if role == "you" else "FRED"
        self.history_text.insert(tk.END, f"{label}: ", (role,))
        self.history_text.insert(tk.END, f"{text}\n\n")
        self.history_text.see(tk.END)
        self.history_text.config(state=tk.DISABLED)

    def _set_output(self, user_text: str, reply_text: str):
        self.output_var.set(f"You: {user_text}\n\nFRED: {reply_text}")

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

        self._append_history("you", user_input)
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

        # Don't touch the canvas/history widgets from this background
        # thread — queue it and let _poll_responses do it on the main
        # thread, same as every other widget mutation here.
        self.response_queue.put(("voice_captured", user_input))
        self._process_input(user_input, speak=True)

    # ---------------------------------------------------------------
    # PROCESSING
    # ---------------------------------------------------------------

    def _process_input(self, user_input: str, speak: bool = False):
        try:
            response = self.orchestrator.process(user_input)
            self.blob.bump(14)  # little impact when the reply arrives
            payload = {"user": user_input, "reply": response}
            if speak:
                self.response_queue.put(("speak", payload))
            else:
                self.response_queue.put(("done", payload))
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
    # QUEUE POLLING — every widget mutation happens here, main thread
    # ---------------------------------------------------------------

    def _poll_responses(self):
        try:
            while True:
                msg_type, payload = self.response_queue.get_nowait()

                if msg_type == "voice_captured":
                    self._append_history("you", payload)
                    self.blob.set_state("thinking")
                elif msg_type == "done":
                    self._append_history("fred", payload["reply"])
                    self._set_output(payload["user"], payload["reply"])
                    self.blob.set_state("idle")
                elif msg_type == "speak":
                    self._append_history("fred", payload["reply"])
                    self._set_output(payload["user"], payload["reply"])
                    threading.Thread(target=self._speak, args=(payload["reply"],), daemon=True).start()
                elif msg_type == "revert_idle":
                    self.blob.set_state("idle")
                elif msg_type == "error":
                    self._set_output("", f"[error] {payload}")
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
