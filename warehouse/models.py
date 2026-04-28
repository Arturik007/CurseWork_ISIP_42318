from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from database import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="keeper")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    documents = db.relationship("Document", backref="author", lazy="dynamic")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    unit = db.Column(db.String(32), nullable=False, default="шт")
    category = db.Column(db.String(128), nullable=False, default="")
    min_stock = db.Column(db.Numeric(18, 4), nullable=False, default=0)

    lines = db.relationship("DocumentLine", backref="product", lazy="dynamic")


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    doc_type = db.Column(db.String(16), nullable=False)
    number = db.Column(db.String(32), unique=True, nullable=False, index=True)
    doc_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="draft")
    notes = db.Column(db.Text, nullable=False, default="")
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    lines = db.relationship(
        "DocumentLine",
        backref="document",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="DocumentLine.id",
    )


class DocumentLine(db.Model):
    __tablename__ = "document_lines"

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    quantity = db.Column(db.Numeric(18, 4), nullable=False)


class StockLedger(db.Model):
    __tablename__ = "stock_ledger"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id"), nullable=False, index=True)
    quantity_signed = db.Column(db.Numeric(18, 4), nullable=False)
    movement_date = db.Column(db.Date, nullable=False)

    product = db.relationship("Product", lazy="joined")
    document = db.relationship("Document", lazy="joined")
