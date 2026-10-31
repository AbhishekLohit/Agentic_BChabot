import sqlite3
from typing import List, Dict, Any
import pandas as pd
from app_setting import AppSettings
from logging import Logger

DB_PATH = "orders.db"  # Path to your SQLite database

class db_info:
    def __init__(self, 
                 db_path:str
    ):
        self.db_path = AppSettings().db_path
    
    def get_connection(self) -> sqlite3.Connection:
        """
        Creates and returns a connection to the SQLite database.
        """
        return sqlite3.connect(self.db_path)


    def get_all_tables(self,conn: sqlite3.Connection) -> List[str]:
        """
        Fetches the list of all table names in the database.
        """
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()
        return tables


    def get_table_schema(self,
                         conn: sqlite3.Connection, 
                         table_name: str
        ) -> List[Dict[str, str]]:
        """
        Fetches schema information (column name, type, nullable, primary key, etc.)
        for the given table.
        """
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name});")
        schema_info = cursor.fetchall()
        cursor.close()

        # Each row of PRAGMA table_info has:
        # (cid, name, type, notnull, dflt_value, pk)
        schema = [
            {
                "column_id": row[0],
                "column_name": row[1],
                "data_type": row[2],
                "not_null": bool(row[3]),
                "default_value": row[4],
                "is_primary_key": bool(row[5]),
            }
            for row in schema_info
        ]
        return schema


    def print_db_schema(self):
        """
        Utility function — prints schema for all tables.
        """
        conn = self.get_connection()
        tables = self.get_all_tables(conn)
        print(f"Found tables: {tables}\n")

        for table in tables:
            print(f"Schema for table: {table}")
            schema = self.get_table_schema(conn, table)
            for col in schema:
                print(f"   - {col['column_name']} ({col['data_type']})"
                    f"{' [PK]' if col['is_primary_key'] else ''}")
            print()
        conn.close()

    def execute_query(query: str, 
                    conn: sqlite3.Connection,
                    customer_id: str,
                    logger: Logger
    ) -> List[Dict[str, Any]]:
        """
        Executes a SQL query based on the query_type argument.
        Supports SELECT, INSERT, UPDATE, DELETE.

        Args:
            query_type: SQL query string to execute (e.g., 'SELECT * FROM table;')
            conn: Active SQLite connection

        Returns:
            List of rows (as dicts) for SELECT queries, or
            an empty list for non-SELECT operations.
        """
        # logger.info(f"Inside query exe function: {query}")
        cursor = conn.cursor()
        try:
            cursor.execute(query, (customer_id,))
            if query.strip().lower().startswith("select"):
                # Return results as list of dicts
                columns = [col[0] for col in cursor.description]
                results = [dict(zip(columns, row)) for row in cursor.fetchall()]
                return results
            else:
                conn.commit()
                return []
        except sqlite3.Error as e:
            print(f"SQL Error: {e}")
            return []
        finally:
            cursor.close()


    def export_order_items_to_excel(self,
                                    output_file: str = "order_items_dump.xlsx"
        ):
        """
        Fetches all data from the order_items table and writes it to an Excel file.

        Args:
            conn: Active SQLite database connection.
            output_file: Path to save the Excel file (.xlsx)
        """

        query = "SELECT * FROM order_items;"
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(query)
            rows = cursor.fetchall()

            # Get column names
            columns = [desc[0] for desc in cursor.description]

            # Create DataFrame
            df = pd.DataFrame(rows, columns=columns)

            # Export to Excel
            df.to_excel(output_file, index=False)

            print(f"Exported order_items table to {output_file}")

        except sqlite3.Error as e:
            print(f"SQL Error while exporting: {e}")

        finally:
            cursor.close()

    # def execute_query(customer_id: str,
    #                 conn: any,
    #                 sql_query: str
    # ) -> List[str]:
    #     cursor = conn.cursor()
    #     cursor.execute(sql_query, (customer_id,))
    #     result = cursor.fetchall()
        
    #     if result:
    #         return [row[0] for row in result]
    #     else:
    #         return []


    def get_customer_details(self,
                    customer_id: str,
                    conn: sqlite3.Connection             
    ) -> Dict[str, Any]:
        """
        Executes a SQL query based on the query_type argument.
        Supports SELECT, INSERT, UPDATE, DELETE.

        Args:
            query_type: SQL query string to execute (e.g., 'SELECT * FROM table;')
            conn: Active SQLite connection

        Returns:
            List of rows (as dicts) for SELECT queries, or
            an empty list for non-SELECT operations.
        """
        try:
            
            cust_order_details = self.execute_query(customer_id,
                                                    conn,
                                                    AppSettings.customer_order_info_query)

            # customer_details = {
            #     "ordered_items": cust_order_details,
            #     "restaurant_names": cust_rest_details,
            #     "restaurant_cities": cust_rest_city_details
            # }
            print(f"Customer details retrieved for customer_id {customer_id}: {cust_order_details}")
        except sqlite3.Error as e:
            print(f"SQL Error: {e}")
            return {}
        finally:
            conn.close()
        return cust_order_details


if __name__ == "__main__":
    dbinfo = db_info(DB_PATH)
    dbinfo.print_db_schema()