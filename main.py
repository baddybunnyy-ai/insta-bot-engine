import os
import re
import time
import uuid
import sqlite3
import glob
import logging
import threading
from urllib.parse import urlparse, parse_qs
import requests
import telebot
from telebot import types, apihelper
import yt_dlp
from flask import Flask

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

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
USER_LOCK_TIMEOUT = 360  # 6 minutes auto-expiry for stuck tasks

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=10)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Concurrency Controls (Render 512MB RAM Safety)
DOWNLOAD_SEMAPHORE = threading.BoundedSemaphore(3)
active_users = {}  # {user_id: timestamp}
active_users_lock = threading.Lock()

# --- SQLite Database ---
def init_db():
    with sqlite3.connect('users.db', timeout=20) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS user_credits 
                     (user_id INTEGER PRIMARY KEY, credits INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS used_rewards 
                     (reward_token TEXT PRIMARY KEY, user_id INTEGER, timestamp INTEGER)''')
        c.execute('''CREATE INDEX IF NOT EXISTS idx_user_rewards ON used_rewards(user_id, timestamp)''')
        conn.commit()

def get_credits(user_id):
    with sqlite3.connect('users.db', timeout=20) as conn:
        c = conn.cursor()
        c.execute("SELECT credits FROM user_credits WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if row is None:
            c.execute("INSERT INTO user_credits VALUES (?, ?)", (user_id, 2))
            conn.commit()
            return 2
        return row[0]

def atomic_deduct_credit(user_id):
    with sqlite3.connect('users.db', timeout=20) as conn:
        c = conn.cursor()
        c.execute("UPDATE user_credits SET credits = credits - 1 WHERE user_id = ? AND credits > 0", (user_id,))
        success = c.rowcount > 0
        conn.commit()
        return success

def atomic_add_credits(user_id, amount=3):
    with sqlite3.connect('users.db', timeout=20) as conn:
        c = conn.cursor()
        c.execute('''INSERT INTO user_credits (user_id, credits) VALUES (?, ?)
                     ON CONFLICT(user_id) DO UPDATE SET credits = credits + ?''', 
                  (user_id, amount, amount))
        conn.commit()

def verify_and_claim_reward_atomic(payload_token, current_user_id):
    clean_token = payload_token.strip()
    if not re.match(r'^[a-zA-Z0-9_\-]{5,64}$', clean_token):
        return False, "Invalid token format."

    now = int(time.time())
    with sqlite3.connect('users.db', timeout=20) as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM used_rewards WHERE reward_token = ?", (clean_token,))
        if c.fetchone() is not None:
            return False, "This reward token has already been claimed."

        c.execute("SELECT MAX(timestamp) FROM used_rewards WHERE user_id = ?", (current_user_id,))
        row = c.fetchone()
        if row and row[0] and (now - row[0]) < 15:
            return False, f"Please wait {15 - (now - row[0])}s before claiming again."

        try:
            c.execute("INSERT INTO used_rewards (reward_token, user_id, timestamp) VALUES (?, ?, ?)",
                      (clean_token, current_user_id, now))
            c.execute('''INSERT INTO user_credits (user_id, credits) VALUES (?, ?)
                         ON CONFLICT(user_id) DO UPDATE SET credits = credits + ?''',
                      (current_user_id, 3, 3))
            conn.commit()
            return True, "Success"
        except sqlite3.IntegrityError:
            return False, "This reward token has already been claimed."

init_db()

# --- Telegram UI Safe Wrappers ---
def safe_edit_message(chat_id, message_id, text, reply_markup=None):
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, parse_mode="Markdown", reply_markup=reply_markup)
    except apihelper.ApiTelegramException:
        try:
            bot.edit_message_text(text.replace("*", "").replace("`", ""), chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
        except Exception:
            pass
    except Exception:
        pass

def safe_delete_message(chat_id, message_id):
    try:
        bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

# --- Fast Background Auto Garbage-Collector ---
def auto_disk_cleaner():
    while True:
        try:
            time.sleep(180)
            now = time.time()
            for f in glob.glob(os.path.join(DOWNLOAD_DIR, "*")):
                try:
                    if os.path.getmtime(f) < (now - 300):
                        os.remove(f)
                        logging.info(f"Purged stale temp file: {f}")
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"Cleaner thread exception: {e}")

threading.Thread(target=auto_disk_cleaner, daemon=True).start()

# --- Keep-Alive Web Server ---
app = Flask('')

@app.route('/')
def home():
    return "Bot Engine Active | Status: Healthy 🟢"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    try:
        from werkzeug.serving import run_simple
        run_simple('0.0.0.0', port, app, use_reloader=False, threaded=True)
    except Exception as e:
        logging.error(f"Flask start error: {e}")

threading.Thread(target=run_flask, daemon=True).start()

# --- URL Helpers & Proxies ---
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
                parts = [p for p in parsed.path.split('/') if p]
                return parts[1] if len(parts) > 1 else None
        elif domain == 'youtu.be':
            return parsed.path.lstrip('/').split('?')[0]
    except Exception:
        pass
    return None

def stream_to_file(download_url, output_path, headers):
    try:
        with requests.get(download_url, headers=headers, stream=True, timeout=(5, 20)) as r:
            r.raise_for_status()
            total_size = 0
            with open(output_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=512 * 1024):
                    if chunk:
                        total_size += len(chunk)
                        if total_size > MAX_FILE_SIZE_BYTES:
                            logging.warning("Proxy stream exceeded 25MB limit.")
                            break
                        f.write(chunk)
            
            if total_size > MAX_FILE_SIZE_BYTES or total_size < 50_000:
                if os.path.exists(output_path):
                    os.remove(output_path)
                return False
                
        return os.path.exists(output_path) and os.path.getsize(output_path) > 50_000
    except Exception as e:
        logging.warning(f"Proxy stream error: {e}")
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        return False

def fetch_youtube_api(url, output_path):
    video_id = extract_youtube_id(url)
    if not video_id:
        return False

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }

    live_nodes = [
        "https://invidious.futo.org",
        "https://invidious.projectsegfau.lt",
        "https://invidious.private.coffee"
    ]
    for node in live_nodes:
        try:
            api_url = f"{node}/api/v1/videos/{video_id}"
            res = requests.get(api_url, headers=headers, timeout=(4, 6))
            if res.status_code == 200:
                format_streams = res.json().get("formatStreams", [])
                valid_mp4s = [s for s in format_streams if s.get("container") == "mp4" or "video/mp4" in s.get("type", "")]
                target_stream = valid_mp4s[-1] if valid_mp4s else (format_streams[-1] if format_streams else None)
                
                if target_stream and target_stream.get("url"):
                    if stream_to_file(target_stream["url"], output_path, headers):
                        return True
        except Exception:
            continue
    return False

def ytdl_progress_hook(d):
    if d.get('status') == 'downloading':
        downloaded = d.get('downloaded_bytes', 0)
        if downloaded > MAX_FILE_SIZE_BYTES:
            raise ValueError("EXCEEDED_MAX_SIZE")

# --- Master Downloader Engine ---
def download_and_send(chat_id, user_id, raw_url):
    file_prefix = os.path.join(DOWNLOAD_DIR, f"dl_{user_id}_{uuid.uuid4().hex[:8]}")
    final_file = f"{file_prefix}.mp4"
    status_msg = None
    refund_needed = True
    size_exceeded = False

    try:
        status_msg = bot.send_message(chat_id, "⚡ *Processing & downloading HD video...*", parse_mode="Markdown")
        
        # 1. Download Phase (Guarded by Semaphore)
        with DOWNLOAD_SEMAPHORE:
            success = False
            is_yt = is_youtube_url(raw_url)

            if is_yt:
                success = fetch_youtube_api(raw_url, final_file)

            if not success:
                ydl_opts = {
                    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                    'outtmpl': f'{file_prefix}.%(ext)s',
                    'quiet': True,
                    'no_warnings': True,
                    'noplaylist': True,
                    'max_filesize': MAX_FILE_SIZE_BYTES,
                    'socket_timeout': 25,
                    'nocheckcertificate': True,
                    'progress_hooks': [ytdl_progress_hook],
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
                    valid = [f for f in downloaded if not f.endswith(('.part', '.ytdl'))]
                    if valid:
                        final_file = valid[0]
                        if 50_000 < os.path.getsize(final_file) <= MAX_FILE_SIZE_BYTES:
                            success = True
                except ValueError as ve:
                    if str(ve) == "EXCEEDED_MAX_SIZE":
                        size_exceeded = True
                    logging.warning(f"File exceeded size limit: {raw_url}")
                except Exception as e:
                    logging.warning(f"yt-dlp error on {raw_url} | Reason: {e}")

        # 2. Upload Phase (Outside Semaphore)
        if success and os.path.exists(final_file):
            safe_edit_message(chat_id, status_msg.message_id, "📤 *Uploading video to Telegram...*")
            try:
                remaining_credits = get_credits(user_id)
                with open(final_file, 'rb') as video:
                    bot.send_video(
                        chat_id,
                        video,
                        caption=f"📥 *Downloaded Successfully!*\n⚡ *Credits remaining:* `{remaining_credits}`",
                        parse_mode="Markdown",
                        timeout=300
                    )
                refund_needed = False
                safe_delete_message(chat_id, status_msg.message_id)
            except Exception as upload_err:
                logging.error(f"Telegram upload failure: {upload_err}")
                safe_edit_message(chat_id, status_msg.message_id, "❌ **Upload Failed.** Delivery timed out. (Credit Refunded)")
        else:
            if size_exceeded:
                safe_edit_message(chat_id, status_msg.message_id, "❌ **File Too Large.** Video exceeds 25MB limit. (Credit Refunded)")
            else:
                safe_edit_message(chat_id, status_msg.message_id, "❌ **Download Failed.** Post must be public & under 25MB. (Credit Refunded)")

    except Exception as global_err:
        logging.error(f"Critical execution error: {global_err}")
        if status_msg:
            safe_edit_message(chat_id, status_msg.message_id, "❌ **Download Error.** Server busy. (Credit Refunded)")
    finally:
        if refund_needed:
            atomic_add_credits(user_id, 1)

        with active_users_lock:
            active_users.pop(user_id, None)

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

    if "reward_" in text:
        parts = text.split("reward_", 1)
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
                markup = types.InlineKeyboardMarkup()
                cache_bypass_url = f"{WEB_APP_URL}/?uid={user_id}&v={int(time.time())}"
                markup.add(types.InlineKeyboardButton(text="⚡ Watch New Ad (+3 Downloads)", web_app=types.WebAppInfo(url=cache_bypass_url)))
                bot.reply_to(
                    message,
                    f"⚠️ **Reward Notice:** {reason}\n\n"
                    f"⚡ Available Balance: **{total_credits} Downloads**\n\n"
                    f"Tap below to watch a fresh ad:",
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

    if not text.startswith(("http://", "https://")):
        bot.reply_to(message, "⚠️ Please send a valid **video URL / link**.")
        return

    now = time.time()
    with active_users_lock:
        last_active = active_users.get(user_id)
        if last_active and (now - last_active) < USER_LOCK_TIMEOUT:
            bot.reply_to(message, "⏳ **Pehle waala download complete hone dein.**", parse_mode="Markdown")
            return
        active_users[user_id] = now

    if not atomic_deduct_credit(user_id):
        with active_users_lock:
            active_users.pop(user_id, None)

        cache_bypass_url = f"{WEB_APP_URL}/?uid={user_id}&v={int(time.time())}"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(text="⚡ Watch Ad (+3 Downloads)", web_app=types.WebAppInfo(url=cache_bypass_url)))
        bot.send_message(
            message.chat.id,
            "🔒 **Out of Download Credits!**\n\n"
            "Tap below to watch a quick ad and get **+3 HD Downloads** instantly!",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return

    try:
        t = threading.Thread(target=download_and_send, args=(message.chat.id, user_id, text))
        t.daemon = True
        t.start()
    except Exception as e:
        logging.error(f"Thread spawn failure: {e}")
        atomic_add_credits(user_id, 1)
        with active_users_lock:
            active_users.pop(user_id, None)
        bot.reply_to(message, "❌ Server busy. Please try again.")

if __name__ == '__main__':
    logging.info("Starting production bot engine...")
    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception:
        pass
    logging.info("Bot Polling Active...🟢")
    bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60)
