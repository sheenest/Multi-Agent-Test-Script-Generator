"""
Ingestion Pipeline — Parse Workday CR Word docs + Excel test case files
and load them into Pinecone with structured metadata.

Two namespaces:
  - 'cr_details'   : Business Requirement + Solution Design from each .docx
  - 'test_cases'   : Grouped test cases from each .xlsx (one vector per group)

Usage:
    python ingest.py --data-dir ./data/solutions
    python ingest.py --data-dir /path/to/your/CR/files
"""

import argparse
import json
import re
from pathlib import Path

from docx import Document
import openpyxl

from shared import pc, INDEX_NAME, embed, get_index


# ═══════════════════════════════════════════════════════════════════════════
# WORD DOCUMENT PARSER
# ═══════════════════════════════════════════════════════════════════════════

def parse_cr_docx(filepath: str) -> dict:
    """
    Parse a CR Word document and extract structured sections.

    Returns:
        {
            "cr_id": "CR-EXP-2026-001",
            "title": "New Expense Item — Client Entertainment Premium",
            "metadata_table": { "Module": "Expenses", ... },
            "business_requirement": "Finance and Compliance require...",
            "solution_design": "1. Configure new Expense Item...\n2. ...",
            "solution_design_points": ["Configure new...", "Enable multi..."],
            "test_plan_summary": "...",
            "full_text": "... everything ..."
        }
    """
    doc = Document(filepath)

    # ── Extract metadata table (Table 0) ──────────────────────────────
    metadata = {}
    if doc.tables:
        for row in doc.tables[0].rows:
            cells = [cell.text.strip() for cell in row.cells]
            if len(cells) >= 2 and cells[0]:
                metadata[cells[0]] = cells[1]

    cr_id = metadata.get("CR ID", "")

    # ── Parse paragraphs by section ───────────────────────────────────
    current_section = None
    title = ""
    sections = {
        "overview": [],
        "business_requirement": [],
        "solution_design": [],
        "test_plan": [],
    }

    for para in doc.paragraphs:
        text = para.text.strip()
        style = para.style.name if para.style else ""

        if not text:
            continue

        # Detect section headings
        if style == "Heading 1":
            if "1." in text:
                current_section = "overview"
            elif "2." in text:
                current_section = "business_requirement"
            elif "3." in text and "Solution" in text:
                current_section = "solution_design"
            elif "4." in text and "Test" in text:
                current_section = "test_plan"
            elif "5." in text or "6." in text or "7." in text:
                current_section = None
            continue

        # Collect title (line 6 area — the descriptive title)
        if not title and "\u2014" in text and style not in ("Heading 1", "Heading 2"):
            title = text

        # Collect section content
        if current_section and current_section in sections:
            sections[current_section].append(text)

    # ── Extract solution design bullet points (List Paragraphs only) ──
    sol_points = []
    in_sol_main = False
    for para in doc.paragraphs:
        text = para.text.strip()
        style = para.style.name if para.style else ""
        if style == "Heading 1" and "3." in text and "Solution" in text:
            in_sol_main = True
            continue
        if style == "Heading 2" and in_sol_main:
            in_sol_main = False  # stop at first sub-heading
        if style == "Heading 1" and in_sol_main:
            in_sol_main = False
        if in_sol_main and style == "List Paragraph" and text:
            sol_points.append(text)

    return {
        "cr_id": cr_id,
        "title": title or metadata.get("Category", "Unknown CR"),
        "metadata_table": metadata,
        "business_requirement": "\n".join(sections["business_requirement"]),
        "solution_design": "\n".join(sections["solution_design"]),
        "solution_design_points": sol_points,
        "test_plan_summary": "\n".join(sections["test_plan"]),
        "full_text": "\n".join(
            sections["business_requirement"]
            + sections["solution_design"]
            + sections["test_plan"]
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# EXCEL TEST CASE PARSER
# ═══════════════════════════════════════════════════════════════════════════

def parse_test_excel(filepath: str) -> dict:
    """
    Parse a test case Excel file and extract grouped test cases + traceability.

    Returns:
        {
            "groups": [
                {
                    "group_num": "1",
                    "component": "Expense Item Configuration",
                    "design_ref": "3.1",
                    "test_cases": [
                        {
                            "tc_num": "P-01",
                            "test_type": "Positive",
                            "scenario": "Item visible in picker",
                            "steps": "User opens new expense...",
                            "expected_result": "Item appears under..."
                        }, ...
                    ]
                }, ...
            ],
            "traceability": [
                {
                    "component": "Expense Item Configuration",
                    "design_ref": "3.1",
                    "positive": "P-01, P-02, P-03",
                    "negative": "\u2014",
                    "regression": "\u2014"
                }, ...
            ],
            "summary": {"total": 16, "positive": 7, "negative": 5, "regression": 4}
        }
    """
    wb = openpyxl.load_workbook(filepath, data_only=True)

    # ── Parse "Test Cases by Component" sheet ─────────────────────────
    ws = wb["Test Cases by Component"]
    groups = []
    current_group = None

    for row in ws.iter_rows(min_row=5, values_only=True):
        cells = [str(c).strip() if c else "" for c in row]

        # Skip empty rows (group separators)
        if not any(cells):
            if current_group:
                groups.append(current_group)
                current_group = None
            continue

        group_num = cells[0]
        component = cells[1]
        design_ref = cells[2]
        tc_num = cells[3]
        test_type = cells[4]
        scenario = cells[5]
        steps = cells[6] if len(cells) > 6 else ""
        expected = cells[7] if len(cells) > 7 else ""

        # New group or continuation
        if group_num and tc_num:
            if current_group is None or current_group["group_num"] != group_num:
                if current_group:
                    groups.append(current_group)
                current_group = {
                    "group_num": group_num,
                    "component": component,
                    "design_ref": design_ref,
                    "test_cases": [],
                }
            current_group["test_cases"].append({
                "tc_num": tc_num,
                "test_type": test_type,
                "scenario": scenario,
                "steps": steps,
                "expected_result": expected,
            })
        elif tc_num and current_group:
            current_group["test_cases"].append({
                "tc_num": tc_num,
                "test_type": test_type,
                "scenario": scenario,
                "steps": steps,
                "expected_result": expected,
            })

    if current_group:
        groups.append(current_group)

    # ── Parse "Traceability Matrix" sheet ─────────────────────────────
    traceability = []
    if "Traceability Matrix" in wb.sheetnames:
        ws_tm = wb["Traceability Matrix"]
        for row in ws_tm.iter_rows(min_row=4, values_only=True):
            cells = [str(c).strip() if c else "" for c in row]
            if cells[1] and cells[1] != "TOTAL":
                traceability.append({
                    "component": cells[1],
                    "design_ref": cells[2],
                    "positive": cells[3] if cells[3] != "\u2014" else "",
                    "negative": cells[4] if cells[4] != "\u2014" else "",
                    "regression": cells[5] if len(cells) > 5 and cells[5] != "\u2014" else "",
                })

    # ── Parse "Summary" sheet ─────────────────────────────────────────
    summary = {}
    if "Summary" in wb.sheetnames:
        ws_s = wb["Summary"]
        for row in ws_s.iter_rows(min_row=3, values_only=True):
            cells = [str(c).strip() if c else "" for c in row]
            if cells[0] and cells[1]:
                key = cells[0].lower().replace(" ", "_")
                try:
                    summary[key] = int(cells[1])
                except ValueError:
                    summary[key] = cells[1]

    return {
        "groups": groups,
        "traceability": traceability,
        "summary": summary,
    }


# ═══════════════════════════════════════════════════════════════════════════
# PINECONE INGESTION
# ═══════════════════════════════════════════════════════════════════════════

def create_index_if_needed():
    existing = [idx.name for idx in pc.list_indexes()]
    if INDEX_NAME not in existing:
        pc.create_index(
            name=INDEX_NAME,
            dimension=1024,
            metric="cosine",
            spec={"serverless": {"cloud": "aws", "region": "us-east-1"}},
        )
        print(f"Created index: {INDEX_NAME}")
    else:
        print(f"Index '{INDEX_NAME}' already exists.")


def ingest_cr(cr_data: dict, test_data: dict):
    """Ingest one CR (docx + xlsx parsed data) into Pinecone."""
    index = get_index()
    cr_id = cr_data["cr_id"]

    # ── 1. Upsert CR details (business req + solution design) ─────────
    cr_text = (
        f"CR: {cr_id} \u2014 {cr_data['title']}\n\n"
        f"BUSINESS REQUIREMENT:\n{cr_data['business_requirement']}\n\n"
        f"SOLUTION DESIGN:\n{cr_data['solution_design']}"
    )

    # Build solution design points as numbered list for metadata
    sol_points_text = "\n".join(
        f"{i+1}. {pt}" for i, pt in enumerate(cr_data["solution_design_points"])
    )

    index.upsert(
        vectors=[{
            "id": cr_id,
            "values": embed(cr_text),
            "metadata": {
                "cr_id": cr_id,
                "title": cr_data["title"],
                "category": cr_data["metadata_table"].get("Category", ""),
                "business_requirement": cr_data["business_requirement"][:3000],
                "solution_design": sol_points_text[:3000],
                "solution_design_points_count": len(cr_data["solution_design_points"]),
                "total_test_cases": test_data["summary"].get("total_test_cases", 0),
                "positive_count": test_data["summary"].get("positive_tests", 0),
                "negative_count": test_data["summary"].get("negative_tests", 0),
                "regression_count": test_data["summary"].get("regression_tests", 0),
            },
        }],
        namespace="cr_details",
    )
    print(f"  \u2713 CR details ingested: {cr_id}")

    # ── 2. Upsert test case groups (one vector per component group) ───
    test_vectors = []
    for group in test_data["groups"]:
        # Build rich text for embedding — includes component, all test cases
        tc_lines = []
        for tc in group["test_cases"]:
            tc_lines.append(
                f"[{tc['test_type']}] {tc['tc_num']}: {tc['scenario']} | "
                f"Steps: {tc['steps']} | Expected: {tc['expected_result']}"
            )
        tc_text = "\n".join(tc_lines)

        group_text = (
            f"CR: {cr_id} \u2014 {cr_data['title']}\n"
            f"Solution Component: {group['component']}\n"
            f"Design Reference: {group['design_ref']}\n"
            f"Test Cases:\n{tc_text}"
        )

        # Count by type within this group
        pos_count = sum(1 for tc in group["test_cases"] if tc["test_type"] == "Positive")
        neg_count = sum(1 for tc in group["test_cases"] if tc["test_type"] == "Negative")
        reg_count = sum(1 for tc in group["test_cases"] if tc["test_type"] == "Regression")

        vec_id = f"{cr_id}-group-{group['group_num']}"
        test_vectors.append({
            "id": vec_id,
            "values": embed(group_text),
            "metadata": {
                "cr_id": cr_id,
                "cr_title": cr_data["title"],
                "group_num": group["group_num"],
                "component": group["component"],
                "design_ref": group["design_ref"],
                "test_cases_json": json.dumps(group["test_cases"]),
                "positive_count": pos_count,
                "negative_count": neg_count,
                "regression_count": reg_count,
                "total_in_group": len(group["test_cases"]),
            },
        })

    if test_vectors:
        for batch_start in range(0, len(test_vectors), 100):
            index.upsert(
                vectors=test_vectors[batch_start:batch_start + 100],
                namespace="test_cases",
            )
        print(f"  \u2713 {len(test_vectors)} test case groups ingested")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Ingest Workday CR files into Pinecone")
    parser.add_argument("--data-dir", default="./data/solutions",
                        help="Directory containing .docx and .xlsx files")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"ERROR: Directory not found: {data_dir}")
        return

    create_index_if_needed()

    # Find all CR .docx files
    docx_files = sorted(data_dir.glob("CR-EXP-*.docx"))
    if not docx_files:
        print(f"No CR-EXP-*.docx files found in {data_dir}")
        return

    print(f"\nFound {len(docx_files)} CR documents to ingest.\n")

    for docx_path in docx_files:
        cr_stem = docx_path.stem  # e.g., "CR-EXP-2026-001"
        xlsx_path = data_dir / f"{cr_stem}_TestCases.xlsx"

        if not xlsx_path.exists():
            print(f"\u26a0 Skipping {cr_stem}: no matching Excel file ({xlsx_path.name})")
            continue

        print(f"Processing: {cr_stem}")
        cr_data = parse_cr_docx(str(docx_path))
        test_data = parse_test_excel(str(xlsx_path))
        ingest_cr(cr_data, test_data)
        print()

    print("\u2705 Ingestion complete!")


if __name__ == "__main__":
    main()