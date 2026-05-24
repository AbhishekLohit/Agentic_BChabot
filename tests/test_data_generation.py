import sys
from pathlib import Path
import time

# -------------------------------------------------
# Add src/ to PYTHONPATH
# -------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

# -------------------------------------------------
# Imports
# -------------------------------------------------

from clients.llm_client import get_configured_llm
from app_models import Test_Data_Generation_Response
from langchain_openai import ChatOpenAI
from utils.file_utils import get_all_prompts
from app_setting import AppSettings
import pandas as pd


# -------------------------------------------------
# Scenario Configuration (NEW)
# -------------------------------------------------

context_builder_test_scenarios = {

    "rule_A_validity_and_ambiguity": {

        "irrelevant_queries": [
            "User asks something unrelated to food ordering"
        ],

        "meaningless_input": [
            "Random characters or numbers",
            "Empty or punctuation only messages"
        ],

        "greeting_only": [
            "User sends greeting without question"
        ],

        "greeting_with_question": [
            "Greeting followed by a valid food-order query"
        ]
    },


    "rule_B_context_completion": {

        "missing_restaurant_context": [
            "User follow-up question is related to resturant but misses mentioning the restaurant name",
			"For example: history has talks about orders at a resturant and latest user question does not mention the resturant name"
        ],

        "missing_food_item_context": [
            "Food item referenced earlier in history but not present in latest query"
        ],

        "missing_order_reference": [
            "User refers to 'last order' or 'that order'"
        ],

        "delivery_context_completion": [
            "Follow-up about delivery time or status"
        ],

        "refund_context_completion": [
            "Follow-up about refunds related to previous order context"
        ],

        "discount_context_completion": [
            "Follow-up about discounts related to earlier order context"
        ]
    },


    "rule_B_timeframe_resolution": {

        "latest_query_has_no_timeframe": [
            "Latest question does not have timeframe BUT Timeframe is mentioned in history"
        ],

        "multiple_timeframes_in_history": [
            "History contains multiple timeframes BUT no timeframe in latest user question"
        ]
    },


    "rule_B_recheck_handling": {

        "valid_recheck": [
            "User asks to recheck previous request"
        ],
    },



    "rule_C_multiple_questions": {

        "multiple_user_questions_same_topic": [
            "Query contains 2-3 multiple user questions on same topics"
        ],
		"multiple_user_questions_different_topic": [
            "Query contains 2-3 multiple user questions on different topics"
        ],

        
    },


    "pronoun_resolution": {

        "single_pronoun_reference": [
            "it",
            "that",
            "those",
            "them"
        ],

        "order_reference_pronouns": [
            "last order",
            "previous order",
            "that order"
        ]
    },


    "edge_cases": {

        "history_with_multiple_restaurants": [
            "History mentions different restaurants"
        ],

        "history_with_multiple_timeframes": [
            "History mentions different time periods"
        ],

        "mixed_intents_in_history": [
            "History contains orders, refunds, spending queries"
        ],

        "very_long_chat_history": [
            "10+ conversation turns"
        ]
    }
}

# -------------------------------------------------
# Test Data Generation Function (UPDATED)
# -------------------------------------------------

def test_data_generation(
    llm: ChatOpenAI,
    prompt: str,
    app_settings: AppSettings,
    scenario_name: str,
    scenario_description: str,
    batch_size: int
) -> Test_Data_Generation_Response:

    print(f"\nExecuting test data generation for scenario: {scenario_name}\n")

    try:

        final_prompt = prompt.format(
            BATCH_SIZE=batch_size,
            START_INDEX=1,
            END_INDEX=batch_size,
            SCENARIO_NAME=scenario_name,
            SCENARIO_DESCRIPTION=scenario_description,
            FOOD_ITEMS=str(app_settings.FOOD_ITEMS),
            RESTAURANT_NAMES=str(app_settings.RESTAURANT_NAMES))

        structured_llm = llm.with_structured_output(
            Test_Data_Generation_Response,
            include_raw=True
        )

        response_raw = structured_llm.invoke(final_prompt)

        print("LLM response received successfully")

        data_result = response_raw["parsed"]

    except Exception as e:
        print(f"Error in test data generation agent: {str(e)}")
        raise

    return data_result


# -------------------------------------------------
# Helper function
# -------------------------------------------------

def format_chat_history(chat_history):

    formatted_turns = []

    for turn in chat_history:
        role = turn['role']
        content = turn['content'].strip()
        formatted_turns.append(f"{role}: {content}")

    return "\n".join(formatted_turns)


# -------------------------------------------------
# MAIN (FULLY UPDATED)
# -------------------------------------------------

def main():

    print("\nStarting FULL scenario-based dataset generation...\n")

    llm = get_configured_llm(
        provider="openai",
        model="gpt-4.1-mini",
        temperature=0.7   # lower = more consistent
    )

    app_settings = AppSettings()
    prompts = get_all_prompts(app_settings)

    rows = []

    # -------------------------------------------------
    # LOOP OVER ALL RULES & SCENARIOS
    # -------------------------------------------------

    for rule_name, sub_scenarios in context_builder_test_scenarios.items():
        # if rule_name == "rule_B_context_completion":
        print(f"\n==============================")
        print(f"Processing Rule: {rule_name}")
        print(f"==============================\n")

        for scenario_name, descriptions in sub_scenarios.items():

            print(f"→ Scenario: {scenario_name}")

            scenario_description = " | ".join(descriptions)

            # Generate EXACTLY 10 samples
            generated_test_data = test_data_generation(
                llm,
                prompts.test_data_generation_prompt,
                app_settings,
                scenario_name,
                scenario_description,
                batch_size=5
            )

            data = generated_test_data.model_dump()

            for i, data_point in enumerate(data["generated_test_data"]):

                chat_history = format_chat_history(data_point["chat_history"])

                row = {
                    "rule": rule_name,
                    "scenario": scenario_name,
                    "chat_history": chat_history,
                    "latest_user_question": data_point["latest_user_question"]
                }

                rows.append(row)

            # Optional: prevent rate limits
            time.sleep(1)

    # -------------------------------------------------
    # CREATE DATAFRAME
    # -------------------------------------------------

    df = pd.DataFrame(rows)

    print("\nFinal Dataset Shape:", df.shape)

    # -------------------------------------------------
    # SAVE DATASET
    # -------------------------------------------------

    output_path = "context_builder_full_eval_dataset_8.csv"

    df.to_csv(output_path, index=False)

    print(f"\nDataset saved successfully at: {output_path}\n")


# -------------------------------------------------
# Run
# -------------------------------------------------

if __name__ == "__main__":
    main()