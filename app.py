from pathlib import Path
from functools import wraps
from decimal import Decimal, InvalidOperation
import hmac
import os
import secrets
import shutil
import sqlite3
import uuid

from flask import Flask, flash, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent


def load_local_env():
    env_file = BASE_DIR / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


load_local_env()

DATA_DIR_SETTING = os.environ.get("ECOMATES_DATA_DIR")
if DATA_DIR_SETTING:
    DATA_DIR = Path(DATA_DIR_SETTING).expanduser()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR = DATA_DIR / "uploads"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH = DATA_DIR / "ecomates.db"

    # Seed a new persistent disk from files already present in the deployed project.
    legacy_db = BASE_DIR / "ecomates.db"
    if not DB_PATH.exists() and legacy_db.exists():
        shutil.copy2(legacy_db, DB_PATH)
    legacy_uploads = BASE_DIR / "static" / "uploads"
    if legacy_uploads.is_dir():
        for old_image in legacy_uploads.iterdir():
            new_image = UPLOAD_DIR / old_image.name
            if old_image.is_file() and not new_image.exists():
                shutil.copy2(old_image, new_image)
else:
    # Keep local development data in the existing project folders.
    UPLOAD_DIR = BASE_DIR / "static" / "uploads"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH = BASE_DIR / "ecomates.db"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}

app = Flask(__name__)
app.secret_key = os.environ.get("ECOMATES_SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped_view


def connect_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with connect_db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            price REAL NOT NULL DEFAULT 0,
            image TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        columns = {row[1] for row in db.execute("PRAGMA table_info(products)")}
        if "price" not in columns:
            db.execute("ALTER TABLE products ADD COLUMN price REAL NOT NULL DEFAULT 0")


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.get("/")
def index():
    with connect_db() as db:
        products = db.execute("SELECT * FROM products ORDER BY created_at DESC, id DESC").fetchall()
    return render_template("index.html", products=products)


@app.get("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin"))

    username = os.environ.get("ECOMATES_ADMIN_USERNAME")
    password = os.environ.get("ECOMATES_ADMIN_PASSWORD")
    if request.method == "POST":
        submitted_username = request.form.get("username", "")
        submitted_password = request.form.get("password", "")
        if not username or not password:
            flash("Admin login is not configured. Set ECOMATES_ADMIN_USERNAME and ECOMATES_ADMIN_PASSWORD, then restart the app.", "error")
        elif hmac.compare_digest(submitted_username.encode("utf-8"), username.encode("utf-8")) and hmac.compare_digest(submitted_password.encode("utf-8"), password.encode("utf-8")):
            session.clear()
            session["admin_logged_in"] = True
            destination = request.args.get("next", "")
            if not destination.startswith("/admin") or destination.startswith("//"):
                destination = url_for("admin")
            return redirect(destination)
        else:
            flash("The username or password was incorrect.", "error")

    return render_template("login.html")


@app.post("/admin/logout")
@admin_required
def admin_logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("admin_login"))


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        try:
            count = int(request.form.get("count", "0"))
        except ValueError:
            count = -1
        try:
            price = Decimal(request.form.get("price", ""))
        except InvalidOperation:
            price = Decimal("-1")
        image = request.files.get("image")

        if not name or not description or count < 0 or not price.is_finite() or price < 0:
            flash("Add a product name, description, valid price, and a count of zero or more.", "error")
        elif not image or not image.filename or not allowed_file(image.filename):
            flash("Choose a PNG, JPG, JPEG, WEBP, or GIF product image.", "error")
        else:
            suffix = Path(secure_filename(image.filename)).suffix.lower()
            filename = f"{uuid.uuid4().hex}{suffix}"
            image.save(UPLOAD_DIR / filename)
            with connect_db() as db:
                db.execute(
                    "INSERT INTO products (name, description, count, price, image) VALUES (?, ?, ?, ?, ?)",
                    (name, description, count, float(price), filename),
                )
            flash(f"{name} has been added to the collection.", "success")
            return redirect(url_for("admin"))

    with connect_db() as db:
        products = db.execute("SELECT * FROM products ORDER BY created_at DESC, id DESC").fetchall()
    return render_template("admin.html", products=products)


@app.post("/admin/delete/<int:product_id>")
@admin_required
def delete_product(product_id):
    with connect_db() as db:
        product = db.execute("SELECT image FROM products WHERE id = ?", (product_id,)).fetchone()
        if product:
            db.execute("DELETE FROM products WHERE id = ?", (product_id,))
            image_path = UPLOAD_DIR / product["image"]
            if image_path.exists():
                image_path.unlink()
            flash("Product removed.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/edit/<int:product_id>")
@admin_required
def edit_product(product_id):
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    try:
        count = int(request.form.get("count", "0"))
    except ValueError:
        count = -1
    try:
        price = Decimal(request.form.get("price", ""))
    except InvalidOperation:
        price = Decimal("-1")
    image = request.files.get("image")

    if not name or not description or count < 0 or not price.is_finite() or price < 0:
        flash("Add a product name, description, valid price, and a count of zero or more.", "error")
        return redirect(url_for("admin"))
    if image and image.filename and not allowed_file(image.filename):
        flash("Choose a PNG, JPG, JPEG, WEBP, or GIF product image.", "error")
        return redirect(url_for("admin"))

    with connect_db() as db:
        product = db.execute("SELECT image FROM products WHERE id = ?", (product_id,)).fetchone()
        if not product:
            flash("That product could not be found.", "error")
            return redirect(url_for("admin"))

        filename = product["image"]
        if image and image.filename:
            suffix = Path(secure_filename(image.filename)).suffix.lower()
            new_filename = f"{uuid.uuid4().hex}{suffix}"
            image.save(UPLOAD_DIR / new_filename)
            old_image_path = UPLOAD_DIR / filename
            if old_image_path.exists():
                old_image_path.unlink()
            filename = new_filename

        db.execute(
            "UPDATE products SET name = ?, description = ?, count = ?, price = ?, image = ? WHERE id = ?",
            (name, description, count, float(price), filename, product_id),
        )
    flash(f"{name} has been updated.", "success")
    return redirect(url_for("admin"))


init_db()

if __name__ == "__main__":
    app.run(debug=True)
