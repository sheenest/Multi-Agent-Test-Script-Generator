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