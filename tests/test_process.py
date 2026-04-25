# test.py
import requests
from datetime import datetime

url = "http://127.0.0.1:8000/process"  # your FastAPI endpoint

payload = {
    "customer_id": "CUST-001",
    "session_id": "SESSION-123",
    "user_query": "Show my orders for ice cream and pizza from Domino’s in Bangalore."
}

response_raw = requests.post(url, json=payload)
response = response_raw.json()
response_entity_extraction = response.get("entity_extraction_response", {})


print("Status code:", response_raw.status_code)
print("Response text:", response)
print("response_entity_extraction text:", response_entity_extraction)


# ⚙️ Return Value
# requests.post() returns a Response object — not the actual data.


# Attribute	Description
# response.status_code	The HTTP status (e.g. 200, 400, 500)
# response.text	The raw text returned by the server (JSON string, HTML, etc.)
# response.json()	Parsed JSON (Python dict), if the response is valid JSON
# response.headers	Response headers from the server
# response.ok	Boolean → True if status_code < 400
# response.reason	Short description of status code (“OK”, “Bad Request”)

# 🔍 Example:
# response = requests.post(url, json=payload)

# print(response.status_code)   # 200
# print(response.ok)            # True
# print(response.text)          # '{"message": "Success"}'
# print(response.json())        # {'message': 'Success'}
