from memory_1 import remember, get_context, flush, forget_all
from llm_1 import ask_fred

# --- OS command placeholders ---
def execute_os_command(command):
    commands = {
        "open_browser": lambda: "Browser opened.",
        "create_file": lambda: "File created.",
        "create_folder": lambda: "Folder created."
    }
    for key in commands:
        if key in command.lower():
            return commands[key]()
    return None

# --- Main loop ---
def main():
    print("F.R.E.D. is online. Type 'exit' to quit.")
    print("Commands: /forget")

    while True:
        user_input = input("You: ").strip()
        if not user_input:
            continue

        if user_input.lower() == "exit":
            flush()  # flush any remaining memory
            break

        # --- Memory commands ---
        if user_input.lower().startswith("/forget"):
            forget_all()
            print("🧹 Memory wiped.")
            continue

        # --- OS commands ---
        os_response = execute_os_command(user_input)
        if os_response:
            print(f"FRED (OS): {os_response}")
            continue

        # --- Build context from last 50 chats ---
        memory_context = get_context(max_chats=50)
        # Convert to simple list of strings for LLM prompt
        context_text = "\n".join([f"User: {m['instruction']}\nAssistant: {m['response']}" for m in memory_context])
        prompt = f"{context_text}\nUser: {user_input}" if context_text else user_input

        # --- Ask F.R.E.D ---
        fred_reply = ask_fred(prompt)
        print(f"FRED: {fred_reply}")

        # --- Save to memory ---
        remember("user", user_input)
        remember("assistant", fred_reply)

if __name__ == "__main__":
    main()