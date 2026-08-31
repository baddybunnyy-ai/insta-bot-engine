import os
import time
import sqlite3
import telebot
from telebot import types
import yt_dlp
from flask import Flask
from threading import Thread

BOT_TOKEN = "8991187008:AAEmpfwuA3JUKLAuWYFjkgsnyHhbEcZFY4E"
WEB_APP_URL = "https://insta-reel-ad.vercel.app"

bot = telebot.TeleBot(BOT_TOKEN)

# SQLite Database Setup (Persistent VIP Pass)
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS vip_users 
                 (user_id INTEGER PRIMARY KEY, expiry_time REAL)''')
    conn.commit()
    conn.close()

def set_vip_pass(user_id):
    expiry = time.time() + (24 * 3600) # 24 Hours
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

init_db()

# Keep-alive Web Server
app = Flask('')
@app.route('/')
def home():
    return "Bot is running 24/7!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# /start command
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message, 
        "👋 **Namaste!**\n\nMujhe koi bhi **Instagram Reel / Video ka link** bhejo, main turant 1080p high speed me download kar dunga!",
        parse_mode="Markdown"
    )

# Listen for WebApp Ad Completion Signal
@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    user_id = message.from_user.id
    set_vip_pass(user_id)
    bot.send_message(
        message.chat.id, 
        "🎉 **24 Hours Free VIP Pass Unlocked!**\n\nAb aap agle 24 ghante tak bina kisi ad ke unlimited reels download kar sakte hain. Link bhejo!",
        parse_mode="Markdown"
    )

# Download and Send Reel
def download_and_send(chat_id, user_id, url):
    msg = bot.send_message(chat_id, "⚡ *Video download ho rahi hai, bas 5 second...*", parse_mode="Markdown")
    file_path = f'video_{user_id}_{int(time.time())}.mp4'
    ydl_opts = {
        'format': 'best',
        'outtmpl': file_path,
        'quiet': True,
        'no_warnings': True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        if os.path.exists(file_path):
            with open(file_path, 'rb') as video:
                bot.send_video(chat_id, video, caption="📥 **Downloaded by @InstaReelsSaverX_bot**")
            os.remove(file_path)
        bot.delete_message(chat_id, msg.message_id)
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        bot.send_message(chat_id, "❌ Video download nahi ho saki. Kripya check karein link public video ka ho.")

# Handle incoming links
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = message.from_user.id
    text = message.text.strip()

    if "instagram.com" not in text:
        bot.reply_to(message, "⚠️ Kripya valid **Instagram link** bhejein.")
        return

    if is_vip_active(user_id):
        # VIP Active -> Instant Download
        download_and_send(message.chat.id, user_id, text)
    else:
        # Pass Expired -> Show Ad Button
        markup = types.InlineKeyboardMarkup()
        web_app_info = types.WebAppInfo(url=WEB_APP_URL)
        ad_button = types.InlineKeyboardButton(text="▶️ Unlock 24h Free Download (5s Ad)", web_app=web_app_info)
        markup.add(ad_button)
        
        bot.send_message(
            message.chat.id,
            "⚡ **Download Karne Ke Liye 24-Hour Pass Unlock Karein:**\n\nNeeche button par click karke 5-second ka ad dekhein aur **24 ghante ke liye VIP download free** karein!",
            reply_markup=markup,
            parse_mode="Markdown"
        )

if __name__ == '__main__':
    keep_alive()
    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling(skip_pending=True)
