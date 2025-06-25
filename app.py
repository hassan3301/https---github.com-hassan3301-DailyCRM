from flask import Flask, render_template, request, redirect, session, make_response, url_for, jsonify
from services.vertex_agent import get_agent_response
import vertexai
from vertexai import agent_engines
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types
from flask_login import login_required, current_user, LoginManager, UserMixin, login_user, logout_user
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, extract
from models import db, Contact, Invoice, Revenue, Interaction, Event, Expense, User
from utils.email_utils import send_email
from authlib.integrations.flask_client import OAuth
import os
from datetime import timedelta, datetime
from dateutil import parser as dateparser
import json, re, ast
import calendar
from weasyprint import HTML
from collections import defaultdict
import uuid
import asyncio

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecret")

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
oauth = OAuth(app)

migrate = Migrate(app, db)

login_manager = LoginManager()
login_manager.login_view = 'login_page'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    """Given *user_id*, return the associated User object."""
    return User.query.get(int(user_id))

vertexai.init(
    project=os.getenv("GOOGLE_CLOUD_PROJECT"),
    location=os.getenv("GOOGLE_CLOUD_REGION"),
    staging_bucket=os.getenv("GOOGLE_CLOUD_BUCKET")
)
AGENT = agent_engines.get(os.getenv("VERTEX_AGENT_ID"))

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

session_service = InMemorySessionService()

runner = Runner(
    agent = AGENT,
    app_name = "Daily",
    session_service=session_service
)

# with app.app_context():
#     db.create_all()


def perform_action(action_name: str, data: dict, user_id: int) -> list[str]:
    logs = []

    # ─── Contacts ────────────────────────────────────────────────────────────────
    if action_name == "create_contact":
        c = Contact(
            name    = data.get("name"),
            email   = data.get("email"),
            phone   = data.get("phone"),
            company = data.get("company"),
            notes   = data.get("notes", ""),
            user_id = user_id
        )
        db.session.add(c); db.session.commit()
        logs.append(f"✅ Contact '{c.name}' created.")

    elif action_name in ("read_all_contacts", "list_contacts"):
        contacts = Contact.query.filter_by(user_id=user_id).all()
        if not contacts:
            logs.append("📭 No contacts found.")
        else:
            for c in contacts:
                logs.append(f"- {c.name} ({c.email}, {c.phone})")

    elif action_name == "update_contact":
        identifier = data.get("identifier","")
        updates    = data.get("updates",{})
        contact = Contact.query.filter(
            (Contact.user_id==user_id),
            ((Contact.email.ilike(identifier))|
             (Contact.name.ilike(f"%{identifier}%")))
        ).first()
        if not contact:
            logs.append(f"❌ No contact matching '{identifier}'.")
        else:
            for field, val in updates.items():
                if hasattr(contact, field):
                    setattr(contact, field, val)
            db.session.commit()
            logs.append(f"✅ Contact '{contact.name}' updated.")

    # ─── Invoices ───────────────────────────────────────────────────────────────
    elif action_name == "create_invoice":
        contact_name = data.get("contact_name","")
        contact = Contact.query.filter(
            Contact.user_id==user_id,
            Contact.name.ilike(f"%{contact_name}%")
        ).first()
        if not contact:
            logs.append(f"❌ Contact '{contact_name}' not found.")
        else:
            # parse due date
            raw = data.get("due_date","")
            dt = dateparser.parse(raw) if raw else None
            if not dt:
                dt = datetime.today()
                logs.append(f"⚠️ Couldn't parse due date '{raw}', defaulted to today.")
            inv = Invoice(
                user_id      = user_id,
                contact_id   = contact.id,
                issue_date   = datetime.today().date(),
                due_date     = dt.date(),
                total_amount = float(data.get("amount",0)),
                notes        = data.get("notes","")
            )
            db.session.add(inv); db.session.commit()
            logs.append(f"✅ Invoice #{inv.id} for {contact.name} created (${inv.total_amount} due {inv.due_date}).")

    elif action_name in ("read_all_invoices","list_invoices"):
        invs = Invoice.query.filter_by(user_id=user_id).order_by(Invoice.due_date.desc()).all()
        if not invs:
            logs.append("📭 No invoices on record.")
        else:
            for inv in invs:
                logs.append(f"- [#{inv.id}] {inv.contact.name}: ${inv.total_amount} due {inv.due_date} ({inv.status})")

    elif action_name == "mark_invoice_paid":
        inv_id = data.get("invoice_id")
        inv = Invoice.query.filter_by(user_id=user_id, id=inv_id).first()
        if not inv:
            logs.append(f"❌ Invoice #{inv_id} not found.")
        elif inv.status=="paid":
            logs.append(f"⚠️ Invoice #{inv.id} is already paid.")
        else:
            inv.status = "paid"
            db.session.commit()
            # record revenue
            rev = Revenue(invoice_id=inv.id, amount=inv.total_amount, date=datetime.today().date())
            db.session.add(rev); db.session.commit()
            logs.append(f"✅ Invoice #{inv.id} marked paid; revenue ${rev.amount} recorded.")

    # ─── Interactions ────────────────────────────────────────────────────────────
    elif action_name == "log_interaction":
        contact_name = data.get("contact_name","")
        contact = Contact.query.filter(
            Contact.user_id==user_id,
            Contact.name.ilike(f"%{contact_name}%")
        ).first()
        if not contact:
            logs.append(f"❌ Contact '{contact_name}' not found.")
        else:
            raw = data.get("date","")
            dt = dateparser.parse(raw) if raw else datetime.today()
            inter = Interaction(
                user_id    = user_id,
                contact_id = contact.id,
                date       = dt.date() if hasattr(dt, "date") else dt,
                type       = data.get("type","note"),
                summary    = data.get("summary","")
            )
            db.session.add(inter); db.session.commit()
            logs.append(f"✅ Logged {inter.type} with {contact.name} on {inter.date}.")

    elif action_name in ("read_interactions","list_interactions"):
        inters = Interaction.query.filter_by(user_id=user_id).order_by(Interaction.date.desc()).limit(5).all()
        if not inters:
            logs.append("📭 No interactions recorded.")
        else:
            for i in inters:
                logs.append(f"- [{i.date}] {i.type} with {i.contact.name}: {i.summary}")

    # ─── Events ──────────────────────────────────────────────────────────────────
    elif action_name == "create_event":
        raw = data.get("date","")
        dt = dateparser.parse(raw)
        if not dt:
            dt = datetime.today()
            logs.append(f"⚠️ Couldn't parse event date '{raw}', defaulted to now.")
        evt = Event(
            user_id    = user_id,
            contact_id = None,
            title      = data.get("title","Untitled"),
            date       = dt,
            description= data.get("description",""),
            location   = data.get("location","")
        )
        # optionally link contact
        if data.get("contact_name"):
            c = Contact.query.filter_by(user_id=user_id).filter(Contact.name.ilike(f"%{data['contact_name']}%")).first()
            if c: evt.contact_id = c.id
        db.session.add(evt); db.session.commit()
        logs.append(f"✅ Event '{evt.title}' created for {evt.date}.")

    elif action_name in ("list_upcoming_events","read_upcoming_events"):
        now = datetime.now()
        evts = Event.query.filter(
            Event.user_id==user_id,
            Event.date>=now
        ).order_by(Event.date.asc()).limit(5).all()
        if not evts:
            logs.append("📭 No upcoming events.")
        else:
            for e in evts:
                who = f" with {e.contact.name}" if e.contact else ""
                logs.append(f"- [{e.date.strftime('%b %d %I:%M %p')}] {e.title}{who}")

    # ─── Expenses ─────────────────────────────────────────────────────────────────
    elif action_name == "create_expense":
        raw = data.get("date","")
        dt = dateparser.parse(raw).date() if raw else datetime.today().date()
        exp = Expense(
            user_id    = user_id,
            amount     = float(data.get("amount",0)),
            category   = data.get("category","Uncategorized"),
            description= data.get("description",""),
            date       = dt
        )
        db.session.add(exp); db.session.commit()
        logs.append(f"✅ Logged expense ${exp.amount:.2f} for '{exp.category}' on {exp.date}.")

    elif action_name in ("read_expenses","list_expenses"):
        exps = Expense.query.filter_by(user_id=user_id).order_by(Expense.date.desc()).limit(5).all()
        if not exps:
            logs.append("📭 No expenses recorded.")
        else:
            for e in exps:
                logs.append(f"- ${e.amount:.2f} ({e.category}) on {e.date}")

    

    else:
        logs.append(f"⚠️ Unknown action: {action_name}")

    return logs

def get_last_4_months():
    today = datetime.today()
    months = []
    for i in range(3, -1, -1):  # last 4 months including current
        month_date = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        month_date = month_date.replace(month=((today.month - i - 1) % 12) + 1)
        if month_date.month > today.month:
            month_date = month_date.replace(year=today.year - 1)
        months.append((month_date.year, month_date.month))
    return months

from sqlalchemy import extract, func

def get_monthly_revenue(user_id):
    now = datetime.now()

    # Get the first day of the current month, then generate the previous 3
    months = []
    for i in range(3, -1, -1):
        month = (now.replace(day=1) - timedelta(days=30 * i)).replace(day=1)
        months.append(month)

    # Create keys in format YYYY-MM
    month_keys = [month.strftime('%Y-%m') for month in months]

    # Get all revenue records for this user in the last 4 months
    start_date = months[0]
    revenues = (
        Revenue.query
        .join(Invoice)
        .filter(Invoice.user_id == user_id)
        .filter(Revenue.date >= start_date.date())
        .all()
    )

    # Aggregate revenue by month
    revenue_totals = defaultdict(float)
    for rev in revenues:
        key = rev.date.strftime('%Y-%m')
        revenue_totals[key] += rev.amount

    # Format labels and values for the chart
    labels = [month.strftime('%B') for month in months]
    values = [revenue_totals.get(key, 0) for key in month_keys]

    return labels, values

@app.route("/", methods=["GET"])
@login_required
def index():
    # ─── 1) Load all dashboard data ──────────────────────────────────────────
    user_id = current_user.id
  

    contacts         = Contact.query.filter_by(user_id=user_id).all()
    invoices         = Invoice.query.filter_by(user_id=user_id).all()
    interactions     = (
        Interaction.query
        .filter_by(user_id=user_id)
        .order_by(Interaction.date.desc())
        .limit(10)
        .all()
    )
    events           = (
        Event.query
        .filter_by(user_id=user_id)
        .filter(Event.date >= datetime.now())
        .order_by(Event.date)
        .limit(5)
        .all()
    )
    recent_expenses  = (
        Expense.query
        .filter_by(user_id=user_id)
        .order_by(Expense.date.desc())
        .limit(5)
        .all()
    )

    # revenue / expense aggregates for the charts
    total_revenue = (
        db.session.query(func.sum(Revenue.amount))
        .join(Invoice).join(Contact)
        .filter(Contact.user_id == user_id)
        .scalar() or 0
    )
    start_of_month   = datetime.today().replace(day=1)
    monthly_revenue  = (
        db.session.query(func.sum(Revenue.amount))
        .join(Invoice).join(Contact)
        .filter(Contact.user_id == user_id, Revenue.date >= start_of_month)
        .scalar() or 0
    )
    total_expenses   = (
        db.session.query(func.sum(Expense.amount))
        .filter_by(user_id=user_id)
        .scalar() or 0
    )
    monthly_expenses = (
        db.session.query(func.sum(Expense.amount))
        .filter_by(user_id=user_id)
        .filter(Expense.date >= start_of_month)
        .scalar() or 0
    )

    # prepare chart data
    category_totals = defaultdict(float)
    for e in recent_expenses:
        category_totals[e.category] += e.amount
    expense_labels = list(category_totals.keys())
    expense_values = [category_totals[c] for c in expense_labels]

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
    weekly_events  = (
        Event.query.join(Contact, isouter=True)
        .filter(((Contact.user_id == user_id)|(Event.contact_id == None)) &
                (Event.date >= start_of_week))
        .all()
    )

    # ─── 2) Seed the agent session once ───────────────────────────────────────
    if not current_user.ai_session_id:
        # Build the same state payload from the data we already loaded
        initial_state = {
            "today":        datetime.today().strftime("%Y-%m-%d"),
            "contacts":     [
                {"name":c.name, "email":c.email, "phone":c.phone, "company":c.company}
                for c in contacts
            ],
            "invoices":     [
                {
                  "id": inv.id,
                  "contact_name": inv.contact.name,
                  "issue_date": str(inv.issue_date),
                  "due_date": str(inv.due_date),
                  "amount": inv.total_amount,
                  "status": inv.status
                }
                for inv in invoices
            ],
            "interactions": [
                {
                  "contact_name": ix.contact.name,
                  "type": ix.type,
                  "date": str(ix.date),
                  "summary": ix.summary
                }
                for ix in interactions
            ],
            "events":       [
                {
                  "title": e.title,
                  "date": str(e.date),
                  "description": e.description,
                  "location": e.location,
                  "contact_name": e.contact.name if e.contact else None
                }
                for e in events
            ],
            "expenses":     [
                {
                  "amount": ex.amount,
                  "category": ex.category,
                  "description": ex.description,
                  "date": str(ex.date)
                }
                for ex in recent_expenses
            ]
        }

        sess = asyncio.run(
            session_service.create_session(
                app_name="Daily",
                user_id=current_user.email,
                session_id=str(uuid.uuid4()),
                state=initial_state
            )
        )

        current_user.ai_session_id = sess.id
        session["ai_session_id"] = sess.id
        db.session.commit()
        

    # ─── 3) Render the dashboard ──────────────────────────────────────────────
    return render_template(
        "index.html",
        contacts=contacts,
        invoices=invoices,
        total_revenue=total_revenue,
        monthly_revenue=monthly_revenue,
        interactions=interactions,
        events=events,
        weekly_events=weekly_events,
        start=start_of_week,
        timedelta=timedelta,
        total_expenses=total_expenses,
        monthly_expenses=monthly_expenses,
        recent_expenses=recent_expenses,
        expense_labels=expense_labels,
        expense_values=expense_values,
        revenue_labels=revenue_labels,
        revenue_values=revenue_values
    )


@app.route("/invoice/<int:invoice_id>/download")
def download_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    contact = Contact.query.get(invoice.contact_id)

    rendered_html = render_template("invoice_pdf.html", invoice=invoice, contact=contact)
    pdf = HTML(string=rendered_html).write_pdf()

    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=invoice_{invoice.id}.pdf'
    return response


FENCED_JSON = re.compile(r"```json\s*([\s\S]*?)```", re.IGNORECASE)

@app.route("/chat", methods=["POST"])
@login_required
def chat():
    user_message = request.json.get("message", "").strip()
    if not user_message:
        return jsonify(error="Empty message"), 400

    sess_id = current_user.ai_session_id
    # Build the ADK message object
    new_message = types.Content(
        role="user",
        parts=[types.Part(text=user_message)]
    )

    text_parts  = []
    action_logs = []

    # Stream with Runner, which automatically loads your state + history
    for event in runner.run(
        user_id    = current_user.email,
        session_id = sess_id,
        new_message= new_message
    ):
        # function_call handling
        if event.is_function_call():
            name = event.function_call.name
            args = json.loads(event.function_call.arguments)
            action_logs.extend(perform_action(name, args, current_user.id))

        # final text response
        if event.is_final_response() and event.content:
            text = event.content.parts[0].text.strip()
            # strip any JSON fences, then append
            cleaned = re.sub(r"```json[\s\S]*?```", "", text).strip()
            if cleaned:
                text_parts.append(cleaned)

    # decide reply
    if action_logs:
        assistant = "\n".join(action_logs)
    else:
        assistant = " ".join(text_parts) or "❌ No response."

    return jsonify(user=user_message, assistant=assistant)




@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/login/google")
def login_with_google():
    redirect_uri = url_for('auth_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route("/auth/callback")
def auth_callback():
    # 1) Complete OAuth and fetch user info
    token     = google.authorize_access_token()
    user_info = google.get("userinfo").json()
    email, name = user_info["email"], user_info["name"]

    # 2) Lookup or create User
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, name=name)
        db.session.add(user)
        db.session.commit()

    user.ai_session_id = None
    db.session.commit()


    # Log in and store in flask.session
    login_user(user)
    session["user_id"]       = user.id
    session["user_name"]     = user.name
    
    return redirect(url_for("index"))

@app.route("/logout", methods=["POST"])
def logout():
    logout_user()
    session.clear()
    return redirect("/")

@app.route("/debug/agent_state")
@login_required
def debug_agent_state():
    sess = AGENT.get_session(
        user_id=current_user.email,
        session_id=current_user.ai_session_id
    )
    # return just the state JSON for easy inspection
    return jsonify(sess.get("state", {}))

if __name__ == "__main__":
    app.run(debug=True)





