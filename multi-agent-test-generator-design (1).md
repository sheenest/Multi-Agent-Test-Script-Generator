# Multi-Agent Test Script Generator

## Complete System Design — Strands SDK + Amazon Bedrock + Pinecone

### 2-Week Build Plan with Full Code, Architecture & Learning Resources

---

## 1. The Problem You're Solving

You have a library of **past solutions** paired with their **test scripts**. When a new solution arrives, you need to automatically generate a full test suite — covering positive, negative, and regression scenarios — by learning from those past examples and adapting them to the new context.

This is a textbook multi-agent + RAG problem: retrieve relevant prior work, reason about differences, and generate structured output.

---

## 2. Architecture — The 30-Second Version

```
                    ┌──────────────────────┐
                    │    NEW SOLUTION       │
                    │    (User Input)       │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   RETRIEVER AGENT    │ ── queries ──▶ Pinecone
                    │   (finds similar     │ ◀── results ──  Vector DB
                    │    past solutions    │
                    │    + test scripts)   │
                    └──────────┬───────────┘
                               │ passes context
                    ┌──────────▼───────────┐
                    │   ANALYZER AGENT     │
                    │   (compares new vs   │
                    │    old, builds test  │
                    │    plan)             │
                    └──────────┬───────────┘
                               │ passes plan
                    ┌──────────▼───────────┐
                    │   GENERATOR AGENT    │
                    │   (writes pytest     │
                    │    scripts: positive,│
                    │    negative,         │
                    │    regression)       │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   3 Test Files       │
                    │   saved to disk      │
                    └──────────────────────┘
```

### Why This Design Works for 2 Weeks

**Pattern: Strands `GraphBuilder` (Sequential Pipeline)**

The Strands SDK provides a built-in `GraphBuilder` class that lets you wire agents into a directed graph with explicit node-to-node edges. For your use case, this is a simple 3-node linear pipeline: `retriever → analyzer → generator`. The GraphBuilder handles input propagation between nodes automatically — each downstream agent receives the output of its upstream dependency — so you don't need to write any glue code to pass data between agents.

This is simpler and more robust than a manual workflow (chaining agent calls yourself) because the Graph gives you built-in execution tracking, timing metrics, and failure reporting per node. You can also later extend it with parallel branches or conditional edges without rewriting the pipeline.

---

## 3. What Goes Into Pinecone

You'll store documents in **two namespaces** within a single Pinecone index. This separation lets you retrieve solutions and their tests independently.

### Namespace: `solutions`

Each vector represents one past solution. The embedding is generated from the combined title + description + tech stack.

```python
# What a solution record looks like in Pinecone
{
    "id": "sol-001",
    "values": [0.012, -0.034, ...],           # 1024-dim Titan embedding
    "metadata": {
        "solution_id": "sol-001",
        "title": "User Authentication Service",
        "description": "OAuth2 login flow with MFA, password reset...",
        "tech_stack": "Python, FastAPI, PostgreSQL, Redis",
        "domain": "authentication",
        "complexity": "medium"
    }
}
```

### Namespace: `test_scripts`

Each vector represents one test function from a past solution. The embedding is generated from the test name + description + code combined.

```python
# What a test script record looks like in Pinecone
{
    "id": "test-sol001-pos-001",
    "values": [0.008, -0.021, ...],
    "metadata": {
        "solution_id": "sol-001",              # links to parent solution
        "test_type": "positive",               # positive | negative | regression
        "test_name": "test_login_valid_credentials",
        "description": "Verifies successful login returns JWT",
        "test_code": "def test_login_valid_credentials(client):\n    ...",
        "framework": "pytest"
    }
}
```

**Why two namespaces?** The Retriever first searches `solutions` by semantic similarity to the new solution, then uses the returned `solution_id` values to fetch their associated test scripts from `test_scripts` via metadata filtering. This two-step approach gives you precise test retrieval tied to the most relevant solutions.

---

## 4. Complete Project Structure

```
test-script-generator/
├── data/
│   └── solutions/                 ← Past solutions as JSON (your training data)
│       ├── sol-001-auth.json
│       ├── sol-002-payments.json
│       └── sol-003-inventory.json
├── agents/
│   ├── __init__.py
│   ├── retriever.py               ← Retriever Agent + Pinecone tools
│   ├── analyzer.py                ← Analyzer Agent (pure reasoning)
│   └── generator.py               ← Generator Agent + file writer tool
├── generated_tests/               ← Output directory (auto-created)
├── shared.py                      ← Shared embedding helper + Pinecone client
├── ingest.py                      ← One-time: load past data into Pinecone
├── pipeline.py                    ← GraphBuilder pipeline (main entry point)
└── requirements.txt
```

**requirements.txt:**
```
strands-agents>=1.30
strands-agents-tools
pinecone>=5.0
boto3
```

---

## 5. Full Code — Build in Order

### 5.1 Shared Utilities (`shared.py`)

This module is imported by every agent and the ingestion script. It holds the Pinecone client and the embedding function so you don't duplicate them.

```python
# shared.py
import json
import boto3
from pinecone import Pinecone

# ── Configuration ──────────────────────────────────────────
BEDROCK_REGION = "us-east-1"
INDEX_NAME = "test-generator"
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
LLM_MODEL = "us.amazon.nova-lite-v1:0"   # ~$0.06/1M input tokens

# ── Clients (initialized once, reused everywhere) ─────────
bedrock_runtime = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
pc = Pinecone()   # reads PINECONE_API_KEY from environment


def get_index():
    """Return a handle to the Pinecone index."""
    return pc.Index(INDEX_NAME)


def embed(text: str) -> list[float]:
    """Generate a 1024-dim embedding via Amazon Titan on Bedrock."""
    body = json.dumps({
        "inputText": text[:8000],   # Titan's input limit
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

### 5.2 Ingestion Pipeline (`ingest.py`)

Run this **once** to load your past solutions and test scripts into Pinecone. After that, run it again whenever you add new past solutions.

```python
# ingest.py
"""Load past solutions and their test scripts into Pinecone."""
import json
from pathlib import Path
from shared import pc, INDEX_NAME, embed, get_index


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


def ingest_solution(solution: dict):
    """
    Ingest one solution + its test scripts.

    Expected JSON format:
    {
        "solution_id": "sol-001",
        "title": "...",
        "description": "...",
        "tech_stack": "Python, FastAPI, ...",
        "domain": "authentication",
        "complexity": "medium",
        "test_scripts": [
            {
                "test_name": "test_valid_login",
                "test_type": "positive",
                "description": "...",
                "test_code": "def test_valid_login(): ...",
                "framework": "pytest"
            }
        ]
    }
    """
    index = get_index()

    # ── 1. Embed + upsert the solution ────────────────────
    sol_text = f"{solution['title']}. {solution['description']}. Tech: {solution['tech_stack']}"
    index.upsert(
        vectors=[{
            "id": solution["solution_id"],
            "values": embed(sol_text),
            "metadata": {k: v for k, v in solution.items() if k != "test_scripts"},
        }],
        namespace="solutions",
    )
    print(f"  ✓ Solution: {solution['solution_id']} — {solution['title']}")

    # ── 2. Embed + upsert each test script ────────────────
    test_vectors = []
    for i, test in enumerate(solution.get("test_scripts", [])):
        test_text = f"{test['test_name']}: {test['description']}\n{test['test_code']}"
        test_vectors.append({
            "id": f"test-{solution['solution_id']}-{test['test_type']}-{i:03d}",
            "values": embed(test_text),
            "metadata": {"solution_id": solution["solution_id"], **test},
        })

    if test_vectors:
        for batch_start in range(0, len(test_vectors), 100):
            index.upsert(
                vectors=test_vectors[batch_start:batch_start + 100],
                namespace="test_scripts",
            )
        print(f"    ✓ {len(test_vectors)} test scripts ingested")


if __name__ == "__main__":
    create_index_if_needed()

    data_dir = Path("data/solutions")
    if not data_dir.exists():
        print(f"No data directory found at {data_dir}. Create it and add JSON files.")
        exit(1)

    for json_file in sorted(data_dir.glob("*.json")):
        print(f"\nProcessing: {json_file.name}")
        solution = json.loads(json_file.read_text())
        ingest_solution(solution)

    print("\n✅ Ingestion complete!")
```

---

### 5.3 Retriever Agent (`agents/retriever.py`)

This agent has two custom tools that query Pinecone. The LLM decides when and how to use them.

```python
# agents/retriever.py
from strands import Agent, tool
from strands.models import BedrockModel
from shared import embed, get_index, LLM_MODEL, BEDROCK_REGION


@tool
def search_solutions(query: str, top_k: int = 5) -> str:
    """Search for past solutions that are similar to the given description.

    Use this tool to find previously completed solutions that resemble
    the new solution being analyzed. Returns solution titles, descriptions,
    tech stacks, and IDs.

    Args:
        query: A description of the new solution to find matches for.
        top_k: How many similar solutions to return (default 5).
    """
    index = get_index()
    results = index.query(
        vector=embed(query),
        top_k=top_k,
        include_metadata=True,
        namespace="solutions",
    )
    if not results["matches"]:
        return "No similar past solutions found in the knowledge base."

    output = []
    for match in results["matches"]:
        m = match["metadata"]
        output.append(
            f"Solution ID: {m['solution_id']}\n"
            f"Title: {m['title']}\n"
            f"Similarity: {match['score']:.3f}\n"
            f"Description: {m['description']}\n"
            f"Tech Stack: {m['tech_stack']}\n"
            f"Domain: {m.get('domain', 'N/A')}\n"
            f"Complexity: {m.get('complexity', 'N/A')}"
        )
    return "\n\n---\n\n".join(output)


@tool
def fetch_test_scripts(solution_id: str, test_type: str = "all") -> str:
    """Fetch the test scripts that belong to a specific past solution.

    Call this AFTER using search_solutions, using the solution_id values
    from the results to retrieve actual test code examples.

    Args:
        solution_id: The solution ID to fetch tests for (e.g. 'sol-001').
        test_type: Filter by type: 'positive', 'negative', 'regression', or 'all'.
    """
    index = get_index()
    filter_dict = {"solution_id": {"$eq": solution_id}}
    if test_type != "all":
        filter_dict["test_type"] = {"$eq": test_type}

    results = index.query(
        vector=embed(f"test scripts for solution {solution_id}"),
        top_k=20,
        include_metadata=True,
        namespace="test_scripts",
        filter=filter_dict,
    )
    if not results["matches"]:
        return f"No test scripts found for solution '{solution_id}'."

    output = []
    for match in results["matches"]:
        m = match["metadata"]
        output.append(
            f"[{m['test_type'].upper()}] {m['test_name']}\n"
            f"Description: {m['description']}\n"
            f"```python\n{m['test_code']}\n```"
        )
    return "\n\n---\n\n".join(output)


# ── Agent Definition ──────────────────────────────────────
retriever_agent = Agent(
    name="retriever",
    model=BedrockModel(model_id=LLM_MODEL, region_name=BEDROCK_REGION),
    tools=[search_solutions, fetch_test_scripts],
    system_prompt="""You are a Retrieval Specialist for a test script generation system.

When given a new solution description, you must:
1. Use search_solutions to find the 3-5 most similar past solutions.
2. For EACH similar solution found, use fetch_test_scripts to retrieve
   all their test scripts (positive, negative, and regression).
3. Present your findings clearly, organized by solution, then by test type.

Always retrieve test scripts for every similar solution you find.
Be thorough — the downstream agents depend on having good examples.""",
    callback_handler=None,   # suppress streaming in pipeline mode
)
```

---

### 5.4 Analyzer Agent (`agents/analyzer.py`)

This agent does pure reasoning — no tools needed. It receives the retrieval context and the new solution, then produces a structured test plan.

```python
# agents/analyzer.py
from strands import Agent
from strands.models import BedrockModel
from shared import LLM_MODEL, BEDROCK_REGION

analyzer_agent = Agent(
    name="analyzer",
    model=BedrockModel(model_id=LLM_MODEL, region_name=BEDROCK_REGION),
    tools=[],   # pure reasoning — no tools needed
    system_prompt="""You are a Test Strategy Analyst. You receive:
1. A NEW solution description that needs test scripts.
2. RETRIEVED past solutions and their existing test scripts.

Produce a detailed TEST PLAN with exactly three sections:

## POSITIVE TESTS (happy path)
For each test, provide:
- test_name: descriptive pytest name (test_<what>_<condition>_<expected>)
- description: what the test verifies
- inputs: sample valid inputs
- expected_outcome: what success looks like
- based_on: which past test this adapts (or "new" if original)

## NEGATIVE TESTS (error handling, edge cases, boundaries)
Same format as above. Cover: invalid inputs, missing fields, auth failures,
boundary values, rate limits, concurrency issues, malformed data.

## REGRESSION TESTS (protect existing behavior)
Same format. Focus on: shared interfaces, backwards compatibility,
integration points that the new solution touches.

Aim for 5-8 tests per section. Be specific about inputs and assertions.
Note which past tests each new test is adapted from.""",
    callback_handler=None,
)
```

---

### 5.5 Generator Agent (`agents/generator.py`)

This agent writes the actual pytest code and saves it to files.

```python
# agents/generator.py
from strands import Agent, tool
from strands.models import BedrockModel
from shared import LLM_MODEL, BEDROCK_REGION
from pathlib import Path


@tool
def save_test_file(filename: str, content: str) -> str:
    """Save a generated test file to the output directory.

    Args:
        filename: File name including extension (e.g. 'test_positive.py').
        content: The complete Python test file content.
    """
    out_dir = Path("generated_tests")
    out_dir.mkdir(exist_ok=True)
    filepath = out_dir / filename
    filepath.write_text(content)
    return f"Saved {filepath} ({len(content)} bytes, {content.count('def test_')} tests)"


generator_agent = Agent(
    name="generator",
    model=BedrockModel(model_id=LLM_MODEL, region_name=BEDROCK_REGION),
    tools=[save_test_file],
    system_prompt="""You are a Test Script Generator. You receive a test plan and
reference test code from past solutions. Write complete, runnable pytest files.

RULES:
1. Create THREE files using save_test_file:
   - test_positive.py  → all positive / happy-path tests
   - test_negative.py  → all negative / edge-case tests
   - test_regression.py → all regression tests

2. Each file MUST include:
   - Proper imports (pytest, fixtures, mocks as needed)
   - Module-level docstring explaining what the file tests
   - pytest markers: @pytest.mark.positive, .negative, or .regression
   - Descriptive test names: test_<what>_<condition>_<expected>
   - Assertions with clear failure messages
   - Type hints on test function parameters
   - A comment on each test noting which past test it adapts (if any)

3. Use fixtures for setup/teardown. Use @pytest.fixture for shared state.
   Use @pytest.mark.parametrize where multiple similar inputs apply.

4. Assume the solution under test is importable. Use unittest.mock for
   external dependencies (APIs, databases).

Save ALL three files. Then provide a brief summary of what was generated.""",
)
```

---

### 5.6 The Pipeline — GraphBuilder Orchestration (`pipeline.py`)

This is the main entry point. It uses Strands' `GraphBuilder` to wire the three agents into a deterministic directed graph.

```python
# pipeline.py
"""
Multi-agent test script generation pipeline.

Uses Strands GraphBuilder to orchestrate:
  retriever → analyzer → generator

Usage:
    python pipeline.py
    python pipeline.py --input solution.txt
"""
import sys
from pathlib import Path
from strands.multiagent import GraphBuilder
from agents.retriever import retriever_agent
from agents.analyzer import analyzer_agent
from agents.generator import generator_agent


def build_pipeline():
    """Construct the 3-agent graph."""
    builder = GraphBuilder()

    # ── Nodes ──────────────────────────────────────────────
    builder.add_node(retriever_agent, "retriever")
    builder.add_node(analyzer_agent, "analyzer")
    builder.add_node(generator_agent, "generator")

    # ── Edges (sequential pipeline) ────────────────────────
    builder.add_edge("retriever", "analyzer")
    builder.add_edge("analyzer", "generator")

    # ── Entry point ────────────────────────────────────────
    builder.set_entry_point("retriever")

    return builder.build()


def run(solution_description: str):
    """Execute the full pipeline on a new solution."""
    print("=" * 70)
    print("  MULTI-AGENT TEST SCRIPT GENERATOR")
    print("=" * 70)

    graph = build_pipeline()

    prompt = (
        f"Generate comprehensive test scripts (positive, negative, and "
        f"regression) for this new solution. First find similar past solutions "
        f"and their test scripts, then analyze the differences, then write "
        f"the test code.\n\n"
        f"NEW SOLUTION:\n{solution_description}"
    )

    result = graph(prompt)

    # ── Print execution summary ────────────────────────────
    print("\n" + "=" * 70)
    print("  EXECUTION SUMMARY")
    print("=" * 70)
    print(f"  Total nodes executed : {result.completed_nodes}/{result.total_nodes}")
    print(f"  Failed nodes         : {result.failed_nodes}")
    print(f"  Execution time       : {result.execution_time:.1f}ms")

    if result.accumulated_usage:
        usage = result.accumulated_usage
        print(f"  Input tokens         : {usage.get('inputTokens', 'N/A')}")
        print(f"  Output tokens        : {usage.get('outputTokens', 'N/A')}")

    print(f"\n  Output files in: ./generated_tests/")
    print("=" * 70)

    return result


# ── Example solution for testing ──────────────────────────
EXAMPLE_SOLUTION = """
Payment Processing Microservice

- REST API built with FastAPI (Python 3.11)
- Endpoints:
  - POST /payments       — create a new payment
  - GET  /payments/{id}  — retrieve payment status
  - POST /refunds        — initiate a refund
  - GET  /health         — health check
- Integrates with Stripe API for payment processing
- PostgreSQL database for transaction records
- JWT authentication required on all endpoints
- Rate limiting: 100 requests/minute per API key
- Supports currencies: USD, EUR, GBP
- Webhook callbacks for async payment status updates
- Input validation with Pydantic models
- Idempotency keys to prevent duplicate charges
"""


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--input":
        solution_text = Path(sys.argv[2]).read_text()
    else:
        solution_text = EXAMPLE_SOLUTION

    run(solution_text)
```

**How the GraphBuilder works here:** The `builder.add_edge("retriever", "analyzer")` call means the Retriever runs first, and its output is automatically forwarded as input to the Analyzer. The graph executes deterministically and returns structured results including execution order, completion status, timing, and token usage — giving you observability for free.

---

### 5.7 Sample Data File

Create this at `data/solutions/sol-001-auth.json`:

```json
{
    "solution_id": "sol-001",
    "title": "User Authentication Service",
    "description": "REST API for user authentication with OAuth2 flow. Supports login, logout, password reset, and MFA via TOTP. Returns JWT access and refresh tokens. Rate-limited to 50 login attempts per hour per IP.",
    "tech_stack": "Python, FastAPI, PostgreSQL, Redis",
    "domain": "authentication",
    "complexity": "medium",
    "test_scripts": [
        {
            "test_name": "test_login_valid_credentials",
            "test_type": "positive",
            "description": "Verify login with valid username/password returns 200 with JWT",
            "test_code": "@pytest.mark.positive\ndef test_login_valid_credentials(client, test_user):\n    \"\"\"Valid credentials should return access and refresh tokens.\"\"\"\n    resp = client.post('/auth/login', json={\n        'username': test_user.username,\n        'password': 'ValidPass123!'\n    })\n    assert resp.status_code == 200\n    data = resp.json()\n    assert 'access_token' in data\n    assert 'refresh_token' in data\n    assert data['token_type'] == 'bearer'",
            "framework": "pytest"
        },
        {
            "test_name": "test_login_wrong_password",
            "test_type": "negative",
            "description": "Login with incorrect password returns 401 Unauthorized",
            "test_code": "@pytest.mark.negative\ndef test_login_wrong_password(client, test_user):\n    \"\"\"Wrong password should return 401 with error detail.\"\"\"\n    resp = client.post('/auth/login', json={\n        'username': test_user.username,\n        'password': 'WrongPass'\n    })\n    assert resp.status_code == 401\n    assert 'Invalid credentials' in resp.json()['detail']",
            "framework": "pytest"
        },
        {
            "test_name": "test_login_rate_limit_exceeded",
            "test_type": "negative",
            "description": "Exceeding 50 attempts/hour returns 429 Too Many Requests",
            "test_code": "@pytest.mark.negative\ndef test_login_rate_limit_exceeded(client, test_user):\n    \"\"\"Rate limit should trigger after 50 failed attempts.\"\"\"\n    for _ in range(51):\n        client.post('/auth/login', json={\n            'username': test_user.username,\n            'password': 'wrong'\n        })\n    resp = client.post('/auth/login', json={\n        'username': test_user.username,\n        'password': 'ValidPass123!'\n    })\n    assert resp.status_code == 429",
            "framework": "pytest"
        },
        {
            "test_name": "test_login_still_works_after_mfa_rollout",
            "test_type": "regression",
            "description": "Basic login flow unaffected by MFA feature addition",
            "test_code": "@pytest.mark.regression\ndef test_login_still_works_after_mfa_rollout(client, user_without_mfa):\n    \"\"\"Users without MFA enabled should login normally.\"\"\"\n    resp = client.post('/auth/login', json={\n        'username': user_without_mfa.username,\n        'password': 'ValidPass123!'\n    })\n    assert resp.status_code == 200\n    assert 'mfa_required' not in resp.json()",
            "framework": "pytest"
        }
    ]
}
```

Create 3-5 similar files for different domains (payments, inventory, notifications, etc.) to give the Retriever enough variety.

---

## 6. Running It — Step by Step

```bash
# 1. Setup environment
cd test-script-generator
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Set credentials
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_DEFAULT_REGION="us-east-1"
export PINECONE_API_KEY="..."

# 3. Create index and load past data
python ingest.py

# 4. Run the pipeline
python pipeline.py

# 5. Check the output
ls generated_tests/
# → test_positive.py  test_negative.py  test_regression.py

# 6. Verify tests are syntactically valid
python -m py_compile generated_tests/test_positive.py
python -m py_compile generated_tests/test_negative.py
python -m py_compile generated_tests/test_regression.py
```

---

## 7. Two-Week Sprint Plan

### Week 1 — Build the Foundation

| Day | Focus | Deliverable |
|---|---|---|
| **Mon** | Environment setup. AWS credentials, Bedrock model access (enable Nova Lite + Titan Embeddings in `us-east-1`), Pinecone free account, install deps. Run a hello-world Strands agent. | `test_bedrock.py` works |
| **Tue** | Write 3-5 sample past solutions as JSON files with their test scripts. Run `ingest.py`. Verify data appears in Pinecone console. | Pinecone populated |
| **Wed** | Build `shared.py` and `agents/retriever.py`. Test retriever in isolation — give it a solution description, verify it finds correct matches and fetches test scripts. | Retriever working |
| **Thu** | Build `agents/analyzer.py`. Test in isolation — give it a solution + retrieved context, verify it produces a structured 3-section test plan. | Analyzer working |
| **Fri** | Build `agents/generator.py` and `pipeline.py`. Wire everything into GraphBuilder. Run end-to-end for the first time. | Full pipeline runs |

### Week 2 — Integrate, Test, Polish

| Day | Focus | Deliverable |
|---|---|---|
| **Mon** | Test with 3 different solution inputs. Review generated test quality. Fix obvious prompt issues. | 3 successful runs |
| **Tue** | Prompt tuning. Typical fixes: add explicit output formatting, add few-shot examples inside system prompts, adjust temperature. | Higher quality output |
| **Wed** | Add more past solutions (aim for 8-10 total). Test retrieval accuracy. Does it find the RIGHT similar solutions? | Better retrieval |
| **Thu** | Edge cases: test with solution that has NO similar past work; test with very short vs very long descriptions. Add try/except around API calls. | Error handling done |
| **Fri** | Final testing, write README, clean up code. Run `py_compile` on all generated outputs. Document any assumptions. | Project complete |

---

## 8. Cost Estimate

| Component | 2-Week Development Cost |
|---|---|
| **Bedrock Nova Lite** (~500 agent calls) | ~$2–5 |
| **Bedrock Titan Embeddings** (~2000 embeddings) | ~$0.04 |
| **Pinecone** (free tier, 1 index, up to ~100K vectors) | $0 |
| **Total** | **~$2–5** |

Nova Lite costs $0.06 per million input tokens and $0.24 per million output tokens. A typical pipeline run uses maybe 5,000–10,000 tokens across all 3 agents, costing less than $0.01 per run.

---

## 9. Learning Resources — Prioritized Reading Order

### Day 1: Start Here

| Resource | Why | Link |
|---|---|---|
| **Strands Python Quickstart** | First agent running in 5 min | [strandsagents.com/.../quickstart/python](https://strandsagents.com/docs/user-guide/quickstart/python/) |
| **Video: "Build Your First AI Agent with Strands"** | Visual walkthrough, ~20 min | [youtube.com/watch?v=PH9nvIFA2v4](https://www.youtube.com/watch?v=PH9nvIFA2v4) |
| **Pinecone Getting Started** | Create your first index | [docs.pinecone.io/.../overview](https://docs.pinecone.io/guides/get-started/overview) |

### Day 2–3: Build Your Tools

| Resource | Why | Link |
|---|---|---|
| **Creating Custom Tools** | The `@tool` decorator deep dive | [strandsagents.com/.../custom-tools](https://strandsagents.com/docs/user-guide/concepts/tools/custom-tools/) |
| **Video: "Strands Tools: Building Custom AI Agents"** | Hands-on tool creation | [youtube.com/watch?v=EGhIZCfOvG4](https://www.youtube.com/watch?v=EGhIZCfOvG4) |
| **Amazon Bedrock Provider Docs** | BedrockModel config, regions, caching | [strandsagents.com/.../amazon-bedrock](https://strandsagents.com/docs/user-guide/concepts/model-providers/amazon-bedrock/) |

### Day 4–5: Multi-Agent Patterns

| Resource | Why | Link |
|---|---|---|
| **Multi-Agent Patterns Overview** | Compare Graph vs Swarm vs Workflow | [strandsagents.com/.../multi-agent-patterns](https://strandsagents.com/docs/user-guide/concepts/multi-agent/multi-agent-patterns/) |
| **Graph Multi-Agent Pattern** | GraphBuilder API reference | [strandsagents.com/.../graph](https://strandsagents.com/docs/user-guide/concepts/multi-agent/graph/) |
| **Blog: "Strands Multi-Agent Systems: Graph"** | Step-by-step GraphBuilder tutorial | [dev.to/aws/strands-multi-agent-systems-graph-4h7f](https://dev.to/aws/strands-multi-agent-systems-graph-4h7f) |
| **Video: "Agents as Tools — Multi AI Agent Systems"** | Multi-agent orchestration patterns | [youtube.com/watch?v=dn3G9jvB98k](https://www.youtube.com/watch?v=dn3G9jvB98k) |

### Week 2: Go Deeper

| Resource | Why | Link |
|---|---|---|
| **AWS Blog: Advanced Orchestration** | GraphBuilder for ReWOO + Reflexion patterns | [aws.amazon.com/blogs/.../customize-agent-workflows...](https://aws.amazon.com/blogs/machine-learning/customize-agent-workflows-with-advanced-orchestration-techniques-using-strands-agents/) |
| **AWS Blog: Multi-Agent Collaboration Patterns** | All 4 patterns compared with code | [aws.amazon.com/blogs/.../multi-agent-collaboration...](https://aws.amazon.com/blogs/machine-learning/multi-agent-collaboration-patterns-with-strands-agents-and-amazon-nova/) |
| **AWS Blog: Strands 1.0 Announcement** | Graph, Swarm, A2A, session management | [aws.amazon.com/blogs/.../strands-agents-1-0...](https://aws.amazon.com/blogs/opensource/introducing-strands-agents-1-0-production-ready-multi-agent-orchestration-made-simple/) |
| **Strands Samples Repository** | 40+ runnable examples by category | [github.com/strands-agents/samples](https://github.com/strands-agents/samples) |
| **Strands Prompt Engineering Guide** | Writing effective system prompts | [strandsagents.com/.../prompt-engineering](https://strandsagents.com/docs/user-guide/safety-security/prompt-engineering/) |
| **Video: "Open-Source Multi-Agent Framework"** | Deeper multi-agent walkthrough | [youtube.com/watch?v=Z6urONR1b2s](https://www.youtube.com/watch?v=Z6urONR1b2s) |
| **Video: "Building Intelligent Agents with Strands"** | Comprehensive hands-on guide | [youtube.com/watch?v=TD2ihEBkdkY](https://www.youtube.com/watch?v=TD2ihEBkdkY) |
| **Amazon Bedrock Pricing** | Verify current token costs | [aws.amazon.com/bedrock/pricing](https://aws.amazon.com/bedrock/pricing/) |

### Community & Blogs

| Resource | Why | Link |
|---|---|---|
| **"Agentic Design — Strands SDK Part 1"** | Multi-part deep tutorial on tools + workflows | [thebigdataguy.substack.com/p/agentic-design-strands-sdk-part-1](https://thebigdataguy.substack.com/p/agentic-design-strands-sdk-part-1) |
| **"Strands SDK Masterclass: Custom Tools"** | Financial tools, API integration examples | [blog.dataopslabs.com/.../building-custom-tools](https://blog.dataopslabs.com/aws-strands-sdk-masterclass-building-custom-tools) |
| **GraphBuilder Walkthrough (shinyaz.com)** | Sequential, parallel, and conditional graph examples | [shinyaz.com/en/blog/2026/04/11/strands-agents-graph](https://shinyaz.com/en/blog/2026/04/11/strands-agents-graph) |
| **Tutorials Dojo: AWS Strands Agents** | Comprehensive cheat-sheet reference | [tutorialsdojo.com/aws-strands-agents](https://tutorialsdojo.com/aws-strands-agents/) |
| **Strands Discord** | Real-time community help | Linked from [strandsagents.com/blog](https://strandsagents.com/blog/) |

---

## 10. Practical Tips for Your 2 Weeks

**Start each agent in isolation.** Don't wire up the GraphBuilder until each agent produces good output on its own. The most common problem is prompt quality, not code bugs.

**Use `callback_handler=None`** on the Retriever and Analyzer agents. This suppresses streaming output so only the final Generator streams to the terminal.

**Test scripts don't need to pass — they need to be valid.** Your success metric for week 1 is: generated files compile without syntax errors (`py_compile`). Quality improvements happen in week 2 via prompt tuning.

**3-5 past solutions is enough to start.** Don't spend days preparing data. Get the pipeline end-to-end, then add more solutions as you discover what the Retriever needs.

**Pin your model.** Use `us.amazon.nova-lite-v1:0` everywhere during development. If you want better code generation quality later, upgrade only the Generator agent to Nova Pro (`us.amazon.nova-pro-v1:0` at $0.80/$3.20 per 1M tokens).

**Save every run.** Timestamp your output directories so you can compare improvements:
```python
from datetime import datetime
out_dir = Path(f"generated_tests/{datetime.now():%Y%m%d_%H%M%S}")
```

**The GraphBuilder is your friend.** It gives you `result.execution_time`, per-node status, and token usage — use these to debug slowness or quality issues. If the Retriever is returning garbage, the Analyzer and Generator can't compensate.
