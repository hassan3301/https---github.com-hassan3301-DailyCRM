from flask_sqlalchemy import SQLAlchemy
import datetime
db = SQLAlchemy()

class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    company = db.Column(db.String(100))
    notes = db.Column(db.Text)
    status = db.Column(db.String(20), default='lead')

    def __repr__(self):
        return f"<Contact {self.name}>"


class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=False)
    issue_date = db.Column(db.Date, default=datetime.date.today)
    due_date = db.Column(db.Date)
    total_amount = db.Column(db.Float)
    status = db.Column(db.String(20), default='unpaid')  # could be 'unpaid', 'paid'
    notes = db.Column(db.Text)

    contact = db.relationship('Contact', backref=db.backref('invoices', lazy=True))


class Revenue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, default=datetime.date.today)

    invoice = db.relationship('Invoice', backref=db.backref('revenue', uselist=False))

class Interaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=False)
    date = db.Column(db.Date, default=datetime.date.today)
    type = db.Column(db.String(20))  # e.g. 'call', 'email', 'meeting', 'note'
    summary = db.Column(db.Text)

    contact = db.relationship('Contact', backref=db.backref('interactions', lazy=True))


class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=True)  # Optional
    title = db.Column(db.String(200), nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    description = db.Column(db.Text)
    location = db.Column(db.String(200))

    contact = db.relationship('Contact', backref=db.backref('events', lazy=True))

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    date = db.Column(db.Date, default=datetime.date.today)
