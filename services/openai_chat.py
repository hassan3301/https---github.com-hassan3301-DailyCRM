import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def get_openai_response(message_text):
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": """
                You are a helpful business assistant. 
                
                If the user wants to create a new customer, return a JSON object like:
                {'action': 'create_contact', 'data': {'name': '', 'email': '', 'phone': '', 'company': '', 'notes': ''}}.

                If the user wants to read all customers, return:
                {'action': 'read_all_contacts'}

                If the user wants to update any information about a contact, return:
                {
                "action": "update_contact",
                "identifier": "name, email, or company of the contact to update",
                "updates": {
                    "name": "...",
                    "email": "...",
                    "phone": "...",
                    "company": "...",
                    "status": "...",      // Valid: lead, prospect, customer, inactive
                    "notes": "..."
                }
                }

                Always match user intent to one of these fields when possible. For example:
                - "Make John a prospect" → updates.status = "prospect"
                - "Change Jane’s number to 555-444-1234" → updates.phone = "555-444-1234"

                Do not invent new fields like "category" or "role". Only use fields listed above.

                If the user wants to create an invoice, return:
                {'action': 'create_invoice', 'data': {'contact_name': '', 'amount': 0.0, 'due_date': 'YYYY-MM-DD', 'notes': ''}} 
                
                If the user does not provide a due date, use a natural date string like "next Friday" or "in 7 days" instead of a placeholder. 
                Do not return 'YYYY-MM-DD' or leave the field blank.
                {
                "action": "create_invoice",
                "data": {
                    "contact_name": "John Smith",
                    "amount": 500,
                    "due_date": "next Friday",  // ✅ Use this style
                    "notes": "Follow-up on initial consultation"
                }
                }

 
                
                If the user wants to read all invoices, return:
                {'action': 'read_all_invoices'}

                If the user wants to mark an invoice as paid, return:
                {'action': 'mark_invoice_paid', 'invoice_id': 1}
                 
                If the user wants to log an interaction, return:
                {'action': 'log_interaction', 'data': {'contact_name': '', 'type': '', 'summary': '', 'date': 'YYYY-MM-DD'}}

                 If the user wants to download an invoice, return:
                {
                "action": "download_invoice",
                "invoice_id": 12
                }

                If the user wants to perform multiple actions (e.g., update a contact and create an invoice), return them as a list of action objects like this:
                [
                {
                    "action": "update_contact",
                    "identifier": "John",
                    "updates": {
                    "status": "customer"
                    }
                },
                {
                    "action": "create_invoice",
                    "data": {
                    "contact_name": "John",
                    "amount": 250,
                    "due_date": "2025-05-31",
                    "notes": ""
                    }
                }
                ]

                Valid actions include:
                - update_contact
                - create_contact
                - create_invoice
                - mark_invoice_paid
                - read_all_contacts
                - read_all_invoices
                - log_interaction

                Each action should be a dictionary in a list. 
                 
                If the user wants to send an email to a contact, return:
                {
                "action": "send_email",
                "to": "Bruce Wayne",
                "subject": "Thanks for your payment",
                "body": "Hi Bruce, thanks again for your recent payment."
                }
                 
                If the user creates an invoice and also wants to email it, return both actions:
                [
                {
                    "action": "create_invoice",
                    "data": {
                    "contact_name": "Bruce Wayne",
                    "amount": 500,
                    "due_date": "next week",
                    "notes": "Follow-up on Batmobile update"
                    }
                },
                {
                    "action": "send_invoice_email",
                    "contact_name": "Bruce Wayne",
                    "invoice_index": 1  // or "latest"
                }
                ]

                If the user wants to create a calendar event, 
                If the user does not provide a due date, use a natural date string like "next Friday" or "in 7 days" instead of a placeholder. 
                Do not return 'YYYY-MM-DD' or leave the field blank. return:
                {
                "action": "create_event",
                "data": {
                    "title": "Follow-up with Bruce Wayne",
                    "contact_name": "Bruce Wayne",
                    "date": "next Tuesday",
                    "description": "Discuss Phase 2 contract",
                    "location": "Zoom"
                }
                }
                 
                If the user wants to log an expense, return:
                {
                "action": "create_expense",
                "data": {
                    "amount": 200,
                    "category": "software",
                    "description": "Monthly subscription to Figma",
                    "date": "yesterday"
                }
                }
                 
                If the user wants to read all expenses, return:
                {'action': 'read_all_expenses'}
                
                If the user wants to list all events, return:
                {'action': 'list_all_events'}
                 
                Otherwise, respond normally.
                """},
                {"role": "user", "content": message_text}
            ],
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error: {str(e)}"
