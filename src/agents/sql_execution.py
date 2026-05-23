from logging import Logger
from app_models import Graph_State,Text2SqlGeneratorResponse
import sqlite3

async def execute_query(
    state: Graph_State,
    db_conn: any,
    logger: Logger
) -> list[str]:
    """
    Execute the given SQL query and return results as a string.
    """
    logger.info("Executing SQL execution agent.")
    sql_query = state.text2sql_generator_response.sql_query
    cursor = db_conn.cursor()
    try:
        cursor.execute(sql_query)
        operator = sql_query.strip().split()[0].lower()
        if not operator.startswith("update") and not operator.startswith("delete") and not operator.startswith("insert"):
            # Return results as list of dicts
            columns = [col[0] for col in cursor.description]
            results = [str(dict(zip(columns, row))) for row in cursor.fetchall()]
            state.database_query_results = results
            logger.info(f"SQL executed successfully. Results: {results}\n")
            
        else:
            db_conn.commit()
            logger.info(f"SQL execution not permitted\n")
    except sqlite3.Error as e:
        logger.info(f"SQL Error: {e}")
        state.sql_query_correction = True
    finally:
        cursor.close()
    return state

    

