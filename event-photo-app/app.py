import os
import secrets
from datetime import datetime
from io import BytesIO

import qrcode
import requests
from flask import (
    Flask, render_template, request, redirect, url_for, session,
    send_from_directory, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

# ----------------------------
# App & Config
# ----------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(16))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'  # (Optional: not used when sending to Telegram)
app.config['QRCODE_FOLDER'] = 'static/qrcodes'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB per Telegram Bot API limit

# Telegram settings (required)
BOT_TOKEN = os.environ.get('BOT_TOKEN', '').strip()
CHAT_ID = os.environ.get('CHAT_ID', '').strip()  # your user/channel/group id

# Ensure folders
os.makedirs(app.config['QRCODE_FOLDER'], exist_ok=True)
os.makedirs('static', exist_ok=True)

# DB init
db = SQLAlchemy(app)

# ----------------------------
# Database Models
# ----------------------------
class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)  # simple storage for demo

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(32), unique=True, nullable=False)  # public token
    name = db.Column(db.String(200), nullable=False)
    date = db.Column(db.String(50), nullable=False)
    limit = db.Column(db.Integer, nullable=False)
    count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ----------------------------
# Helpers
# ----------------------------

def require_login():
    return "user" in session

def send_telegram_message(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return False, "Missing BOT_TOKEN or CHAT_ID"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=20,
        )
        return r.ok, r.text
    except Exception as e:
        return False, str(e)


def send_telegram_document(file_storage, caption: str):
    """Send uploaded file to Telegram as full-quality document."""
    if not BOT_TOKEN or not CHAT_ID:
        return False, "Missing BOT_TOKEN or CHAT_ID"
    try:
        files = {
            'document': (secure_filename(file_storage.filename), file_storage.stream, file_storage.mimetype)
        }
        data = {"chat_id": CHAT_ID, "caption": caption}
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
            files=files,
            data=data,
            timeout=60,
        )
        return r.ok, r.text
    except Exception as e:
        return False, str(e)


def generate_qr_for_link(link: str, outfile: str):
    img = qrcode.make(link)
    img.save(outfile)

# ----------------------------
# Routes: Auth
# ----------------------------
@app.route("/")
def home():
    if require_login():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()
        user = Admin.query.filter_by(username=u, password=p).first()
        if user:
            session["user"] = user.username
            return redirect(url_for("dashboard"))
        else:
            error = "Invalid username or password"
    return render_template("login.html", error=error)

@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()
        if not u or not p:
            error = "Username and password required"
        elif Admin.query.filter_by(username=u).first():
            error = "Username already taken"
        else:
            db.session.add(Admin(username=u, password=p))
            db.session.commit()
            return redirect(url_for("login"))
    return render_template("register.html", error=error)

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))

# ----------------------------
# Routes: Dashboard & Events
# ----------------------------
@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if not require_login():
        return redirect(url_for("login"))

    created = None
    if request.method == "POST":
        name = request.form.get("event_name", "").strip()
        date = request.form.get("event_date", "").strip()
        limit = request.form.get("limit", "").strip()

        try:
            n_limit = int(limit)
        except:
            n_limit = 0

        if not name or not date or n_limit < 100 or n_limit > 5000:
            created = {"error": "Please provide name/date and a limit between 100 and 5000."}
        else:
            token = secrets.token_hex(6)
            ev = Event(event_id=token, name=name, date=date, limit=n_limit, count=0)
            db.session.add(ev)
            db.session.commit()

            # Announce new event in Telegram (so your chat is separated)
            send_telegram_message(f"📸 NEW EVENT\n{name}\nDate: {date}\nLimit: {n_limit}")

            # Generate public upload link + QR
            upload_link = url_for('guest_upload', event_id=token, _external=True)
            qr_path = os.path.join(app.config['QRCODE_FOLDER'], f"{token}.png")
            generate_qr_for_link(upload_link, qr_path)

            created = {
                "event_id": token,
                "name": name,
                "date": date,
                "limit": n_limit,
                "upload_link": upload_link,
                "qr_path": "/" + qr_path.replace('\\', '/')
            }

    events = Event.query.order_by(Event.created_at.desc()).all()
    return render_template("dashboard.html", user=session.get("user"), events=events, created=created)

# Public: serve QR image if needed
@app.route('/qr/<event_id>.png')
def qr_image(event_id):
    path = os.path.join(app.config['QRCODE_FOLDER'], f"{event_id}.png")
    if not os.path.exists(path):
        return "QR not found", 404
    return send_from_directory(app.config['QRCODE_FOLDER'], f"{event_id}.png")

# Public guest upload page
@app.route("/upload/<event_id>", methods=["GET", "POST"])
def guest_upload(event_id):
    ev = Event.query.filter_by(event_id=event_id).first()
    if not ev:
        return render_template("upload.html", not_found=True)

    if request.method == "POST":
        # Check limit first
        if ev.count >= ev.limit:
            return render_template("upload.html", event=ev, limit_reached=True)

        file = request.files.get("photo")
        if not file or file.filename == "":
            return render_template("upload.html", event=ev, error="Please choose a file.")

        # Send header (optional) and then the document to Telegram
        send_telegram_message(f"📸 Event: {ev.name}\n📅 Date: {ev.date}")
        ok, resp = send_telegram_document(file, caption=f"Upload for {ev.name} ({ev.date}) — {secure_filename(file.filename)}")
        if not ok:
            return render_template("upload.html", event=ev, error="Failed to send to Telegram. Check BOT_TOKEN/CHAT_ID or file size (max ~50MB).")

        # Increase count
        ev.count += 1
        db.session.commit()

        return render_template("upload.html", event=ev, success=True)

    # GET
    return render_template("upload.html", event=ev)

# Serve uploaded files if you ever store locally (not required when using Telegram only)
@app.route("/uploads/<path:filename>")
def serve_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ----------------------------
# Bootstrap DB on first run
# ----------------------------
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

    app.run(host="0.0.0.0", port=5000)
