import csv
import io
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import joinedload
from config import Config, BASE_DIR
from database import db
from models import Document, DocumentLine, Product, StockLedger, User

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "warning"

ROLES = ("admin", "keeper", "manager")
ROLE_LABELS = {"admin": "Администратор", "keeper": "Кладовщик", "manager": "Менеджер"}


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def roles_required(*allowed):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in allowed:
                flash("Недостаточно прав для этого раздела.", "error")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)

        return wrapped

    return decorator


def admin_required(f):
    return roles_required("admin")(f)


def document_edit_roles(f):
    return roles_required("admin", "keeper")(f)


def _balance_subquery():
    return (
        db.session.query(
            StockLedger.product_id.label("pid"),
            func.coalesce(func.sum(StockLedger.quantity_signed), 0).label("qty"),
        )
        .group_by(StockLedger.product_id)
        .subquery()
    )


def current_balance(product_id: int) -> Decimal:
    q = (
        db.session.query(func.coalesce(func.sum(StockLedger.quantity_signed), 0))
        .filter(StockLedger.product_id == product_id)
        .scalar()
    )
    return Decimal(q or 0)


def balance_as_of(product_id: int, as_of: date) -> Decimal:
    q = (
        db.session.query(func.coalesce(func.sum(StockLedger.quantity_signed), 0))
        .filter(
            StockLedger.product_id == product_id,
            StockLedger.movement_date <= as_of,
        )
        .scalar()
    )
    return Decimal(q or 0)


def next_document_number(doc_type: str) -> str:
    prefix = "ПР" if doc_type == "incoming" else "РС"
    last = (
        Document.query.filter(Document.number.like(f"{prefix}-%"))
        .order_by(Document.id.desc())
        .first()
    )
    n = 1
    if last and last.number.startswith(f"{prefix}-"):
        try:
            n = int(last.number.split("-", 1)[1]) + 1
        except (ValueError, IndexError):
            n = last.id + 1
    return f"{prefix}-{n:06d}"


def post_document(doc: Document) -> tuple[bool, str]:
    if doc.status != "draft":
        return False, "Документ не в статусе черновика."
    if not doc.lines:
        return False, "Добавьте хотя бы одну строку."
    if doc.doc_type == "outgoing":
        for line in doc.lines:
            bal = current_balance(line.product_id)
            if bal < Decimal(line.quantity):
                sku = line.product.sku if line.product else ""
                return (
                    False,
                    f"Недостаточно остатка для {sku}: на складе {bal}, требуется {line.quantity}.",
                )
    sign = Decimal(1) if doc.doc_type == "incoming" else Decimal(-1)
    for line in doc.lines:
        db.session.add(
            StockLedger(
                product_id=line.product_id,
                document_id=doc.id,
                quantity_signed=Decimal(line.quantity) * sign,
                movement_date=doc.doc_date,
            )
        )
    doc.status = "posted"
    db.session.commit()
    return True, ""


def cancel_document(doc: Document) -> tuple[bool, str]:
    if doc.status != "posted":
        return False, "Отменить можно только проведённый документ."
    StockLedger.query.filter_by(document_id=doc.id).delete()
    doc.status = "cancelled"
    db.session.commit()
    return True, ""


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username, is_active=True).first()
        if user and user.check_password(password):
            login_user(user, remember=bool(request.form.get("remember")))
            flash("Вы вошли в систему.", "success")
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Неверный логин или пароль.", "error")
    return render_template("login.html", role_labels=ROLE_LABELS)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Сессия завершена.", "info")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    bsq = _balance_subquery()
    low = (
        db.session.query(Product, bsq.c.qty)
        .outerjoin(bsq, Product.id == bsq.c.pid)
        .filter(
            or_(
                and_(bsq.c.qty.isnot(None), bsq.c.qty < Product.min_stock),
                and_(bsq.c.qty.is_(None), Product.min_stock > 0),
            )
        )
        .order_by(Product.name)
        .limit(20)
        .all()
    )
    neg = (
        db.session.query(Product, bsq.c.qty)
        .outerjoin(bsq, Product.id == bsq.c.pid)
        .filter(bsq.c.qty < 0)
        .order_by(Product.name)
        .all()
    )
    draft_count = Document.query.filter_by(status="draft").count()
    return render_template(
        "dashboard.html",
        low_stock=low,
        negative=neg,
        draft_count=draft_count,
        role_labels=ROLE_LABELS,
    )


# --- Номенклатура ---


@app.route("/products")
@login_required
def products_list():
    q = (request.args.get("q") or "").strip()
    query = Product.query
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            or_(func.lower(Product.sku).like(like), func.lower(Product.name).like(like))
        )
    products = query.order_by(Product.name).all()
    bsq = _balance_subquery()
    balances = {
        r.pid: Decimal(r.qty or 0)
        for r in db.session.query(bsq).all()
    }
    return render_template(
        "products/list.html",
        products=products,
        balances=balances,
        search_q=q,
        role_labels=ROLE_LABELS,
    )


@app.route("/products/new", methods=["GET", "POST"])
@document_edit_roles
def products_new():
    if request.method == "POST":
        sku = (request.form.get("sku") or "").strip()
        name = (request.form.get("name") or "").strip()
        unit = (request.form.get("unit") or "").strip() or "шт"
        category = (request.form.get("category") or "").strip()
        try:
            min_stock = Decimal(request.form.get("min_stock") or "0")
        except Exception:
            min_stock = Decimal(0)
        err = []
        if not sku:
            err.append("Укажите артикул.")
        if not name:
            err.append("Укажите наименование.")
        if Product.query.filter_by(sku=sku).first():
            err.append("Артикул уже занят.")
        if err:
            for e in err:
                flash(e, "error")
            return render_template(
                "products/form.html",
                product=None,
                form_data=request.form,
                role_labels=ROLE_LABELS,
            )
        p = Product(sku=sku, name=name, unit=unit, category=category, min_stock=min_stock)
        db.session.add(p)
        db.session.commit()
        flash("Товар создан.", "success")
        return redirect(url_for("products_list"))
    return render_template("products/form.html", product=None, form_data=None, role_labels=ROLE_LABELS)


@app.route("/products/<int:pid>/edit", methods=["GET", "POST"])
@document_edit_roles
def products_edit(pid):
    p = Product.query.get_or_404(pid)
    if request.method == "POST":
        sku = (request.form.get("sku") or "").strip()
        name = (request.form.get("name") or "").strip()
        unit = (request.form.get("unit") or "").strip() or "шт"
        category = (request.form.get("category") or "").strip()
        try:
            min_stock = Decimal(request.form.get("min_stock") or "0")
        except Exception:
            min_stock = Decimal(0)
        err = []
        if not sku:
            err.append("Укажите артикул.")
        if not name:
            err.append("Укажите наименование.")
        other = Product.query.filter(Product.sku == sku, Product.id != p.id).first()
        if other:
            err.append("Артикул уже занят другим товаром.")
        if err:
            for e in err:
                flash(e, "error")
            return render_template(
                "products/form.html",
                product=p,
                form_data=request.form,
                role_labels=ROLE_LABELS,
            )
        p.sku, p.name, p.unit, p.category, p.min_stock = sku, name, unit, category, min_stock
        db.session.commit()
        flash("Изменения сохранены.", "success")
        return redirect(url_for("products_list"))
    return render_template("products/form.html", product=p, form_data=None, role_labels=ROLE_LABELS)


@app.route("/products/<int:pid>/delete", methods=["POST"])
@document_edit_roles
def products_delete(pid):
    p = Product.query.get_or_404(pid)
    if DocumentLine.query.filter_by(product_id=p.id).first():
        flash("Нельзя удалить товар: он используется в документах.", "error")
        return redirect(url_for("products_list"))
    db.session.delete(p)
    db.session.commit()
    flash("Товар удалён.", "info")
    return redirect(url_for("products_list"))


# --- Остатки ---


@app.route("/stock")
@login_required
def stock_view():
    bsq = _balance_subquery()
    rows = (
        db.session.query(Product, func.coalesce(bsq.c.qty, 0))
        .outerjoin(bsq, Product.id == bsq.c.pid)
        .order_by(Product.name)
        .all()
    )
    return render_template("stock.html", rows=rows, role_labels=ROLE_LABELS)


@app.route("/stock/history")
@login_required
def stock_history():
    q = (request.args.get("q") or "").strip()
    query = (
        StockLedger.query.options(joinedload(StockLedger.product), joinedload(StockLedger.document))
        .join(Document)
        .filter(Document.status == "posted")
    )
    if q:
        like = f"%{q.lower()}%"
        query = query.join(Product).filter(
            or_(func.lower(Product.sku).like(like), func.lower(Product.name).like(like))
        )
    entries = query.order_by(StockLedger.movement_date.desc(), StockLedger.id.desc()).limit(500).all()
    return render_template("stock_history.html", entries=entries, search_q=q, role_labels=ROLE_LABELS)


# --- Документы ---


@app.route("/documents")
@login_required
def documents_list():
    status = request.args.get("status") or ""
    doc_type = request.args.get("type") or ""
    query = Document.query.options(joinedload(Document.author)).order_by(Document.doc_date.desc(), Document.id.desc())
    if status in ("draft", "posted", "cancelled"):
        query = query.filter_by(status=status)
    if doc_type in ("incoming", "outgoing"):
        query = query.filter_by(doc_type=doc_type)
    docs = query.limit(200).all()
    return render_template(
        "documents/list.html",
        documents=docs,
        f_status=status,
        f_type=doc_type,
        role_labels=ROLE_LABELS,
    )


def _document_form_parse():
    doc_date_s = request.form.get("doc_date") or ""
    notes = (request.form.get("notes") or "").strip()
    err = []
    try:
        doc_date = datetime.strptime(doc_date_s, "%Y-%m-%d").date()
    except ValueError:
        doc_date = None
        err.append("Укажите корректную дату документа.")
    return doc_date, notes, err


@app.route("/documents/incoming/new", methods=["GET", "POST"])
@document_edit_roles
def documents_incoming_new():
    if request.method == "POST":
        doc_date, notes, err = _document_form_parse()
        if err:
            for e in err:
                flash(e, "error")
            return render_template(
                "documents/form.html",
                document=None,
                doc_type="incoming",
                form_data=request.form,
                products=Product.query.order_by(Product.name).all(),
                role_labels=ROLE_LABELS,
            )
        doc = Document(
            doc_type="incoming",
            number=next_document_number("incoming"),
            doc_date=doc_date,
            status="draft",
            notes=notes,
            user_id=current_user.id,
        )
        db.session.add(doc)
        db.session.commit()
        flash("Черновик приходной накладной создан. Добавьте строки.", "success")
        return redirect(url_for("documents_edit", did=doc.id))
    today = date.today().isoformat()
    return render_template(
        "documents/form.html",
        document=None,
        doc_type="incoming",
        form_data={"doc_date": today, "notes": ""},
        products=Product.query.order_by(Product.name).all(),
        role_labels=ROLE_LABELS,
    )


@app.route("/documents/outgoing/new", methods=["GET", "POST"])
@document_edit_roles
def documents_outgoing_new():
    if request.method == "POST":
        doc_date, notes, err = _document_form_parse()
        if err:
            for e in err:
                flash(e, "error")
            return render_template(
                "documents/form.html",
                document=None,
                doc_type="outgoing",
                form_data=request.form,
                products=Product.query.order_by(Product.name).all(),
                role_labels=ROLE_LABELS,
            )
        doc = Document(
            doc_type="outgoing",
            number=next_document_number("outgoing"),
            doc_date=doc_date,
            status="draft",
            notes=notes,
            user_id=current_user.id,
        )
        db.session.add(doc)
        db.session.commit()
        flash("Черновик расходной накладной создан. Добавьте строки.", "success")
        return redirect(url_for("documents_edit", did=doc.id))
    today = date.today().isoformat()
    return render_template(
        "documents/form.html",
        document=None,
        doc_type="outgoing",
        form_data={"doc_date": today, "notes": ""},
        products=Product.query.order_by(Product.name).all(),
        role_labels=ROLE_LABELS,
    )


@app.route("/documents/<int:did>", methods=["GET"])
@login_required
def documents_view(did):
    doc = Document.query.options(joinedload(Document.lines).joinedload(DocumentLine.product)).get_or_404(did)
    return render_template(
        "documents/detail.html",
        document=doc,
        balances={line.product_id: current_balance(line.product_id) for line in doc.lines},
        role_labels=ROLE_LABELS,
    )


@app.route("/documents/<int:did>/edit", methods=["GET", "POST"])
@document_edit_roles
def documents_edit(did):
    doc = Document.query.options(joinedload(Document.lines).joinedload(DocumentLine.product)).get_or_404(did)
    if doc.status != "draft":
        flash("Редактировать можно только черновик.", "warning")
        return redirect(url_for("documents_view", did=doc.id))
    if request.method == "POST":
        doc_date, notes, err = _document_form_parse()
        if err:
            for e in err:
                flash(e, "error")
        else:
            doc.doc_date = doc_date
            doc.notes = notes
            db.session.commit()
            flash("Шапка документа обновлена.", "success")
        return redirect(url_for("documents_edit", did=doc.id))
    products = Product.query.order_by(Product.name).all()
    product_balances = {p.id: current_balance(p.id) for p in products}
    return render_template(
        "documents/edit.html",
        document=doc,
        products=products,
        product_balances=product_balances,
        role_labels=ROLE_LABELS,
    )


@app.route("/documents/<int:did>/lines/add", methods=["POST"])
@document_edit_roles
def documents_line_add(did):
    doc = Document.query.get_or_404(did)
    if doc.status != "draft":
        flash("Нельзя менять проведённый документ.", "error")
        return redirect(url_for("documents_view", did=doc.id))
    try:
        product_id = int(request.form.get("product_id") or 0)
    except ValueError:
        product_id = 0
    try:
        qty = Decimal(request.form.get("quantity") or "0")
    except Exception:
        qty = Decimal(0)
    if not Product.query.get(product_id):
        flash("Выберите товар.", "error")
        return redirect(url_for("documents_edit", did=doc.id))
    if qty <= 0:
        flash("Количество должно быть больше нуля.", "error")
        return redirect(url_for("documents_edit", did=doc.id))
    db.session.add(DocumentLine(document_id=doc.id, product_id=product_id, quantity=qty))
    db.session.commit()
    flash("Строка добавлена.", "success")
    return redirect(url_for("documents_edit", did=doc.id))


@app.route("/documents/<int:did>/lines/<int:lid>/delete", methods=["POST"])
@document_edit_roles
def documents_line_delete(did, lid):
    doc = Document.query.get_or_404(did)
    if doc.status != "draft":
        flash("Нельзя менять проведённый документ.", "error")
        return redirect(url_for("documents_view", did=doc.id))
    line = DocumentLine.query.filter_by(id=lid, document_id=doc.id).first_or_404()
    db.session.delete(line)
    db.session.commit()
    flash("Строка удалена.", "info")
    return redirect(url_for("documents_edit", did=doc.id))


@app.route("/documents/<int:did>/post", methods=["POST"])
@document_edit_roles
def documents_post(did):
    doc = Document.query.options(joinedload(Document.lines)).get_or_404(did)
    ok, msg = post_document(doc)
    if ok:
        flash("Документ проведён.", "success")
        return redirect(url_for("documents_view", did=doc.id))
    flash(msg, "error")
    return redirect(url_for("documents_edit", did=doc.id) if doc.status == "draft" else url_for("documents_view", did=doc.id))


@app.route("/documents/<int:did>/cancel", methods=["POST"])
@document_edit_roles
def documents_cancel(did):
    doc = Document.query.get_or_404(did)
    ok, msg = cancel_document(doc)
    if ok:
        flash("Проведение документа отменено.", "info")
    else:
        flash(msg, "error")
    return redirect(url_for("documents_view", did=doc.id))


# --- Отчёты ---


@app.route("/reports")
@login_required
def report_index():
    return render_template("reports/index.html", role_labels=ROLE_LABELS)


@app.route("/reports/stock-date", methods=["GET", "POST"])
@login_required
def report_stock_date():
    rows = []
    as_of = None
    if request.method == "POST" or request.args.get("as_of"):
        raw = request.form.get("as_of") or request.args.get("as_of") or ""
        try:
            as_of = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            flash("Некорректная дата.", "error")
    if as_of:
        products = Product.query.order_by(Product.name).all()
        rows = [(p, balance_as_of(p.id, as_of)) for p in products]
    return render_template("reports/stock_date.html", rows=rows, as_of=as_of, role_labels=ROLE_LABELS)


@app.route("/reports/turnover", methods=["GET", "POST"])
@login_required
def report_turnover():
    rows = []
    d_from = d_to = None
    if request.method == "POST" or request.args.get("date_from"):
        df = request.form.get("date_from") or request.args.get("date_from") or ""
        dt = request.form.get("date_to") or request.args.get("date_to") or ""
        try:
            d_from = datetime.strptime(df, "%Y-%m-%d").date()
            d_to = datetime.strptime(dt, "%Y-%m-%d").date()
        except ValueError:
            flash("Укажите корректный период.", "error")
        if d_from and d_to and d_from > d_to:
            flash("Дата «с» не может быть позже «по».", "error")
            d_from = d_to = None
    if d_from and d_to:
        products = Product.query.order_by(Product.name).all()
        for p in products:
            opening = balance_as_of(p.id, d_from - timedelta(days=1))
            incoming = (
                db.session.query(func.coalesce(func.sum(StockLedger.quantity_signed), 0))
                .join(Document)
                .filter(
                    Document.status == "posted",
                    Document.doc_type == "incoming",
                    StockLedger.product_id == p.id,
                    StockLedger.movement_date >= d_from,
                    StockLedger.movement_date <= d_to,
                )
                .scalar()
            )
            outgoing = (
                db.session.query(func.coalesce(func.sum(-StockLedger.quantity_signed), 0))
                .join(Document)
                .filter(
                    Document.status == "posted",
                    Document.doc_type == "outgoing",
                    StockLedger.product_id == p.id,
                    StockLedger.movement_date >= d_from,
                    StockLedger.movement_date <= d_to,
                )
                .scalar()
            )
            inc = Decimal(incoming or 0)
            out = Decimal(outgoing or 0)
            closing = opening + inc - out
            rows.append((p, opening, inc, out, closing))
    return render_template(
        "reports/turnover.html",
        rows=rows,
        date_from=d_from,
        date_to=d_to,
        role_labels=ROLE_LABELS,
    )


@app.route("/reports/movement", methods=["GET", "POST"])
@login_required
def report_movement():
    entries = []
    product = None
    if request.method == "POST" or request.args.get("product_id"):
        try:
            pid = int(request.form.get("product_id") or request.args.get("product_id") or 0)
        except ValueError:
            pid = 0
        product = Product.query.get(pid)
        if product:
            entries = (
                StockLedger.query.options(joinedload(StockLedger.document))
                .join(Document)
                .filter(
                    Document.status == "posted",
                    StockLedger.product_id == pid,
                )
                .order_by(StockLedger.movement_date, StockLedger.id)
                .all()
            )
        elif pid:
            flash("Товар не найден.", "error")
    products = Product.query.order_by(Product.name).all()
    return render_template(
        "reports/movement.html",
        entries=entries,
        product=product,
        products=products,
        role_labels=ROLE_LABELS,
    )


# --- Экспорт CSV ---


def _csv_response(filename: str, rows: list[list]):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    for row in rows:
        w.writerow(row)
    data = buf.getvalue().encode("utf-8-sig")
    return send_file(
        io.BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/reports/stock-date/export")
@login_required
def report_stock_date_export():
    raw = request.args.get("as_of") or ""
    try:
        as_of = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        flash("Укажите дату в параметре as_of.", "error")
        return redirect(url_for("report_stock_date"))
    products = Product.query.order_by(Product.name).all()
    rows = [["Артикул", "Наименование", "Ед.", "Остаток на дату", str(as_of)]]
    for p in products:
        rows.append([p.sku, p.name, p.unit, str(balance_as_of(p.id, as_of))])
    return _csv_response(f"ostatki_{as_of}.csv", rows)


@app.route("/reports/turnover/export")
@login_required
def report_turnover_export():
    df = request.args.get("date_from") or ""
    dt = request.args.get("date_to") or ""
    try:
        d_from = datetime.strptime(df, "%Y-%m-%d").date()
        d_to = datetime.strptime(dt, "%Y-%m-%d").date()
    except ValueError:
        flash("Укажите период.", "error")
        return redirect(url_for("report_turnover"))
    products = Product.query.order_by(Product.name).all()
    rows = [["Артикул", "Наименование", "Начало", "Приход", "Расход", "Конец"]]
    for p in products:
        opening = balance_as_of(p.id, d_from - timedelta(days=1))
        incoming = (
            db.session.query(func.coalesce(func.sum(StockLedger.quantity_signed), 0))
            .join(Document)
            .filter(
                Document.status == "posted",
                Document.doc_type == "incoming",
                StockLedger.product_id == p.id,
                StockLedger.movement_date >= d_from,
                StockLedger.movement_date <= d_to,
            )
            .scalar()
        )
        outgoing = (
            db.session.query(func.coalesce(func.sum(-StockLedger.quantity_signed), 0))
            .join(Document)
            .filter(
                Document.status == "posted",
                Document.doc_type == "outgoing",
                StockLedger.product_id == p.id,
                StockLedger.movement_date >= d_from,
                StockLedger.movement_date <= d_to,
            )
            .scalar()
        )
        inc = Decimal(incoming or 0)
        out = Decimal(outgoing or 0)
        closing = opening + inc - out
        rows.append([p.sku, p.name, str(opening), str(inc), str(out), str(closing)])
    return _csv_response(f"oborot_{d_from}_{d_to}.csv", rows)


# --- Аналитика ---


@app.route("/analytics")
@login_required
def analytics():
    products = Product.query.order_by(Product.name).all()
    return render_template("reports/analytics.html", products=products, role_labels=ROLE_LABELS)


@app.route("/api/analytics/movement")
@login_required
def api_movement_chart():
    try:
        pid = int(request.args.get("product_id") or 0)
    except ValueError:
        pid = 0
    if not Product.query.get(pid):
        return jsonify(labels=[], values=[])
    rows = (
        db.session.query(StockLedger.movement_date, func.sum(StockLedger.quantity_signed))
        .join(Document)
        .filter(Document.status == "posted", StockLedger.product_id == pid)
        .group_by(StockLedger.movement_date)
        .order_by(StockLedger.movement_date)
        .all()
    )
    labels = [r[0].isoformat() for r in rows]
    values = [float(r[1]) for r in rows]
    return jsonify(labels=labels, values=values)


@app.route("/api/analytics/abc")
@login_required
def api_abc():
    df = request.args.get("date_from") or ""
    dt = request.args.get("date_to") or ""
    try:
        d_from = datetime.strptime(df, "%Y-%m-%d").date()
        d_to = datetime.strptime(dt, "%Y-%m-%d").date()
    except ValueError:
        return jsonify(items=[])
    q = (
        db.session.query(
            Product.id,
            Product.sku,
            Product.name,
            func.sum(func.abs(StockLedger.quantity_signed)).label("turnover"),
        )
        .join(StockLedger, StockLedger.product_id == Product.id)
        .join(Document)
        .filter(
            Document.status == "posted",
            StockLedger.movement_date >= d_from,
            StockLedger.movement_date <= d_to,
        )
        .group_by(Product.id)
        .order_by(func.sum(func.abs(StockLedger.quantity_signed)).desc())
        .all()
    )
    total = sum(float(r.turnover or 0) for r in q) or 1.0
    cum = 0.0
    items = []
    for r in q:
        v = float(r.turnover or 0)
        share = v / total * 100
        cum += share
        cls = "A" if cum <= 80 else ("B" if cum <= 95 else "C")
        items.append(
            {
                "sku": r.sku,
                "name": r.name,
                "turnover": v,
                "share": round(share, 2),
                "cum_share": round(cum, 2),
                "class": cls,
            }
        )
    return jsonify(items=items)


# --- Пользователи (админ) ---


@app.route("/users")
@admin_required
def users_list():
    users = User.query.order_by(User.username).all()
    return render_template("users/list.html", users=users, role_labels=ROLE_LABELS)


@app.route("/users/new", methods=["GET", "POST"])
@admin_required
def users_new():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        role = request.form.get("role") or "keeper"
        if role not in ROLES:
            role = "keeper"
        err = []
        if len(username) < 2:
            err.append("Логин слишком короткий.")
        if len(password) < 4:
            err.append("Пароль не короче 4 символов.")
        if User.query.filter_by(username=username).first():
            err.append("Такой пользователь уже есть.")
        if err:
            for e in err:
                flash(e, "error")
            return render_template("users/form.html", user=None, form_data=request.form, roles=ROLES, role_labels=ROLE_LABELS)
        u = User(username=username, role=role)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        flash("Пользователь создан.", "success")
        return redirect(url_for("users_list"))
    return render_template("users/form.html", user=None, form_data=None, roles=ROLES, role_labels=ROLE_LABELS)


@app.route("/users/<int:uid>/edit", methods=["GET", "POST"])
@admin_required
def users_edit(uid):
    u = User.query.get_or_404(uid)
    if request.method == "POST":
        role = request.form.get("role") or u.role
        if role not in ROLES:
            role = u.role
        is_active = request.form.get("is_active") == "1"
        password = request.form.get("password") or ""
        u.role = role
        u.is_active = is_active
        if password:
            if len(password) < 4:
                flash("Пароль не короче 4 символов.", "error")
                return render_template("users/form.html", user=u, form_data=request.form, roles=ROLES, role_labels=ROLE_LABELS)
            u.set_password(password)
        db.session.commit()
        flash("Данные пользователя обновлены.", "success")
        return redirect(url_for("users_list"))
    return render_template("users/form.html", user=u, form_data=None, roles=ROLES, role_labels=ROLE_LABELS)


# --- Настройки / резервная копия ---


@app.route("/settings")
@admin_required
def settings():
    path = os.path.join(BASE_DIR, "warehouse.db")
    exists = os.path.isfile(path)
    size = os.path.getsize(path) if exists else 0
    return render_template("settings.html", db_exists=exists, db_size=size, role_labels=ROLE_LABELS)


@app.route("/settings/backup")
@admin_required
def backup_db():
    path = os.path.join(BASE_DIR, "warehouse.db")
    if not os.path.isfile(path):
        flash("Файл базы не найден.", "error")
        return redirect(url_for("settings"))
    return send_file(
        path,
        as_attachment=True,
        download_name=f"warehouse_backup_{date.today().isoformat()}.db",
        mimetype="application/octet-stream",
    )


@app.context_processor
def inject_globals():
    return {
        "ROLE_LABELS": ROLE_LABELS,
        "today_iso": date.today().isoformat(),
        "wms_version": "1.0.0",
        "now_year": date.today().year,
    }


def init_db():
    with app.app_context():
        db.create_all()
        if User.query.count() == 0:
            admin = User(username="admin", role="admin")
            admin.set_password("admin")
            db.session.add(admin)
            db.session.commit()


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)
