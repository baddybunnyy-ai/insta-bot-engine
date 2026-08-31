import os
import re
import time
import sqlite3
import glob
import requests
import telebot
from telebot import types
import yt_dlp
from flask import Flask
from threading import Thread

# Static FFmpeg Initialization
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

# Dedicated YouTube Stream Extractor (Bypasses Datacenter IP Block)
def download_youtube_direct(url, file_path):
    yt_match = re.search(r'(?:shorts/|v=|youtu\.be/|embed/)([a-zA-Z0-9_-]{11})', url)
    if not yt_match:
        return False
    
    video_id = yt_match.group(1)
    
    # High-reliability Invidious API instances
    instances = [
        "https://invidious.nerdvpn.de",
        "https://inv.nadeko.net",
        "https://invidious.no-valis.org",
        "https://invidious.drgns.space",
        "https://inv.in.projectsegfau.lt"
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    for instance in instances:
        try:
            api_url = f"{instance}/api/v1/videos/{video_id}"
            res = requests.get(api_url, headers=headers, timeout=6)
            if res.status_code == 200:
                data = res.json()
                streams = data.get("formatStreams", [])
                if streams:
                    # Pick highest quality MP4 stream
                    download_url = streams[-1].get("url")
                    if download_url:
                        with requests.get(download_url, headers=headers, stream=True, timeout=25) as r:
                            r.raise_for_status()
                            with open(file_path, 'wb') as f:
                                for chunk in r.iter_content(chunk_size=1024 * 1024):
                                    if chunk:
                                        f.write(chunk)
                        if os.path.exists(file_path) and os.path.getsize(file_path) > 10000:
                            return True
        except Exception as e:
            continue
    return False

# Universal Downloader Handler
def download_and_send(chat_id, user_id, url, remaining_credits):
    msg = bot.send_message(chat_id, "⚡ *Processing & downloading HD video, please wait...*", parse_mode="Markdown")
    file_prefix = f"dl_{user_id}_{int(time.time())}"
    mp4_target = f"{file_prefix}.mp4"
    success = False

    # 1. Check if URL is YouTube / YouTube Shorts
    if "youtube.com" in url or "youtu.be" in url:
        success = download_youtube_direct(url, mp4_target)

    # 2. If not YouTube or if API failed -> fallback to yt-dlp (Instagram, Pinterest, X, etc.)
    if not success:
        ydl_opts = {
            'format': 'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best',
            'outtmpl': f'{file_prefix}.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'nocheckcertificate': True,
            'max_filesize': 25 * 1024 * 1024,
        }
        if FFMPEG_PATH:
            ydl_opts['ffmpeg_location'] = FFMPEG_PATH
            ydl_opts['merge_output_format'] = 'mp4'

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            
            downloaded = glob.glob(f"{file_prefix}*")
            valid = [f for f in downloaded if not f.endswith('.part') and not f.endswith('.ytdl')]
            if valid:
                mp4_target = valid[0]
                success = True
        except Exception as e:
            print(f"yt-dlp fallback error: {e}")

    # Send Video
    if success and os.path.exists(mp4_target) and os.path.getsize(mp4_target) > 0:
        try:
            with open(mp4_target, 'rb') as video:
                bot.send_video(
                    chat_id, 
                    video, 
                    caption=f"📥 **Downloaded via All-in-One Saver Bot**\n⚡ *Credits remaining:* `{remaining_credits}`"
                )
            bot.delete_message(chat_id, msg.message_id)
        except Exception as e:
            print(f"Telegram upload error: {e}")
        
        # Cleanup
        for f in glob.glob(f"{file_prefix}*"):
            try:
                os.remove(f)
            except Exception:
                pass
    else:
        # Refund Credit
        add_credits(user_id, 1)
        for f in glob.glob(f"{file_prefix}*"):
            try:
                os.remove(f)
            except Exception:
                pass
        try:
            bot.delete_message(chat_id, msg.message_id)
        except Exception:
            pass
        bot.send_message(
            chat_id, 
            "❌ **Download Failed.** (Credit Refunded)\n\nPlease make sure:\n1. The link is from a public post.\n2. The video is under 25MB."
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
