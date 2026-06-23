from dotenv import load_dotenv
import os

load_dotenv("api.env")

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")