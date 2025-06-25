# services/vertex_agent.py

import os
from dotenv import load_dotenv
import vertexai
from vertexai.preview import reasoning_engines

load_dotenv()

# Initialize Vertex AI with your project and region
vertexai.init(
    project=os.getenv("GOOGLE_CLOUD_PROJECT"),
    location=os.getenv("GOOGLE_CLOUD_REGION", "us-central1")
)

AGENT_RESOURCE_ID = os.getenv(
    "VERTEX_AGENT_RESOURCE_ID",
    "projects/330581348284/locations/us-central1/reasoningEngines/6261595574882009088"
)

# In-memory session store (swap with Redis/db in production)
user_sessions = {}

def get_user_session(user_id: str):
    """Get or create a Vertex Reasoning Engine session for a user."""
    if user_id not in user_sessions:
        session = reasoning_engines.create_user_session(
            agent_resource_name=AGENT_RESOURCE_ID,
            user_id=user_id
        )
        user_sessions[user_id] = session
    return user_sessions[user_id]

def get_agent_response(message: str, user_id: str) -> str:
    """Send user message to the Vertex agent and return the response text."""
    session = get_user_session(user_id)
    response = session.send_message(message)
    return response.text
