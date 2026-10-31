import json

LOG_PATH = r"C:\Users\Dell\.gemini\antigravity-ide\brain\ffed27a1-3863-4dc5-846d-207f4a743649\.system_generated\logs\transcript.jsonl"

with open(LOG_PATH, "r", encoding="utf-8") as f:
    for line in f:
        data = json.loads(line)
        step = data.get("step_index")
        if step == 166:
            content = data.get("content") or ""
            clean_content = content.encode("ascii", "replace").decode("ascii")
            print(f"--- STEP 166 FULL CONTENT ---")
            print(clean_content)
            print("-" * 50)
