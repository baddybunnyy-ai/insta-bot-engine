import os
import time
import uuid
import sqlite3
import glob
import threading
import requests
from urllib.parse import urlparse, parse_qs
import telebot
from telebot import types
import yt_dlp
from flask import Flask

# Static FFmpeg binary initialization
try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = None

# Configurations
BOT_TOKEN = "8991187008:AAEmpfwuA3JUKLAuWYFjkgsnyHhbEcZFY4E"
WEB_APP_URL = "https://insta-reel-ad.vercel.app"
MAX_FILE_SIZE_BYTES = 48 * 1024 * 1024  # 48 MB limit

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=10)

user_last_request = {}
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Boot-up Cleanup
for old_file in glob.glob(f"{DOWNLOAD_DIR}/dl_*"):
    try:
        os.remove(old_file)
    except Exception:
        pass

# --- SQLite Database ---
def init_db():
    with sqlite3.connect('users.db', timeout=20) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS user_credits 
                     (user_id INTEGER PRIMARY KEY, credits INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS used_rewards 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, reward_token TEXT, user_id INTEGER, timestamp INTEGER)''')
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
    """
    Allows claiming rewards even with static tokens by using a 25-second cooldown timer.
    Prevents repeated tapping on old links while allowing genuine new ad completions.
    """
    clean_token = payload_token.strip()
    if len(clean_token) < 3:
        return False, "Invalid token format."

    now = int(time.time())
    COOLDOWN_SECONDS = 25  # Minimum time required to watch an ad

    with sqlite3.connect('users.db', timeout=20) as conn:
        c = conn.cursor()
        
        # Check when this user last claimed a reward
        c.execute("SELECT MAX(timestamp) FROM used_rewards WHERE user_id = ?", (current_user_id,))
        row = c.fetchone()
        last_claim = row[0] if row and row[0] else 0

        if (now - last_claim) < COOLDOWN_SECONDS:
            remaining = COOLDOWN_SECONDS - (now - last_claim)
            return False, f"Please wait {remaining}s before claiming again."

        # Insert log and credit balance
        c.execute("INSERT INTO used_rewards (reward_token, user_id, timestamp) VALUES (?, ?, ?)", 
                  (clean_token, current_user_id, now))
        c.execute('''INSERT INTO user_credits (user_id, credits) VALUES (?, ?)
                     ON CONFLICT(user_id) DO UPDATE SET credits = credits + ?''',
                  (current_user_id, 3, 3))
        conn.commit()
        return True, "Success"

init_db()

# --- Keep-Alive Web Server ---
app = Flask('')

@app.route('/')
def home():
    return "Bot Engine Active | Status: Healthy 🟢"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()

# --- YouTube External Gateways (Bypasses Cloud IP Blocks) ---
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

def fetch_youtube_api(url, output_path):
    video_id = extract_youtube_id(url)
    if not video_id:
        return False

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
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
            res = requests.get(api_url, headers=headers, timeout=5)
            if res.status_code == 200:
                format_streams = res.json().get("formatStreams", [])
                valid_mp4s = [s for s in format_streams if s.get("container") == "mp4" or "video/mp4" in s.get("type", "")]
                target_stream = valid_mp4s[-1] if valid_mp4s else (format_streams[-1] if format_streams else None)
                
                if target_stream and target_stream.get("url"):
                    with requests.get(target_stream["url"], headers=headers, stream=True, timeout=30) as r:
                        r.raise_for_status()
                        total = 0
                        with open(output_path, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=1024 * 1024):
                                if chunk:
                                    total += len(chunk)
                                    if total > MAX_FILE_SIZE_BYTES:
                                        break
                                    f.write(chunk)
                    if os.path.exists(output_path) and 100_000 < os.path.getsize(output_path) <= MAX_FILE_SIZE_BYTES:
                        return True
        except Exception:
            continue
    return False

# --- Master Downloader Engine ---
def download_and_send(chat_id, user_id, raw_url):
    msg = bot.send_message(chat_id, "⚡ *Processing & downloading HD video, please wait...*", parse_mode="Markdown")
    
    unique_id = uuid.uuid4().hex[:10]
    file_prefix = os.path.join(DOWNLOAD_DIR, f"dl_{user_id}_{unique_id}")
    final_file = f"{file_prefix}.mp4"
    
    success = False
    refund_needed = True

    try:
        # 1. YouTube Proxy Attempt
        domain = urlparse(raw_url).netloc.lower()
        if "youtube.com" in domain or "youtu.be" in domain:
            success = fetch_youtube_api(raw_url, final_file)

        # 2. Native yt-dlp Engine (Instagram, X, Pinterest, FB, YT Fallback)
        if not success:
            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': f'{file_prefix}.%(ext)s',
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'max_filesize': MAX_FILE_SIZE_BYTES,
                'nocheckcertificate': True,
                'cachedir': False,
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
                    file_size = os.path.getsize(final_file)
                    if 100_000 < file_size <= MAX_FILE_SIZE_BYTES:
                        success = True
            except Exception:
                pass

        # 3. Telegram Delivery
        if success and os.path.exists(final_file):
            try:
                remaining_credits = get_credits(user_id)
                with open(final_file, 'rb') as video:
                    bot.send_video(
                        chat_id, 
                        video, 
                        caption=f"📥 *Downloaded Successfully!*\n⚡ *Credits remaining:* `{remaining_credits}`",
                        parse_mode="Markdown",
                        timeout=120
                    )
                bot.delete_message(chat_id, msg.message_id)
                refund_needed = False
            except Exception:
                bot.send_message(chat_id, "❌ **Upload Failed.** Telegram delivery timed out. (Credit Refunded)")
        else:
            bot.send_message(chat_id, "❌ **Download Failed.** Please make sure the post is public and under 48MB. (Credit Refunded)")

    finally:
        if refund_needed:
            atomic_add_credits(user_id, 1)
            try:
                bot.delete_message(chat_id, msg.message_id)
            except Exception:
                pass

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
                markup.add(types.InlineKeyboardButton(text="⚡ Watch Ad (+3 Downloads)", web_app=types.WebAppInfo(url=cache_bypass_url)))
                
                bot.reply_to(
                    message,
                    f"⚠️ **Reward Notice:** {reason}\n\n"
                    f"⚡ Available Balance: **{total_credits} Downloads**\n\n"
                    f"Naye downloads add karne ke liye niche button se ad dekhein:",
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
    now = time.time()
    text = message.text.strip()

    if user_id in user_last_request and (now - user_last_request[user_id]) < 3:
        bot.reply_to(message, "⏳ **Please wait a few seconds before sending another link.**", parse_mode="Markdown")
        return
    user_last_request[user_id] = now

    if not text.startswith("http://") and not text.startswith("https://"):
        bot.reply_to(message, "⚠️ Please send a valid **video URL / link**.")
        return

    if atomic_deduct_credit(user_id):
        t = threading.Thread(target=download_and_send, args=(message.chat.id, user_id, text))
        t.start()
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
    print("[*] Starting Bot Engine...")
    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception:
        pass
    print("[*] Bot Polling Active...🟢")
    bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60)
