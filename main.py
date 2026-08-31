import os
import time
import sqlite3
import glob
import telebot
from telebot import types
import yt_dlp
from flask import Flask
from threading import Thread

BOT_TOKEN = "8991187008:AAEmpfwuA3JUKLAuWYFjkgsnyHhbEcZFY4E"
WEB_APP_URL = "https://insta-reel-ad.vercel.app"

bot = telebot.TeleBot(BOT_TOKEN)

# SQLite Database Setup (Credits System)
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS user_credits 
                 (user_id INTEGER PRIMARY KEY, credits INTEGER)''')
    conn.commit()
    conn.close()

def get_credits(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT credits FROM user_credits WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row is None:
        c.execute("INSERT INTO user_credits VALUES (?, ?)", (user_id, 2))
        conn.commit()
        credits = 2
    else:
        credits = row[0]
    conn.close()
    return credits

def deduct_credit(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("UPDATE user_credits SET credits = credits - 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def add_credits(user_id, amount=3):
    current = get_credits(user_id)
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("UPDATE user_credits SET credits = ? WHERE user_id = ?", (current + amount, user_id))
    conn.commit()
    conn.close()

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

# /start command & Ad Reward Listener
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if "reward_" in text:
        add_credits(user_id, 3)
        total_credits = get_credits(user_id)
        bot.reply_to(
            message,
            f"🎉 **+3 Download Credits Added!**\n\n"
            f"⚡ Total Available Balance: **{total_credits} Downloads**\n\n"
            f"📥 **Send your video link now to download!**",
            parse_mode="Markdown"
        )
        return

    credits = get_credits(user_id)
    welcome_text = (
        "🚀 **All-in-One Video Downloader Bot**\n\n"
        "Send me any link from supported platforms:\n"
        "• **Instagram** (Reels, Posts, Videos)\n"
        "• **YouTube** (Shorts & Videos)\n"
        "• **Twitter / X** (Videos & GIFs)\n"
        "• **Pinterest** (Videos & Media)\n"
        "• **Facebook & Reddit**\n\n"
        f"🎁 **Your Balance:** `{credits} Free Downloads`\n\n"
        "Paste your link to start downloading!"
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")

# Universal Multi-Platform Downloader Handler
def download_and_send(chat_id, user_id, url, remaining_credits):
    msg = bot.send_message(chat_id, "⚡ *Downloading your video in HD, please wait...*", parse_mode="Markdown")
    file_prefix = f"dl_{user_id}_{int(time.time())}"
    
    ydl_opts = {
        # Strict single-stream selection to avoid FFmpeg requirement on Render
        'format': 'best[ext=mp4][vcodec!=none][acodec!=none]/best[vcodec!=none][acodec!=none]/best[ext=mp4]/best',
        'outtmpl': f'{file_prefix}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'max_filesize': 25 * 1024 * 1024,  # 25 MB Limit
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Sec-Fetch-Mode': 'navigate'
        },
        # YouTube Datacenter IP bypass client emulation
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web_creator']
            }
        }
    }
    
    downloaded_files = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        # Search for the output file with any extension
        downloaded_files = glob.glob(f"{file_prefix}.*")
        
        if downloaded_files:
            file_to_send = downloaded_files[0]
            with open(file_to_send, 'rb') as video:
                bot.send_video(
                    chat_id, 
                    video, 
                    caption=f"📥 **Downloaded via All-in-One Saver Bot**\n⚡ *Credits remaining:* `{remaining_credits}`"
                )
            for f in downloaded_files:
                os.remove(f)
            bot.delete_message(chat_id, msg.message_id)
        else:
            raise Exception("File not found on disk after download.")
            
    except Exception as e:
        add_credits(user_id, 1)  # Refund credit if failed
        for f in glob.glob(f"{file_prefix}.*"):
            try:
                os.remove(f)
            except:
                pass
        try:
            bot.delete_message(chat_id, msg.message_id)
        except:
            pass
        bot.send_message(
            chat_id, 
            "❌ **Download Failed.** (Credit Refunded)\n\nPlease make sure:\n1. The link is from a public post/account.\n2. The video is under 25MB."
        )

# Handle incoming links
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = message.from_user.id
    text = message.text.strip()

    if not text.startswith("http://") and not text.startswith("https://"):
        bot.reply_to(message, "⚠️ Please send a valid **video URL / link**.")
        return

    credits = get_credits(user_id)

    if credits > 0:
        deduct_credit(user_id)
        remaining = credits - 1
        download_and_send(message.chat.id, user_id, text, remaining)
    else:
        cache_bypass_url = f"{WEB_APP_URL}/?uid={user_id}&v={int(time.time())}"
        markup = types.InlineKeyboardMarkup()
        web_app_info = types.WebAppInfo(url=cache_bypass_url)
        ad_button = types.InlineKeyboardButton(text="⚡ Watch Ad (+3 Downloads)", web_app=web_app_info)
        markup.add(ad_button)
        
        bot.send_message(
            message.chat.id,
            "🔒 **Out of Download Credits!**\n\n"
            "You have used all your free downloads.\n\n"
            "Tap below to watch a quick ad and get **+3 HD Downloads** instantly!",
            reply_markup=markup,
            parse_mode="Markdown"
        )

if __name__ == '__main__':
    keep_alive()
    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling(skip_pending=True)
