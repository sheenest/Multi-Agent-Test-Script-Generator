# Building an Agentic AI Test Case Generator for Workday Expenses Change Requests

A step-by-step guide to building a 3-agent RAG pipeline that automatically generates test cases (positive, negative, and regression) for new Workday Expenses Change Requests, using the AWS Strands Agents SDK, Amazon Bedrock, and Pinecone vector database.

---

## Table of Contents

1. [Solution Overview](#1-solution-overview)
2. [Architecture Design](#2-architecture-design)
3. [How the Pipeline Works](#3-how-the-pipeline-works)
4. [Knowledge Base Data Model](#4-knowledge-base-data-model)
5. [Understanding the Source Documents](#5-understanding-the-source-documents)
6. [Environment Setup](#6-environment-setup)
7. [Project Structure](#7-project-structure)
8. [Script 1 — shared.py (Shared Configuration)](#8-script-1--sharedpy-shared-configuration)
9. [Script 2 — ingest.py (Ingestion Pipeline)](#9-script-2--ingestpy-ingestion-pipeline)
10. [Script 3 — agents/retriever.py (Retriever Agent)](#10-script-3--agentsretrieverpy-retriever-agent)
11. [Script 4 — agents/analyzer.py (Analyzer Agent)](#11-script-4--agentsanalyzerpy-analyzer-agent)
12. [Script 5 — agents/generator.py (Generator Agent)](#12-script-5--agentsgeneratorpy-generator-agent)
13. [Script 6 — pipeline.py (Main Orchestration Pipeline)](#13-script-6--pipelinepy-main-orchestration-pipeline)
14. [Running the Solution](#14-running-the-solution)
15. [Output Format](#15-output-format)
16. [Cost Estimate](#16-cost-estimate)

---

## 1. Solution Overview

When a Workday functional consultant writes a new Change Request for the Expenses module, they need to produce a comprehensive set of test cases covering every distinct component of the Solution Design. Each test case must be categorised as one of three types:

- **Positive** — the expense report submits successfully (all validation rules pass)
- **Negative** — a hard stop triggers and blocks submission (a custom validation rule fires)
- **Regression** — existing functionality remains unaffected by the new CR

This solution automates that process. It takes in the Business Requirement and Solution Design of a new CR, searches a knowledge base of 10 past CRs for similar patterns, and generates a complete set of grouped test cases in the same Excel format the team already uses.

The technology stack is designed to be low-cost and GPU-free:

- **AWS Strands Agents SDK** — lightweight Python framework for building multi-agent systems with tool use
- **Amazon Bedrock (Nova Lite)** — serverless LLM inference at ~$0.06 per 1M input tokens, no GPU required
- **Amazon Titan Embed Text v2** — generates 1024-dimensional embeddings for semantic search
- **Pinecone (Serverless)** — managed vector database with a free tier that covers this use case

---

## 2. Architecture Design

The solution uses exactly 3 agents arranged in a sequential pipeline, orchestrated by the Strands `GraphBuilder`. Each agent has a single, well-scoped responsibility, which makes prompts easier to tune and failures easier to diagnose.

```
                    ┌─────────────────────────────┐
                    │     New CR Input             │
                    │  (Business Req + Sol Design) │
                    └──────────────┬──────────────┘
                                   │
                                   ▼
┌──────────────┐   ┌─────────────────────────────┐
│              │◄──│  Agent 1 — RETRIEVER         │
│   Pinecone   │   │  Tools:                      │
│   Index      │──►│    search_similar_crs        │
│              │   │    fetch_test_groups          │
│  ┌─────────┐ │   └──────────────┬──────────────┘
│  │cr_details│ │                  │ past CRs + grouped test cases
│  │(10 CRs) │ │                  ▼
│  ├─────────┤ │   ┌─────────────────────────────┐
│  │test_cases│ │   │  Agent 2 — ANALYZER          │
│  │(~50 grps)│ │   │  No tools (pure reasoning)   │
│  └─────────┘ │   │  Maps design → test groups    │
│              │   └──────────────┬──────────────┘
│  ┌─────────┐ │                  │ structured test plan
│  │Ingestion│ │                  ▼
│  │Pipeline │─┘   ┌─────────────────────────────┐
│  │(one-time)│    │  Agent 3 — GENERATOR          │
│  └─────────┘     │  Tool: save_test_excel        │
└──────────────┘   │  Produces formatted .xlsx      │
                   └──────────────┬──────────────┘
                                  │
                                  ▼
                   ┌─────────────────────────────┐
                   │  Output: CR_TestCases.xlsx   │
                   │  Sheet 1: Test Cases by Comp │
                   │  Sheet 2: Traceability Matrix│
                   │  Sheet 3: Summary            │
                   └─────────────────────────────┘
```

**Why 3 agents and not more?** Each agent has a single, well-scoped job. The Retriever knows how to search (Pinecone queries, metadata filtering). The Analyzer knows Workday testing patterns (which solution design components need which test types). The Generator knows output formatting (Excel structure, numbering conventions, Workday-specific language). Keeping them separate means you can tune each prompt independently without side effects.

**Why not fewer?** Combining retrieval and analysis into one agent would force a single prompt to handle both tool calls and complex reasoning, which degrades quality on smaller models like Nova Lite. The separation lets each agent's context window focus on its specific task.

---

## 3. How the Pipeline Works

When you run the pipeline with a new CR, here is exactly what happens at each stage:

**Stage 1 — Retriever Agent** receives the new CR's business requirement and solution design as input. It calls `search_similar_crs` with the full text as a semantic query against the `cr_details` namespace in Pinecone, returning the 3-5 most similar past CRs ranked by cosine similarity. For each similar CR found, it then calls `fetch_test_groups` to retrieve every test case group from the `test_cases` namespace, filtered by that CR's ID. The output is a comprehensive dossier of reference material: which past CRs are relevant, what their solution designs looked like, and the exact test cases (with scenarios, steps, and expected results) that were written for each solution component.

**Stage 2 — Analyzer Agent** receives the Retriever's output plus the original new CR input. With no tools — this is pure reasoning — it compares the new CR's solution design against the reference patterns and produces a structured test plan. Each solution design point gets mapped to a test group (e.g., "Validation Rule 1 — Per-Event Cap" becomes Group 3 with specific positive, negative, and boundary test cases). The Analyzer adapts patterns from similar past CRs: if a past CR had a $500 cap with boundary tests at exactly $500.00 and $500.01, and the new CR has a $2,000 cap, the Analyzer applies the same boundary pattern at $2,000.00 and $2,000.01. The final group is always Regression.

**Stage 3 — Generator Agent** receives the Analyzer's structured test plan and calls `save_test_excel` once with the complete test data as a JSON structure. The tool builds a formatted Excel workbook with three sheets matching the team's existing format, applies professional styling (blue headers, borders, column widths), and saves the file to the `generated_tests/` directory.

The Strands `GraphBuilder` handles the plumbing: each agent's full output (including tool call results) is automatically passed as context to the next agent in the chain.

---

## 4. Knowledge Base Data Model

The Pinecone index (`workday-cr-tests`) uses two namespaces to store different aspects of the past CRs:

**Namespace: `cr_details`** stores one vector per CR (10 total). The embedding is generated from the combined business requirement and solution design text. Metadata includes the CR ID, title, category, the full business requirement text, numbered solution design points, and test case counts by type. This namespace is used for the initial semantic similarity search — "find me past CRs that dealt with similar problems."

**Namespace: `test_cases`** stores one vector per test group (approximately 50 total across the 10 CRs). Each vector represents a single solution component's test cases — for example, "Validation Rule 1 — Per-Transaction Cap ($500)" with its 4 test cases. The embedding is generated from the component name combined with all test case details. Metadata includes the `cr_id` for filtering, the component name, design reference, and the actual test cases stored as a JSON string. This namespace is used for the targeted retrieval — "get me every test group for CR-EXP-2026-001."

The two-namespace design means the Retriever can do a fast, broad semantic search first (across 10 vectors), then do targeted metadata-filtered lookups to get the detailed test cases (across ~50 vectors, filtered to just the relevant CRs). This is much more efficient than searching across all test case vectors directly.

---

## 5. Understanding the Source Documents

Before building the solution, it is important to understand the exact structure of the documents being parsed. Each CR consists of two files:

**Word Document (CR Details)** follows this structure:

- **Table 0** — Metadata table with key-value pairs (CR ID, Module, Category, Priority, etc.)
- **Section 2 (Heading 1: "2. Business Requirement")** — List Paragraph items describing what the business needs
- **Section 3 (Heading 1: "3. Solution Design")** — List Paragraph items describing the technical implementation (5-7 numbered points per CR). Sub-sections 3.1-3.4 cover Approval Workflow Impact, Reporting, Security, and Migration
- **Section 4 (Heading 1: "4. Test Plan")** — Test tables organised by component
- **Sections 5-7** — Costing, Rollback Plan, and Sign-Off

The parser extracts the List Paragraph items under Section 3 (stopping at the first Heading 2 sub-section) as the core solution design points. These are the items that directly map to test groups.

**Excel File (Test Cases)** has three sheets:

- **"Test Cases by Component"** — Row 4 is the header row (Group #, Solution Component, Design Ref, TC #, Test Type, Scenario, Test Steps, Expected Result). Data starts at row 5. Groups are separated by blank rows. Each group corresponds to one solution design component and contains a mix of Positive, Negative, and Regression test cases.
- **"Traceability Matrix"** — Maps each solution component to its test case IDs by type (Positive, Negative, Regression).
- **"Summary"** — Total counts by test type.

Across the 10 CRs in the knowledge base, the data ranges are: 5-7 solution design points per CR, 4-6 test groups per CR, and 11-16 test cases per CR.

---

## 6. Environment Setup

### Prerequisites

- Python 3.10 or higher
- An AWS account with Amazon Bedrock access enabled (specifically: Amazon Nova Lite model and Amazon Titan Embed Text v2 in your chosen region)
- A Pinecone account (the free Starter plan is sufficient)

### Step 1 — Create the Project Directory

```bash
mkdir -p workday-cr-test-gen/{agents,data/solutions,generated_tests}
cd workday-cr-test-gen
```

### Step 2 — Create and Activate a Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows
```

### Step 3 — Install Dependencies

Create the following `requirements.txt`:

```text
# Strands Agents SDK
strands-agents>=1.30
strands-agents-tools

# AWS
boto3>=1.35
awscli

# Pinecone Vector Database
pinecone>=9.0

# Document Parsing
python-docx>=1.1
openpyxl>=3.1

# Utilities
python-dotenv
rich
pydantic>=2.0
```

Then install:

```bash
pip install -r requirements.txt
```

**Note on package names:** The Pinecone Python SDK v9+ is installed as `pinecone` (not `pinecone-client`, which was the old v2 package). The Strands Agents SDK (as of v1.38) requires Python 3.10 or higher.

### Step 4 — Configure Credentials

Create a `.env` file in the project root:

```bash
# AWS Credentials (for Amazon Bedrock)
AWS_ACCESS_KEY_ID=your-access-key-here
AWS_SECRET_ACCESS_KEY=your-secret-key-here
AWS_DEFAULT_REGION=us-east-1

# Pinecone
PINECONE_API_KEY=your-pinecone-api-key-here

# Project Configuration
LLM_MODEL_ID=us.amazon.nova-lite-v1:0
PINECONE_INDEX_NAME=workday-cr-tests
```

**AWS setup notes:** You need an IAM user or role with `bedrock:InvokeModel` permissions for both `amazon.titan-embed-text-v2:0` and `us.amazon.nova-lite-v1:0`. If you have not used Bedrock before, you must request model access in the AWS console under Bedrock > Model access.

**Pinecone setup notes:** Sign up at pinecone.io. On the free Starter plan, create an API key from the console. The ingestion script will create the index automatically.

### Step 5 — Place the CR Data Files

Copy your 10 Word documents and 10 Excel test case files into `data/solutions/`:

```
data/solutions/
├── CR-EXP-2026-001.docx
├── CR-EXP-2026-001_TestCases.xlsx
├── CR-EXP-2026-002.docx
├── CR-EXP-2026-002_TestCases.xlsx
├── ...
├── CR-EXP-2026-010.docx
└── CR-EXP-2026-010_TestCases.xlsx
```

The naming convention matters — the ingestion script pairs each `.docx` with its `_TestCases.xlsx` by matching the CR ID stem.

### Step 6 — Create the Empty Package File

```bash
touch agents/__init__.py
```

---

## 7. Project Structure

Once all files are created (covered in the sections below), the complete project looks like this:

```
workday-cr-test-gen/
├── .env                           ← Your credentials (not committed to git)
├── requirements.txt               ← Python dependencies
├── shared.py                      ← Shared config, Bedrock client, embedding helper
├── ingest.py                      ← Parses .docx + .xlsx files, loads into Pinecone
├── pipeline.py                    ← Main entry point — GraphBuilder orchestration
├── agents/
│   ├── __init__.py                ← Empty package file
│   ├── retriever.py               ← Agent 1: Pinecone search tools
│   ├── analyzer.py                ← Agent 2: Test plan reasoning (no tools)
│   └── generator.py               ← Agent 3: Excel output tool
├── data/
│   └── solutions/                 ← Your 10 past CR files (20 files total)
│       ├── CR-EXP-2026-001.docx
│       ├── CR-EXP-2026-001_TestCases.xlsx
│       └── ...
└── generated_tests/               ← Output directory (auto-created at runtime)
```

---

## 8. Script 1 — shared.py (Shared Configuration)

This is the foundational module imported by every other script. It initialises the AWS Bedrock client for LLM inference, the Pinecone client for vector storage, and provides the `embed()` helper function that converts text into 1024-dimensional vectors using Amazon Titan.

The `embed()` function truncates input to 8,000 characters (Titan's practical limit for good-quality embeddings), requests normalised vectors (so cosine similarity works correctly), and returns a plain Python list of floats that Pinecone accepts directly.

All configuration is loaded from environment variables via `python-dotenv`, with sensible defaults — the region defaults to `us-east-1`, the model defaults to Nova Lite, and the index name defaults to `workday-cr-tests`.

```python
"""
Shared configuration, embedding helper, and Pinecone client.
Used by all agents and the ingestion pipeline.
"""

import json
import os
import boto3
from dotenv import load_dotenv
from pinecone import Pinecone

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────
BEDROCK_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
INDEX_NAME = os.environ.get("PINECONE_INDEX_NAME", "workday-cr-tests")
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
LLM_MODEL = os.environ.get("LLM_MODEL_ID", "us.amazon.nova-lite-v1:0")

# ── Clients ────────────────────────────────────────────────────────────────
bedrock_runtime = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
pc = Pinecone()  # reads PINECONE_API_KEY from environment


def get_index():
    """Return a handle to the Pinecone index."""
    return pc.Index(INDEX_NAME)


def embed(text: str) -> list[float]:
    """Generate a 1024-dim embedding via Amazon Titan on Bedrock."""
    body = json.dumps({
        "inputText": text[:8000],
        "dimensions": 1024,
        "normalize": True,
    })
    response = bedrock_runtime.invoke_model(
        modelId=EMBEDDING_MODEL,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(response["body"].read())["embedding"]
```

---

## 9. Script 2 — ingest.py (Ingestion Pipeline)

This is a one-time setup script that parses all 10 CR document pairs and loads them into Pinecone. It has three main components:

**The Word Document Parser (`parse_cr_docx`)** reads the `.docx` file using `python-docx` and extracts structured data by walking through paragraphs and detecting heading styles. It identifies `Heading 1` paragraphs to determine which section it is in (Business Requirement, Solution Design, Test Plan), then collects the `List Paragraph` items within each section. The solution design points are extracted specifically — only the numbered items under Section 3, stopping at the first `Heading 2` sub-section (which begins the boilerplate Approval Workflow Impact section). The metadata table (CR ID, Module, Category) is read from the first table in the document.

**The Excel Parser (`parse_test_excel`)** reads the `.xlsx` file using `openpyxl` and extracts the grouped test case structure from the "Test Cases by Component" sheet. It detects group boundaries by watching for blank rows (which separate groups in the Excel layout). Each group captures its group number, solution component name, design reference, and all test cases with their type, scenario, steps, and expected result. It also reads the Traceability Matrix and Summary sheets for completeness counts.

**The Pinecone Ingestion** creates the index if it does not exist (1024 dimensions, cosine metric, serverless spec), then for each CR it upserts one vector to the `cr_details` namespace (embedding the combined business requirement and solution design text) and one vector per test group to the `test_cases` namespace (embedding the component name with all its test cases). The test cases themselves are stored as a JSON string in the metadata, so the Retriever Agent can return them verbatim.

```python
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
```

---

## 10. Script 3 — agents/retriever.py (Retriever Agent)

The Retriever is the first agent in the pipeline. It has two Strands `@tool`-decorated functions that give it access to Pinecone:

`search_similar_crs` takes the new CR's text as a query, embeds it, and runs a cosine similarity search against the `cr_details` namespace. It returns the top-k most similar past CRs with their full metadata — CR ID, title, business requirement, solution design points, and test case counts. The agent uses this to identify which past CRs are relevant references.

`fetch_test_groups` takes a specific CR ID and retrieves all test case groups for that CR from the `test_cases` namespace using a metadata filter (`cr_id == "CR-EXP-2026-001"`). A dummy embedding is still required by Pinecone's query API even when filtering by metadata, but the filter ensures only vectors from the specified CR are returned. The results are sorted by group number and formatted with full test case details.

The system prompt instructs the agent to always search first, then fetch test groups for at least the top 3 results. This ensures the downstream Analyzer has enough reference material to work with.

```python
"""
Retriever Agent — Finds similar past CRs and their grouped test cases
from Pinecone.

Tools:
  - search_similar_crs: Semantic search over CR business requirements +
    solution designs
  - fetch_test_groups:  Retrieve test case groups for a specific CR,
    with full test details
"""

import json
from strands import Agent, tool
from strands.models import BedrockModel
from shared import embed, get_index, LLM_MODEL, BEDROCK_REGION


@tool
def search_similar_crs(query: str, top_k: int = 5) -> str:
    """Search for past Change Requests with similar business requirements
    or solution designs.

    Use this to find previously completed Workday Expenses CRs that are
    similar to a new CR. Returns CR IDs, titles, business requirements,
    solution design points, and test case counts.

    Args:
        query: The business requirement and/or solution design text of
               the new CR.
        top_k: Number of similar CRs to return (default 5).
    """
    index = get_index()
    results = index.query(
        vector=embed(query),
        top_k=top_k,
        include_metadata=True,
        namespace="cr_details",
    )
    if not results["matches"]:
        return "No similar past CRs found in the knowledge base."

    output = []
    for match in results["matches"]:
        m = match["metadata"]
        output.append(
            f"CR ID: {m['cr_id']}\n"
            f"Title: {m['title']}\n"
            f"Category: {m.get('category', 'N/A')}\n"
            f"Similarity Score: {match['score']:.3f}\n"
            f"Business Requirement:\n{m['business_requirement']}\n"
            f"Solution Design:\n{m['solution_design']}\n"
            f"Test Counts: {m.get('positive_count', 0)} positive, "
            f"{m.get('negative_count', 0)} negative, "
            f"{m.get('regression_count', 0)} regression "
            f"({m.get('total_test_cases', 0)} total)"
        )
    return "\n\n" + "=" * 60 + "\n\n".join(output)


@tool
def fetch_test_groups(cr_id: str) -> str:
    """Fetch all test case groups for a specific past CR.

    Each group corresponds to a distinct Solution Design component and
    contains the actual test cases (positive, negative, regression)
    with their scenarios, steps, and expected results.

    Call this AFTER using search_similar_crs to retrieve the detailed
    test cases for each relevant past CR.

    Args:
        cr_id: The CR identifier (e.g., 'CR-EXP-2026-001').
    """
    index = get_index()
    results = index.query(
        vector=embed(f"test case groups for {cr_id}"),
        top_k=20,
        include_metadata=True,
        namespace="test_cases",
        filter={"cr_id": {"$eq": cr_id}},
    )
    if not results["matches"]:
        return f"No test case groups found for {cr_id}."

    # Sort by group number
    matches_sorted = sorted(
        results["matches"],
        key=lambda m: m["metadata"].get("group_num", "0"),
    )

    output = []
    for match in matches_sorted:
        m = match["metadata"]
        test_cases = json.loads(m.get("test_cases_json", "[]"))

        tc_lines = []
        for tc in test_cases:
            tc_lines.append(
                f"  [{tc['test_type']:10s}] {tc['tc_num']}: "
                f"{tc['scenario']}\n"
                f"              Steps: {tc['steps']}\n"
                f"              Expected: {tc['expected_result']}"
            )

        output.append(
            f"Group {m['group_num']}: {m['component']}\n"
            f"Design Reference: {m['design_ref']}\n"
            f"Counts: {m.get('positive_count', 0)}P / "
            f"{m.get('negative_count', 0)}N / "
            f"{m.get('regression_count', 0)}R\n"
            f"Test Cases:\n" + "\n".join(tc_lines)
        )
    return "\n\n---\n\n".join(output)


# ── Agent Definition ──────────────────────────────────────────────────
retriever_agent = Agent(
    name="retriever",
    model=BedrockModel(model_id=LLM_MODEL, region_name=BEDROCK_REGION),
    tools=[search_similar_crs, fetch_test_groups],
    system_prompt="""You are a Retrieval Specialist for a Workday Expenses \
Change Request test generation system.

You have access to a knowledge base containing 10 past Change Requests for \
the Workday Expenses Module. Each CR has a Word document (with Business \
Requirements and Solution Design) and an Excel file (with test cases grouped \
by Solution Design component).

When given a NEW Change Request's Business Requirement and Solution Design, \
you MUST:

1. Use search_similar_crs to find the 3-5 most similar past CRs. Search \
using the full business requirement + solution design text as the query.

2. For EACH similar CR found (especially the top 3), use fetch_test_groups \
to retrieve ALL their grouped test cases. This is critical — the downstream \
agents need these concrete test case examples.

3. Present your findings organized by:
   a. Which past CRs are most relevant and WHY (what patterns match)
   b. For each CR, list every test group with its component name, design \
ref, and all test cases with their type, scenario, steps, and expected result.

Be thorough. The quality of the generated test cases depends entirely on the \
reference examples you retrieve. Always fetch test groups for at least the \
top 3 CRs.""",
    callback_handler=None,
)
```

---

## 11. Script 4 — agents/analyzer.py (Analyzer Agent)

The Analyzer is the second agent and the reasoning engine of the pipeline. It has no tools — it receives the Retriever's output (similar past CRs with their test groups) plus the original new CR input, and produces a structured test plan through pure LLM reasoning.

The system prompt is the most critical piece of the entire solution. It encodes Workday Expenses domain knowledge: what constitutes a positive test (expense report submits successfully), what constitutes a negative test (hard stop fires and blocks submission), and what regression tests should always be included. It also defines the grouping rules — each group maps to one distinct solution design component, and the last group is always Regression.

The prompt instructs the Analyzer to aim for 3-5 test cases per validation rule (mixing positive and negative), always include boundary tests at exact thresholds ($0.01 above and below), and reference which past CR each new test adapts from. The total typically ranges from 12-20 test cases depending on CR complexity.

```python
"""
Analyzer Agent — Compares the new CR against retrieved past CRs and
produces a structured test plan grouped by Solution Design component.

No tools — this is a pure reasoning agent that receives the Retriever's
output and generates a detailed test plan.
"""

from strands import Agent
from strands.models import BedrockModel
from shared import LLM_MODEL, BEDROCK_REGION


analyzer_agent = Agent(
    name="analyzer",
    model=BedrockModel(model_id=LLM_MODEL, region_name=BEDROCK_REGION),
    tools=[],
    system_prompt="""You are a Test Strategy Analyst for Workday Expenses \
Change Requests.

You receive:
1. A NEW Change Request with its Business Requirement and Solution Design.
2. RETRIEVED past CRs with their solution designs and grouped test cases.

Your job is to produce a STRUCTURED TEST PLAN for the new CR. You MUST \
follow these rules exactly:

=== GROUPING RULES ===

Break the test plan into GROUPS, where each group corresponds to ONE \
distinct component of the Solution Design. Typical components in Workday \
Expenses CRs:

- Expense Item Configuration (new items, groups, visibility)
- Each distinct Validation Rule (one group per rule — e.g., amount cap, \
eligibility, required fields, duplicate detection, date window)
- Receipt Requirements
- Custom Fields
- Approval Workflow changes
- Eligibility Rules
- Regression (always the last group — covers existing functionality \
unaffected by the CR)

Number groups sequentially: Group 1, Group 2, etc.

=== TEST TYPE DEFINITIONS (WORKDAY EXPENSES SPECIFIC) ===

POSITIVE test: The expense report SUBMITS SUCCESSFULLY.
  - The worker fills in all required fields correctly, meets all validation \
criteria, and the report passes all custom validation rules on submit.
  - Include boundary cases that PASS (e.g., amount exactly at the cap).

NEGATIVE test: A HARD STOP triggers and BLOCKS submission.
  - The custom validation rule fires and prevents the expense report from \
being submitted. A validation error message appears.
  - Examples: amount exceeds cap, missing required field, ineligible worker, \
duplicate detected, date outside allowed window.
  - Include boundary cases that FAIL (e.g., amount $0.01 above threshold).

REGRESSION test: Existing functionality remains UNAFFECTED by the new CR.
  - Other expense items, approval workflows, existing validation rules, \
and reports continue to work as before.
  - Always include: "Other expense items unaffected", "Approval workflow \
intact", "Existing reports/rollups correct".

=== OUTPUT FORMAT ===

For each group, provide:

GROUP [#]: [Solution Component Name]
Design Reference: [section ref from Solution Design, e.g., "3.1" or \
"3.3 Rule 1"]
Based On: [which past CR(s) this adapts from, or "New pattern"]

Test Cases:
  [P/N/R]-[##] | [Type] | [Scenario]
  Steps: [detailed Workday steps]
  Expected Result: [specific outcome — for Positive: "Submits successfully" \
/ for Negative: "Hard stop: [validation message]"]

=== ADDITIONAL RULES ===

- Aim for 3-5 test cases per validation rule (mix of positive and negative).
- Every validation rule MUST have at least 1 positive and 1 negative test.
- Always include boundary tests (exactly at threshold, $0.01 above/below).
- The last group is ALWAYS Regression with 3-4 tests.
- Reference which past CR test case each new test adapts from.
- Total test cases typically range from 12-20 depending on CR complexity.
- Use Workday-specific language: "expense report", "expense line", "submit", \
"validation rule fires", "hard stop error", "soft stop warning".""",
    callback_handler=None,
)
```

---

## 12. Script 5 — agents/generator.py (Generator Agent)

The Generator is the third and final agent. It receives the Analyzer's structured test plan and transforms it into a professionally formatted Excel file that matches the team's existing test case format.

It has one tool, `save_test_excel`, which accepts a CR ID, title, and a JSON string containing the grouped test cases. The tool uses `openpyxl` to build a workbook with three sheets:

- **Sheet 1 ("Test Cases by Component")** — the main test case table with blue column headers, bordered cells, text wrapping, and blank rows between groups. Columns match the existing format exactly: Group #, Solution Component, Design Ref, TC #, Test Type, Scenario, Test Steps, Expected Result.
- **Sheet 2 ("Traceability Matrix")** — automatically derived from the test case data, mapping each component to its positive, negative, and regression test case IDs, with a total row at the bottom.
- **Sheet 3 ("Summary")** — aggregate counts by test type plus metadata about the number of solution components and coverage.

The system prompt gives the Generator strict rules on test case numbering (P-01/N-01/R-01 sequential across all groups), the JSON structure the tool expects, and Workday-specific expected result formatting ("Hard stop: [exact validation message]" for negative tests).

```python
"""
Generator Agent — Takes the Analyzer's test plan and produces the final
Excel test case file in the exact format matching the existing CR test files.

Output format matches the uploaded CR test case Excel files:
  Sheet 1: "Test Cases by Component" — grouped test cases with all columns
  Sheet 2: "Traceability Matrix" — component to test case mapping
  Sheet 3: "Summary" — counts by type

Tool: save_test_excel — builds and saves the formatted .xlsx file
"""

import json
import re
from pathlib import Path
from strands import Agent, tool
from strands.models import BedrockModel
from shared import LLM_MODEL, BEDROCK_REGION


@tool
def save_test_excel(cr_id: str, cr_title: str, test_plan_json: str) -> str:
    """Save the generated test cases as a formatted Excel file.

    The test_plan_json must be a JSON string with this exact structure:
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
                        "steps": "User opens new expense report...",
                        "expected_result": "Item appears under group..."
                    }
                ]
            }
        ]
    }

    Args:
        cr_id: The CR identifier (e.g., 'CR-EXP-2026-NEW').
        cr_title: The descriptive title of the CR.
        test_plan_json: JSON string containing the structured test groups.
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    try:
        plan = json.loads(test_plan_json)
    except json.JSONDecodeError as e:
        return f"ERROR: Invalid JSON \u2014 {e}"

    groups = plan.get("groups", [])
    if not groups:
        return "ERROR: No test groups found in the plan."

    wb = openpyxl.Workbook()

    # ── Styles ────────────────────────────────────────────────────────
    header_font = Font(bold=True, size=11)
    header_fill = PatternFill(
        start_color="4472C4", end_color="4472C4", fill_type="solid"
    )
    header_font_white = Font(bold=True, size=11, color="FFFFFF")
    title_font = Font(bold=True, size=14)
    subtitle_font = Font(bold=True, size=12, color="4472C4")
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )
    wrap = Alignment(wrap_text=True, vertical="top")

    # === Sheet 1: Test Cases by Component =============================
    ws1 = wb.active
    ws1.title = "Test Cases by Component"

    # Title rows
    ws1.cell(row=1, column=1,
             value=f"{cr_id} \u2014 Test Cases").font = title_font
    ws1.cell(row=2, column=1, value=cr_title).font = subtitle_font

    # Header row
    headers = [
        "Group #", "Solution Component", "Design Ref", "TC #",
        "Test Type", "Scenario", "Test Steps", "Expected Result",
    ]
    for col, header in enumerate(headers, 1):
        cell = ws1.cell(row=4, column=col, value=header)
        cell.font = header_font_white
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = Alignment(
            horizontal="center", vertical="center"
        )

    current_row = 5
    for group in groups:
        for tc in group.get("test_cases", []):
            for col, val in enumerate([
                group["group_num"],
                group["component"],
                group["design_ref"],
                tc["tc_num"],
                tc["test_type"],
                tc["scenario"],
                tc.get("steps", ""),
                tc.get("expected_result", ""),
            ], 1):
                cell = ws1.cell(row=current_row, column=col, value=val)
                cell.border = thin_border
                cell.alignment = wrap
            current_row += 1
        current_row += 1  # blank row between groups

    # Column widths
    col_widths = [8, 40, 12, 8, 10, 35, 50, 45]
    for i, w in enumerate(col_widths, 1):
        ws1.column_dimensions[get_column_letter(i)].width = w

    # === Sheet 2: Traceability Matrix =================================
    ws2 = wb.create_sheet("Traceability Matrix")
    ws2.cell(row=1, column=1,
             value=f"{cr_id} \u2014 Traceability Matrix").font = title_font

    tm_headers = [
        "#", "Solution Component", "Design Ref",
        "Positive", "Negative", "Regression",
    ]
    for col, header in enumerate(tm_headers, 1):
        cell = ws2.cell(row=3, column=col, value=header)
        cell.font = header_font_white
        cell.fill = header_fill
        cell.border = thin_border

    total_pos = total_neg = total_reg = 0
    for row_idx, group in enumerate(groups, 4):
        tcs = group.get("test_cases", [])
        pos = [tc["tc_num"] for tc in tcs
               if tc["test_type"] == "Positive"]
        neg = [tc["tc_num"] for tc in tcs
               if tc["test_type"] == "Negative"]
        reg = [tc["tc_num"] for tc in tcs
               if tc["test_type"] == "Regression"]
        total_pos += len(pos)
        total_neg += len(neg)
        total_reg += len(reg)

        for col, val in enumerate([
            group["group_num"],
            group["component"],
            group["design_ref"],
            ", ".join(pos) or "\u2014",
            ", ".join(neg) or "\u2014",
            ", ".join(reg) or "\u2014",
        ], 1):
            cell = ws2.cell(row=row_idx, column=col, value=val)
            cell.border = thin_border
            cell.alignment = wrap

    # Total row
    total_row = 4 + len(groups)
    ws2.cell(row=total_row, column=2, value="TOTAL").font = header_font
    ws2.cell(row=total_row, column=4, value=total_pos).font = header_font
    ws2.cell(row=total_row, column=5, value=total_neg).font = header_font
    ws2.cell(row=total_row, column=6, value=total_reg).font = header_font
    for col in range(1, 7):
        ws2.cell(row=total_row, column=col).border = thin_border

    tm_widths = [6, 45, 12, 20, 20, 25]
    for i, w in enumerate(tm_widths, 1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    # === Sheet 3: Summary =============================================
    ws3 = wb.create_sheet("Summary")
    ws3.cell(row=1, column=1,
             value=f"{cr_id} \u2014 Test Summary").font = title_font

    total_all = total_pos + total_neg + total_reg
    summary_data = [
        ("Total Test Cases", total_all),
        ("Positive Tests", total_pos),
        ("Negative Tests", total_neg),
        ("Regression Tests", total_reg),
        ("", ""),
        ("Solution Components", len(groups)),
        ("Groups with Positive", sum(
            1 for g in groups if any(
                tc["test_type"] == "Positive"
                for tc in g.get("test_cases", [])
            )
        )),
        ("Groups with Negative", sum(
            1 for g in groups if any(
                tc["test_type"] == "Negative"
                for tc in g.get("test_cases", [])
            )
        )),
    ]

    ws3.cell(row=3, column=1, value="Metric").font = header_font_white
    ws3.cell(row=3, column=1).fill = header_fill
    ws3.cell(row=3, column=2, value="Count").font = header_font_white
    ws3.cell(row=3, column=2).fill = header_fill

    for i, (metric, count) in enumerate(summary_data, 4):
        ws3.cell(row=i, column=1, value=metric).border = thin_border
        ws3.cell(row=i, column=2, value=count).border = thin_border

    ws3.column_dimensions["A"].width = 25
    ws3.column_dimensions["B"].width = 12

    # ── Save ──────────────────────────────────────────────────────────
    out_dir = Path("generated_tests")
    out_dir.mkdir(exist_ok=True)
    filename = f"{cr_id}_TestCases.xlsx"
    filepath = out_dir / filename
    wb.save(str(filepath))

    return (
        f"\u2713 Saved {filepath}\n"
        f"  Total test cases: {total_all} "
        f"({total_pos}P / {total_neg}N / {total_reg}R)\n"
        f"  Groups: {len(groups)}\n"
        f"  Sheets: Test Cases by Component, Traceability Matrix, Summary"
    )


# ── Agent Definition ──────────────────────────────────────────────────
generator_agent = Agent(
    name="generator",
    model=BedrockModel(model_id=LLM_MODEL, region_name=BEDROCK_REGION),
    tools=[save_test_excel],
    system_prompt="""You are a Test Case Generator for Workday Expenses \
Change Requests.

You receive a detailed test plan from the Analyzer agent and reference \
test cases from past CRs. Your job is to produce the FINAL test case \
Excel file.

=== CRITICAL: OUTPUT FORMAT ===

You MUST call save_test_excel with a JSON string in this EXACT structure:

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
                    "steps": "User opens new expense report and browses \
item picker",
                    "expected_result": "Item appears under correct expense \
group"
                }
            ]
        }
    ]
}

=== NUMBERING RULES ===

- Positive tests: P-01, P-02, P-03, etc. (sequential across all groups)
- Negative tests: N-01, N-02, N-03, etc. (sequential across all groups)
- Regression tests: R-01, R-02, R-03, etc. (sequential across all groups)

=== TEST CASE QUALITY RULES ===

For EVERY test case, provide:
- tc_num: Use the P/N/R numbering above
- test_type: Exactly "Positive", "Negative", or "Regression"
- scenario: Short description (5-10 words)
- steps: Detailed Workday-specific steps the tester performs
- expected_result:
  * Positive: "Submits successfully" or "Saves without errors" with specifics
  * Negative: "Hard stop: [exact validation message text]"
  * Regression: "[Existing feature] behaves as before" with specifics

=== WORKDAY EXPENSES CONTEXT ===

- Expense reports contain one or more expense lines
- Each line has: expense item, amount, currency, date, memo, receipt, \
custom fields
- "Submit" triggers all validation rules on the report/lines
- Hard stop = blocks submission with error message
- Soft stop = warning that can be acknowledged to proceed
- Always test in the context of: Create expense report -> add expense \
line -> fill fields -> submit

Call save_test_excel ONCE with the complete test plan. Then provide a \
brief summary.""",
)
```

---

## 13. Script 6 — pipeline.py (Main Orchestration Pipeline)

This is the entry point that ties everything together. It uses the Strands `GraphBuilder` to construct a directed graph with three nodes (one per agent) connected in sequence: Retriever -> Analyzer -> Generator.

The `build_pipeline` function creates the graph declaratively — `add_node` registers each agent, `add_edge` defines the flow, and `set_entry_point` designates where input enters. When the graph executes, it passes the full output of each node as input to the next.

The `run` function constructs a detailed prompt that includes the new CR's business requirement and solution design, along with instructions for each agent's role. Even though each agent has its own system prompt, the initial user prompt provides the specific context and ground rules for this particular execution.

The script supports three modes: `--example` (uses a built-in Team Building Activity CR for testing), `--input` (reads from a text file split by a "SOLUTION DESIGN" separator line), and interactive mode (prompts you to paste the two sections).

```python
"""
Workday CR Test Case Generator — Main Pipeline

3-agent sequential graph using Strands GraphBuilder:
  Retriever -> Analyzer -> Generator

Usage:
    # Interactive — paste or type the new CR details
    python pipeline.py

    # From a file
    python pipeline.py --input new_cr.txt

    # With a custom CR ID
    python pipeline.py --input new_cr.txt --cr-id CR-EXP-2026-011
"""

import sys
import argparse
from pathlib import Path
from strands.multiagent import GraphBuilder
from agents.retriever import retriever_agent
from agents.analyzer import analyzer_agent
from agents.generator import generator_agent


def build_pipeline():
    """Construct the 3-agent sequential graph."""
    builder = GraphBuilder()

    # ── Nodes ──────────────────────────────────────────────────────────
    builder.add_node(retriever_agent, "retriever")
    builder.add_node(analyzer_agent, "analyzer")
    builder.add_node(generator_agent, "generator")

    # ── Edges (sequential: retriever -> analyzer -> generator) ─────────
    builder.add_edge("retriever", "analyzer")
    builder.add_edge("analyzer", "generator")

    # ── Entry point ────────────────────────────────────────────────────
    builder.set_entry_point("retriever")

    return builder.build()


def run(business_requirement: str, solution_design: str,
        cr_id: str = "CR-EXP-2026-NEW"):
    """Execute the full test generation pipeline."""

    print("=" * 70)
    print("  WORKDAY CR TEST CASE GENERATOR")
    print("  3-Agent Pipeline: Retriever -> Analyzer -> Generator")
    print("=" * 70)
    print(f"\n  CR ID: {cr_id}")
    print(f"  Business Requirement: {business_requirement[:80]}...")
    print(f"  Solution Design Points: "
          f"{solution_design.count(chr(10)) + 1} lines")
    print()

    graph = build_pipeline()

    prompt = f"""Generate a complete set of test cases for this NEW \
Workday Expenses Change Request.

CR ID: {cr_id}

=== BUSINESS REQUIREMENT ===
{business_requirement}

=== SOLUTION DESIGN ===
{solution_design}

=== INSTRUCTIONS ===
1. RETRIEVER: Search the knowledge base for the 3-5 most similar past CRs.
   Fetch ALL their test case groups. Pay attention to:
   - CRs with similar validation rules (amount caps, required fields, \
eligibility)
   - CRs with similar expense item configurations
   - CRs with similar approval workflow changes
   - The test case patterns used (boundary tests, missing field tests, etc.)

2. ANALYZER: Compare this new CR against the retrieved past CRs. For each
   distinct Solution Design component, determine the required test cases:
   - Map each solution design point to a test group
   - For validation rules: positive (passes) + negative (hard stop fires)
   - For configuration: positive (item works correctly)
   - Always include boundary tests at exact thresholds
   - Final group is always Regression

3. GENERATOR: Produce the final Excel file using the save_test_excel tool.
   CR ID: {cr_id}
   The Excel must match the format of existing CR test case files."""

    result = graph(prompt)

    # ── Print execution summary ────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  EXECUTION SUMMARY")
    print("=" * 70)
    print(f"  Nodes completed : "
          f"{result.completed_nodes}/{result.total_nodes}")
    print(f"  Failed nodes    : {result.failed_nodes}")
    print(f"  Execution time  : {result.execution_time:.1f}ms")

    if result.accumulated_usage:
        usage = result.accumulated_usage
        print(f"  Input tokens    : "
              f"{usage.get('inputTokens', 'N/A')}")
        print(f"  Output tokens   : "
              f"{usage.get('outputTokens', 'N/A')}")

    print(f"\n  Output: ./generated_tests/{cr_id}_TestCases.xlsx")
    print("=" * 70)

    return result


# ═══════════════════════════════════════════════════════════════════════
# EXAMPLE NEW CR — For testing the pipeline
# ═══════════════════════════════════════════════════════════════════════

EXAMPLE_BUSINESS_REQUIREMENT = """
The Travel & Events team requires a new expense item for 'Team Building \
Activities' to track department-sponsored team events separately from \
general entertainment. Each event expense must be tied to a pre-approved \
event code from HR. A per-event cap of $2,000 must be enforced, and a \
per-worker annual cap of $5,000 across all team building claims. Events \
older than 45 days should not be reimbursable. Only full-time employees \
in active status are eligible. A mandatory 'Event Description' field with \
minimum 30 characters is required for all claims.
"""

EXAMPLE_SOLUTION_DESIGN = """
1. Configure new Expense Item 'Team Building Activity' \
(ID: EXP_TEAM_BUILD) under new expense group 'Team Events'. Available \
to all cost centers.
2. Add mandatory custom fields: 'HR Event Code' (text, validated against \
HR event reference table), 'Event Description' (text, min 30 chars), \
'Number of Attendees' (integer, min 2).
3. Create Validation Rule 1 (CR_EXP_TeamBuild_EventCap): On expense line \
submit, if item is 'Team Building Activity' and line amount exceeds \
$2,000, hard stop with message 'Per-event cap of $2,000 exceeded.'
4. Create Validation Rule 2 (CR_EXP_TeamBuild_AnnualCap): Track cumulative \
per-worker calendar-year spend; if total exceeds $5,000, hard stop with \
message 'Annual team building cap of $5,000 exceeded.'
5. Create Validation Rule 3 (CR_EXP_TeamBuild_DateWindow): If expense date \
is more than 45 days before submission date, hard stop with message \
'Team building expenses must be submitted within 45 days.'
6. Create Validation Rule 4 (CR_EXP_TeamBuild_Eligibility): If worker is \
not Full-Time Employee in Active status, hard stop with message 'Only \
active full-time employees are eligible.'
7. Create Validation Rule 5 (CR_EXP_TeamBuild_EventCode): If HR Event Code \
is blank or does not match HR reference table, hard stop with message \
'Valid HR Event Code required.'
8. Receipt requirement set to Always for this item. No changes to approval \
workflow; inherits current chain.
"""


def interactive_input():
    """Prompt the user to paste business requirement and solution design."""
    print("\n" + "=" * 70)
    print("  WORKDAY CR TEST CASE GENERATOR \u2014 Interactive Mode")
    print("=" * 70)

    cr_id = input("\nEnter CR ID (e.g., CR-EXP-2026-011): ").strip()
    if not cr_id:
        cr_id = "CR-EXP-2026-NEW"

    print("\nPaste the BUSINESS REQUIREMENT "
          "(press Enter twice when done):")
    lines = []
    while True:
        line = input()
        if not line and lines and not lines[-1]:
            break
        lines.append(line)
    business_req = "\n".join(lines).strip()

    print("\nPaste the SOLUTION DESIGN (press Enter twice when done):")
    lines = []
    while True:
        line = input()
        if not line and lines and not lines[-1]:
            break
        lines.append(line)
    solution_design = "\n".join(lines).strip()

    return cr_id, business_req, solution_design


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate test cases for a new Workday CR"
    )
    parser.add_argument(
        "--input",
        help="Text file with Business Requirement and Solution Design",
    )
    parser.add_argument(
        "--cr-id", default="CR-EXP-2026-NEW", help="CR identifier"
    )
    parser.add_argument(
        "--example", action="store_true",
        help="Run with the built-in example CR",
    )
    args = parser.parse_args()

    if args.example:
        run(EXAMPLE_BUSINESS_REQUIREMENT, EXAMPLE_SOLUTION_DESIGN,
            args.cr_id)

    elif args.input:
        text = Path(args.input).read_text()
        # Split on "SOLUTION DESIGN" or "Solution Design" marker
        import re
        parts = re.split(
            r"(?i)\n\s*(?:solution\s+design|SOLUTION\s+DESIGN)\s*\n",
            text, maxsplit=1,
        )
        if len(parts) == 2:
            run(parts[0].strip(), parts[1].strip(), args.cr_id)
        else:
            print("Could not split input into Business Requirement "
                  "and Solution Design.")
            print("Use a 'SOLUTION DESIGN' line to separate the "
                  "two sections.")
            sys.exit(1)

    else:
        cr_id, biz_req, sol_design = interactive_input()
        if biz_req and sol_design:
            run(biz_req, sol_design, cr_id)
        else:
            print("Both Business Requirement and Solution Design "
                  "are required.")
            sys.exit(1)
```

---

## 14. Running the Solution

### Step 1 — Ingest the Knowledge Base (One-Time)

```bash
python ingest.py --data-dir ./data/solutions
```

This parses all 10 CR document pairs, generates embeddings, and upserts approximately 60 vectors into Pinecone (10 in `cr_details`, ~50 in `test_cases`). It takes about 2-3 minutes depending on network latency to Bedrock and Pinecone.

### Step 2 — Generate Test Cases

Run with the built-in example to verify everything works:

```bash
python pipeline.py --example --cr-id CR-EXP-2026-011
```

Run interactively with your own CR:

```bash
python pipeline.py
```

Run from a text file:

```bash
python pipeline.py --input new_cr.txt --cr-id CR-EXP-2026-012
```

The text file should have the business requirement first, then a line containing just "SOLUTION DESIGN", then the solution design points.

### Step 3 — Check the Output

```bash
ls generated_tests/
# CR-EXP-2026-011_TestCases.xlsx
```

Open the Excel file to review the generated test cases. The three sheets (Test Cases by Component, Traceability Matrix, Summary) will be formatted to match your existing CR test case files.

---

## 15. Output Format

The generated Excel file has three sheets matching the existing CR test case format:

**Sheet 1 — "Test Cases by Component"** contains all test cases grouped by solution design component, with blank rows between groups:

| Group # | Solution Component | Design Ref | TC # | Test Type | Scenario | Test Steps | Expected Result |
|---|---|---|---|---|---|---|---|
| 1 | Expense Item Configuration | 3.1 | P-01 | Positive | Item visible in picker | User opens new expense report... | Item appears under Team Events group |
| 2 | Validation Rule 1 — Per-Event Cap | 3.3 Rule 1 | P-03 | Positive | Submit at $2,000 exactly | Enter $2,000.00, all fields valid... | Submits successfully (at cap) |
| 2 | Validation Rule 1 — Per-Event Cap | 3.3 Rule 1 | N-01 | Negative | Amount exceeds $2,000 | Enter $2,100, attempt to submit | Hard stop: Per-event cap of $2,000 exceeded |

**Sheet 2 — "Traceability Matrix"** maps each component to its test case IDs by type.

**Sheet 3 — "Summary"** shows total counts: typically 12-20 test cases with a healthy distribution across positive, negative, and regression types.

---

## 16. Cost Estimate

For a single test generation run (one new CR):

| Component | Estimated Usage | Cost |
|---|---|---|
| Amazon Nova Lite (3 agent calls) | ~15K input + ~5K output tokens | ~$0.002 |
| Amazon Titan Embed (retrieval queries) | ~5 embedding calls | ~$0.001 |
| Pinecone (serverless, free tier) | <100 queries | $0.00 |
| **Total per run** | | **~$0.003** |

For the one-time ingestion of 10 CRs: approximately 60 embedding calls at ~$0.01 total.

The entire solution can run hundreds of test generation cycles for under $1. Nova Lite was chosen specifically for this cost profile — if output quality needs improvement, you can upgrade to Nova Pro (`us.amazon.nova-pro-v1:0`) at roughly 5x the cost per token, which is still well under $0.02 per run.
