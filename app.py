from flask import Flask, render_template, request, session, jsonify, url_for
from datetime import datetime, timedelta
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from google.oauth2 import service_account
import vertexai
from vertexai import agent_engines

import os, json, re
import markdown

load_dotenv()

# ── Flask basic setup (no DB, no login) ────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")  # demo-safe default
app.config["PREFERRED_URL_SCHEME"] = "https"
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

print("Starting Flask app (demo mode, no auth/DB)…")
print(f"Loaded VERTEX_AGENT_ID: {os.getenv('VERTEX_AGENT_ID')}")

# ── Vertex init ────────────────────────────────────────────────────────────────
cred_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
if cred_json:
    credentials = service_account.Credentials.from_service_account_info(json.loads(cred_json))
else:
    credentials = None

print(credentials)

vertexai.init(
    project=os.getenv("GOOGLE_CLOUD_PROJECT"),
    location=os.getenv("GOOGLE_CLOUD_REGION"),
    staging_bucket=os.getenv("GOOGLE_CLOUD_BUCKET"),
    credentials=credentials,
)

print("Initializing Vertex AI agent…")
AGENT = agent_engines.get(os.getenv("VERTEX_AGENT_ID"))
print("Vertex AI agent initialized.")

# Demo constants you requested
DEMO_USER_ID = "hnishat@hotmail.com"
DEMO_STATE = {"user_id": "1", "timezone": "America/Toronto"}

def get_or_create_agent_session_id() -> str:
    """
    Creates a Vertex Agent session lazily and caches the ID in the Flask session.
    Ensures the same user_id is used for .create_session() and .stream_query().
    """
    sess_id = session.get("ai_session_id")
    if not sess_id:
        agent_session = AGENT.create_session(
            user_id=DEMO_USER_ID,
            state=DEMO_STATE
        )
        sess_id = agent_session["id"]
        session["ai_session_id"] = sess_id
        # (optional) store a display name for header
        session["user_name"] = "Demo User"
        print(f"Created new Agent session: {sess_id}")
    return sess_id

# ── Helpers ───────────────────────────────────────────────────────────────────
def fix_markdown_tables(markdown_text: str) -> str:
    """
    Detects malformed markdown tables and reforms them into valid GFM.
    Ensures correct separator row and trims trailing pipes.
    """
    lines = markdown_text.split("\n")
    fixed_lines = []
    inside_table = False
    table_rows = []

    def flush_table():
        nonlocal fixed_lines, table_rows
        if not table_rows:
            return
        cleaned = [
            re.split(r"\s*\|\s*", row.strip("| "))
            for row in table_rows
            if row.strip().count("|") >= 2
        ]
        max_cols = max(len(row) for row in cleaned) if cleaned else 0
        padded = [row + [""] * (max_cols - len(row)) for row in cleaned]
        if padded:
            fixed_lines.append("| " + " | ".join(padded[0]) + " |")
            fixed_lines.append("| " + " | ".join(["---"] * max_cols) + " |")
            for row in padded[1:]:
                fixed_lines.append("| " + " | ".join(row) + " |")
        table_rows = []

    for line in lines:
        if re.match(r"^\s*\|.*\|\s*$", line):
            inside_table = True
            table_rows.append(line)
        else:
            if inside_table:
                flush_table()
                inside_table = False
            fixed_lines.append(line)
    if inside_table:
        flush_table()
    return "\n".join(fixed_lines)

FENCED_JSON = re.compile(r"```json\s*([\s\S]*?)```", re.IGNORECASE)

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    # Ensure we have a session ready so chatting works instantly
    get_or_create_agent_session_id()
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    DEMO_USER_ID = "hnishat@hotmail.com"
    DEMO_STATE = {"user_id": "1", "timezone": "America/Toronto"}

    user_message = (request.json.get("message") or "").strip()
    if not user_message:
        return jsonify(error="Empty message"), 400

    # ensure session exists and create engine session lazily
    sess_id = session.get("ai_session_id")
    if not sess_id:
        agent_session = AGENT.create_session(
            user_id=DEMO_USER_ID,
            state=DEMO_STATE
        )
        sess_id = agent_session["id"]
        session["ai_session_id"] = sess_id
        session["user_name"] = "Demo User"

    # Collect ONLY assistant text parts
    text_parts = []
    for event in AGENT.stream_query(
        user_id=DEMO_USER_ID,
        session_id=sess_id,
        message=user_message
    ):
        content = event.get("content")
        if not content:
            continue
        for part in content.get("parts", []):
            # ✅ Keep plain text from the model
            if "text" in part and isinstance(part["text"], str):
                txt = part["text"].strip()
                if txt:
                    text_parts.append(txt)
            # 🚫 Ignore function_response and other tool payloads
            # elif "function_response" in part:
            #     pass

    # Join only the model text
    assistant_reply = " ".join(text_parts).strip() or "No response."

    # Optional: strip fenced JSON/code blocks if the model includes them
    assistant_reply = re.sub(r"```(?:json|sql|text)?\s*[\s\S]*?```", "", assistant_reply, flags=re.IGNORECASE).strip()

    # Optional: collapse extremely long lines (safety for any residual dumps)
    if len(assistant_reply) > 6000:
        assistant_reply = assistant_reply[:6000] + "\n…"

    # Render Markdown → HTML (tables allowed)
    html_reply = markdown.markdown(assistant_reply, extensions=["tables"])
    return jsonify(user=user_message, assistant=html_reply)


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
