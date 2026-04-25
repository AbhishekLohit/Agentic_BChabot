import requests
import uuid

url = "http://127.0.0.1:8000/process"  # your FastAPI endpoint

# Simulate a unique user and session
user_id = "Cust_0410"
session_id = "2"  # generate new session per run

def chat_with_bot():
    print(f"🧑 User ID: {user_id}")
    print(f"💬 Session ID: {session_id}")
    print("Type 'exit' to stop.\n")

    while True:
        user_message = input("You: ").strip()
        if user_message.lower() in {"exit", "quit"}:
            print("👋 Ending chat.")
            break

        payload = {
            "customer_id": user_id,
            "session_id": session_id,
            "user_query": user_message
        }

        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            # bot_reply = response.json().get("generated_response", "")
            print(f"🤖 Bot: {response.json()}\n")
        except Exception as e:
            print(f"⚠️ Error: {e}\n")

if __name__ == "__main__":
    chat_with_bot()
