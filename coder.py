import os
from openai import OpenAI

client = OpenAI(
    base_url="https://llm.hidevs.xyz/v1",
    api_key=os.environ.get("HIDEVS_API_KEY"),
)

print("Starting Coder Assistant (type 'exit' to quit)...")

while True:
    user_input = input("\nWhat do you want me to do?\n> ")
    if user_input.lower().strip() in ["exit", "quit"]:
        break
        
    response = client.chat.completions.create(
        model="gemini-3.5-flash-lite",
        messages=[{"role": "user", "content": user_input}],
    )
    print(response.choices[0].message.content)
