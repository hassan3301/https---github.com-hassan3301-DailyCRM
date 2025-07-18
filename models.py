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

    line_items = db.relationship('InvoiceLineItem', back_populates='invoice', cascade="all, delete-orphan")




class Revenue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
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

class ExpenseCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False, unique=False)

    user = db.relationship('User', backref=db.backref('expense_categories', lazy=True))
    expenses = db.relationship('Expense', back_populates='category', cascade='all, delete-orphan')


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('expense_category.id'), nullable=False)
    description = db.Column(db.Text)
    date = db.Column(db.Date, default=datetime.today)

    user = db.relationship('User', backref=db.backref('expenses', lazy=True))
    category = db.relationship('ExpenseCategory', back_populates='expenses')


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Float, nullable=False)

    user = db.relationship('User', backref=db.backref('products', lazy=True))

    def __repr__(self):
        return f"<Product {self.name} - ${self.price}>"


class InvoiceLineItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)  # snapshot of product.price at time of creation

    invoice = db.relationship('Invoice', back_populates='line_items')
    product = db.relationship('Product')

    @property
    def line_total(self):
        return self.quantity * self.unit_price

class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    report_type = db.Column(db.String(50))
    period = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.today)
    title = db.Column(db.String(255))
    description = db.Column(db.Text)
    file_path = db.Column(db.String(255))

