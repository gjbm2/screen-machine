import os
from openai import OpenAI
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

# Fetch the API key from environment
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError("OPENAI_API_KEY not found in environment. Check your .env file or environment variables.")

client = OpenAI(api_key=api_key)

# List all files
files = client.files.list()

# Delete them
for f in files.data:
    print(f"Deleting {f.id} ({f.filename})")
    client.files.delete(file_id=f.id)
