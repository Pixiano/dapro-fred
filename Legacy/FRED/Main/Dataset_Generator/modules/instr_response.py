import random

# List of instruction templates
INSTRUCTION_TEMPLATES = [
    "Provide detailed information about '{keyword}'.",
    "Explain everything important regarding '{keyword}'.",
    "Give a comprehensive overview of '{keyword}'.",
    "Summarize key points and insights on '{keyword}'.",
    "Write an in-depth explanation of '{keyword}'.",
    "Describe '{keyword}' thoroughly with examples if possible.",
    "Provide a full guide on understanding '{keyword}'.",
    "Explain '{keyword}' clearly as if teaching a beginner.",
    "Explain '{keyword}.'"
]

def instruction_response(article):
    """
    Converts a cleaned article dict into an instruction-response pair.
    Instruction is randomly chosen.
    """
    keyword = article.get("keyword", "")
    content = article.get("content", "")

    instruction = random.choice(INSTRUCTION_TEMPLATES).format(keyword=keyword)

    return {
        "instruction": instruction,
        "response": content
    }