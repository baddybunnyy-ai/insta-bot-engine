import os
import re
import time
import uuid
import sqlite3
import glob
import requests
from urllib.parse import urlparse, parse_qs
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

# Configurations
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8991187008:AAEmpfwuA3JUKLAuWYFjkgsnyHhbEcZFY4E")
WEB_APP_URL = "https://insta-reel-ad.vercel.app"
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB Limit

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)

# --- SQLite Database ---
def init_db():
    with sqlite3.connect('users.db', timeout=15) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS user_credits 
                     (user_id INTEGER PRIMARY KEY, credits INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS used_rewards 
                     (reward_token TEXT PRIMARY KEY, user_id INTEGER, timestamp INTEGER)''')
        conn.commit()

def get_credits(user_id):
    with sqlite3.connect('users.db', timeout=15) as conn:
        c = conn.cursor()
        c.execute("SELECT credits FROM user_credits WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if row is None:
            c.execute("INSERT INTO user_credits VALUES (?, ?)", (user_id, 2))
            conn.commit()
            return 2
        return row[0]

def atomic_deduct_credit(user_id):
    """Deducts 1 credit only if balance is strictly greater than 0"""
    with sqlite3.connect('users.db', timeout=15) as conn:
        c = conn.cursor()
        c.execute("UPDATE user_credits SET credits = credits - 1 WHERE user_id = ? AND credits > 0", (user_id,))
        success = c.rowcount > 0
        conn.commit()
        return success

def atomic_add_credits(user_id, amount=3):
    """Safely increments credits without race conditions"""
    with sqlite3.connect('users.db', timeout=15) as conn:
        c = conn.cursor()
        c.execute('''INSERT INTO user_credits (user_id, credits) VALUES (?, ?)
                     ON CONFLICT(user_id) DO UPDATE SET credits = credits + ?''', 
                  (user_id, amount, amount))
        conn.commit()

def verify_and_claim_reward_atomic(payload_token, current_user_id):
    clean_token = payload_token.strip()
    if not clean_token:
        return False, "Empty token provided."

    with sqlite3.connect('users.db', timeout=15) as conn:
        c = conn.cursor()
        try:
            c.execute("INSERT INTO used_rewards VALUES (?, ?, ?)", (clean_token, current_user_id, int(time.time())))
            c.execute('''INSERT INTO user_credits (user_id, credits) VALUES (?, ?)
                         ON CONFLICT(user_id) DO UPDATE SET credits = credits + ?''',
                      (current_user_id, 3, 3))
            conn.commit()
            return True, "Success"
        except sqlite3.IntegrityError:
            return False, "This reward token has already been claimed."
        except Exception as e:
            return False, f"Database error: {e}"

init_db()

# --- Render Keep-Alive Server (Dynamic Port Binding) ---
app = Flask('')

@app.route('/')
def home():
    has_cookies = os.path.exists("cookies.txt")
    return f"Bot Engine Active | Cookies Loaded: {has_cookies}"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# --- URL & Stream Processing ---
def is_youtube_url(url):
    try:
        domain = urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain in ['youtube.com', 'youtu.be', 'm.youtube.com']
    except Exception:
        return False

def extract_youtube_id(url):
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]

        if domain in ['youtube.com', 'm.youtube.com']:
            if parsed.path == '/watch':
                return parse_qs(parsed.query).get('v', [None])[0]
            elif parsed.path.startswith(('/shorts/', '/embed/', '/live/', '/v/')):
                path_parts = [p for p in parsed.path.split('/') if p]
                return path_parts[1] if len(path_parts) > 1 else None
        elif domain == 'youtu.be':
            return parsed.path.lstrip('/').split('?')[0]
    except Exception:
        pass
    return None

def stream_to_file(download_url, output_path, headers):
    """Streams data to disk with 25MB cutoff and immediate cleanup"""
    try:
        with requests.get(download_url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            total_size = 0
            with open(output_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        total_size += len(chunk)
                        if total_size > MAX_FILE_SIZE_BYTES:
                            break
                        f.write(chunk)
            
            if total_size > MAX_FILE_SIZE_BYTES:
                if os.path.exists(output_path):
                    os.remove(output_path)
                return False
                
        return os.path.exists(output_path) and os.path.getsize(output_path) > 10000
    except Exception:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        return False

# --- YouTube Active Gateways ---
def fetch_youtube_api(url, output_path):
    video_id = extract_youtube_id(url)
    if not video_id:
        return False

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

    live_invidious_nodes = [
        "https://invidious.futo.org",
        "https://invidious.projectsegfau.lt",
        "https://invidious.private.coffee"
    ]
    for node in live_invidious_nodes:
        try:
            api_url = f"{node}/api/v1/videos/{video_id}"
            res = requests.get(api_url, headers=headers, timeout=5)
            if res.status_code == 200:
                format_streams = res.json().get("formatStreams", [])
                valid_mp4s = [s for s in format_streams if s.get("container") == "mp4" or "video/mp4" in s.get("type", "")]
                target_stream = valid_mp4s[-1] if valid_mp4s else (format_streams[-1] if format_streams else None)
                
                if target_stream and target_stream.get("url"):
                    if stream_to_file(target_stream["url"], output_path, headers):
                        print(f"[*] YouTube stream fetched via {node}")
                        return True
        except Exception:
            continue

    return False

# --- Master Downloader Engine ---
def download_and_send(chat_id, user_id, raw_url):
    msg = bot.send_message(chat_id, "⚡ *Processing & downloading HD video, please wait...*", parse_mode="Markdown")
    
    unique_id = uuid.uuid4().hex[:10]
    file_prefix = f"dl_{user_id}_{unique_id}"
    final_file = f"{file_prefix}.mp4"
    success = False

    is_yt = is_youtube_url(raw_url)

    # 1. External Resolvers for YouTube
    if is_yt:
        success = fetch_youtube_api(raw_url, final_file)

    # 2. Native yt-dlp Engine (Instagram, Pinterest, X, Facebook, Reddit + YT Fallback)
    if not success:
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best',
            'outtmpl': f'{file_prefix}.%(ext)s',
            'quiet': False,
            'no_warnings': False,
            'noplaylist': True,
            'nocheckcertificate': True,
            'max_filesize': MAX_FILE_SIZE_BYTES,
            'extractor_args': {'twitter': {'api': 'syndication'}}
        }
        
        if os.path.exists('cookies.txt'):
            ydl_opts['cookiefile'] = 'cookies.txt'

        if FFMPEG_PATH:
            ydl_opts['ffmpeg_location'] = FFMPEG_PATH
            ydl_opts['merge_output_format'] = 'mp4'

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([raw_url])
            
            downloaded = glob.glob(f"{file_prefix}*")
            valid = [f for f in downloaded if not f.endswith('.part') and not f.endswith('.ytdl')]
            if valid:
                final_file = valid[0]
                if os.path.getsize(final_file) <= MAX_FILE_SIZE_BYTES:
                    success = True
                    tag = "YT-Fallback" if is_yt else "Native"
                    print(f"[LOG: {tag}-yt-dlp] Extraction successful: {raw_url}")
        except Exception as e:
            tag = "YT-Fallback" if is_yt else "Native"
            print(f"[LOG: {tag}-yt-dlp Failed] {raw_url} | Reason: {e}")

    # 3. Telegram Delivery & Fail-safe Refund
    if success and os.path.exists(final_file) and os.path.getsize(final_file) > 0:
        try:
            remaining_credits = get_credits(user_id)
            with open(final_file, 'rb') as video:
                bot.send_video(
                    chat_id, 
                    video, 
                    caption=f"📥 *Downloaded Successfully!*\n⚡ *Credits remaining:* `{remaining_credits}`",
                    parse_mode="Markdown"
                )
            bot.delete_message(chat_id, msg.message_id)
        except Exception as e:
            print(f"[LOG: Telegram Delivery Failed] {e}")
            atomic_add_credits(user_id, 1)
            try:
                bot.delete_message(chat_id, msg.message_id)
            except Exception:
                pass
            bot.send_message(chat_id, "❌ **Upload Failed.** Delivery timed out. (Credit Refunded)")
    else:
        atomic_add_credits(user_id, 1)
        try:
            bot.delete_message(chat_id, msg.message_id)
        except Exception:
            pass
        bot.send_message(
            chat_id, 
            "❌ **Download Failed.** (Credit Refunded)\n\nPlease make sure the post is public and under 25MB."
        )

    # Cleanup temp files
    for f in glob.glob(f"{file_prefix}*"):
        try:
            os.remove(f)
        except Exception:
            pass

# --- Command & Message Handlers ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    # Handle reward token claim
    if "reward_" in text:
        parts = text.split("reward_")
        if len(parts) > 1:
            token = parts[1].strip()
            verified, reason = verify_and_claim_reward_atomic(token, user_id)
            total_credits = get_credits(user_id)
            
            if verified:
                bot.reply_to(
                    message,
                    f"🎉 **+3 Download Credits Added!**\n\n"
                    f"⚡ Total Balance: **{total_credits} Downloads**\n\n"
                    f"📥 Send your link now to download!",
                    parse_mode="Markdown"
                )
                return
            else:
                # Provide an interactive button instead of a dead-end error
                markup = types.InlineKeyboardMarkup()
                cache_bypass_url = f"{WEB_APP_URL}/?uid={user_id}&v={int(time.time())}"
                markup.add(types.InlineKeyboardButton(text="⚡ Watch Ad (+3 Downloads)", web_app=types.WebAppInfo(url=cache_bypass_url)))
                
                bot.reply_to(
                    message,
                    f"⚠️ **Reward Notice:** {reason}\n\n"
                    f"⚡ Available Balance: **{total_credits} Downloads**\n\n"
                    f"Naye downloads add karne ke liye niche button par tap karke ad dekhein:",
                    reply_markup=markup,
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
        f"🎁 **Balance:** `{credits} Free Downloads`"
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = message.from_user.id
    text = message.text.strip()

    if not text.startswith("http://") and not text.startswith("https://"):
        bot.reply_to(message, "⚠️ Please send a valid **video URL / link**.")
        return

    if atomic_deduct_credit(user_id):
        download_and_send(message.chat.id, user_id, text)
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
    try:
        bot.remove_webhook()
        time.sleep(2)
    except Exception:
        pass
    print("[*] Bot Polling Active...")
    bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
