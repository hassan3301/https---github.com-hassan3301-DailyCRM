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


os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


app = Flask(__name__)
app.secret_key = "supersecret"  # for storing session info

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

app.secret_key = os.getenv("SECRET_KEY")
oauth = OAuth(app)

WIZARD_SCHEMAS = {
    "create_contact": {
        "model": Contact,
        "fields": [
            {"name": "name", "prompt": "What is the customer's name?"},
            {"name": "email", "prompt": "What is their email?"},
            {"name": "phone", "prompt": "Phone number?"},
            {"name": "company", "prompt": "Company name?"}
        ]
    },
    "create_invoice": {
        "model": Invoice,
        "fields": [
            {"name": "contact_name", "prompt": "Who is this invoice for?"},
            {"name": "amount", "prompt": "How much is the invoice for?"},
            {"name": "due_date", "prompt": "When is it due?"}
        ]
    },
    "create_event": {
        "model": Event,
        "fields": [
            {"name": "title", "prompt": "What is the event title?"},
            {"name": "date", "prompt": "When is the event? (e.g., 'next Friday at 3 PM')"},
            {"name": "description", "prompt": "Any description for the event?"},
            {"name": "location", "prompt": "Where is the event located? (optional)"},
            {"name": "contact_name", "prompt": "Is this event with a contact? (optional)"}
        ]
    },
    "log_interaction": {
        "model": Interaction,
        "fields": [
            {"name": "contact_name", "prompt": "Who is this interaction with?"},
            {"name": "type", "prompt": "What type of interaction is this? (e.g., 'call', 'email', 'meeting')"},
            {"name": "summary", "prompt": "Please summarize the interaction."},
            {"name": "date", "prompt": "When did this interaction occur? (e.g., 'yesterday', 'last week')"}
        ]
    },
    "create_expense": {
        "model": Expense,
        "fields": [
            {"name": "amount", "prompt": "How much was the expense?"},
            {"name": "category", "prompt": "What category does this expense fall under?"},
            {"name": "description", "prompt": "Please describe the expense."},
            {"name": "date", "prompt": "When did this expense occur? (e.g., 'yesterday', 'last week')"}
        ]
    },
    "send_email": {
        "model": None,  # No model for this action
        "fields": [
            {"name": "to", "prompt": "Who should the email be sent to?"},
            {"name": "subject", "prompt": "What is the subject of the email?"},
            {"name": "body", "prompt": "What is the body of the email?"}
        ]
    },
    "send_invoice_email": {
        "model": None,  # No model for this action
        "fields": [
            {"name": "contact_name", "prompt": "Who should the invoice be sent to?"},
            {"name": "invoice_index", "prompt": "Which invoice should be sent? (1 for the most recent, 2 for the second most recent, etc.)"}
        ]
    },

}


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

@app.route("/", methods=["GET", "POST"])
def index():
    if "user_id" not in session:
        return redirect("/login")

    response = ""
    
    user_id = session["user_id"]

    contacts = Contact.query.filter_by(user_id=user_id).all()
    invoices = Invoice.query.join(Contact).filter(Contact.user_id == user_id).all()
    interactions = Interaction.query.join(Contact).filter(Contact.user_id == user_id).order_by(Interaction.date.desc()).limit(10).all()
    events = Event.query.join(Contact, isouter=True).filter(
        (Contact.user_id == user_id) | (Event.contact_id == None)
    ).filter(Event.date >= datetime.datetime.now()).order_by(Event.date).limit(5).all()

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

    total_expenses = db.session.query(func.sum(Expense.amount)).filter_by(user_id=user_id).scalar() or 0
    monthly_expenses = (
        db.session.query(func.sum(Expense.amount))
        .filter_by(user_id=user_id)
        .filter(Expense.date >= start_of_month)
        .scalar()
        or 0
    )
    recent_expenses = Expense.query.filter_by(user_id=user_id).order_by(Expense.date.desc()).limit(5).all()

    start_of_week = datetime.datetime.now() - datetime.timedelta(days=datetime.datetime.now().weekday())
    end_of_week = start_of_week + datetime.timedelta(days=7)
    weekly_events = Event.query.join(Contact, isouter=True).filter(
        ((Contact.user_id == user_id) | (Event.contact_id == None)) &
        (Event.date >= start_of_week) & (Event.date < end_of_week)
    ).all()

    shared_data = {
        "contacts": contacts,
        "invoices": invoices,
        "total_revenue": total_revenue,
        "monthly_revenue": monthly_revenue,
        "interactions": interactions,
        "events": events,
        "weekly_events": weekly_events,
        "start": start_of_week,
        "timedelta": timedelta,
        "total_expenses": total_expenses,
        "monthly_expenses": monthly_expenses,
        "recent_expenses": recent_expenses,
    }


    if request.method == "POST":
        user_message = request.form.get("message")
        assistant_reply = get_openai_response(user_message)

        if session.get("wizard"):
            wizard = session["wizard"]
            schema = WIZARD_SCHEMAS[wizard["type"]]
            fields = schema["fields"]
            step = wizard["step"]

            # Save last answer if we're past the first step
            if step > 0:
                last_field = fields[step - 1]["name"]
                wizard["data"][last_field] = user_message

            # Ask next field
            if step < len(fields):
                prompt = fields[step]["prompt"]
                wizard["step"] += 1
                session["wizard"] = wizard
                return render_template("index.html", response=prompt, **shared_data)

            # Final step – create the object
            data = wizard["data"]
            model = schema["model"]

            # Model-specific post-processing
            if wizard["type"] == "create_contact":
                obj = model(**data, user_id=session["user_id"])

            elif wizard["type"] == "create_invoice":
                contact = Contact.query.filter(Contact.name.ilike(f"%{data['contact_name']}%")).first()
                obj = model(
                    contact_id=contact.id if contact else None,
                    due_date=dateparser.parse(data["due_date"]).date(),
                    total_amount=float(data["amount"]),
                    user_id=session["user_id"]
                )

            elif wizard["type"] == "create_event":
                contact = Contact.query.filter(Contact.name.ilike(f"%{data.get('contact_name', '')}%")).first()
                parsed_date = dateparser.parse(data["date"], settings={"RELATIVE_BASE": datetime.datetime.now()})
                obj = model(
                    title=data["title"],
                    contact_id=contact.id if contact else None,
                    date=parsed_date,
                    description=data.get("description", ""),
                    location=data.get("location", ""),
                    user_id=session["user_id"]
                )

            elif wizard["type"] == "log_interaction":
                contact = Contact.query.filter(Contact.name.ilike(f"%{data['contact_name']}%")).first()
                parsed_date = dateparser.parse(data["date"], settings={"RELATIVE_BASE": datetime.datetime.now()})
                obj = model(
                    contact_id=contact.id if contact else None,
                    type=data["type"],
                    summary=data["summary"],
                    date=parsed_date,
                    user_id=session["user_id"]
                )

            elif wizard["type"] == "create_expense":
                parsed_date = dateparser.parse(data["date"], settings={"RELATIVE_BASE": datetime.datetime.now()})
                obj = model(
                    amount=float(data["amount"]),
                    category=data.get("category", "Uncategorized"),
                    description=data.get("description", ""),
                    date=parsed_date.date() if parsed_date else datetime.date.today(),
                    user_id=session["user_id"]
                )

            elif wizard["type"] == "send_email":
                contact = Contact.query.filter(Contact.name.ilike(f"%{data['to']}%")).first()
                if not contact or not contact.email:
                    return render_template("index.html", response="❌ Contact not found or has no email.", **shared_data)

                try:
                    send_email(contact.email, data["subject"], data["body"])
                    response_message = f"📧 Email sent to {contact.name} at {contact.email}."
                except Exception as e:
                    response_message = f"❌ Failed to send email: {str(e)}"
                return render_template("index.html", response=response_message, **shared_data)
            
            elif wizard["type"] == "send_invoice_email":
                contact = Contact.query.filter(Contact.name.ilike(f"%{data['contact_name']}%")).first()
                if not contact:
                    return render_template("index.html", response="❌ Contact not found.", **shared_data)
                try:
                    from utils.email_utils import send_invoice_email
                    invoice_index = int(data.get("invoice_index", 1))
                    invoices = Invoice.query.filter_by(contact_id=contact.id).order_by(Invoice.due_date.desc()).all()

                    if 1 <= invoice_index <= len(invoices):
                        invoice = invoices[invoice_index - 1]
                        send_invoice_email(contact, invoice, app)
                        response_message = f"📧 Invoice #{invoice.id} emailed to {contact.name} at {contact.email}."
                    else:
                        response_message = f"❌ Invoice index {invoice_index} out of range for {contact.name}."
                except Exception as e:
                    response_message = f"❌ Failed to send invoice email: {str(e)}"
            else:
                return render_template("index.html", response="⚠️ Unknown wizard type.", **shared_data)
        

            # Save and exit wizard
            db.session.add(obj)
            db.session.commit()
            session.pop("wizard")
            return render_template("index.html", response=f"✅ {model.__name__} created!", **shared_data)

        try:
            # Extract JSON array or object from assistant's response
            match = re.search(r"\[.*\]|\{.*\}", assistant_reply, re.DOTALL)
            if match:
                raw = match.group(0)

                # Try parsing with json, fallback to ast
                try:
                    actions = json.loads(raw)
                except json.JSONDecodeError:
                    actions = ast.literal_eval(raw)

                # If a single dict, make it a list
                if isinstance(actions, dict):
                    actions = [actions]

                response_log = []
                for parsed in actions:
                    action = parsed.get("action")

                    #if action == "create_contact":
                    #    data = parsed.get("data", {})
                    #    new_contact = Contact(
                    #        name=data.get("name"),
                    #        email=data.get("email"),
                    #        phone=data.get("phone"),
                    #        company=data.get("company"),
                    #        notes=data.get("notes", ""),
                    #        user_id=session["user_id"]
                    #    )
                    #    db.session.add(new_contact)
                    #    db.session.commit()
                    #    response_log.append(f"✅ New contact '{new_contact.name}' added to the CRM.")
                    if parsed.get("action") == "start_wizard":
                        wizard_type = parsed.get("type")
                        if wizard_type in WIZARD_SCHEMAS:
                            session["wizard"] = {
                                "type": wizard_type,
                                "step": 0,
                                "data": {}
                            }
                            prompt = WIZARD_SCHEMAS[wizard_type]["fields"][0]["prompt"]
                            return render_template("index.html", response=prompt, **shared_data)

                    elif action == "read_all_contacts":
                        contacts = Contact.query.all()
                        if not contacts:
                            response_log.append("📭 Your contact list is currently empty.")
                        else:
                            contact_lines = [
                                f"- {c.name} ({c.email}, {c.phone}, {c.company})"
                                for c in contacts
                            ]
                            response_log.append("📇 Here are your contacts:\n\n" + "\n".join(contact_lines))

                    elif action == "update_contact":
                        identifier = parsed.get("identifier", "")
                        updates = parsed.get("updates", {})
                        contact = Contact.query.filter(
                            (Contact.email.ilike(identifier)) | (Contact.name.ilike(f"%{identifier}%"))
                        ).first()

                        if not contact:
                            response_log.append(f"❌ No contact found with identifier '{identifier}'.")
                        else:
                            valid_fields = {"name", "email", "phone", "company", "status", "notes"}
                            invalid_fields = []

                            for key, value in updates.items():
                                if key in valid_fields:
                                    setattr(contact, key, value)
                                else:
                                    invalid_fields.append(key)

                            db.session.commit()
                            updated_fields = ', '.join([k for k in updates if k in valid_fields])
                            if invalid_fields:
                                response_log.append(f"⚠️ Updated: {updated_fields}. Skipped: {', '.join(invalid_fields)}")
                            else:
                                response_log.append(f"✅ Updated contact '{contact.name}': {updated_fields}.")

                    #elif action == "create_invoice":
                    #    data = parsed.get("data", {})
                    #    contact_name = data.get("contact_name", "")
                    #    contact = Contact.query.filter(Contact.name.ilike(f"%{contact_name}%")).first()

                    #    if not contact:
                    #        response_log.append(f"❌ Could not find contact '{contact_name}'")
                    #    else:
                    #        try:
                    #            raw_date = data.get("due_date", "")
                    #            parsed_date = dateparser.parse(
                    #                raw_date,
                    #                settings={
                    #                    "RELATIVE_BASE": datetime.datetime.now(),
                    #                    "PREFER_DATES_FROM": "future",
                    #                    "STRICT_PARSING": True
                    #                }
                    #            )

                    #            if parsed_date and parsed_date.date() >= datetime.date.today():
                    #                due_date = parsed_date.date()
                    #            else:
                    #                due_date = datetime.date.today()
                    #                response_log.append(f"⚠️ Could not understand due date '{raw_date}'. Defaulted to today.")

                    #            amount = float(data.get("amount"))

                    #            invoice = Invoice(
                    #                contact_id=contact.id,
                    #                due_date=due_date,
                    #                total_amount=amount,
                    #                notes=data.get("notes", ""),
                    #                user_id=session["user_id"]
                    #            )
                    #            db.session.add(invoice)
                    #            db.session.commit()

                    #            download_url = url_for('download_invoice', invoice_id=invoice.id, _external=True)
                    #            response_log.append(f"✅ Invoice created for {contact.name} - ${amount} due {due_date}.")
                    #            response_log.append(f"📄 [Download Invoice PDF]({download_url})")

                    #            response_log.append(f"✅ Invoice created for {contact.name} - ${amount} due {due_date}.")
                    #        except Exception as e:
                    #            response_log.append(f"❌ Error creating invoice: {str(e)}")


                    elif action == "read_all_invoices":
                        invoices = Invoice.query.order_by(Invoice.due_date.desc()).all()
                        if not invoices:
                            response_log.append("📭 You have no invoices on record.")
                        else:
                            lines = [
                                f"- [#{inv.id}] {inv.contact.name}: ${inv.total_amount} due {inv.due_date} ({inv.status})"
                                for inv in invoices
                            ]
                            response_log.append("📄 Here are your invoices:\n\n" + "\n".join(lines))

                    elif action == "mark_invoice_paid":
                        invoice_id = parsed.get("invoice_id")
                        invoice = Invoice.query.get(invoice_id)

                        if not invoice:
                            response_log.append(f"❌ Invoice #{invoice_id} not found.")
                        elif invoice.status == "paid":
                            response_log.append(f"⚠️ Invoice #{invoice.id} is already marked as paid.")
                        else:
                            invoice.status = "paid"
                            db.session.commit()

                            revenue_entry = Revenue(
                                invoice_id=invoice.id,
                                amount=invoice.total_amount
                            )
                            db.session.add(revenue_entry)
                            db.session.commit()

                            response_log.append(f"✅ Invoice #{invoice.id} marked as paid and ${invoice.total_amount} recorded as revenue.")

                    #elif parsed.get("action") == "log_interaction":
                    #    data = parsed.get("data", {})
                    #    contact_name = data.get("contact_name", "")
                    #    contact = Contact.query.filter(Contact.name.ilike(f"%{contact_name}%")).first()

                    #    if not contact:
                    #        response_log.append(f"❌ Could not find contact '{contact_name}'")
                    #    else:
                    #        try:
                                # Parse human-readable date or default to today
                    #            raw_date = data.get("date", "")
                    #            parsed_date = dateparser.parse(raw_date)
                    #            if parsed_date:
                    #                parsed_date = parsed_date.date()
                    #            else:
                    #                parsed_date = datetime.date.today()

                    #            interaction = Interaction(
                    #                contact_id=contact.id,
                    #                type=data.get("type"),
                    #                summary=data.get("summary"),
                    #                date=parsed_date,
                    #                user_id=session["user_id"]
                    #            )
                    #            db.session.add(interaction)
                    #            db.session.commit()
                    #            response_log.append(f"✅ Logged a {interaction.type} with {contact.name} on {parsed_date}.")
                    #        except Exception as e:
                    #            response_log.append(f"❌ Error logging interaction: {str(e)}")

                    #elif action == "download_invoice":
                    #    invoice_id = parsed.get("invoice_id")
                    #    invoice = Invoice.query.get(invoice_id)

                    #    if invoice:
                    #        download_url = url_for('download_invoice', invoice_id=invoice.id, _external=True)
                    #        response_log.append(
                    #            f"📄 <a href='{download_url}' target='_blank'>Click here to download invoice #{invoice.id}</a>"
                    #        )
                    #    else:
                    #        response_log.append(f"❌ Invoice #{invoice_id} not found.")

                    #elif action == "send_email":
                    #    to_name = parsed.get("to")
                    #    subject = parsed.get("subject", "(No subject)")
                    #    body = parsed.get("body", "")

                        # contact = Contact.query.filter(Contact.name.ilike(f"%{to_name}%")).first()

                        # if not contact:
                        #     response_log.append(f"❌ Could not find contact '{to_name}' to send email.")
                        # elif not contact.email:
                        #     response_log.append(f"❌ Contact '{to_name}' does not have an email address.")
                        # else:
                        #     try:
                        #         send_email(contact.email, subject, body)
                        #         response_log.append(f"📧 Email sent to {contact.name} at {contact.email}.")
                        #     except Exception as e:
                        #         response_log.append(f"❌ Failed to send email: {str(e)}")

                    # elif action == "send_invoice_email":
                    #     contact_name = parsed.get("contact_name")
                    #     invoice_index = parsed.get("invoice_index", 1)

                    #     try:
                    #         invoice_index = int(parsed.get("invoice_index", 1))
                    #     except ValueError:
                    #         invoice_index = 1

                        
                    #     contact = Contact.query.filter(Contact.name.ilike(f"%{contact_name}%")).first()
                    #     if not contact:
                    #         response_log.append(f"❌ Could not find contact '{contact_name}' to send email.")
                    #         continue

                    #     invoices = Invoice.query.filter_by(contact_id=contact.id).order_by(Invoice.due_date.desc()).all()

                    #     if 1 <= invoice_index <= len(invoices):
                    #         invoice = invoices[invoice_index - 1]
                    #         try:
                    #             from utils.email_utils import send_invoice_email
                    #             send_invoice_email(contact, invoice, app)
                    #             response_log.append(f"📧 Invoice #{invoice.id} emailed to {contact.name} at {contact.email}.")
                    #         except Exception as e:
                    #             response_log.append(f"❌ Failed to send invoice email: {str(e)}")
                    #     else:
                    #         response_log.append(f"❌ Invoice index {invoice_index} out of range for {contact.name}.")

                    # elif action == "create_event":
                    #     data = parsed.get("data", {})
                    #     title = data.get("title")
                    #     raw_date = data.get("date", "")
                    #     description = data.get("description", "")
                    #     location = data.get("location", "")

                    #     parsed_date = dateparser.parse(
                    #         raw_date,
                    #         settings={
                    #             "RELATIVE_BASE": datetime.datetime.now(),
                    #             "PREFER_DATES_FROM": "future",
                    #             "RETURN_AS_TIMEZONE_AWARE": False
                    #         },
                    #         languages=["en"]
                    #     )

                    #     if not parsed_date:
                    #         parsed_date = datetime.datetime.now()
                    #         response_log.append(f"⚠️ Could not understand event date '{raw_date}'. Defaulted to now.")

                    #     contact = None
                    #     if "contact_name" in data:
                    #         contact = Contact.query.filter(Contact.name.ilike(f"%{data['contact_name']}%")).first()

                    #     event = Event(
                    #         title=title,
                    #         contact_id=contact.id if contact else None,
                    #         date=parsed_date,
                    #         description=description,
                    #         location=location,
                    #         user_id=session["user_id"]
                    #     )
                    #     db.session.add(event)
                    #     db.session.commit()

                    #     who = f" with {contact.name}" if contact else ""
                    #     response_log.append(f"📅 Event created: '{title}'{who} on {parsed_date.strftime('%Y-%m-%d %H:%M')} at {location}")

                    elif action == "list_upcoming_events":
                        now = datetime.datetime.now()
                        events = Event.query.filter(Event.date >= now).order_by(Event.date.asc()).limit(5).all()

                        if not events:
                            response_log.append("📭 No upcoming events.")
                        else:
                            response_log.append("📅 Upcoming Events:")
                            for e in events:
                                who = f" with {e.contact.name}" if e.contact else ""
                                response_log.append(f"- {e.date.strftime('%b %d %I:%M %p')} — {e.title}{who}")

                    # elif action == "create_expense":
                    #     data = parsed.get("data", {})
                    #     try:
                    #         amount = float(data.get("amount"))
                    #         category = data.get("category", "Uncategorized")
                    #         description = data.get("description", "")
                    #         raw_date = data.get("date", "")

                    #         parsed_date = dateparser.parse(raw_date, settings={"RELATIVE_BASE": datetime.datetime.now()})
                    #         expense_date = parsed_date.date() if parsed_date else datetime.date.today()

                    #         expense = Expense(
                    #             amount=amount,
                    #             category=category,
                    #             description=description,
                    #             date=expense_date
                    #         )
                    #         db.session.add(expense)
                    #         db.session.commit()
                    #         response_log.append(f"💸 Logged ${amount:.2f} expense for '{category}' on {expense_date}.")
                    #     except Exception as e:
                    #         response_log.append(f"❌ Error logging expense: {str(e)}")

                    elif action == "read_expenses":
                        expenses = Expense.query.order_by(Expense.date.desc()).limit(5).all()
                        if not expenses:
                            response_log.append("📭 No expenses recorded.")
                        else:
                            response_log.append("💸 Recent Expenses:")
                            for e in expenses:
                                response_log.append(f"- ${e.amount:.2f} for {e.category} on {e.date}")

                    else:
                        response_log.append(f"⚠️ Unknown action: {parsed.get('action')}")
                response = "\n".join(response_log)
            else:
                response = assistant_reply

        except Exception as e:
            response = f"❌ Error parsing or executing actions: {str(e)}"

    user_id = session["user_id"]

    contacts = Contact.query.filter_by(user_id=user_id).all()
    invoices = Invoice.query.join(Contact).filter(Contact.user_id == user_id).all()
    interactions = Interaction.query.join(Contact).filter(Contact.user_id == user_id).order_by(Interaction.date.desc()).limit(10).all()
    events = Event.query.join(Contact, isouter=True).filter(
        (Contact.user_id == user_id) | (Event.contact_id == None)
    ).filter(Event.date >= datetime.datetime.now()).order_by(Event.date).limit(5).all()

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

    total_expenses = db.session.query(func.sum(Expense.amount)).filter_by(user_id=user_id).scalar() or 0
    monthly_expenses = (
        db.session.query(func.sum(Expense.amount))
        .filter_by(user_id=user_id)
        .filter(Expense.date >= start_of_month)
        .scalar()
        or 0
    )
    recent_expenses = Expense.query.filter_by(user_id=user_id).order_by(Expense.date.desc()).limit(5).all()

    start_of_week = datetime.datetime.now() - datetime.timedelta(days=datetime.datetime.now().weekday())
    end_of_week = start_of_week + datetime.timedelta(days=7)
    weekly_events = Event.query.join(Contact, isouter=True).filter(
        ((Contact.user_id == user_id) | (Event.contact_id == None)) &
        (Event.date >= start_of_week) & (Event.date < end_of_week)
    ).all()

    return render_template(
        "index.html",
        response=response,
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
    )


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
    return render_template("add_contact.html")

if __name__ == "__main__":
    app.run(debug=True)

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
