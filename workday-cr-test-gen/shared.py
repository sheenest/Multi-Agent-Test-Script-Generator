"""
Shared configuration, embedding helper, and Pinecone client.
Used by all agents and the ingestion pipeline.
"""

import json
import os
import sys
import boto3
from dotenv import load_dotenv
from pinecone import Pinecone
from strands.models import BedrockModel
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent)
if PROJECT_ROOT not in sys.path:
        sys.path.insert(0,PROJECT_ROOT)

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────
BEDROCK_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
INDEX_NAME = os.environ.get("PINECONE_INDEX_NAME", "workday-cr-tests")
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
LLM_MODEL = os.environ.get("LLM_MODEL_ID", "us.meta.llama4-scout-17b-instruct-v1:0")

# print("LLM_Model")
# print(LLM_MODEL)

# ── Clients ────────────────────────────────────────────────────────────────
bedrock_runtime = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)

# Retrieve API key from environment variable
api_key = os.environ.get("PINECONE_API_KEY")

# Initialize Pinecone client
pc = Pinecone(api_key=api_key)

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
    # response = bedrock_runtime.invoke_model(
    #     modelId=EMBEDDING_MODEL,
    #     body=body,
    #     contentType="application/json",
    #     accept="application/json",
    # )

    response = bedrock_runtime.invoke_model(
        modelId=EMBEDDING_MODEL,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(response["body"].read())["embedding"]

def get_model():
    return BedrockModel (
        model_id = LLM_MODEL,
        region_name = BEDROCK_REGION,
        streaming = False,
        include_tool_result_status=False,
    )
