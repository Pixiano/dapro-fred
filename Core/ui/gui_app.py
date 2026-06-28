# Core/ui/gui_app.py
# FRED GUI Application - Graphical interface for conversation

import tkinter as tk
from tkinter import scrolledtext, ttk
import threading
import queue
from typing import Optional

from orchestrator.orchestrator import FREDOrchestrator
from ui.hud import HUDWindow


class FREDGUIApp:
    """GUI application for FRED conversation."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("F.R.E.D. - Personal Assistant")
        self.root.geometry("900x700")
        self.root.configure(bg="#1a1a1a")

        self.orchestrator = FREDOrchestrator()
        self.response_queue = queue.Queue()
        self.processing = False

        self._setup_ui()
        self._poll_responses()

    def _setup_ui(self):
        """Build the GUI layout."""
        # Header with status
        header = tk.Frame(self.root, bg="#2a2a2a", height=60)
        header.pack(fill=tk.X, padx=0, pady=0)

        title = tk.Label(
            header,
            text="F.R.E.D.",
            font=("Courier", 24, "bold"),
            fg="#00cc00",
            bg="#2a2a2a",
        )
        title.pack(side=tk.LEFT, padx=15, pady=10)

        self.status_label = tk.Label(
            header,
            text="IDLE",
            font=("Courier", 12, "bold"),
            fg="#ffffff",
            bg="#2a2a2a",
            padx=15,
            pady=8,
            relief=tk.SUNKEN,
        )
        self.status_label.pack(side=tk.RIGHT, padx=15, pady=10)

        # Main conversation area
        conv_frame = tk.Frame(self.root, bg="#1a1a1a")
        conv_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        conv_label = tk.Label(
            conv_frame,
            text="Conversation:",
            font=("Courier", 10),
            fg="#888888",
            bg="#1a1a1a",
        )
        conv_label.pack(anchor=tk.W)

        self.conversation_text = scrolledtext.ScrolledText(
            conv_frame,
            height=20,
            width=100,
            bg="#0a0a0a",
            fg="#00cc00",
            font=("Courier", 9),
            wrap=tk.WORD,
            relief=tk.SUNKEN,
            borderwidth=1,
        )
        self.conversation_text.pack(fill=tk.BOTH, expand=True, pady=5)
        self.conversation_text.config(state=tk.DISABLED)

        # Configure tags for styling
        self.conversation_text.tag_config("user", foreground="#00ccff")
        self.conversation_text.tag_config("assistant", foreground="#00cc00")
        self.conversation_text.tag_config("system", foreground="#888888")

        # Input area
        input_frame = tk.Frame(self.root, bg="#1a1a1a")
        input_frame.pack(fill=tk.X, padx=10, pady=10)

        input_label = tk.Label(
            input_frame,
            text="You:",
            font=("Courier", 10),
            fg="#00ccff",
            bg="#1a1a1a",
        )
        input_label.pack(anchor=tk.W)

        self.input_text = tk.Entry(
            input_frame,
            bg="#0a0a0a",
            fg="#00ccff",
            font=("Courier", 10),
            relief=tk.SUNKEN,
            borderwidth=1,
            insertbackground="#00ccff",
        )
        self.input_text.pack(fill=tk.X, pady=5)
        self.input_text.bind("<Return>", self._on_submit)
        self.input_text.focus()

        # Button area
        button_frame = tk.Frame(self.root, bg="#1a1a1a")
        button_frame.pack(fill=tk.X, padx=10, pady=5)

        send_btn = tk.Button(
            button_frame,
            text="Send (Enter)",
            command=self._on_submit,
            bg="#2a5a3a",
            fg="#00cc00",
            font=("Courier", 10),
            padx=15,
            pady=5,
            relief=tk.RAISED,
        )
        send_btn.pack(side=tk.LEFT, padx=5)

        clear_btn = tk.Button(
            button_frame,
            text="Clear",
            command=self._clear_conversation,
            bg="#2a2a2a",
            fg="#888888",
            font=("Courier", 10),
            padx=15,
            pady=5,
            relief=tk.RAISED,
        )
        clear_btn.pack(side=tk.LEFT, padx=5)

        exit_btn = tk.Button(
            button_frame,
            text="Exit",
            command=self.root.quit,
            bg="#5a2a2a",
            fg="#cc0000",
            font=("Courier", 10),
            padx=15,
            pady=5,
            relief=tk.RAISED,
        )
        exit_btn.pack(side=tk.RIGHT, padx=5)

    def _on_submit(self, event=None):
        """Handle user input submission."""
        user_input = self.input_text.get().strip()

        if not user_input:
            return

        if user_input.lower() in ("exit", "quit"):
            self.root.quit()
            return

        self.input_text.delete(0, tk.END)
        self._append_message(f"You: {user_input}", "user")

        # Process in background thread
        if not self.processing:
            self.processing = True
            thread = threading.Thread(target=self._process_input, args=(user_input,))
            thread.daemon = True
            thread.start()

    def _process_input(self, user_input: str):
        """Process user input in background thread."""
        try:
            self.orchestrator.hud.set_state("thinking")
            response = self.orchestrator.process(user_input)
            self.response_queue.put(("response", response))
            self.orchestrator.hud.set_state("idle")
        except Exception as e:
            self.response_queue.put(("error", str(e)))
        finally:
            self.processing = False

    def _poll_responses(self):
        """Poll the response queue for new messages."""
        try:
            while True:
                msg_type, content = self.response_queue.get_nowait()

                if msg_type == "response":
                    self._append_message(f"FRED: {content}", "assistant")
                elif msg_type == "error":
                    self._append_message(f"[Error] {content}", "system")

        except queue.Empty:
            pass

        self.root.after(200, self._poll_responses)

    def _append_message(self, text: str, tag: str = "system"):
        """Append a message to the conversation display."""
        self.conversation_text.config(state=tk.NORMAL)
        self.conversation_text.insert(tk.END, text + "\n", tag)
        self.conversation_text.see(tk.END)
        self.conversation_text.config(state=tk.DISABLED)

    def _clear_conversation(self):
        """Clear the conversation history."""
        self.conversation_text.config(state=tk.NORMAL)
        self.conversation_text.delete("1.0", tk.END)
        self.conversation_text.config(state=tk.DISABLED)

    def shutdown(self):
        """Clean shutdown."""
        self.orchestrator.hud.shutdown()
        self.orchestrator.shutdown()


def run_gui():
    """Launch the FRED GUI application."""
    root = tk.Tk()
    app = FREDGUIApp(root)

    def on_close():
        app.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
