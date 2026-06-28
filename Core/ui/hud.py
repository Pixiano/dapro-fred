# Core/ui/hud.py

import tkinter as tk
import threading
import queue
from typing import Literal


class HUDWindow:
    """
    Always-on-top HUD window displaying FRED's state and live transcript.
    Runs in a background thread, non-blocking. Thread-safe via queue.
    """

    STATE_COLORS = {
        "idle": "#2a2a2a",
        "listening": "#1e5a96",
        "thinking": "#8b6914",
        "speaking": "#2d7a3d",
    }

    STATE_LABELS = {
        "idle": "IDLE",
        "listening": "LISTENING",
        "thinking": "THINKING",
        "speaking": "SPEAKING",
    }

    def __init__(self):
        self.update_queue = queue.Queue()
        self.root = None
        self.thread = None
        self.running = False
        self.state = "idle"
        self.transcript = ""
        self._start_thread()

    def _start_thread(self):
        """Start the HUD in a background thread."""
        self.running = True
        self.thread = threading.Thread(target=self._run_window, daemon=True)
        self.thread.start()

    def _run_window(self):
        """Initialize and run the tkinter window loop."""
        try:
            self.root = tk.Tk()
            self.root.title("F.R.E.D.")
            self.root.geometry("400x200")
            self.root.attributes("-topmost", True)

            # Dark theme
            self.root.configure(bg="#1a1a1a")

            # State indicator
            self.state_frame = tk.Frame(self.root, bg="#1a1a1a")
            self.state_frame.pack(fill=tk.X, padx=10, pady=10)

            self.state_label = tk.Label(
                self.state_frame,
                text="IDLE",
                font=("Courier", 14, "bold"),
                fg="#ffffff",
                bg="#2a2a2a",
                padx=15,
                pady=8,
            )
            self.state_label.pack()

            # Transcript area
            self.transcript_frame = tk.Frame(self.root, bg="#1a1a1a")
            self.transcript_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

            transcript_title = tk.Label(
                self.transcript_frame,
                text="Transcript:",
                font=("Courier", 10),
                fg="#888888",
                bg="#1a1a1a",
            )
            transcript_title.pack(anchor=tk.W)

            self.transcript_text = tk.Text(
                self.transcript_frame,
                height=6,
                width=50,
                bg="#0a0a0a",
                fg="#00cc00",
                font=("Courier", 9),
                wrap=tk.WORD,
                relief=tk.SUNKEN,
                borderwidth=1,
            )
            self.transcript_text.pack(fill=tk.BOTH, expand=True)
            self.transcript_text.config(state=tk.DISABLED)

            # Start polling for queue messages
            self._process_queue()

            self.root.mainloop()
        except Exception as e:
            print(f"[HUD Error] {e}")
            self.running = False

    def _process_queue(self):
        """Process update messages from the queue."""
        if not self.running:
            return

        try:
            while True:
                msg_type, msg_data = self.update_queue.get_nowait()

                if msg_type == "state":
                    self.state = msg_data
                    self.state_label.config(
                        text=self.STATE_LABELS[self.state],
                        bg=self.STATE_COLORS[self.state],
                    )
                elif msg_type == "transcript":
                    self.transcript = msg_data
                    self.transcript_text.config(state=tk.NORMAL)
                    self.transcript_text.delete("1.0", tk.END)
                    self.transcript_text.insert("1.0", self.transcript)
                    self.transcript_text.config(state=tk.DISABLED)

        except queue.Empty:
            pass
        except Exception as e:
            pass

        if self.root and self.running:
            self.root.after(100, self._process_queue)

    def set_state(self, state: Literal["idle", "listening", "thinking", "speaking"]):
        """Queue a state update (thread-safe)."""
        self.update_queue.put(("state", state))

    def set_transcript(self, text: str):
        """Queue a transcript update (thread-safe)."""
        self.update_queue.put(("transcript", text))

    def shutdown(self):
        """Gracefully shutdown the HUD window."""
        self.running = False
        if self.root:
            try:
                self.root.quit()
                self.root.destroy()
            except:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
