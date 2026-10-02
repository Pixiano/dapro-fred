# Core/state/conversation_state.py

from datetime import datetime


class ConversationState:
    """
    Handles short-term conversational state for F.R.E.D.

    Responsibilities:
    - Store active session messages
    - Provide recent conversational context
    - Manage session lifecycle
    """

    def __init__(self):
        self.messages = []

    def add_message(self, role: str, content: str):
        """
        Add a message to active session state.
        """

        if not content or not content.strip():
            return

        entry = {
            "role": role,
            "content": content.strip(),
            "timestamp": datetime.now().isoformat()
        }

        self.messages.append(entry)

    def get_recent_messages(self, limit: int = 10) -> list:
        """
        Return the most recent conversation messages.
        """

        return self.messages[-limit:]

    def get_all_messages(self) -> list:
        """
        Return full active session history.
        """

        return self.messages.copy()

    def clear(self):
        """
        Reset active session state.
        """

        self.messages.clear()

    def message_count(self) -> int:
        """
        Return number of active session messages.
        """

        return len(self.messages)

    def export_session(self) -> dict:
        """
        Export current session state.
        Useful for debugging, saving, or analytics.
        """

        return {
            "message_count": len(self.messages),
            "messages": self.messages.copy()
        }