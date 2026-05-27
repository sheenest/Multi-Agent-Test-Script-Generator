# Python Environment Setup Guide

## Multi-Agent Test Script Generator — Strands SDK + Bedrock + Pinecone

---

## 1. Prerequisites

Before anything else, confirm you have **Python 3.10 or higher** installed. Both the Strands SDK and the Pinecone SDK require it.

### Check Your Python Version

```bash
python --version
# or, on some systems:
python3 --version
```

You should see `Python 3.10.x`, `3.11.x`, `3.12.x`, or `3.13.x`. If you're below 3.10, you'll need to upgrade before continuing.

### Installing Python (If Needed)

**macOS:**
```bash
# Using Homebrew (recommended)
brew install python@3.12
```

**Ubuntu / Debian:**
```bash
sudo apt update
sudo apt install python3.12 python3.12-venv python3.12-dev
```

**Windows:**

Download the installer from [python.org/downloads](https://www.python.org/downloads/). During installation, check the box that says **"Add Python to PATH"**.

### Verify pip Is Available

```bash
pip --version
# or:
pip3 --version
```

If pip isn't found, install it:
```bash
python -m ensurepip --upgrade
```

---

## 2. Create the Project Directory

```bash
mkdir test-script-generator
cd test-script-generator
```

Create the full folder structure the project expects:

```bash
mkdir -p agents data/solutions generated_tests
touch agents/__init__.py
```

Your directory should now look like this:

```
test-script-generator/
├── agents/
│   └── __init__.py
├── data/
│   └── solutions/
└── generated_tests/
```

---

## 3. Create and Activate the Virtual Environment

A virtual environment isolates your project's dependencies from your system Python and other projects. This prevents version conflicts and keeps things clean.

### Create It

```bash
python -m venv .venv
```

This creates a `.venv/` folder inside your project containing a self-contained Python installation.

### Activate It

**macOS / Linux:**
```bash
source .venv/bin/activate
```

**Windows (Command Prompt):**
```cmd
.venv\Scripts\activate
```

**Windows (PowerShell):**
```powershell
.venv\Scripts\Activate.ps1
```

After activation, your terminal prompt will change to show `(.venv)` at the beginning, confirming you're inside the virtual environment:

```
(.venv) ~/test-script-generator $
```

### Verify You're Using the Right Python

```bash
which python        # macOS/Linux — should point to .venv/bin/python
where python        # Windows — should point to .venv\Scripts\python.exe
python --version    # confirm it's 3.10+
```

---

## 4. The Requirements File

Create a file called `requirements.txt` in your project root with the following contents:

```txt
# =============================================================================
# Multi-Agent Test Script Generator — Python Dependencies
# =============================================================================
# Install with:  pip install -r requirements.txt
# Python:        >= 3.10 required
# =============================================================================

# ── Strands Agents SDK ──────────────────────────────────────────────────────
# Core SDK: agent loop, model providers, tool system, GraphBuilder
strands-agents>=1.30

# Community tools package: pre-built tools (calculator, file ops, etc.)
# Not strictly required for this project since we write custom tools,
# but useful for experimentation and future expansion.
strands-agents-tools

# ── Amazon Bedrock (AWS) ────────────────────────────────────────────────────
# AWS SDK for Python — used for Bedrock runtime calls (embeddings via Titan)
# and pulled in automatically by strands-agents, but pinned here for clarity.
boto3>=1.35

# AWS CLI — for configuring credentials (aws configure)
awscli

# ── Pinecone Vector Database ────────────────────────────────────────────────
# Official Pinecone Python SDK (v9+)
# Note: the package name is "pinecone", NOT "pinecone-client" (renamed in v5.1)
pinecone>=9.0

# ── Utilities ───────────────────────────────────────────────────────────────
# Data validation / structured output (used by Strands internally, useful
# if you want to add structured output to your agents later)
pydantic>=2.0

# Pretty terminal output for development / debugging
rich

# Load environment variables from a .env file (optional but recommended)
python-dotenv
```

### Install Everything

```bash
pip install -r requirements.txt
```

This will take 1–2 minutes on first run. You'll see pip resolving dependencies and downloading packages.

### Verify the Installation

Run these quick checks to confirm everything installed correctly:

```bash
# Check Strands SDK
python -c "import strands; print('Strands SDK OK')"

# Check Strands can import BedrockModel and GraphBuilder
python -c "from strands.models import BedrockModel; from strands.multiagent import GraphBuilder; print('Strands imports OK')"

# Check Pinecone SDK
python -c "from pinecone import Pinecone; print('Pinecone SDK OK')"

# Check boto3 (AWS SDK)
python -c "import boto3; print(f'boto3 OK — version {boto3.__version__}')"
```

All four should print without errors. If any fail, see the Troubleshooting section below.

---

## 5. Configure Environment Variables

Your project needs three sets of credentials: AWS (for Bedrock), Pinecone, and optionally some project config. The cleanest approach is a `.env` file.

### Create the `.env` File

```bash
touch .env
```

Add the following (replace with your actual values):

```env
# =============================================================================
# AWS Credentials (for Amazon Bedrock)
# =============================================================================
# Option A: Set these directly
AWS_ACCESS_KEY_ID=your-access-key-id-here
AWS_SECRET_ACCESS_KEY=your-secret-access-key-here
AWS_DEFAULT_REGION=us-east-1

# Option B: If you've already run 'aws configure', you can skip the above.
# Boto3 will automatically read from ~/.aws/credentials.

# =============================================================================
# Pinecone
# =============================================================================
PINECONE_API_KEY=your-pinecone-api-key-here

# =============================================================================
# Project Configuration (optional)
# =============================================================================
# Which Bedrock model to use for agents (Amazon Nova Lite is cheap and capable)
LLM_MODEL_ID=us.amazon.nova-lite-v1:0
EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0
PINECONE_INDEX_NAME=test-generator
```

### Add `.env` to `.gitignore`

Never commit credentials to version control:

```bash
echo ".env" >> .gitignore
echo ".venv/" >> .gitignore
echo "generated_tests/" >> .gitignore
echo "__pycache__/" >> .gitignore
```

### Loading the `.env` File

Add this at the top of any script that needs credentials (or add it to `shared.py`):

```python
from dotenv import load_dotenv
load_dotenv()  # reads .env file into os.environ
```

Alternatively, if you prefer not to use `python-dotenv`, just export the variables in your terminal before running:

```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_DEFAULT_REGION="us-east-1"
export PINECONE_API_KEY="..."
```

---

## 6. Run the Smoke Test

Create a file called `test_setup.py` to verify the full stack is wired up:

```python
#!/usr/bin/env python
"""Smoke test: verify all dependencies and credentials are working."""

import sys
from dotenv import load_dotenv

load_dotenv()


def check_python():
    v = sys.version_info
    assert v.major == 3 and v.minor >= 10, f"Python 3.10+ required, got {v.major}.{v.minor}"
    print(f"  ✓ Python {v.major}.{v.minor}.{v.micro}")


def check_strands():
    from strands import Agent
    from strands.models import BedrockModel
    from strands.multiagent import GraphBuilder
    print("  ✓ Strands SDK (Agent, BedrockModel, GraphBuilder)")


def check_pinecone():
    import os
    from pinecone import Pinecone

    api_key = os.environ.get("PINECONE_API_KEY")
    if not api_key:
        print("  ⚠ Pinecone SDK installed but PINECONE_API_KEY not set")
        return

    pc = Pinecone()
    indexes = [idx.name for idx in pc.list_indexes()]
    print(f"  ✓ Pinecone connected — {len(indexes)} index(es) found")


def check_aws():
    import boto3

    try:
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        account = identity["Account"]
        print(f"  ✓ AWS credentials valid — account {account}")
    except Exception as e:
        print(f"  ⚠ AWS credentials issue: {e}")


def check_bedrock():
    import boto3
    import os

    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    try:
        client = boto3.client("bedrock", region_name=region)
        response = client.list_foundation_models(byOutputModality="TEXT")
        model_count = len(response.get("modelSummaries", []))
        print(f"  ✓ Bedrock access OK — {model_count} text models available in {region}")
    except Exception as e:
        print(f"  ⚠ Bedrock access issue: {e}")


def check_bedrock_agent():
    """Try running a minimal Strands agent on Bedrock."""
    import os
    from strands import Agent
    from strands.models import BedrockModel

    model_id = os.environ.get("LLM_MODEL_ID", "us.amazon.nova-lite-v1:0")
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

    try:
        model = BedrockModel(model_id=model_id, region_name=region)
        agent = Agent(model=model, callback_handler=None)
        result = agent("Reply with exactly: SETUP_OK")
        text = str(result)
        if "SETUP_OK" in text.upper() or len(text) > 0:
            print(f"  ✓ Bedrock agent test passed (model: {model_id})")
        else:
            print(f"  ⚠ Agent responded but output unexpected: {text[:100]}")
    except Exception as e:
        print(f"  ⚠ Bedrock agent test failed: {e}")


if __name__ == "__main__":
    print("\n" + "=" * 55)
    print("  ENVIRONMENT SETUP — SMOKE TEST")
    print("=" * 55 + "\n")

    checks = [
        ("Python Version", check_python),
        ("Strands SDK", check_strands),
        ("Pinecone SDK", check_pinecone),
        ("AWS Credentials", check_aws),
        ("Bedrock Access", check_bedrock),
        ("Bedrock Agent (live)", check_bedrock_agent),
    ]

    for name, fn in checks:
        print(f"[{name}]")
        try:
            fn()
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
        print()

    print("=" * 55)
    print("  If all checks show ✓, you're ready to build!")
    print("=" * 55 + "\n")
```

### Run It

```bash
python test_setup.py
```

Expected output when everything is configured:

```
=======================================================
  ENVIRONMENT SETUP — SMOKE TEST
=======================================================

[Python Version]
  ✓ Python 3.12.4

[Strands SDK]
  ✓ Strands SDK (Agent, BedrockModel, GraphBuilder)

[Pinecone SDK]
  ✓ Pinecone connected — 0 index(es) found

[AWS Credentials]
  ✓ AWS credentials valid — account 123456789012

[Bedrock Access]
  ✓ Bedrock access OK — 47 text models available in us-east-1

[Bedrock Agent (live)]
  ✓ Bedrock agent test passed (model: us.amazon.nova-lite-v1:0)

=======================================================
  If all checks show ✓, you're ready to build!
=======================================================
```

---

## 7. Quick Reference — Daily Commands

Commands you'll use every day during the 2-week sprint:

```bash
# Activate the virtual environment (do this every time you open a new terminal)
source .venv/bin/activate          # macOS/Linux
.venv\Scripts\activate.bat         # Windows CMD
.venv\Scripts\Activate.ps1         # Windows PowerShell

# Install a new package and add it to requirements.txt
pip install some-package
pip freeze | grep some-package >> requirements.txt

# Recreate the environment from scratch (if something breaks)
deactivate
rm -rf .venv
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Check what's installed
pip list

# Deactivate when done
deactivate
```

---

## 8. Troubleshooting

### "No module named 'strands'"
You're not inside the virtual environment. Run `source .venv/bin/activate` first.

### "ModuleNotFoundError: No module named 'pinecone'" after installing
Make sure you installed `pinecone` (not `pinecone-client`). The package was renamed:
```bash
pip uninstall pinecone-client    # remove old name if present
pip install pinecone             # install current name
```

### "botocore.exceptions.NoCredentialsError"
AWS credentials aren't configured. Either set environment variables, run `aws configure`, or populate the `.env` file.

### "AccessDeniedException" from Bedrock
You need to enable model access in the Bedrock console. Go to [Amazon Bedrock Console](https://console.aws.amazon.com/bedrock/) → **Model access** → **Manage model access** → enable **Amazon Nova Lite** and **Amazon Titan Text Embeddings V2**.

### "Could not resolve host: api.pinecone.io"
Check your internet connection. If you're behind a corporate proxy, set `HTTPS_PROXY`:
```bash
export HTTPS_PROXY="http://your-proxy:port"
```

### pip install hangs or is very slow
Try upgrading pip itself:
```bash
pip install --upgrade pip
```
Or use `uv` as a faster alternative to pip:
```bash
pip install uv
uv pip install -r requirements.txt
```

### Conflicts between packages
If you get dependency resolution errors, try installing with `--no-deps` and then fixing manually:
```bash
pip install -r requirements.txt --no-deps
pip check    # shows any broken dependencies
```

---

## 9. Final Project File Tree

After completing this setup guide, your project should look like this:

```
test-script-generator/
├── .venv/                         ← virtual environment (do NOT commit)
├── .env                           ← credentials (do NOT commit)
├── .gitignore                     ← excludes .venv, .env, __pycache__, etc.
├── requirements.txt               ← all Python dependencies
├── test_setup.py                  ← smoke test script
├── agents/
│   ├── __init__.py
│   ├── retriever.py               ← (build in Week 1, Day 3)
│   ├── analyzer.py                ← (build in Week 1, Day 4)
│   └── generator.py               ← (build in Week 1, Day 5)
├── data/
│   └── solutions/                 ← past solutions as JSON files
│       └── (add .json files here)
├── generated_tests/               ← output directory for generated tests
├── shared.py                      ← (build in Week 1, Day 3)
├── ingest.py                      ← (build in Week 1, Day 2)
└── pipeline.py                    ← (build in Week 1, Day 5)
```

You're now ready to start building. Head to the system design guide and begin with Day 1 of the sprint plan.
