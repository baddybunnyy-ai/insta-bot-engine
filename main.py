import os
import time
import sqlite3
import urllib.parse
import requests
import telebot
from telebot import types
import yt_dlp
from flask import Flask
from threading import Thread

BOT_TOKEN = "8991187008:AAEmpfwuA3JUKLAuWYFjkgsnyHhbEcZFY4E"
BOT_USERNAME = "InstaReelsSaverX_bot"
GPLINKS_API_KEY = "20697e4a93aa9b4e560cd3cdc2fdb642da367ce4"

bot = telebot.TeleBot(BOT_TOKEN)

# SQLite Database Setup (Persistent VIP Pass & Verification Tokens)
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS vip_users 
                 (user_id INTEGER PRIMARY KEY, expiry_time REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS pending_tokens 
                 (token TEXT PRIMARY KEY, user_id INTEGER, created_at REAL)''')
    conn.commit()
    conn.close()

def set_vip_pass(user_id):
    expiry = time.time() + (24 * 3600)  # 24 Hours VIP Pass
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO vip_users VALUES (?, ?)", (user_id, expiry))
    conn.commit()
    conn.close()

def is_vip_active(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT expiry_time FROM vip_users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row and time.time() < row[0]:
        return True
    return False

# Generate Shortened Verification Link via GPLinks API
def create_gplink(user_id):
    token = f"vip_{user_id}_{int(time.time())}"
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO pending_tokens VALUES (?, ?, ?)", (token, user_id, time.time()))
    conn.commit()
    conn.close()
    
    target_url = f"https://t.me/{BOT_USERNAME}?start={token}"
    encoded_target = urllib.parse.quote(target_url)
    
    api_url = f"https://api.gplinks.com/api?api={GPLINKS_API_KEY}&url={encoded_target}&format=text"
    try:
        res = requests.get(api_url, timeout=10)
        if res.status_code == 200 and res.text.startswith("http"):
            return res.text.strip()
    except Exception as e:
        print("GPLinks API error:", e)
    
    return target_url

init_db()

# Keep-alive Web Server for Render Hosting
app = Flask('')
@app.route('/')
def home():
    return "Bot is running 24/7!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# /start command & Deep-Link Token Verification
@bot.message_handler(commands=['start'])
def send_welcome(message):
    text = message.text.strip()
    user_id = message.from_user.id
    
    # Check if user returned from GPLinks shortlink verification
    if " " in text:
        token = text.split(" ", 1)[1].strip()
        if token.startswith("vip_"):
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            c.execute("SELECT user_id FROM pending_tokens WHERE token = ?", (token,))
            row = c.fetchone()
            if row:
                c.execute("DELETE FROM pending_tokens WHERE token = ?", (token,))
                conn.commit()
                conn.close()
                set_vip_pass(user_id)
                bot.reply_to(
                    message,
                    "🎉 **24-Hour VIP Pass Activated!**\n\n"
                    "You now have **24 hours of unlimited, direct HD downloads** without any wait.\n\n"
                    "📥 **Send your video link now!**",
                    parse_mode="Markdown"
                )
                return
            conn.close()

    welcome_text = (
        "🚀 **All-in-One Video Downloader Bot**\n\n"
        "Send me any link from supported platforms:\n"
        "• **Instagram** (Reels, Posts, Videos)\n"
        "• **YouTube** (Shorts & Videos)\n"
        "• **Twitter / X** (Videos & GIFs)\n"
        "• **Pinterest** (Videos & Media)\n"
        "• **Facebook & Reddit**\n\n"
        "I'll download it in HD quality instantly!"
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")

# Download and Send Media Handler
def download_and_send(chat_id, user_id, url):
    msg = bot.send_message(chat_id, "⚡ *Downloading your video in HD, please wait...*", parse_mode="Markdown")
    file_path = f'video_{user_id}_{int(time.time())}.mp4'
    
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': file_path,
        'quiet': True,
        'no_warnings': True,
        'max_filesize': 50 * 1024 * 1024  # 50 MB Telegram Bot API limit
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        if os.path.exists(file_path):
            with open(file_path, 'rb') as video:
                bot.send_video(
                    chat_id, 
                    video, 
                    caption="📥 **Downloaded via All-in-One Saver Bot**"
                )
            os.remove(file_path)
        bot.delete_message(chat_id, msg.message_id)
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        bot.send_message(
            chat_id, 
            "❌ **Download Failed.**\n\nPlease make sure:\n1. The link is from a public post/account.\n2. The video is under Telegram's 50MB size limit."
        )

# Handle incoming links
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = message.from_user.id
    text = message.text.strip()

    # Check if input is a valid URL
    if not text.startswith("http://") and not text.startswith("https://"):
        bot.reply_to(message, "⚠️ Please send a valid **video URL / link**.")
        return

    if is_vip_active(user_id):
        # VIP Active -> Instant Direct Download
        download_and_send(message.chat.id, user_id, text)
    else:
        # Pass Expired -> Create GPLink & Show Button
        short_url = create_gplink(user_id)
        markup = types.InlineKeyboardMarkup()
        unlock_btn = types.InlineKeyboardButton(text="⚡ Unlock 24h Free VIP Pass", url=short_url)
        markup.add(unlock_btn)
        
        bot.send_message(
            message.chat.id,
            "🔒 **VIP Pass Required**\n\n"
            "To unlock **24 Hours of Unlimited HD Downloads**:\n"
            "1️⃣ Tap the button below.\n"
            "2️⃣ Complete the quick 10-second verification.\n"
            "3️⃣ You will be redirected back with your VIP pass active!\n\n"
            "👇 *Click below to verify:*",
            reply_markup=markup,
            parse_mode="Markdown"
        )

if __name__ == '__main__':
    keep_alive()
    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling(skip_pending=True)
