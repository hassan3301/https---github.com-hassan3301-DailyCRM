from flask import Flask, render_template, request, redirect, session, make_response, url_for, redirect 
from services.openai_chat import get_openai_response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from models import db, Contact, Invoice, Revenue, Interaction, Event, Expense, User
from utils.email_utils import send_email
from authlib.integrations.flask_client import OAuth
import os
import datetime
from datetime import timedelta
import json
import re
import ast
import dateparser
from weasyprint import HTML
import ollama


os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


app = Flask(__name__)
app.secret_key = "supersecret"  # for storing session info

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

app.secret_key = os.getenv("SECRET_KEY")
oauth = OAuth(app)


google = oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    access_token_url='https://oauth2.googleapis.com/token',
    access_token_params=None,
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    authorize_params={
        'access_type': 'offline',
        'prompt': 'consent'
    },
    api_base_url='https://www.googleapis.com/oauth2/v2/',
    userinfo_endpoint='https://www.googleapis.com/oauth2/v2/userinfo',
    client_kwargs={'scope': 'email profile'},
)

MODEL = "gemma3:4b"

SYSTEM_PROMPT = """
You are the assistant for Daily CRM.  
Your job: map the user’s instruction into exactly one action + JSON, but only once you have **all** required parameters.  

Actions & required parameters:
  • add_client(name, email, phone)
  • track_revenue(invoice_id, amount, date)
  • create_invoice(contact_id, total_amount, due_date)
  • log_interaction(contact_id, type, summary, date)
  • schedule_event(title, date, time, location)

Optional parameters (only if user mentions them):
  add_client(company, notes)
  create_invoice(notes)
  schedule_event(contact_id, description)

**Important rules**  
1. **Full context.** You see every question you’ve asked and every answer the user has given so far.  
2. **No repeats.** Never ask again for a parameter once the user has given you a **non-empty** answer.  
3. **One at a time.** If any required field is still missing, ask **exactly one** question for the next missing field (e.g., “What is the new client’s phone number?”).  
4. **Validate.** If the user’s answer is blank or invalid for that field, ask again for the **same** field.  
5. **Finish only when complete.** Once **all** required parameters have valid, non-empty values, output **only** the JSON:
```json
{
  "action": "<action_name>",
  "parameters": { /* all required + any optional provided */ }
}
and stop; do not ask further questions or output anything else.
"""

def dispatch_command(cmd: dict, user_id: int) -> list[str]:
    logs: list[str] = []
    action = cmd.get("action")
    p = cmd.get("parameters", {})

    if action == "add_client":
        c = Contact(
            user_id=user_id,
            name=p["name"],
            email=p["email"],
            phone=p["phone"],
            company=p.get("company", ""),
            notes=p.get("notes", ""),
            status="lead"
        )
        db.session.add(c)
        db.session.commit()
        logs.append(f"✅ Client '{c.name}' added (ID: {c.id}).")

    elif action == "track_revenue":
        inv = Invoice.query.get(p["invoice_id"])
        if not inv:
            logs.append(f"❌ Invoice #{p['invoice_id']} not found.")
        else:
            date = dateparser.parse(p["date"], settings={"RETURN_AS_TIMEZONE_AWARE": False})
            r = Revenue(
                invoice_id=inv.id,
                amount=p["amount"],
                date=(date.date() if date else datetime.date.today())
            )
            db.session.add(r)
            db.session.commit()
            logs.append(f"✅ Recorded ${r.amount} revenue for invoice #{inv.id} on {r.date}.")

    elif action == "create_invoice":
        contact = Contact.query.get(p["contact_id"])
        if not contact:
            logs.append(f"❌ Contact #{p['contact_id']} not found.")
        else:
            due = dateparser.parse(p["due_date"], settings={"RETURN_AS_TIMEZONE_AWARE": False})
            inv = Invoice(
                user_id=user_id,
                contact_id=contact.id,
                due_date=(due.date() if due else datetime.date.today()),
                total_amount=p["total_amount"],
                notes=p.get("notes", "")
            )
            db.session.add(inv)
            db.session.commit()
            logs.append(f"✅ Invoice #{inv.id} for {contact.name}: ${inv.total_amount} due {inv.due_date}.")

    elif action == "log_interaction":
        contact = Contact.query.get(p["contact_id"])
        if not contact:
            logs.append(f"❌ Contact #{p['contact_id']} not found.")
        else:
            date = dateparser.parse(p["date"], settings={"RETURN_AS_TIMEZONE_AWARE": False})
            it = Interaction(
                user_id=user_id,
                contact_id=contact.id,
                date=(date.date() if date else datetime.date.today()),
                type=p["type"],
                summary=p["summary"]
            )
            db.session.add(it)
            db.session.commit()
            logs.append(f"✅ Logged {it.type} with {contact.name} on {it.date}.")

    elif action == "schedule_event":
        # contact_id is optional
        cid = p.get("contact_id")
        contact = Contact.query.get(cid) if cid else None

        # build datetime
        date_part = p["date"]
        time_part = p["time"]
        dt = dateparser.parse(f"{date_part} {time_part}", settings={"RETURN_AS_TIMEZONE_AWARE": False})
        ev = Event(
            user_id=user_id,
            contact_id=(contact.id if contact else None),
            title=p["title"],
            date=(dt if dt else datetime.datetime.now()),
            description=p.get("description", ""),
            location=p["location"]
        )
        db.session.add(ev)
        db.session.commit()
        who = f" for {contact.name}" if contact else ""
        logs.append(f"✅ Event '{ev.title}'{who} scheduled on {ev.date} at {ev.location}.")

    else:
        logs.append(f"⚠️ Unknown action: {action}")

    return logs

@app.route("/", methods=["GET", "POST"])
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))
    user_id = session["user_id"]
    response = ""

    # (Re)initialize Gemma chat context on GET
    if "gemma_history" not in session or request.method == "GET":
        session["gemma_history"] = [{"role": "system", "content": SYSTEM_PROMPT}]

    if request.method == "POST":
        text = request.form.get("message", "").strip()
        if text:
            session["gemma_history"].append({"role": "user", "content": text})
            try:
                gm_text = ollama.chat(
                    model=MODEL,
                    messages=session["gemma_history"]
                )["message"]["content"].strip()
            except Exception as e:
                response = f"❌ Gemma error: {e}"
            else:
                session["gemma_history"].append({"role": "assistant", "content": gm_text})
                try:
                    cmd = json.loads(gm_text)
                except json.JSONDecodeError:
                    response = gm_text
                else:
                    logs = dispatch_command(cmd, user_id)
                    response = "\n".join(logs)
                    session.pop("gemma_history", None)

    # ——— Dashboard metrics ——————————————————————————————————————
    contacts = Contact.query.filter_by(user_id=user_id).all()
    invoices = Invoice.query.filter_by(user_id=user_id).all()
    interactions = (
        Interaction.query
        .filter_by(user_id=user_id)
        .order_by(Interaction.date.desc())
        .limit(10)
        .all()
    )
    events = (
        Event.query
        .filter(Event.user_id == user_id, Event.date >= datetime.datetime.now())
        .order_by(Event.date)
        .limit(5)
        .all()
    )

    # Total and monthly revenue
    total_revenue = (
        db.session.query(func.sum(Revenue.amount))
        .join(Invoice)
        .join(Contact)
        .filter(Contact.user_id == user_id)
        .scalar()
        or 0
    )
    start_of_month = datetime.date.today().replace(day=1)
    monthly_revenue = (
        db.session.query(func.sum(Revenue.amount))
        .join(Invoice)
        .join(Contact)
        .filter(Contact.user_id == user_id, Revenue.date >= start_of_month)
        .scalar()
        or 0
    )

    # Total and monthly expenses
    total_expenses = (
        db.session.query(func.sum(Expense.amount))
        .filter_by(user_id=user_id)
        .scalar()
        or 0
    )
    monthly_expenses = (
        db.session.query(func.sum(Expense.amount))
        .filter(Expense.user_id == user_id, Expense.date >= start_of_month)
        .scalar()
        or 0
    )
    recent_expenses = (
        Expense.query
        .filter_by(user_id=user_id)
        .order_by(Expense.date.desc())
        .limit(5)
        .all()
    )

    # Upcoming events this week
    start_of_week = datetime.datetime.now() - datetime.timedelta(days=datetime.datetime.now().weekday())
    end_of_week = start_of_week + datetime.timedelta(days=7)
    weekly_events = (
        Event.query
        .filter(
            Event.user_id == user_id,
            Event.date >= start_of_week,
            Event.date < end_of_week
        )
        .order_by(Event.date)
        .all()
    )

    return render_template(
        "index.html",
        response=response,
        contacts=contacts,
        invoices=invoices,
        interactions=interactions,
        events=events,
        total_revenue=total_revenue,
        monthly_revenue=monthly_revenue,
        total_expenses=total_expenses,
        monthly_expenses=monthly_expenses,
        recent_expenses=recent_expenses,
        weekly_events=weekly_events,
        start=start_of_week,
        timedelta=datetime.timedelta
    )

# @app.route("/", methods=["GET", "POST"])
# def index():
#     if "user_id" not in session:
#         return redirect("/login")

#     response = ""

#     if request.method == "POST":
#         user_message = request.form.get("message")
#         assistant_reply = get_openai_response(user_message)

#         try:
#             # Extract JSON array or object from assistant's response
#             match = re.search(r"\[.*\]|\{.*\}", assistant_reply, re.DOTALL)
#             if match:
#                 raw = match.group(0)

#                 # Try parsing with json, fallback to ast
#                 try:
#                     actions = json.loads(raw)
#                 except json.JSONDecodeError:
#                     actions = ast.literal_eval(raw)

#                 # If a single dict, make it a list
#                 if isinstance(actions, dict):
#                     actions = [actions]

#                 response_log = []
#                 for parsed in actions:
#                     action = parsed.get("action")

#                     if action == "create_contact":
#                         data = parsed.get("data", {})
#                         new_contact = Contact(
#                             name=data.get("name"),
#                             email=data.get("email"),
#                             phone=data.get("phone"),
#                             company=data.get("company"),
#                             notes=data.get("notes", ""),
#                             user_id=session["user_id"]
#                         )
#                         db.session.add(new_contact)
#                         db.session.commit()
#                         response_log.append(f"✅ New contact '{new_contact.name}' added to the CRM.")

#                     elif action == "read_all_contacts":
#                         contacts = Contact.query.all()
#                         if not contacts:
#                             response_log.append("📭 Your contact list is currently empty.")
#                         else:
#                             contact_lines = [
#                                 f"- {c.name} ({c.email}, {c.phone}, {c.company})"
#                                 for c in contacts
#                             ]
#                             response_log.append("📇 Here are your contacts:\n\n" + "\n".join(contact_lines))

#                     elif action == "update_contact":
#                         identifier = parsed.get("identifier", "")
#                         updates = parsed.get("updates", {})
#                         contact = Contact.query.filter(
#                             (Contact.email.ilike(identifier)) | (Contact.name.ilike(f"%{identifier}%"))
#                         ).first()

#                         if not contact:
#                             response_log.append(f"❌ No contact found with identifier '{identifier}'.")
#                         else:
#                             valid_fields = {"name", "email", "phone", "company", "status", "notes"}
#                             invalid_fields = []

#                             for key, value in updates.items():
#                                 if key in valid_fields:
#                                     setattr(contact, key, value)
#                                 else:
#                                     invalid_fields.append(key)

#                             db.session.commit()
#                             updated_fields = ', '.join([k for k in updates if k in valid_fields])
#                             if invalid_fields:
#                                 response_log.append(f"⚠️ Updated: {updated_fields}. Skipped: {', '.join(invalid_fields)}")
#                             else:
#                                 response_log.append(f"✅ Updated contact '{contact.name}': {updated_fields}.")

#                     elif action == "create_invoice":
#                         data = parsed.get("data", {})
#                         contact_name = data.get("contact_name", "")
#                         contact = Contact.query.filter(Contact.name.ilike(f"%{contact_name}%")).first()

#                         if not contact:
#                             response_log.append(f"❌ Could not find contact '{contact_name}'")
#                         else:
#                             try:
#                                 raw_date = data.get("due_date", "")
#                                 parsed_date = dateparser.parse(
#                                     raw_date,
#                                     settings={
#                                         "RELATIVE_BASE": datetime.datetime.now(),
#                                         "PREFER_DATES_FROM": "future",
#                                         "STRICT_PARSING": True
#                                     }
#                                 )

#                                 if parsed_date and parsed_date.date() >= datetime.date.today():
#                                     due_date = parsed_date.date()
#                                 else:
#                                     due_date = datetime.date.today()
#                                     response_log.append(f"⚠️ Could not understand due date '{raw_date}'. Defaulted to today.")

#                                 amount = float(data.get("amount"))

#                                 invoice = Invoice(
#                                     contact_id=contact.id,
#                                     due_date=due_date,
#                                     total_amount=amount,
#                                     notes=data.get("notes", ""),
#                                     user_id=session["user_id"]
#                                 )
#                                 db.session.add(invoice)
#                                 db.session.commit()

#                                 download_url = url_for('download_invoice', invoice_id=invoice.id, _external=True)
#                                 response_log.append(f"✅ Invoice created for {contact.name} - ${amount} due {due_date}.")
#                                 response_log.append(f"📄 [Download Invoice PDF]({download_url})")

#                                 response_log.append(f"✅ Invoice created for {contact.name} - ${amount} due {due_date}.")
#                             except Exception as e:
#                                 response_log.append(f"❌ Error creating invoice: {str(e)}")


#                     elif action == "read_all_invoices":
#                         invoices = Invoice.query.order_by(Invoice.due_date.desc()).all()
#                         if not invoices:
#                             response_log.append("📭 You have no invoices on record.")
#                         else:
#                             lines = [
#                                 f"- [#{inv.id}] {inv.contact.name}: ${inv.total_amount} due {inv.due_date} ({inv.status})"
#                                 for inv in invoices
#                             ]
#                             response_log.append("📄 Here are your invoices:\n\n" + "\n".join(lines))

#                     elif action == "mark_invoice_paid":
#                         invoice_id = parsed.get("invoice_id")
#                         invoice = Invoice.query.get(invoice_id)

#                         if not invoice:
#                             response_log.append(f"❌ Invoice #{invoice_id} not found.")
#                         elif invoice.status == "paid":
#                             response_log.append(f"⚠️ Invoice #{invoice.id} is already marked as paid.")
#                         else:
#                             invoice.status = "paid"
#                             db.session.commit()

#                             revenue_entry = Revenue(
#                                 invoice_id=invoice.id,
#                                 amount=invoice.total_amount
#                             )
#                             db.session.add(revenue_entry)
#                             db.session.commit()

#                             response_log.append(f"✅ Invoice #{invoice.id} marked as paid and ${invoice.total_amount} recorded as revenue.")

#                     elif parsed.get("action") == "log_interaction":
#                         data = parsed.get("data", {})
#                         contact_name = data.get("contact_name", "")
#                         contact = Contact.query.filter(Contact.name.ilike(f"%{contact_name}%")).first()

#                         if not contact:
#                             response_log.append(f"❌ Could not find contact '{contact_name}'")
#                         else:
#                             try:
#                                 # Parse human-readable date or default to today
#                                 raw_date = data.get("date", "")
#                                 parsed_date = dateparser.parse(raw_date)
#                                 if parsed_date:
#                                     parsed_date = parsed_date.date()
#                                 else:
#                                     parsed_date = datetime.date.today()

#                                 interaction = Interaction(
#                                     contact_id=contact.id,
#                                     type=data.get("type"),
#                                     summary=data.get("summary"),
#                                     date=parsed_date,
#                                     user_id=session["user_id"]
#                                 )
#                                 db.session.add(interaction)
#                                 db.session.commit()
#                                 response_log.append(f"✅ Logged a {interaction.type} with {contact.name} on {parsed_date}.")
#                             except Exception as e:
#                                 response_log.append(f"❌ Error logging interaction: {str(e)}")

#                     elif action == "download_invoice":
#                         invoice_id = parsed.get("invoice_id")
#                         invoice = Invoice.query.get(invoice_id)

#                         if invoice:
#                             download_url = url_for('download_invoice', invoice_id=invoice.id, _external=True)
#                             response_log.append(
#                                 f"📄 <a href='{download_url}' target='_blank'>Click here to download invoice #{invoice.id}</a>"
#                             )
#                         else:
#                             response_log.append(f"❌ Invoice #{invoice_id} not found.")

#                     elif action == "send_email":
#                         to_name = parsed.get("to")
#                         subject = parsed.get("subject", "(No subject)")
#                         body = parsed.get("body", "")

#                         contact = Contact.query.filter(Contact.name.ilike(f"%{to_name}%")).first()

#                         if not contact:
#                             response_log.append(f"❌ Could not find contact '{to_name}' to send email.")
#                         elif not contact.email:
#                             response_log.append(f"❌ Contact '{to_name}' does not have an email address.")
#                         else:
#                             try:
#                                 send_email(contact.email, subject, body)
#                                 response_log.append(f"📧 Email sent to {contact.name} at {contact.email}.")
#                             except Exception as e:
#                                 response_log.append(f"❌ Failed to send email: {str(e)}")

#                     elif action == "send_invoice_email":
#                         contact_name = parsed.get("contact_name")
#                         invoice_index = parsed.get("invoice_index", 1)

#                         try:
#                             invoice_index = int(parsed.get("invoice_index", 1))
#                         except ValueError:
#                             invoice_index = 1

                        
#                         contact = Contact.query.filter(Contact.name.ilike(f"%{contact_name}%")).first()
#                         if not contact:
#                             response_log.append(f"❌ Could not find contact '{contact_name}' to send email.")
#                             continue

#                         invoices = Invoice.query.filter_by(contact_id=contact.id).order_by(Invoice.due_date.desc()).all()

#                         if 1 <= invoice_index <= len(invoices):
#                             invoice = invoices[invoice_index - 1]
#                             try:
#                                 from utils.email_utils import send_invoice_email
#                                 send_invoice_email(contact, invoice, app)
#                                 response_log.append(f"📧 Invoice #{invoice.id} emailed to {contact.name} at {contact.email}.")
#                             except Exception as e:
#                                 response_log.append(f"❌ Failed to send invoice email: {str(e)}")
#                         else:
#                             response_log.append(f"❌ Invoice index {invoice_index} out of range for {contact.name}.")

#                     elif action == "create_event":
#                         data = parsed.get("data", {})
#                         title = data.get("title")
#                         raw_date = data.get("date", "")
#                         description = data.get("description", "")
#                         location = data.get("location", "")

#                         parsed_date = dateparser.parse(
#                             raw_date,
#                             settings={
#                                 "RELATIVE_BASE": datetime.datetime.now(),
#                                 "PREFER_DATES_FROM": "future",
#                                 "RETURN_AS_TIMEZONE_AWARE": False
#                             },
#                             languages=["en"]
#                         )

#                         if not parsed_date:
#                             parsed_date = datetime.datetime.now()
#                             response_log.append(f"⚠️ Could not understand event date '{raw_date}'. Defaulted to now.")

#                         contact = None
#                         if "contact_name" in data:
#                             contact = Contact.query.filter(Contact.name.ilike(f"%{data['contact_name']}%")).first()

#                         event = Event(
#                             title=title,
#                             contact_id=contact.id if contact else None,
#                             date=parsed_date,
#                             description=description,
#                             location=location,
#                             user_id=session["user_id"]
#                         )
#                         db.session.add(event)
#                         db.session.commit()

#                         who = f" with {contact.name}" if contact else ""
#                         response_log.append(f"📅 Event created: '{title}'{who} on {parsed_date.strftime('%Y-%m-%d %H:%M')} at {location}")

#                     elif action == "list_upcoming_events":
#                         now = datetime.datetime.now()
#                         events = Event.query.filter(Event.date >= now).order_by(Event.date.asc()).limit(5).all()

#                         if not events:
#                             response_log.append("📭 No upcoming events.")
#                         else:
#                             response_log.append("📅 Upcoming Events:")
#                             for e in events:
#                                 who = f" with {e.contact.name}" if e.contact else ""
#                                 response_log.append(f"- {e.date.strftime('%b %d %I:%M %p')} — {e.title}{who}")

#                     elif action == "create_expense":
#                         data = parsed.get("data", {})
#                         try:
#                             amount = float(data.get("amount"))
#                             category = data.get("category", "Uncategorized")
#                             description = data.get("description", "")
#                             raw_date = data.get("date", "")

#                             parsed_date = dateparser.parse(raw_date, settings={"RELATIVE_BASE": datetime.datetime.now()})
#                             expense_date = parsed_date.date() if parsed_date else datetime.date.today()

#                             expense = Expense(
#                                 amount=amount,
#                                 category=category,
#                                 description=description,
#                                 date=expense_date
#                             )
#                             db.session.add(expense)
#                             db.session.commit()
#                             response_log.append(f"💸 Logged ${amount:.2f} expense for '{category}' on {expense_date}.")
#                         except Exception as e:
#                             response_log.append(f"❌ Error logging expense: {str(e)}")

#                     elif action == "read_expenses":
#                         expenses = Expense.query.order_by(Expense.date.desc()).limit(5).all()
#                         if not expenses:
#                             response_log.append("📭 No expenses recorded.")
#                         else:
#                             response_log.append("💸 Recent Expenses:")
#                             for e in expenses:
#                                 response_log.append(f"- ${e.amount:.2f} for {e.category} on {e.date}")

#                     else:
#                         response_log.append(f"⚠️ Unknown action: {parsed.get('action')}")
#                 response = "\n".join(response_log)
#             else:
#                 response = assistant_reply

#         except Exception as e:
#             response = f"❌ Error parsing or executing actions: {str(e)}"

#     user_id = session["user_id"]

#     contacts = Contact.query.filter_by(user_id=user_id).all()
#     invoices = Invoice.query.join(Contact).filter(Contact.user_id == user_id).all()
#     interactions = Interaction.query.join(Contact).filter(Contact.user_id == user_id).order_by(Interaction.date.desc()).limit(10).all()
#     events = Event.query.join(Contact, isouter=True).filter(
#         (Contact.user_id == user_id) | (Event.contact_id == None)
#     ).filter(Event.date >= datetime.datetime.now()).order_by(Event.date).limit(5).all()

#     total_revenue = (
#         db.session.query(func.sum(Revenue.amount))
#         .join(Invoice)
#         .join(Contact)
#         .filter(Contact.user_id == user_id)
#         .scalar()
#         or 0
#     )

#     start_of_month = datetime.date.today().replace(day=1)
#     monthly_revenue = (
#         db.session.query(func.sum(Revenue.amount))
#         .join(Invoice)
#         .join(Contact)
#         .filter(Contact.user_id == user_id, Revenue.date >= start_of_month)
#         .scalar()
#         or 0
#     )

#     total_expenses = db.session.query(func.sum(Expense.amount)).filter_by(user_id=user_id).scalar() or 0
#     monthly_expenses = (
#         db.session.query(func.sum(Expense.amount))
#         .filter_by(user_id=user_id)
#         .filter(Expense.date >= start_of_month)
#         .scalar()
#         or 0
#     )
#     recent_expenses = Expense.query.filter_by(user_id=user_id).order_by(Expense.date.desc()).limit(5).all()

#     start_of_week = datetime.datetime.now() - datetime.timedelta(days=datetime.datetime.now().weekday())
#     end_of_week = start_of_week + datetime.timedelta(days=7)
#     weekly_events = Event.query.join(Contact, isouter=True).filter(
#         ((Contact.user_id == user_id) | (Event.contact_id == None)) &
#         (Event.date >= start_of_week) & (Event.date < end_of_week)
#     ).all()

#     return render_template(
#         "index.html",
#         response=response,
#         contacts=contacts,
#         invoices=invoices,
#         total_revenue=total_revenue,
#         monthly_revenue=monthly_revenue,
#         interactions=interactions,
#         events=events,
#         weekly_events=weekly_events,
#         start=start_of_week,
#         timedelta=timedelta,
#         total_expenses=total_expenses,
#         monthly_expenses=monthly_expenses,
#         recent_expenses=recent_expenses,
#     )


@app.route("/contacts")
def view_contacts():
    contacts = Contact.query.all()
    return render_template("contacts.html", contacts=contacts)

@app.route("/add_contact", methods=["GET", "POST"])
def add_contact():
    if request.method == "POST":
        new_contact = Contact(
            name=request.form["name"],
            email=request.form["email"],
            phone=request.form["phone"],
            company=request.form["company"],
            notes=request.form["notes"]
        )
        db.session.add(new_contact)
        db.session.commit()
        return redirect("/contacts")

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

@app.route("/login")
def login():
    redirect_uri = url_for('auth_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route("/auth/callback")
def auth_callback():
    token = google.authorize_access_token()
    resp = google.get("userinfo")
    user_info = resp.json()

    email = user_info["email"]
    name = user_info["name"]

    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, name=name)
        db.session.add(user)
        db.session.commit()

    session["user_id"] = user.id
    session["user_name"] = user.name
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/")


#with app.app_context():
#    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)