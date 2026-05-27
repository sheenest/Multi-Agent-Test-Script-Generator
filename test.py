# Check Strands SDK
python -c "import strands; print('Strands SDK OK')"

# Check Strands can import BedrockModel and GraphBuilder
python -c "from strands.models import BedrockModel; from strands.multiagent import GraphBuilder; print('Strands imports OK')"

# Check Pinecone SDK
python -c "from pinecone import Pinecone; print('Pinecone SDK OK')"

# Check boto3 (AWS SDK)
python -c "import boto3; print(f'boto3 OK — version {boto3.__version__}')"
