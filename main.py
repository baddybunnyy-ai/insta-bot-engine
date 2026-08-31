import os
import re
import time
import sqlite3
import glob
import telebot
from telebot import types
import yt_dlp
from flask import Flask
from threading import Thread

# Static FFmpeg binary initialization
try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = None

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
    return "Bot Engine 24/7 Active!"

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
        "• **Instagram** (Reels, Posts, Stories)\n"
        "• **YouTube** (Shorts & Videos)\n"
        "• **Pinterest** (Videos & Media)\n"
        "• **Twitter / X** (Videos & GIFs)\n"
        "• **Facebook & Reddit**\n\n"
        f"🎁 **Your Balance:** `{credits} Free Downloads`\n\n"
        "Paste your link to start downloading!"
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")

# URL Cleaner & YouTube Shorts Bypass
def clean_and_bypass_url(url):
    # Convert YouTube Shorts directly to standard video format
    yt_match = re.search(r'(?:shorts/|v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
    if yt_match and ('youtube.com' in url or 'youtu.be' in url):
        video_id = yt_match.group(1)
        return f"https://www.youtube.com/watch?v={video_id}"
    return url

# Universal Downloader Engine with Multi-Client Auto Fallback
def download_and_send(chat_id, user_id, raw_url, remaining_credits):
    target_url = clean_and_bypass_url(raw_url)
    msg = bot.send_message(chat_id, "⚡ *Processing & downloading HD video, please wait...*", parse_mode="Markdown")
    file_prefix = f"dl_{user_id}_{int(time.time())}"
    
    # Try different client extractors if YouTube blocks datacenter IP
    client_strategies = [
        ['web_safari'],
        ['mweb', 'web_safari'],
        ['tv_embedded', 'android_vr'],
        None  # Default fallback
    ] if ('youtube.com' in target_url or 'youtu.be' in target_url) else [None]
    
    success = False

    for clients in client_strategies:
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best',
            'outtmpl': f'{file_prefix}.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'nocheckcertificate': True,
            'max_filesize': 25 * 1024 * 1024,  # 25 MB Limit
        }
        
        if FFMPEG_PATH:
            ydl_opts['ffmpeg_location'] = FFMPEG_PATH
            ydl_opts['merge_output_format'] = 'mp4'
            
        if clients:
            ydl_opts['extractor_args'] = {
                'youtube': {
                    'player_client': clients,
                    'player_skip': ['webpage', 'configs']
                }
            }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([target_url])
            
            # Check downloaded file
            downloaded_files = glob.glob(f"{file_prefix}*")
            valid_files = [f for f in downloaded_files if not f.endswith('.part') and not f.endswith('.ytdl')]
            
            if valid_files:
                file_to_send = valid_files[0]
                with open(file_to_send, 'rb') as video:
                    bot.send_video(
                        chat_id, 
                        video, 
                        caption=f"📥 **Downloaded via All-in-One Saver Bot**\n⚡ *Credits remaining:* `{remaining_credits}`"
                    )
                for f in downloaded_files:
                    try:
                        os.remove(f)
                    except Exception:
                        pass
                bot.delete_message(chat_id, msg.message_id)
                success = True
                break
        except Exception as e:
            print(f"Strategy {clients} failed: {e}")
            for f in glob.glob(f"{file_prefix}*"):
                try:
                    os.remove(f)
                except Exception:
                    pass
            continue

    if not success:
        add_credits(user_id, 1)  # Refund credit if all strategies fail
        try:
            bot.delete_message(chat_id, msg.message_id)
        except Exception:
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
