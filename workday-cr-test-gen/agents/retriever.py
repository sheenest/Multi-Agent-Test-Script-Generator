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

import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

from shared import embed, get_index, LLM_MODEL, BEDROCK_REGION , get_model

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
    # model=BedrockModel(model_id=LLM_MODEL, region_name=BEDROCK_REGION),
    model = get_model(),
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