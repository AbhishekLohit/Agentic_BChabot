import requests
import pandas as pd
from src.app_models import ChatMessage

# 🔧 CONFIG
API_URL = "http://localhost:8000/process-debug"
INPUT_FILE = "tests/data/golden_data_set_ver1.xlsx"
OUTPUT_FILE = "debug_results.xlsx"


# ✅ Summary columns
ALL_COLUMNS = [
    "test_id",
    "customer_id",
    "session_id",
    "user_query",
    "chat_history_raw",
    "context_user_query",
    "classification",
    "sub_query",
    "sql_generated",
    "db_results_present",
    "out_of_scope_queries",
    "fallback_flag",
    "fallback_reason",
    "fallback_node",
    "final_response"
]


# 🧠 Parse chat history
def parse_chat_history(chat_history_str):
    lines = chat_history_str.strip().split("\n")
    chat_messages = []

    for line in lines:
        line = line.strip()

        if line.lower().startswith("user:"):
            chat_messages.append(
                ChatMessage(role="user", content=line.replace("user:", "").strip())
            )
        elif line.lower().startswith("assistant:"):
            chat_messages.append(
                ChatMessage(role="assistant", content=line.replace("assistant:", "").strip())
            )

    return chat_messages


# 🔍 Flatten Graph State
def flatten_graph_state(graph_state, chat_history_str):
    flat = {}

    try:
        # -------------------------------
        # Input Request
        # -------------------------------
        input_req = graph_state.get("input_request") or {}

        flat["customer_id"] = input_req.get("customer_id", "NA")
        flat["session_id"] = input_req.get("session_id", "NA")
        flat["user_query"] = input_req.get("user_query", "NA")
        flat["chat_history_raw"] = chat_history_str

        # -------------------------------
        # Context Builder
        # -------------------------------
        context = graph_state.get("context_builder_response") or {}
        flat["context_user_query"] = context.get("user_query", "NA")

        # -------------------------------
        # Supervisor
        # -------------------------------
        supervisor = graph_state.get("suprivisor_response") or {}
        classified = supervisor.get("classified_sub_queries") or []

        if isinstance(classified, list) and len(classified) > 0:
            first = classified[0] or {}
            flat["classification"] = first.get("class_label", "NA")
            flat["sub_query"] = first.get("sub_query", "NA")
        else:
            flat["classification"] = "NA"
            flat["sub_query"] = "NA"

        # -------------------------------
        # Flags
        # -------------------------------
        flat["sql_generated"] = graph_state.get("text2sql_generator_response", "NA")
        flat["db_results_present"] = graph_state.get("database_query_results", "NA")

        # -------------------------------
        # Out of Scope
        # -------------------------------
        oos = graph_state.get("out_of_scope_queries") or []
        if isinstance(oos, list):
            flat["out_of_scope_queries"] = ", ".join(map(str, oos))
        else:
            flat["out_of_scope_queries"] = str(oos)

        # -------------------------------
        # Fallback
        # -------------------------------
        fallback = graph_state.get("fallback_response") or {}

        flat["fallback_flag"] = fallback.get("fallback_flag", "NA")
        flat["fallback_reason"] = fallback.get("fallback_reason", "NA")
        flat["fallback_node"] = fallback.get("fallback_node", "NA")

        # -------------------------------
        # Final Response
        # -------------------------------
        response_gen = graph_state.get("response_generation_response") or {}
        flat["final_response"] = response_gen.get("generated_response", "NA")

    except Exception as e:
        print(f"Error occurred while flattening graph state: {e}")
        print("Graph state snapshot:", graph_state)  # 🔥 Debugging gold

    return flat


# 🔥 Flatten metrics → ONE ROW per test (dynamic agents)
def flatten_metrics_single_row(agent_metrics: dict, test_id: int):
    row = {"test_id": test_id}

    metric_fields = [
        "latency",
        "model_cost_without_cache",
        "model_cost_with_cache",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "model_used"
    ]

    # No metrics case
    if not agent_metrics:
        return row

    for agent_name, metrics in agent_metrics.items():

        # Handle Pydantic objects
        if hasattr(metrics, "dict"):
            metrics = metrics.dict()

        for field in metric_fields:
            col_name = f"{agent_name}_{field}"
            row[col_name] = metrics.get(field, "NA")

    return row


# 📥 Load Excel
df = pd.read_excel(INPUT_FILE)

# 🔥 limit rows for testing
# df = df.iloc[20:25,:]

rows = []
metrics_rows = []


# 🚀 Run Tests
for idx, row in df.iterrows():
    print(f"Running test case {idx + 1}")

    chat_history_str = row.get("chat_history", "")
    latest_question = row.get("latest_user_question", "")

    try:
        parsed_history = parse_chat_history(chat_history_str)

        payload = {
            "user_query": str(latest_question),
            "customer_id": "Cust_0410",
            "session_id": f"sess_{idx}",
            "chat_history": [msg.dict() for msg in parsed_history]
        }

        response = requests.post(API_URL, json=payload)
        response.raise_for_status()

        graph_state = response.json()

        # -------- Summary --------
        flat_output = flatten_graph_state(graph_state, chat_history_str)
        flat_output["test_id"] = idx + 1

        for col in ALL_COLUMNS:
            if col not in flat_output:
                flat_output[col] = None

        rows.append(flat_output)

        # -------- Metrics --------
        agent_metrics = graph_state.get("agent_metrics", {})
        metrics_row = flatten_metrics_single_row(agent_metrics, idx + 1)
        metrics_rows.append(metrics_row)

    except Exception as e:
        print(f"❌ Error in test case {idx + 1}: {e}")

        # -------- Summary NA Row --------
        error_row = {
            "test_id": idx + 1,
            "customer_id": f"cust_{idx}",
            "session_id": f"sess_{idx}",
            "user_query": str(latest_question),
            "chat_history_raw": chat_history_str,
        }

        for col in ALL_COLUMNS:
            if col not in error_row:
                error_row[col] = "NA"

        rows.append(error_row)

        # -------- Metrics NA Row --------
        metrics_rows.append(flatten_metrics_single_row({}, idx + 1))


# 📊 Convert to DataFrame
summary_df = pd.DataFrame(rows)[ALL_COLUMNS]

metrics_df = pd.DataFrame(metrics_rows)

# Ensure consistent schema
metrics_df = metrics_df.fillna("NA")

# Optional: sort columns for readability
metrics_cols = ["test_id"] + sorted([c for c in metrics_df.columns if c != "test_id"])
metrics_df = metrics_df[metrics_cols]


# 📊 Save to Excel
with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:

    # Summary sheet
    summary_df.to_excel(writer, sheet_name="summary", index=False)

    # ✅ Single metrics sheet
    metrics_df.to_excel(writer, sheet_name="agent_metrics", index=False)


print(f"\n✅ Results saved to {OUTPUT_FILE}")