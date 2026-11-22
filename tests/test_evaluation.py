"""Run with: python -m unittest discover -s tests -p 'test_evaluation.py' -v"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from evaluation.run import load_cases
from evaluation.scoring import compare_results, expected_sql_values, parse_result_values, score_case, summarize


class EvaluationScoringTests(unittest.TestCase):
    def test_aliases_and_values_match(self):
        self.assertTrue(compare_results([[12.0]], [[12]]))
        self.assertTrue(compare_results([["B"], ["a"]], [["A"], ["b"]], unordered=True))
        self.assertFalse(compare_results([[1]], [[2]]))

    def test_parse_result_rejects_executable_strings(self):
        self.assertEqual(parse_result_values(["{'value': 3}"]), [[3]])
        self.assertEqual(parse_result_values(["__import__('os').system('echo unsafe')"]), [])

    def test_route_and_numeric_scoring(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (amount INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?)", [(5,), (8,)])
        case = {"id": "test", "category": "sql", "expected_routes": ["BankSQL"],
                "expected_sql": "SELECT SUM(amount) FROM t", "expected_fallback": False}
        state = {"banksql_queries": ["total"], "database_query_results": ["{'total': 13}"],
                 "text2sql_generator_response": {"sql_query": "SELECT SUM(amount) AS total FROM t"}}
        result = score_case(case, state, conn=conn)
        self.assertTrue(result["passed"])
        self.assertTrue(all(result["checks"].values()))
        conn.close()

    def test_route_mismatch_fails(self):
        case = {"id": "test", "category": "route", "expected_routes": ["HelpCenter"]}
        result = score_case(case, {"banksql_queries": ["hello"]})
        self.assertFalse(result["passed"])

    def test_reference_rejects_mutations(self):
        conn = sqlite3.connect(":memory:")
        for sql in ("DELETE FROM t", "SELECT 1; DROP TABLE t", "UPDATE t SET a=1"):
            with self.assertRaises(ValueError):
                expected_sql_values(conn, sql)
        conn.close()

    def test_summary(self):
        summary = summarize([{"passed": True, "checks": {"routing": True}},
                             {"passed": False, "checks": {"routing": False}}])
        self.assertEqual(summary["pass_rate"], .5)
        self.assertEqual(summary["criteria"]["routing"]["total"], 2)

    def test_dataset_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "cases.jsonl"
            file.write_text('{"id":"one","category":"sql","question":"hello","expected_routes":["BankSQL"]}\n')
            self.assertEqual(len(load_cases(file)), 1)
            file.write_text('{"id":"one","category":"sql","question":"hello","expected_routes":["BankSQL"]}\n' * 2)
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                load_cases(file)


if __name__ == "__main__":
    unittest.main()
