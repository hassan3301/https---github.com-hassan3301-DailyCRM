import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv
from weasyprint import HTML
from flask import render_template
from models import Contact, Invoice
import os

load_dotenv()

def send_email(to_email, subject, body, pdf_bytes=None, filename=None):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.getenv("EMAIL_ADDRESS")
    msg["To"] = to_email
    msg.set_content(body)

    if pdf_bytes:
        msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=filename)

    with smtplib.SMTP_SSL(os.getenv("EMAIL_HOST"), int(os.getenv("EMAIL_PORT"))) as smtp:
        smtp.login(os.getenv("EMAIL_ADDRESS"), os.getenv("EMAIL_PASSWORD"))
        smtp.send_message(msg)


def send_invoice_email(contact, invoice, app):
    with app.app_context():
        rendered_html = render_template("invoice_pdf.html", contact=contact, invoice=invoice)
    pdf = HTML(string=rendered_html).write_pdf()
    
    subject = f"Invoice #{invoice.id} from Daily"
    body = f"Hi {contact.name},\n\nPlease find attached your invoice for ${invoice.total_amount} due {invoice.due_date}.\n\nThank you!"
    
    send_email(contact.email, subject, body, pdf_bytes=pdf, filename=f"invoice_{invoice.id}.pdf")
