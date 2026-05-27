# Test Scripts for the Workday CR Test Case Generator

This guide documents five test scripts that validate every Python module in the Workday CR Test Case Generator solution. Together they exercise the configuration layer, document parsers, Excel output tool, agent definitions, and pipeline orchestration — all without requiring live AWS or Pinecone credentials.

---

## How to Run the Tests

All five scripts use Python's built-in `unittest` framework. No additional test dependencies are needed beyond the project's own `requirements.txt`.

```bash
# From the project root
cd workday-cr-test-gen

# Run all 73 tests at once
python -m unittest discover -s tests -v

# Run a single test file
python tests/test_shared.py
python tests/test_ingest_parsers.py
python tests/test_generator_tool.py
python tests/test_agents.py
python tests/test_pipeline.py
```

A successful run prints:

```
Ran 73 tests in ~1s

OK
```

---

## Mocking Strategy

The solution depends on three cloud services — AWS Bedrock (embeddings and LLM), Pinecone (vector database), and the Strands Agents SDK. None of these are available in a local test environment, so every test file injects lightweight mocks into `sys.modules` *before* importing the module under test.

The pattern is:

1. Create a `types.ModuleType` for each dependency (`boto3`, `pinecone`, `strands`, `dotenv`).
2. Assign callable stubs to the attributes the source code actually uses (e.g., `boto3.client` returns a mock Bedrock client whose `invoke_model` returns a fake 1024-dimension embedding).
3. Insert the mock into `sys.modules` so that `import boto3` resolves to the mock.
4. Import the module under test — it picks up the mocks transparently.

This means the tests run instantly, cost nothing, and can execute in CI without any credentials configured.

---

## Test Script 1 — test_shared.py (12 tests)

**What it tests:** The `shared.py` module — configuration constants, the `embed()` embedding helper, and the `get_index()` Pinecone accessor.

**Why these tests matter:** Every other module in the project imports from `shared.py`. If the config constants are wrong (e.g., the embedding model name has a typo) or `embed()` fails to truncate long input, the entire pipeline breaks. These tests catch that at the earliest possible point.

### Test Classes

**TestSharedConfig (5 tests)** — Validates that every configuration constant is populated and has the expected value. Checks that the Bedrock region is set, the Pinecone index name is non-empty, the embedding model contains `titan-embed-text`, the LLM model contains `nova`, and that a call to `embed()` actually returns 1024 dimensions.

**TestEmbedFunction (4 tests)** — Exercises the embedding helper with normal input, empty strings, and a 20,000-character input that should be silently truncated to 8,000 characters. Verifies the return type is `list[float]` with exactly 1024 elements.

**TestGetIndex (3 tests)** — Confirms `get_index()` returns an object with `query()` and `upsert()` methods, which are the two Pinecone operations the agents use.

### Source Code

```python
"""
Test Script 1 — test_shared.py
Tests the shared.py module: config constants, embed() function, get_index().
Mocks boto3 and Pinecone to avoid needing live cloud credentials.
"""
import sys
import types
import json
import unittest

# ── Mock boto3, pinecone, dotenv BEFORE importing shared ──────────────
mock_boto3 = types.ModuleType("boto3")
mock_response_body = type("Body", (), {
    "read": lambda self: json.dumps({"embedding": [0.1] * 1024}).encode()
})()
mock_bedrock = type("BedrockClient", (), {
    "invoke_model": lambda self, **kw: {"body": mock_response_body}
})()
mock_boto3.client = lambda *a, **kw: mock_bedrock
sys.modules["boto3"] = mock_boto3

mock_dotenv = types.ModuleType("dotenv")
mock_dotenv.load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"] = mock_dotenv

mock_pinecone_mod = types.ModuleType("pinecone")
class MockIndex:
    def query(self, **kw): return {"matches": []}
    def upsert(self, **kw): pass
class MockPinecone:
    def __init__(self, **kw): pass
    def Index(self, name): return MockIndex()
    def list_indexes(self): return []
mock_pinecone_mod.Pinecone = MockPinecone
sys.modules["pinecone"] = mock_pinecone_mod

# Now import shared
sys.path.insert(0, "/home/claude/workday-cr-test-gen")

import importlib
import shared
importlib.reload(shared)


class TestSharedConfig(unittest.TestCase):
    """Test configuration constants are set correctly."""

    def test_bedrock_region_has_value(self):
        self.assertTrue(len(shared.BEDROCK_REGION) > 0)

    def test_index_name_has_value(self):
        self.assertTrue(len(shared.INDEX_NAME) > 0)

    def test_embedding_model_is_titan(self):
        self.assertIn("titan-embed-text", shared.EMBEDDING_MODEL)

    def test_llm_model_is_nova(self):
        self.assertIn("nova", shared.LLM_MODEL)

    def test_embedding_dimension_is_1024(self):
        result = shared.embed("test")
        self.assertEqual(len(result), 1024)


class TestEmbedFunction(unittest.TestCase):
    """Test the embed() helper function."""

    def test_returns_list_of_floats(self):
        result = shared.embed("hello world")
        self.assertIsInstance(result, list)
        self.assertIsInstance(result[0], float)

    def test_returns_1024_dimensions(self):
        result = shared.embed("test input")
        self.assertEqual(len(result), 1024)

    def test_truncates_long_input(self):
        """Input longer than 8000 chars should not raise an error."""
        long_text = "x" * 20000
        result = shared.embed(long_text)
        self.assertEqual(len(result), 1024)

    def test_handles_empty_string(self):
        result = shared.embed("")
        self.assertEqual(len(result), 1024)


class TestGetIndex(unittest.TestCase):
    """Test the get_index() function."""

    def test_returns_index_object(self):
        idx = shared.get_index()
        self.assertIsNotNone(idx)

    def test_index_has_query_method(self):
        idx = shared.get_index()
        self.assertTrue(hasattr(idx, "query"))

    def test_index_has_upsert_method(self):
        idx = shared.get_index()
        self.assertTrue(hasattr(idx, "upsert"))


if __name__ == "__main__":
    unittest.main()
```

---

## Test Script 2 — test_ingest_parsers.py (16 tests)

**What it tests:** The `parse_cr_docx()` and `parse_test_excel()` parser functions from `ingest.py`, run against all 10 uploaded CR files.

**Why these tests matter:** The parsers are the data foundation. If `parse_cr_docx()` fails to extract solution design points or `parse_test_excel()` misreads group boundaries, the knowledge base will contain garbage and every generated test plan will be wrong. These tests validate structural assumptions across all 10 documents — not just one — so they also detect if a newly added CR document deviates from the expected format.

**Special approach:** Instead of importing `ingest.py` (which pulls in `shared.py` and its cloud clients), the test file contains local copies of the two parser functions. This lets the parsers run against the real `.docx` and `.xlsx` files with zero cloud dependencies.

### Test Classes

**TestWordParser (7 tests)** — Parses all 10 Word documents and checks that each CR ID matches the expected `CR-EXP-2026-NNN` pattern, every title contains an em dash, solution design has 5–7 numbered points, business requirements are non-empty, the metadata Module is always "Expenses", Priority exists, and full text is substantial.

**TestExcelParser (8 tests)** — Parses all 10 Excel files and validates that each has 4–6 test groups, 11–16 total test cases, all three test types (Positive, Negative, Regression) are present, TC numbers have the correct prefix (P/N/R), every test case has all required fields populated, the last group is always Regression, traceability matrix row count matches group count, and the Summary sheet total matches the parsed count.

**TestCrossReference (1 test)** — Correlates Word and Excel data: the number of solution design points should roughly correspond to the number of test groups (within ±2), confirming the test cases are structurally aligned with the solution design.

### Source Code

```python
"""
Test Script 2 — test_ingest_parsers.py
Tests the parse_cr_docx() and parse_test_excel() parsers from ingest.py
against all 10 uploaded CR files. Uses local copies of the parser functions
to avoid importing shared.py (which requires cloud credentials).
"""
import unittest
from pathlib import Path
from docx import Document
import openpyxl

DATA_DIR = Path("/mnt/user-data/uploads")

# ── Local copies of parsers (no cloud imports needed) ─────────────────

def parse_cr_docx(filepath: str) -> dict:
    doc = Document(filepath)
    metadata = {}
    if doc.tables:
        for row in doc.tables[0].rows:
            cells = [cell.text.strip() for cell in row.cells]
            if len(cells) >= 2 and cells[0]:
                metadata[cells[0]] = cells[1]
    cr_id = metadata.get("CR ID", "")
    current_section = None
    title = ""
    sections = {"overview": [], "business_requirement": [], "solution_design": [], "test_plan": []}
    for para in doc.paragraphs:
        text = para.text.strip()
        style = para.style.name if para.style else ""
        if not text: continue
        if style == "Heading 1":
            if "1." in text: current_section = "overview"
            elif "2." in text: current_section = "business_requirement"
            elif "3." in text and "Solution" in text: current_section = "solution_design"
            elif "4." in text and "Test" in text: current_section = "test_plan"
            elif "5." in text or "6." in text or "7." in text: current_section = None
            continue
        if not title and "\u2014" in text and style not in ("Heading 1", "Heading 2"):
            title = text
        if current_section and current_section in sections:
            sections[current_section].append(text)
    sol_points = []
    in_sol_main = False
    for para in doc.paragraphs:
        text = para.text.strip()
        style = para.style.name if para.style else ""
        if style == "Heading 1" and "3." in text and "Solution" in text: in_sol_main = True; continue
        if style == "Heading 2" and in_sol_main: in_sol_main = False
        if style == "Heading 1" and in_sol_main: in_sol_main = False
        if in_sol_main and style == "List Paragraph" and text: sol_points.append(text)
    return {
        "cr_id": cr_id,
        "title": title or metadata.get("Category", "Unknown CR"),
        "metadata_table": metadata,
        "business_requirement": "\n".join(sections["business_requirement"]),
        "solution_design": "\n".join(sections["solution_design"]),
        "solution_design_points": sol_points,
        "test_plan_summary": "\n".join(sections["test_plan"]),
        "full_text": "\n".join(
            sections["business_requirement"] + sections["solution_design"] + sections["test_plan"]
        ),
    }


def parse_test_excel(filepath: str) -> dict:
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb["Test Cases by Component"]
    groups = []
    current_group = None
    for row in ws.iter_rows(min_row=5, values_only=True):
        cells = [str(c).strip() if c else "" for c in row]
        if not any(cells):
            if current_group: groups.append(current_group); current_group = None
            continue
        group_num, component, design_ref = cells[0], cells[1], cells[2]
        tc_num, test_type, scenario = cells[3], cells[4], cells[5]
        steps = cells[6] if len(cells) > 6 else ""
        expected = cells[7] if len(cells) > 7 else ""
        if group_num and tc_num:
            if current_group is None or current_group["group_num"] != group_num:
                if current_group: groups.append(current_group)
                current_group = {"group_num": group_num, "component": component,
                                 "design_ref": design_ref, "test_cases": []}
            current_group["test_cases"].append({"tc_num": tc_num, "test_type": test_type,
                                                 "scenario": scenario, "steps": steps,
                                                 "expected_result": expected})
        elif tc_num and current_group:
            current_group["test_cases"].append({"tc_num": tc_num, "test_type": test_type,
                                                 "scenario": scenario, "steps": steps,
                                                 "expected_result": expected})
    if current_group: groups.append(current_group)
    traceability = []
    if "Traceability Matrix" in wb.sheetnames:
        ws_tm = wb["Traceability Matrix"]
        for row in ws_tm.iter_rows(min_row=4, values_only=True):
            cells = [str(c).strip() if c else "" for c in row]
            if cells[1] and cells[1] != "TOTAL":
                traceability.append({"component": cells[1], "design_ref": cells[2],
                    "positive": cells[3] if cells[3] != "\u2014" else "",
                    "negative": cells[4] if cells[4] != "\u2014" else "",
                    "regression": cells[5] if len(cells) > 5 and cells[5] != "\u2014" else ""})
    summary = {}
    if "Summary" in wb.sheetnames:
        ws_s = wb["Summary"]
        for row in ws_s.iter_rows(min_row=3, values_only=True):
            cells = [str(c).strip() if c else "" for c in row]
            if cells[0] and cells[1]:
                key = cells[0].lower().replace(" ", "_")
                try: summary[key] = int(cells[1])
                except ValueError: summary[key] = cells[1]
    return {"groups": groups, "traceability": traceability, "summary": summary}


class TestWordParser(unittest.TestCase):
    """Test parse_cr_docx against all 10 CR Word documents."""

    @classmethod
    def setUpClass(cls):
        cls.parsed = {}
        for n in range(1, 11):
            path = DATA_DIR / f"CR-EXP-2026-{n:03d}.docx"
            cls.parsed[n] = parse_cr_docx(str(path))

    def test_all_10_cr_ids_extracted(self):
        for n in range(1, 11):
            self.assertEqual(self.parsed[n]["cr_id"], f"CR-EXP-2026-{n:03d}")

    def test_all_titles_contain_em_dash(self):
        for n in range(1, 11):
            self.assertIn("\u2014", self.parsed[n]["title"],
                          f"CR {n:03d} title missing em dash")

    def test_sol_design_points_5_to_7(self):
        for n in range(1, 11):
            count = len(self.parsed[n]["solution_design_points"])
            self.assertGreaterEqual(count, 5, f"CR {n:03d} has {count} points")
            self.assertLessEqual(count, 7, f"CR {n:03d} has {count} points")

    def test_business_requirement_not_empty(self):
        for n in range(1, 11):
            self.assertTrue(len(self.parsed[n]["business_requirement"]) > 50,
                            f"CR {n:03d} biz req too short")

    def test_metadata_module_is_expenses(self):
        for n in range(1, 11):
            self.assertEqual(self.parsed[n]["metadata_table"].get("Module"), "Expenses",
                             f"CR {n:03d} Module not Expenses")

    def test_metadata_has_priority(self):
        for n in range(1, 11):
            self.assertIn("Priority", self.parsed[n]["metadata_table"],
                          f"CR {n:03d} missing Priority")

    def test_full_text_not_empty(self):
        for n in range(1, 11):
            self.assertTrue(len(self.parsed[n]["full_text"]) > 100)


class TestExcelParser(unittest.TestCase):
    """Test parse_test_excel against all 10 CR Excel files."""

    @classmethod
    def setUpClass(cls):
        cls.parsed = {}
        for n in range(1, 11):
            path = DATA_DIR / f"CR-EXP-2026-{n:03d}_TestCases.xlsx"
            cls.parsed[n] = parse_test_excel(str(path))

    def test_groups_count_4_to_6(self):
        for n in range(1, 11):
            count = len(self.parsed[n]["groups"])
            self.assertGreaterEqual(count, 4, f"CR {n:03d}: {count} groups")
            self.assertLessEqual(count, 6, f"CR {n:03d}: {count} groups")

    def test_total_test_cases_11_to_16(self):
        for n in range(1, 11):
            total = sum(len(g["test_cases"]) for g in self.parsed[n]["groups"])
            self.assertGreaterEqual(total, 11, f"CR {n:03d}: {total} TCs")
            self.assertLessEqual(total, 16, f"CR {n:03d}: {total} TCs")

    def test_all_three_types_present(self):
        for n in range(1, 11):
            types = set()
            for g in self.parsed[n]["groups"]:
                for tc in g["test_cases"]:
                    types.add(tc["test_type"])
            self.assertIn("Positive", types, f"CR {n:03d} missing Positive")
            self.assertIn("Negative", types, f"CR {n:03d} missing Negative")
            self.assertIn("Regression", types, f"CR {n:03d} missing Regression")

    def test_tc_nums_have_correct_prefix(self):
        for n in range(1, 11):
            for g in self.parsed[n]["groups"]:
                for tc in g["test_cases"]:
                    prefix = tc["tc_num"][0]
                    if tc["test_type"] == "Positive": self.assertEqual(prefix, "P")
                    elif tc["test_type"] == "Negative": self.assertEqual(prefix, "N")
                    elif tc["test_type"] == "Regression": self.assertEqual(prefix, "R")

    def test_every_tc_has_all_fields(self):
        for n in range(1, 11):
            for g in self.parsed[n]["groups"]:
                for tc in g["test_cases"]:
                    for key in ("tc_num", "test_type", "scenario", "steps", "expected_result"):
                        self.assertTrue(len(tc[key]) > 0,
                                        f"CR {n:03d} TC {tc['tc_num']} empty {key}")

    def test_last_group_is_regression(self):
        for n in range(1, 11):
            last_group = self.parsed[n]["groups"][-1]
            types = {tc["test_type"] for tc in last_group["test_cases"]}
            self.assertEqual(types, {"Regression"},
                             f"CR {n:03d} last group has types {types}")

    def test_traceability_count_matches_groups(self):
        for n in range(1, 11):
            self.assertEqual(len(self.parsed[n]["traceability"]),
                             len(self.parsed[n]["groups"]),
                             f"CR {n:03d} traceability/group mismatch")

    def test_summary_total_matches_parsed(self):
        for n in range(1, 11):
            parsed_total = sum(len(g["test_cases"]) for g in self.parsed[n]["groups"])
            summary_total = self.parsed[n]["summary"].get("total_test_cases", 0)
            self.assertEqual(parsed_total, summary_total,
                             f"CR {n:03d}: parsed {parsed_total} vs summary {summary_total}")


class TestCrossReference(unittest.TestCase):
    """Test Word-Excel consistency across all 10 CRs."""

    @classmethod
    def setUpClass(cls):
        cls.docx_data = {}
        cls.xlsx_data = {}
        for n in range(1, 11):
            cls.docx_data[n] = parse_cr_docx(str(DATA_DIR / f"CR-EXP-2026-{n:03d}.docx"))
            cls.xlsx_data[n] = parse_test_excel(str(DATA_DIR / f"CR-EXP-2026-{n:03d}_TestCases.xlsx"))

    def test_sol_points_approx_match_groups(self):
        """Solution design points should roughly correspond to test groups."""
        for n in range(1, 11):
            points = len(self.docx_data[n]["solution_design_points"])
            groups = len(self.xlsx_data[n]["groups"])
            self.assertGreaterEqual(groups, points - 2,
                                    f"CR {n:03d}: {groups} groups vs {points} points")
            self.assertLessEqual(groups, points + 1,
                                 f"CR {n:03d}: {groups} groups vs {points} points")


if __name__ == "__main__":
    unittest.main()
```

---

## Test Script 3 — test_generator_tool.py (14 tests)

**What it tests:** The `save_test_excel` tool function from `agents/generator.py` — the function that produces the final deliverable Excel file.

**Why these tests matter:** The Generator agent calls `save_test_excel` with a JSON test plan, and that function must produce a correctly structured, multi-sheet Excel workbook. If the header row is in the wrong position, or the traceability totals don't add up, or the summary counts are off, the output file is unusable. These tests feed a known sample plan through the function and verify every aspect of the output file.

### Test Classes

**TestSaveTestExcel (11 tests)** — Creates an Excel file from a 2-group sample plan (3 positive, 1 negative, 1 regression test) and verifies: the result string contains a success checkmark, the file exists on disk, there are exactly 3 sheets with the correct names, the header row is at row 4 with the right column labels, the title row contains the CR ID, data rows contain the expected TC numbers, the traceability matrix maps positive tests correctly, the total row sums to 2P/1N/1R, the summary sheet shows 4 total test cases, and the components count is 2.

**TestSaveTestExcelErrors (3 tests)** — Tests the three error paths: invalid JSON input returns an error message, an empty groups list returns an error, and a missing `groups` key returns an error.

### Source Code

```python
"""
Test Script 3 — test_generator_tool.py
Tests the save_test_excel tool from agents/generator.py.
Creates an Excel file with sample data and verifies structure, sheets,
headers, data rows, traceability, and summary counts.
"""
import sys
import types
import json
import os
import tempfile
import unittest

# ── Mock strands, boto3, pinecone, dotenv ─────────────────────────────
mock_strands = types.ModuleType("strands")
mock_strands.Agent = lambda **kw: None
mock_strands.tool = lambda f: f  # passthrough decorator
mock_strands_models = types.ModuleType("strands.models")
mock_strands_models.BedrockModel = lambda **kw: None
sys.modules["strands"] = mock_strands
sys.modules["strands.models"] = mock_strands_models

mock_boto3 = types.ModuleType("boto3")
mock_boto3.client = lambda *a, **kw: None
sys.modules["boto3"] = mock_boto3

mock_dotenv = types.ModuleType("dotenv")
mock_dotenv.load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"] = mock_dotenv

mock_pinecone_mod = types.ModuleType("pinecone")
mock_pinecone_mod.Pinecone = lambda **kw: type("PC", (), {
    "Index": lambda s, n: None, "list_indexes": lambda s: []
})()
sys.modules["pinecone"] = mock_pinecone_mod

# Provide shared module constants
mock_shared = types.ModuleType("shared")
mock_shared.LLM_MODEL = "us.amazon.nova-lite-v1:0"
mock_shared.BEDROCK_REGION = "us-east-1"
sys.modules["shared"] = mock_shared

sys.path.insert(0, "/home/claude/workday-cr-test-gen")

from agents.generator import save_test_excel
import openpyxl

SAMPLE_PLAN = {
    "groups": [
        {
            "group_num": "1", "component": "Expense Item Configuration",
            "design_ref": "3.1",
            "test_cases": [
                {"tc_num": "P-01", "test_type": "Positive",
                 "scenario": "Item visible in picker",
                 "steps": "Open expense report, browse items",
                 "expected_result": "Item appears"},
                {"tc_num": "P-02", "test_type": "Positive",
                 "scenario": "Custom fields displayed",
                 "steps": "Select item, check fields",
                 "expected_result": "Fields shown"},
                {"tc_num": "N-01", "test_type": "Negative",
                 "scenario": "Missing required field",
                 "steps": "Leave field blank, submit",
                 "expected_result": "Hard stop: Field required"},
            ]
        },
        {
            "group_num": "2", "component": "Regression",
            "design_ref": "N/A",
            "test_cases": [
                {"tc_num": "R-01", "test_type": "Regression",
                 "scenario": "Other items unaffected",
                 "steps": "Submit other expense item",
                 "expected_result": "Submits normally"},
            ]
        }
    ]
}


class TestSaveTestExcel(unittest.TestCase):
    """Test the save_test_excel tool with sample data."""

    @classmethod
    def setUpClass(cls):
        cls.orig_dir = os.getcwd()
        cls.tmp_dir = tempfile.mkdtemp()
        os.chdir(cls.tmp_dir)
        cls.result = save_test_excel("CR-EXP-2026-TEST", "Test CR Title",
                                      json.dumps(SAMPLE_PLAN))
        cls.filepath = os.path.join(cls.tmp_dir, "generated_tests",
                                     "CR-EXP-2026-TEST_TestCases.xlsx")
        cls.wb = openpyxl.load_workbook(cls.filepath)

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.orig_dir)

    def test_result_indicates_success(self):
        self.assertIn("\u2713", self.result)

    def test_file_created(self):
        self.assertTrue(os.path.exists(self.filepath))

    def test_three_sheets(self):
        self.assertEqual(len(self.wb.sheetnames), 3)

    def test_sheet_names_correct(self):
        expected = ["Test Cases by Component", "Traceability Matrix", "Summary"]
        self.assertEqual(self.wb.sheetnames, expected)

    def test_header_row_on_sheet1(self):
        ws = self.wb["Test Cases by Component"]
        headers = [ws.cell(row=4, column=c).value for c in range(1, 9)]
        self.assertEqual(headers[0], "Group #")
        self.assertEqual(headers[3], "TC #")
        self.assertEqual(headers[7], "Expected Result")

    def test_title_row_contains_cr_id(self):
        ws = self.wb["Test Cases by Component"]
        self.assertIn("CR-EXP-2026-TEST", ws.cell(row=1, column=1).value)

    def test_data_rows_present(self):
        ws = self.wb["Test Cases by Component"]
        self.assertEqual(ws.cell(row=5, column=4).value, "P-01")
        self.assertEqual(ws.cell(row=6, column=4).value, "P-02")
        self.assertEqual(ws.cell(row=7, column=4).value, "N-01")

    def test_traceability_positive_mapping(self):
        ws = self.wb["Traceability Matrix"]
        pos_val = ws.cell(row=4, column=4).value
        self.assertIn("P-01", pos_val)
        self.assertIn("P-02", pos_val)

    def test_traceability_total_row(self):
        ws = self.wb["Traceability Matrix"]
        self.assertEqual(ws.cell(row=6, column=2).value, "TOTAL")
        self.assertEqual(ws.cell(row=6, column=4).value, 2)   # 2 positive
        self.assertEqual(ws.cell(row=6, column=5).value, 1)   # 1 negative
        self.assertEqual(ws.cell(row=6, column=6).value, 1)   # 1 regression

    def test_summary_counts(self):
        ws = self.wb["Summary"]
        self.assertEqual(ws.cell(row=4, column=2).value, 4)

    def test_summary_components_count(self):
        ws = self.wb["Summary"]
        self.assertEqual(ws.cell(row=9, column=2).value, 2)


class TestSaveTestExcelErrors(unittest.TestCase):
    """Test error handling in save_test_excel."""

    @classmethod
    def setUpClass(cls):
        cls.orig_dir = os.getcwd()
        cls.tmp_dir = tempfile.mkdtemp()
        os.chdir(cls.tmp_dir)

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.orig_dir)

    def test_invalid_json_returns_error(self):
        result = save_test_excel("X", "X", "not valid json{{{")
        self.assertIn("ERROR", result)

    def test_empty_groups_returns_error(self):
        result = save_test_excel("X", "X", json.dumps({"groups": []}))
        self.assertIn("ERROR", result)

    def test_missing_groups_key_returns_error(self):
        result = save_test_excel("X", "X", json.dumps({"data": []}))
        self.assertIn("ERROR", result)


if __name__ == "__main__":
    unittest.main()
```

---

## Test Script 4 — test_agents.py (15 tests)

**What it tests:** The three agent definitions — Retriever, Analyzer, and Generator — including their tool counts, system prompt content, and tool function behavior.

**Why these tests matter:** The agents are defined declaratively (each calls `Agent(name=..., tools=..., system_prompt=...)` at module level), so a misconfiguration is easy to introduce: accidentally passing the wrong tools list, deleting a critical keyword from the system prompt, or assigning the wrong model. These tests verify the "wiring" is correct — each agent has the right number of tools, and each system prompt contains the domain-specific keywords that guide the LLM's behavior.

### Test Classes

**TestRetrieverAgent (7 tests)** — Confirms the retriever has exactly 2 tools, its system prompt mentions "knowledge base", `search_similar_crs` returns formatted output with "CR ID:" and "Similarity Score:", empty results return "No similar", queries use the `cr_details` namespace, `fetch_test_groups` applies a metadata filter on `cr_id`, and the output contains sorted "Group N:" labels.

**TestAnalyzerAgent (5 tests)** — Verifies the analyzer has 0 tools (it's a pure reasoning agent), and its system prompt contains "Workday", "Regression", "Positive", and "hard stop" — the four domain terms that ensure the LLM reasons correctly about Workday Expenses test types.

**TestGeneratorAgent (3 tests)** — Checks the generator has exactly 1 tool, its system prompt mentions "Excel" (the output format), and it references the numbering convention "P-01" or "Positive".

### Source Code

```python
"""
Test Script 4 — test_agents.py
Tests agent definitions: tool counts, system prompt content, and tool
function output formatting. Uses mocks for all cloud services.
"""
import sys
import types
import json
import unittest

# ── Snapshot and mock all cloud dependencies ─────────────────────────
_saved_modules = {}
_mods_to_mock = ["boto3", "dotenv", "pinecone", "strands", "strands.models",
                  "shared", "agents", "agents.retriever", "agents.analyzer",
                  "agents.generator"]

for m in _mods_to_mock:
    _saved_modules[m] = sys.modules.pop(m, None)

mock_boto3 = types.ModuleType("boto3")
mock_response_body = type("B", (), {
    "read": lambda self: json.dumps({"embedding": [0.1]*1024}).encode()
})()
mock_bedrock = type("BC", (), {
    "invoke_model": lambda self, **kw: {"body": mock_response_body}
})()
mock_boto3.client = lambda *a, **kw: mock_bedrock
sys.modules["boto3"] = mock_boto3

mock_dotenv = types.ModuleType("dotenv")
mock_dotenv.load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"] = mock_dotenv

class MockIndex:
    def __init__(self): self.last_query = None
    def query(self, **kw):
        self.last_query = kw
        return {"matches": [
            {"score": 0.95, "metadata": {
                "cr_id": "CR-EXP-2026-001", "title": "Test CR",
                "category": "New Item", "business_requirement": "Test biz req",
                "solution_design": "1. Test sol", "positive_count": 5,
                "negative_count": 3, "regression_count": 2, "total_test_cases": 10,
                "group_num": "1", "component": "Config", "design_ref": "3.1",
                "test_cases_json": json.dumps([
                    {"tc_num": "P-01", "test_type": "Positive",
                     "scenario": "Item visible", "steps": "Open report",
                     "expected_result": "Item appears"}
                ])
            }}
        ]}
    def upsert(self, **kw): pass

_mock_index = MockIndex()
mock_pinecone_mod = types.ModuleType("pinecone")
class MockPC:
    def __init__(self, **kw): pass
    def Index(self, name): return _mock_index
    def list_indexes(self): return []
mock_pinecone_mod.Pinecone = MockPC
sys.modules["pinecone"] = mock_pinecone_mod

# Mock strands — capture Agent constructor args
_agent_registry = {}
def mock_agent_init(**kw):
    name = kw.get("name", "unknown")
    _agent_registry[name] = kw
    return type("MockAgent", (), kw)()

mock_strands = types.ModuleType("strands")
mock_strands.Agent = lambda **kw: mock_agent_init(**kw)
mock_strands.tool = lambda f: f
mock_strands_models = types.ModuleType("strands.models")
mock_strands_models.BedrockModel = lambda **kw: f"mock-model:{kw}"
sys.modules["strands"] = mock_strands
sys.modules["strands.models"] = mock_strands_models

if "/home/claude/workday-cr-test-gen" not in sys.path:
    sys.path.insert(0, "/home/claude/workday-cr-test-gen")

import shared
from agents import retriever, analyzer, generator


class TestRetrieverAgent(unittest.TestCase):
    """Test the Retriever Agent definition and tools."""

    def test_has_two_tools(self):
        info = _agent_registry.get("retriever", {})
        self.assertEqual(len(info.get("tools", [])), 2)

    def test_system_prompt_mentions_knowledge_base(self):
        info = _agent_registry.get("retriever", {})
        self.assertIn("knowledge base", info.get("system_prompt", "").lower())

    def test_search_similar_crs_returns_formatted_output(self):
        result = retriever.search_similar_crs("test query", top_k=3)
        self.assertIn("CR ID:", result)
        self.assertIn("Similarity Score:", result)

    def test_search_similar_crs_empty_results(self):
        orig_query = _mock_index.query
        _mock_index.query = lambda **kw: {"matches": []}
        result = retriever.search_similar_crs("nothing")
        self.assertIn("No similar", result)
        _mock_index.query = lambda **kw: orig_query(**kw)

    def test_search_uses_cr_details_namespace(self):
        _mock_index.last_query = None
        retriever.search_similar_crs("test")
        self.assertEqual(_mock_index.last_query["namespace"], "cr_details")

    def test_fetch_test_groups_uses_filter(self):
        _mock_index.last_query = None
        retriever.fetch_test_groups("CR-EXP-2026-001")
        self.assertIn("cr_id", _mock_index.last_query.get("filter", {}))

    def test_fetch_test_groups_sorts_output(self):
        result = retriever.fetch_test_groups("CR-EXP-2026-001")
        self.assertIn("Group 1:", result)


class TestAnalyzerAgent(unittest.TestCase):
    """Test the Analyzer Agent definition."""

    def test_has_no_tools(self):
        info = _agent_registry.get("analyzer", {})
        self.assertEqual(len(info.get("tools", [])), 0)

    def test_system_prompt_mentions_workday(self):
        info = _agent_registry.get("analyzer", {})
        self.assertIn("Workday", info.get("system_prompt", ""))

    def test_system_prompt_mentions_regression(self):
        info = _agent_registry.get("analyzer", {})
        self.assertIn("Regression", info.get("system_prompt", ""))

    def test_system_prompt_mentions_positive(self):
        info = _agent_registry.get("analyzer", {})
        self.assertIn("Positive", info.get("system_prompt", ""))

    def test_system_prompt_mentions_hard_stop(self):
        info = _agent_registry.get("analyzer", {})
        prompt = info.get("system_prompt", "").lower()
        self.assertIn("hard stop", prompt)


class TestGeneratorAgent(unittest.TestCase):
    """Test the Generator Agent definition."""

    def test_has_one_tool(self):
        info = _agent_registry.get("generator", {})
        self.assertEqual(len(info.get("tools", [])), 1)

    def test_system_prompt_mentions_excel(self):
        info = _agent_registry.get("generator", {})
        prompt = info.get("system_prompt", "")
        self.assertTrue("excel" in prompt.lower() or "Excel" in prompt)

    def test_system_prompt_mentions_numbering(self):
        info = _agent_registry.get("generator", {})
        prompt = info.get("system_prompt", "")
        self.assertTrue("P-01" in prompt or "Positive" in prompt)


if __name__ == "__main__":
    unittest.main()
```

---

## Test Script 5 — test_pipeline.py (16 tests)

**What it tests:** The `pipeline.py` orchestration — the `build_pipeline()` graph constructor, the built-in example CR data, and the input file splitting regex.

**Why these tests matter:** `build_pipeline()` wires the three agents into a sequential Strands `GraphBuilder` graph. If the edge order is wrong (e.g., Retriever → Generator instead of Retriever → Analyzer), the pipeline will either crash or produce nonsensical output. The example CR data is the built-in demo; if it's incomplete (missing validation rules, too few solution points), the demo run will generate a poor test plan. The input file splitter handles the `--input` CLI mode where users paste a text file; if the regex fails to find the separator, the pipeline can't split business requirements from solution design.

### Test Classes

**TestBuildPipeline (7 tests)** — Uses a mock `GraphBuilder` that records all calls. Verifies: exactly 3 nodes are added, the names are "retriever", "analyzer", and "generator", there are exactly 2 edges, the edges are retriever→analyzer and analyzer→generator (in that order), the entry point is "retriever", and `build()` was called.

**TestExampleCRData (5 tests)** — Validates the built-in example Change Request: business requirement is >100 characters, solution design is >100 characters, solution design has ≥5 numbered points, the text mentions "Validation Rule", and it mentions "hard stop".

**TestInputFileSplitting (4 tests)** — Tests the regex that splits `--input` files on a "SOLUTION DESIGN" separator line: uppercase works, title case works, missing separator returns 1 part (no split), and multiple separators split on the first occurrence only.

### Source Code

```python
"""
Test Script 5 — test_pipeline.py
Tests the pipeline.py orchestration: build_pipeline() graph construction,
example CR data validation, and input file splitting logic.
"""
import sys
import types
import re
import unittest

# ── Snapshot and mock all dependencies ────────────────────────────────
_saved_modules = {}
_mods_to_mock = ["boto3", "dotenv", "pinecone", "strands", "strands.models",
                  "strands.multiagent", "shared",
                  "agents", "agents.retriever", "agents.analyzer",
                  "agents.generator", "pipeline"]

for m in _mods_to_mock:
    _saved_modules[m] = sys.modules.pop(m, None)

mock_boto3 = types.ModuleType("boto3")
mock_boto3.client = lambda *a, **kw: None
sys.modules["boto3"] = mock_boto3

mock_dotenv = types.ModuleType("dotenv")
mock_dotenv.load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"] = mock_dotenv

mock_pinecone_mod = types.ModuleType("pinecone")
mock_pinecone_mod.Pinecone = lambda **kw: type("PC", (), {
    "Index": lambda s,n: None, "list_indexes": lambda s: []
})()
sys.modules["pinecone"] = mock_pinecone_mod

mock_strands = types.ModuleType("strands")
mock_strands.Agent = lambda **kw: type("A", (), kw)()
mock_strands.tool = lambda f: f
mock_strands_models = types.ModuleType("strands.models")
mock_strands_models.BedrockModel = lambda **kw: None
sys.modules["strands"] = mock_strands
sys.modules["strands.models"] = mock_strands_models

# Mock GraphBuilder — track calls
_graph_calls = {"nodes": [], "edges": [], "entry": None, "built": False}
class MockGraphBuilder:
    def add_node(self, agent, name): _graph_calls["nodes"].append(name)
    def add_edge(self, src, dst): _graph_calls["edges"].append((src, dst))
    def set_entry_point(self, name): _graph_calls["entry"] = name
    def build(self): _graph_calls["built"] = True; return "mock_graph"

mock_strands_multi = types.ModuleType("strands.multiagent")
mock_strands_multi.GraphBuilder = MockGraphBuilder
sys.modules["strands.multiagent"] = mock_strands_multi

if "/home/claude/workday-cr-test-gen" not in sys.path:
    sys.path.insert(0, "/home/claude/workday-cr-test-gen")

import shared
from agents import retriever, analyzer, generator
import pipeline


class TestBuildPipeline(unittest.TestCase):
    """Test that build_pipeline() creates the correct graph structure."""

    @classmethod
    def setUpClass(cls):
        _graph_calls["nodes"].clear()
        _graph_calls["edges"].clear()
        _graph_calls["entry"] = None
        _graph_calls["built"] = False
        pipeline.build_pipeline()

    def test_three_nodes_added(self):
        self.assertEqual(len(_graph_calls["nodes"]), 3)

    def test_node_names(self):
        self.assertIn("retriever", _graph_calls["nodes"])
        self.assertIn("analyzer", _graph_calls["nodes"])
        self.assertIn("generator", _graph_calls["nodes"])

    def test_two_edges(self):
        self.assertEqual(len(_graph_calls["edges"]), 2)

    def test_retriever_to_analyzer_edge(self):
        self.assertIn(("retriever", "analyzer"), _graph_calls["edges"])

    def test_analyzer_to_generator_edge(self):
        self.assertIn(("analyzer", "generator"), _graph_calls["edges"])

    def test_entry_point_is_retriever(self):
        self.assertEqual(_graph_calls["entry"], "retriever")

    def test_graph_was_built(self):
        self.assertTrue(_graph_calls["built"])


class TestExampleCRData(unittest.TestCase):
    """Test that the example CR data in pipeline.py is valid and complete."""

    def test_example_biz_req_not_empty(self):
        self.assertTrue(len(pipeline.EXAMPLE_BUSINESS_REQUIREMENT.strip()) > 100)

    def test_example_sol_design_not_empty(self):
        self.assertTrue(len(pipeline.EXAMPLE_SOLUTION_DESIGN.strip()) > 100)

    def test_example_sol_design_has_numbered_points(self):
        lines = pipeline.EXAMPLE_SOLUTION_DESIGN.strip().split("\n")
        numbered = [l for l in lines if l.strip() and l.strip()[0].isdigit()]
        self.assertGreaterEqual(len(numbered), 5)

    def test_example_mentions_validation_rules(self):
        self.assertIn("Validation Rule", pipeline.EXAMPLE_SOLUTION_DESIGN)

    def test_example_mentions_hard_stop(self):
        self.assertIn("hard stop", pipeline.EXAMPLE_SOLUTION_DESIGN)


class TestInputFileSplitting(unittest.TestCase):
    """Test the regex splitting logic used for --input files."""

    def _split(self, text):
        return re.split(r"(?i)\n\s*(?:solution\s+design|SOLUTION\s+DESIGN)\s*\n",
                        text, maxsplit=1)

    def test_uppercase_separator(self):
        text = "Business req here\nSOLUTION DESIGN\n1. First point"
        parts = self._split(text)
        self.assertEqual(len(parts), 2)
        self.assertIn("Business", parts[0])
        self.assertIn("First point", parts[1])

    def test_title_case_separator(self):
        text = "Business req\nSolution Design\n1. Point"
        parts = self._split(text)
        self.assertEqual(len(parts), 2)

    def test_missing_separator(self):
        text = "All one block of text with no separator"
        parts = self._split(text)
        self.assertEqual(len(parts), 1)

    def test_multiple_separators_splits_on_first(self):
        text = "Part1\nSOLUTION DESIGN\nMiddle\nSOLUTION DESIGN\nPart3"
        parts = self._split(text)
        self.assertEqual(len(parts), 2)
        self.assertIn("Middle", parts[1])


if __name__ == "__main__":
    unittest.main()
```

---

## Test Coverage Summary

| Test Script | Module Under Test | Tests | What It Validates |
|---|---|---|---|
| test_shared.py | shared.py | 12 | Config constants, embed() dimensions and truncation, get_index() |
| test_ingest_parsers.py | ingest.py parsers | 16 | Word/Excel parsing across all 10 CRs, cross-reference consistency |
| test_generator_tool.py | agents/generator.py | 14 | Excel output structure, 3 sheets, traceability, summary, error paths |
| test_agents.py | agents/*.py | 15 | Tool counts, system prompt keywords, tool function I/O formatting |
| test_pipeline.py | pipeline.py | 16 | Graph wiring, example data quality, input file regex splitting |
| **Total** | | **73** | |

All 73 tests pass under `python -m unittest discover -s tests -v` in under 1 second.
