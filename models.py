from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from flask_login import UserMixin
db = SQLAlchemy()

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255))
    ai_session_id  = db.Column(db.String(64), nullable=True)

    def is_active(self):
        return True

    def get_id(self):
        return str(self.id)

# Contact belongs to a user
class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    company = db.Column(db.String(100))
    notes = db.Column(db.Text)
    status = db.Column(db.String(20), default='lead')

    user = db.relationship('User', backref=db.backref('contacts', lazy=True))

    def __repr__(self):
        return f"<Contact {self.name}>"

# Invoice belongs to a user and a contact
class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=False)
    issue_date = db.Column(db.Date, default=datetime.today)
    due_date = db.Column(db.Date)
    total_amount = db.Column(db.Float)
    status = db.Column(db.String(20), default='unpaid')  # 'unpaid', 'paid'
    notes = db.Column(db.Text)

    user = db.relationship('User', backref=db.backref('invoices', lazy=True))
    contact = db.relationship('Contact', backref=db.backref('invoices', lazy=True))

class Revenue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, default=datetime.today)

    invoice = db.relationship('Invoice', backref=db.backref('revenue', uselist=False))

class Interaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=False)
    date = db.Column(db.Date, default=datetime.today)
    type = db.Column(db.String(20))  # 'call', 'email', etc.
    summary = db.Column(db.Text)

    user = db.relationship('User', backref=db.backref('interactions', lazy=True))
    contact = db.relationship('Contact', backref=db.backref('interactions', lazy=True))

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    description = db.Column(db.Text)
    location = db.Column(db.String(200))

    user = db.relationship('User', backref=db.backref('events', lazy=True))
    contact = db.relationship('Contact', backref=db.backref('events', lazy=True))

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    date = db.Column(db.Date, default=datetime.today)

    user = db.relationship('User', backref=db.backref('expenses', lazy=True))
