from flask import Flask, render_template, request, redirect, session, url_for, jsonify
from flask_login import login_required, current_user, LoginManager, login_user, logout_user
from sqlalchemy import func
from authlib.integrations.flask_client import OAuth
from datetime import timedelta, datetime
from collections import defaultdict
import re, os, json
from google.oauth2 import service_account
import vertexai
from vertexai import agent_engines
from dotenv import load_dotenv
import markdown
from flask_migrate import Migrate
from calendar import monthrange
from werkzeug.middleware.proxy_fix import ProxyFix

# Local import
from models import db, Contact, Invoice, Revenue, Interaction, Expense, User, Product, InvoiceLineItem, Report, ExpenseCategory

load_dotenv()
# Flask setup
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PREFERRED_URL_SCHEME'] = 'https'
db.init_app(app)
migrate = Migrate(app, db)
# OAuth & Login setup
oauth = OAuth(app)

app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["SESSION_COOKIE_SECURE"] = True  # ensures session cookie is sent over HTTPS
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

login_manager = LoginManager()
login_manager.login_view = 'login_page'
login_manager.init_app(app)


print("Starting Flask app...")
print(f"Loaded SECRET_KEY: {os.getenv('SECRET_KEY')}")
print(f"Loaded DB URL: {os.getenv('DATABASE_URL')}")
print(f"Loaded VERTEX_AGENT_ID: {os.getenv('VERTEX_AGENT_ID')}")

# Google OAuth config
google = oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    access_token_url='https://oauth2.googleapis.com/token',
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    api_base_url='https://www.googleapis.com/oauth2/v2/',
    userinfo_endpoint='https://www.googleapis.com/oauth2/v2/userinfo',
    client_kwargs={'scope': 'email profile'},
)

cred_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")

if cred_json:
    # Convert the JSON string to a dict
    cred_dict = json.loads(cred_json)
    # Create credentials object
    credentials = service_account.Credentials.from_service_account_info(cred_dict)
else:
    credentials = None


vertexai.init(
    project=os.getenv("GOOGLE_CLOUD_PROJECT"),
    location=os.getenv("GOOGLE_CLOUD_REGION"),
    staging_bucket=os.getenv("GOOGLE_CLOUD_BUCKET"),
    credentials=credentials
)

#print(os.getenv("VERTEX_AGENT_ID"))
print("Initializing Vertex AI agent...")
AGENT = agent_engines.get(os.getenv("VERTEX_AGENT_ID"))
print("Vertex AI agent initialized.")

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route("/", methods=["GET"])
@login_required
def index():
    # ─── 1) Load all dashboard data ──────────────────────────────────────────
    user_id = current_user.id

    today = datetime.today()
    start_of_month = today.replace(day=1)
    last_day = monthrange(today.year, today.month)[1]
    end_of_month = today.replace(day=last_day, hour=23, minute=59, second=59)
  

    contacts         = Contact.query.filter_by(user_id=user_id).all()
    invoices         = Invoice.query.filter_by(user_id=user_id).all()
    products         = Product.query.filter_by(user_id=user_id).all()
    interactions     = (
        Interaction.query
        .filter_by(user_id=user_id)
        .order_by(Interaction.date.desc())
        .limit(10)
        .all()
    )

    category_expenses = (
        db.session.query(ExpenseCategory.name, func.sum(Expense.amount).label("total"))
        .join(Expense)
        .filter(Expense.user_id == user_id)
        .group_by(ExpenseCategory.name)
        .order_by(func.sum(Expense.amount).desc())
        .all()
    )

# ─── Total Revenue ───────────────────────────────
    total_revenue = (
        db.session.query(func.sum(Revenue.amount))
        .filter(Revenue.user_id == user_id)   # ✅ direct filter
        .scalar() or 0
    )

    # ─── Monthly Revenue ─────────────────────────────

    monthly_revenue = (
        db.session.query(func.sum(Revenue.amount))
        .filter(
            Revenue.user_id == user_id,
            Revenue.date >= start_of_month,
            Revenue.date <= end_of_month
        )
        .scalar() or 0
    )

    total_expenses   = (
        db.session.query(func.sum(Expense.amount))
        .filter_by(user_id=user_id)
        .scalar() or 0
    )

    monthly_expenses = (
        db.session.query(func.sum(Expense.amount))
        .filter(
            Expense.user_id == user_id,
            Expense.date >= start_of_month,
            Expense.date <= end_of_month
        )
        .scalar() or 0
    )

    reports = (
         Report.query
        .filter_by(user_id=user_id)
        .order_by(Report.created_at.desc())
        .limit(20)  # or more, or all
        .all()
    )

    # prepare chart data
    expense_labels = [name for name, _ in category_expenses]
    expense_values = [total for _, total in category_expenses]

    now    = datetime.now()
    months = [(now.replace(day=1) - timedelta(days=30*i)).replace(day=1)
              for i in range(3, -1, -1)]
    month_keys     = [m.strftime("%Y-%m") for m in months]
    revenues       = (
        Revenue.query.join(Invoice)
        .filter(Invoice.user_id == user_id, Revenue.date >= months[0])
        .all()
    )
    revenue_totals = defaultdict(float)
    for rev in revenues:
        revenue_totals[rev.date.strftime("%Y-%m")] += rev.amount
    revenue_labels = [m.strftime("%B") for m in months]
    revenue_values = [revenue_totals.get(k, 0) for k in month_keys]

    start_of_week  = datetime.now() - timedelta(days=datetime.now().weekday())


    # ─── 3) Render the dashboard ──────────────────────────────────────────────
    return render_template(
        "index.html",
        contacts=contacts,
        invoices=invoices,
        total_revenue=total_revenue,
        monthly_revenue=monthly_revenue,
        interactions=interactions,
        products=products,
        start=start_of_week,
        timedelta=timedelta,
        total_expenses=total_expenses,
        monthly_expenses=monthly_expenses,
        category_expenses=category_expenses,
        expense_labels=expense_labels,
        expense_values=expense_values,
        revenue_labels=revenue_labels,
        revenue_values=revenue_values,
        reports=reports
    )



def fix_markdown_tables(markdown_text: str) -> str:
    """
    Detects malformed markdown tables and reformats them into valid GitHub-Flavored Markdown.
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

        # Strip and split each row
        cleaned = [
            re.split(r'\s*\|\s*', row.strip('| '))
            for row in table_rows
            if row.strip().count('|') >= 2  # Skip rows with not enough columns
        ]
        max_cols = max(len(row) for row in cleaned)
        # Pad all rows to the same length
        padded = [row + [''] * (max_cols - len(row)) for row in cleaned]

        # Header and separator
        fixed_lines.append("| " + " | ".join(padded[0]) + " |")
        fixed_lines.append("| " + " | ".join(["---"] * max_cols) + " |")

        for row in padded[1:]:
            fixed_lines.append("| " + " | ".join(row) + " |")

        table_rows = []

    for line in lines:
        if re.match(r'^\s*\|.*\|\s*$', line):
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

@app.route("/chat", methods=["POST"])
@login_required
def chat():
    user_message = request.json.get("message", "").strip()
    if not user_message:
        return jsonify(error="Empty message"), 400

    sess_id = current_user.ai_session_id

    text_parts = []

    # ✅ This loop is for Vertex deployed AgentEngine
    for event in AGENT.stream_query(
        user_id=current_user.email,   # MUST match the user_id used for .create_session()
        session_id=sess_id,
        message=user_message
    ):
        print("DEBUG event:", event)  # So you can inspect the shape

    content = event.get("content")
    if content:
        for part in content.get("parts", []):
            # ✅ 1) Plain text parts
            if "text" in part:
                text_parts.append(part["text"].strip())

            # ✅ 2) Function response parts
            elif "function_response" in part:
                func_resp = part["function_response"]
                response_data = func_resp.get("response")
                if response_data:
                    # If your function sets a friendly message, use it
                    message = response_data.get("message")
                    if message:
                        text_parts.append(message.strip())
                    else:
                        # Or fallback to pretty-print the whole JSON
                        text_parts.append(str(response_data))

    assistant_reply = " ".join(text_parts).strip() or "❌ No response."
    assistant_reply = fix_markdown_tables(assistant_reply)
    html_reply = markdown.markdown(assistant_reply, extensions=['tables'])
    return jsonify(user=user_message, assistant=html_reply)


@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/login/google")
def login_with_google():
    redirect_uri = url_for('auth_callback', _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/auth/callback")
def auth_callback():
    token = google.authorize_access_token()
    user_info = google.get("userinfo").json()
    email, name = user_info["email"], user_info["name"]

    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, name=name)
        db.session.add(user)
        db.session.commit()

    # 💡 Reload the user so SQLAlchemy doesn't have a stale object
    db.session.refresh(user)

    # ✅ Use only this int version
    safe_user_id = int(user.id)
    print(f"DEBUG safe_user_id: {safe_user_id} ({type(safe_user_id)})")

    initial_state = {
        "user_id": int(safe_user_id),  # <-- FORCED int, no float!
        "user_name": str(user.name)  # Always string
    }

    print(f"DEBUG state about to send: {initial_state}")

    agent_session = AGENT.create_session(
        user_id=email,
        
        state={"user_id": user.id}
    )

    user.ai_session_id = agent_session['id']
    db.session.commit()

    login_user(user)
    session["user_id"] = safe_user_id
    session["user_name"] = user.name

    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    logout_user()
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
