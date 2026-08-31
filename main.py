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

# /start command
@bot.message_handler(commands=['start'])
def send_welcome(message):
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

# Listen for Monetag WebApp Ad Completion Signal
@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    user_id = message.from_user.id
    set_vip_pass(user_id)
    bot.send_message(
        message.chat.id, 
        "🎉 **24-Hour VIP Pass Unlocked!**\n\nYou now have unlimited, direct downloads across all platforms for the next 24 hours. Send your link to download!",
        parse_mode="Markdown"
    )

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
        # VIP Active -> Instant Download
        download_and_send(message.chat.id, user_id, text)
    else:
        # Pass Expired -> Show Monetag Ad Button
        cache_bypass_url = f"{WEB_APP_URL}/?v={int(time.time())}"
        markup = types.InlineKeyboardMarkup()
        web_app_info = types.WebAppInfo(url=cache_bypass_url)
        ad_button = types.InlineKeyboardButton(text="⚡ Unlock 24h Free VIP Pass", web_app=web_app_info)
        markup.add(ad_button)
        
        bot.send_message(
            message.chat.id,
            "🔒 **VIP Pass Required**\n\n"
            "Tap below to watch a quick ad and enjoy **24 Hours of Unlimited HD Downloads** across all platforms!",
            reply_markup=markup,
            parse_mode="Markdown"
        )

if __name__ == '__main__':
    keep_alive()
    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling(skip_pending=True)
