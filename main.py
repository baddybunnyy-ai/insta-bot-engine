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
    expiry = time.time() + (24 * 3600) # 24 Hours Pass
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
        "👋 **Welcome to Instagram Reels Downloader!**\n\nSend me any public **Instagram Reel, Video, or Post link**, and I will download it for you in high quality instantly!",
        parse_mode="Markdown"
    )

# Listen for WebApp Ad Completion Signal
@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    user_id = message.from_user.id
    set_vip_pass(user_id)
    bot.send_message(
        message.chat.id, 
        "🎉 **24-Hour VIP Pass Unlocked!**\n\nYou now have unlimited, ad-free downloads for the next 24 hours. Send your link to download!",
        parse_mode="Markdown"
    )

# Download and Send Reel
def download_and_send(chat_id, user_id, url):
    msg = bot.send_message(chat_id, "⚡ *Downloading your video, please wait...*", parse_mode="Markdown")
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
        bot.send_message(chat_id, "❌ Unable to download the video. Please make sure the link is from a public account.")

# Handle incoming links
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = message.from_user.id
    text = message.text.strip()

    if "instagram.com" not in text:
        bot.reply_to(message, "⚠️ Please send a valid **Instagram link**.")
        return

    if is_vip_active(user_id):
        # VIP Active -> Instant Download
        download_and_send(message.chat.id, user_id, text)
    else:
        # Pass Expired -> Show Ad Button
        markup = types.InlineKeyboardMarkup()
        web_app_info = types.WebAppInfo(url=WEB_APP_URL)
        ad_button = types.InlineKeyboardButton(text="▶️ Unlock 24h Free Pass (5s Ad)", web_app=web_app_info)
        markup.add(ad_button)
        
        bot.send_message(
            message.chat.id,
            "⚡ **Unlock 24-Hour Free Pass:**\n\nTap the button below to watch a quick 5-second ad and enjoy **unlimited free downloads for 24 hours**!",
            reply_markup=markup,
            parse_mode="Markdown"
        )

if __name__ == '__main__':
    keep_alive()
    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling(skip_pending=True)
