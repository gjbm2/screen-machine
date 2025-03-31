from openai import OpenAI
import os

client = OpenAI(api_key="")

# List all files
files = client.files.list()

# Delete them
for f in files.data:
    print(f"Deleting {f.id} ({f.filename})")
    client.files.delete(file_id=f.id)
