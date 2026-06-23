#user_input.py
def get_keywords(min_keywords=3):
    """Prompt user to enter keywords, separated by commas."""
    while True:
        raw_input = input(f"Enter at least {min_keywords} keywords, separated by commas: ")
        keywords = [k.strip() for k in raw_input.split(",") if k.strip()]
        if len(keywords) >= min_keywords:
            return keywords
        print(f"[⚠️] Please enter at least {min_keywords} keywords.")