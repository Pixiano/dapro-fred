#fred_api.py (for api ofc)

import requests
from config import GROQ_API_KEY

def ask_fred(prompt: str):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "llama-3-8b-instruct",
        "messages": [
            {"role": "system", "content": "You are F.R.E.D. — Friendly, Responsive, Rational, Rakish Electronic Dude. Keep replies short, witty, clear."},
            {"role": "user", "content": prompt}
        ]
    }

    response = requests.post(url, headers=headers, json=data)
    return response.json()["choices"][0]["message"]["content"]

if __name__ == "__main__":
    print(ask_fred("Hey Fred, how are you?"))