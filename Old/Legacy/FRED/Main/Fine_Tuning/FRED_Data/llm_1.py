import os
import requests
from mistralai import Mistral
from dotenv import load_dotenv

# ---------------- Load API Key ----------------
load_dotenv("api.env")
api_key = os.getenv("MISTRAL_API_KEY")
if not api_key:
    raise ValueError("MISTRAL_API_KEY not set in api.env")

client = Mistral(api_key=api_key)

# ---------------- System prompt ----------------
SYSTEM_PROMPT = ( "You are F.R.E.D. — Friendly, Responsive, Rational, Rakish Electronic Dude.\n" "- Always answer concisely (1–3 sentences).\n" "- Inject a touch of wit or rakish charm.\n" "- Be clear and rational, never vague.\n" "- Occasionally, take inspiration from JARVIS, Tony Stark's AI in Iron Man.\n" "- Avoid long introductions; get straight to the point." ) # ---------------- Model settings ---------------- MODEL_CONFIG = { "temperature": 0.8, # playful and little bit of focus "max_tokens": 500, # enough for 2–3 sentences + context "top_p": 0.9, # slightly diverse but coherent "frequency_penalty": 0.5, # reduce repetition "presence_penalty": 0.3, # slight exploration "stop": ["\n", "User:", "FRED:"], "n": 1 # single confident response }

# ---------------- Minimal ask function ----------------
def ask_fred(prompt: str):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt}
    ]

    try:
        response = client.chat.complete(
            model="mistral-large-2411",
            messages=messages,
            temperature=0.75,
            max_tokens=500
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        print("Mistral API failed:", e)
        # Fallback to local LM Studio if available
        try:
            lm_response = requests.post(
                "http://localhost:1234/v1/chat/completions",
                json={
                    "model": "mistral-7b-instruct-v0.2",
                    "messages": messages,
                    "temperature": 0.65,
                    "max_tokens": 200
                },
                timeout=60
            )
            if lm_response.status_code == 200:
                return lm_response.json()["choices"][0]["message"]["content"].strip()
            else:
                return f"Error {lm_response.status_code}: {lm_response.text}"
        except Exception as le:
            return f"Fallback failed: {le}"

# ---------------- Example usage ----------------
if __name__ == "__main__":
    while True:
        user_input = input("You: ").strip()
        if user_input.lower() == "exit":
            break
        reply = ask_fred(user_input)
        print("FRED:", reply)