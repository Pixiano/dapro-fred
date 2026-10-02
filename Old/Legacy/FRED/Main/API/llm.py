#llm.py 

import os
import requests
import json
from mistralai import Mistral
from dotenv import load_dotenv
from memory import search_memory
from datetime import datetime

# ---------------- Load API Key ----------------
load_dotenv("api.env")
api_key = os.getenv("MISTRAL_API_KEY")
if not api_key:
    raise ValueError("MISTRAL_API_KEY not set in api.env")

client = Mistral(api_key=api_key)

# ---------------- System Prompt ----------------
SYSTEM_PROMPT = (
    "You are F.R.E.D. — Friendly, Responsive, Rational, Rakish Electronic Dude.\n"
    "- Always answer concisely (1–3 sentences).\n"
    "- Inject a touch of wit or rakish charm.\n"
    "- Be clear and rational, never vague.\n"
    "- Avoid long introductions; get straight to the point.\n"
)

# ---------------- Model settings ----------------
MODEL_CONFIG = {
    "temperature": 0.65,
    "max_tokens": 8192,
    "top_p": 0.9,
    "frequency_penalty": 0.5,
    "presence_penalty": 0.3,
    "n": 1
}

# ---------------- Tools (function calling) ----------------
def get_time():
    """Return the current system date and time."""
    now = datetime.now()
    return {
        "date": now.strftime("%A, %B %d, %Y"),
        "time": now.strftime("%H:%M:%S")
    }

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the current date and time on the local machine.",
            "parameters": {}
        }
    }
]

# ---------------- Memory triggers ----------------
TRIGGER_WORDS = ["remember", "recall", "past", "first chat", "conversation", "last time", "earlier"]
TOP_N = 5
MEMORY_REFRESH_PERIOD = 5

# ---------------- Session caches ----------------
session_embeddings = {}
message_counter = 0


# ---------------- Ask F.R.E.D. with memory ----------------
def ask_fred_with_memory(prompt: str, context: list = []):
    global message_counter
    message_counter += 1
    memory_context = []

    if message_counter % MEMORY_REFRESH_PERIOD == 0:
        if any(word.lower() in prompt.lower() for word in TRIGGER_WORDS):
            results = search_memory(prompt, top_n=TOP_N)
            if results:
                for m in results:
                    if m['content'] not in session_embeddings:
                        session_embeddings[m['content']] = m.get('embedding', None)
                    memory_context.append({"role": "system", "content": f"[Memory] {m['content']}"})

    full_context = memory_context + context
    if len(full_context) + 1 > 50:
        full_context = full_context[-49:]

    return ask_fred(prompt, full_context)


# ---------------- Ask F.R.E.D. normal ----------------
def ask_fred(prompt: str, context: list = []):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(context)
    messages.append({"role": "user", "content": prompt})

    try:
        # --- Call Mistral API with tool capability ---
        response = client.chat.complete(
            model="mistral-large-latest",
            messages=messages,
            temperature=MODEL_CONFIG["temperature"],
            max_tokens=MODEL_CONFIG["max_tokens"],
            top_p=MODEL_CONFIG["top_p"],
            frequency_penalty=MODEL_CONFIG["frequency_penalty"],
            presence_penalty=MODEL_CONFIG["presence_penalty"],
            tools=TOOLS
        )

        choice = response.choices[0]
        message = choice.message

        # --- If model requests tool call (like get_time) ---
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tool_call in message.tool_calls:
                if tool_call.function.name == "get_time":
                    result = get_time()
                    print("🕒 FRED requested time:", result)

                    # Convert message to JSON-safe dict before appending
                    messages.append({
                        "role": getattr(message, "role", "assistant"),
                        "content": getattr(message, "content", "")
                    })
                    messages.append({
                        "role": "tool",
                        "name": "get_time",
                        "content": json.dumps(result)
                    })

                    # --- Send updated conversation back for final response ---
                    follow_up = client.chat.complete(
                        model="mistral-large-latest",
                        messages=messages,
                        temperature=MODEL_CONFIG["temperature"],
                        max_tokens=MODEL_CONFIG["max_tokens"]
                    )
                    final_text = follow_up.choices[0].message.content.strip()
                    print("DEBUG Mistral (after tool):", final_text)
                    return final_text

        # --- Normal case (no tool used) ---
        final_text = message.content.strip()
        return final_text

    except Exception as e:
        print("DEBUG ask_fred error (Mistral failed):", e)

        # --- LM Studio fallback ---
        try:
            # Make sure all messages are JSON-serializable
            safe_messages = []
            for msg in messages:
                if isinstance(msg, dict):
                    safe_messages.append(msg)
                else:
                    safe_messages.append({
                        "role": getattr(msg, "role", "assistant"),
                        "content": getattr(msg, "content", str(msg))
                    })

            lm_response = requests.post(
                "http://localhost:1234/v1/chat/completions",
                json={
                    "model": "mistral-7b-instruct-v0.2",
                    "messages": safe_messages,
                    "temperature": MODEL_CONFIG["temperature"],
                    "max_tokens": MODEL_CONFIG["max_tokens"],
                    "top_p": MODEL_CONFIG["top_p"],
                    "frequency_penalty": MODEL_CONFIG["frequency_penalty"],
                    "presence_penalty": MODEL_CONFIG["presence_penalty"]
                },
                timeout=600
            )

            if lm_response.status_code == 200:
                lm_text = lm_response.json()["choices"][0]["message"]["content"].strip()
                print("🖥️ Using LM Studio (fallback)")
                print("DEBUG LM Studio response:", lm_text)
                return lm_text
            else:
                return f"❌ LM Studio error {lm_response.status_code}: {lm_response.text}"

        except Exception as le:
            return f"❌ LM Studio request failed: {le}"


# ---------------- Embeddings with fallback ----------------
def get_embedding(text: str, dim: int = 384):
    if text in session_embeddings:
        return session_embeddings[text]

    try:
        emb = client.embeddings.create(model="mistral-embed", inputs=text)
        session_embeddings[text] = emb.data[0].embedding
        return session_embeddings[text]
    except Exception as e:
        print("DEBUG embedding error (Mistral):", e)

    try:
        lm_response = requests.post(
            "http://localhost:1234/v1/embeddings",
            json={"model": "mistral-embed", "input": text},
            timeout=60
        )
        if lm_response.status_code == 200:
            print("🖥️ Using LM Studio embeddings (fallback)")
            session_embeddings[text] = lm_response.json()["data"][0]["embedding"]
            return session_embeddings[text]
    except Exception as le:
        print("DEBUG embedding error (LM Studio):", le)

    print("[WARN] Falling back to zero-vector embedding.")
    session_embeddings[text] = [0.0] * dim
    return session_embeddings[text]