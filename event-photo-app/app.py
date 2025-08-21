# app.py
import os
import qrcode
from flask import Flask, render_template, request, redirect, url_for, session, send_file
import requests
from io import BytesIO
from datetime import datetime
import secrets

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)  # for session security

# Replace with your bot token + chat ID
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_USER_ID"

# Store events in memory (can be DB later)
events = {}

# Simple login (change username/password)
ADMIN_USER = "admin"
ADMIN_PASS = "1234"

# -------------- ROUTES -----------------

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form["username"] == ADMIN_USER and request.form["password"] == ADMIN_PASS:
            session["admin"] = True
            return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "admin" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        event_name = request.form["event_name"]
        event_date = request.form["event_date"]
        limit = int(request.form["limit"])

        event_id = secrets.token_hex(4)
        events[event_id] = {
            "name": event_name,
            "date": event_date,
            "limit": limit,
            "uploads": 0
        }

        # Generate QR link
        upload_link = request.url_root + "upload/" + event_id
        img = qrcode.make(upload_link)
        qr_path = f"static/{event_id}.png"
        img.save(qr_path)

        return render_template("dashboard.html", events=events, qr_path=qr_path, link=upload_link)

    return render_template("dashboard.html", events=events)

@app.route("/upload/<event_id>", methods=["GET", "POST"])
def upload(event_id):
    if event_id not in events:
        return "Event not found"

    event = events[event_id]

    if request.method == "POST":
        if event["uploads"] >= event["limit"]:
            return "❌ Limit reached. Thank you for uploading."

        file = request.files["photo"]
        if file:
            file_bytes = file.read()

            # Send to Telegram
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
            files = {"document": file_bytes}
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": f"📸 New upload for {event['name']} ({event['date']})"
            }
            requests.post(url, data=data, files={"document": file})

            event["uploads"] += 1
            return "✅ Uploaded successfully!"

    return render_template("upload.html", event=event)

# -------------- START -----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
