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

# ==========================================
# 🔐 CONFIGURATIONS (Hardcoded as requested)
# ==========================================
BOT_TOKEN = "8991187008:AAEmpfwuA3JUKLAuWYFjkgsnyHhbEcZFY4E"
WEB_APP_URL = "https://insta-reel-ad.vercel.app"
MAX_FILE_SIZE_BYTES = 48 * 1024 * 1024  # 48 MB limit (Telegram max is 50MB)

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=10)

# --- Global State & Folders ---
user_last_request = {}  # Anti-Spam Rate Limiting
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Boot-up Cleanup (Deletes old stuck files if server restarted)
for old_file in glob.glob(f"{DOWNLOAD_DIR}/dl_*"):
    try:
        os.remove(old_file)
    except:
        pass

# --- SQLite Database (Optimized for Concurrency) ---
def init_db():
    with sqlite3.connect('users.db', timeout=20) as conn:
        conn.execute('PRAGMA journal_mode=WAL')  # Prevents DB locking errors
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS user_credits 
                     (user_id INTEGER PRIMARY KEY, credits INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS used_rewards 
                     (reward_token TEXT PRIMARY KEY, user_id INTEGER, timestamp INTEGER)''')
        conn.commit()

def get_credits(user_id):
    with sqlite3.connect('users.db', timeout=20) as conn:
        c = conn.cursor()
        c.execute("SELECT credits FROM user_credits WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if row is None:
            c.execute("INSERT INTO user_credits VALUES (?, ?)", (user_id, 2)) # 2 Free credits for new users
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
    if len(clean_token) < 5:
        return False, "Invalid token format."

    with sqlite3.connect('users.db', timeout=20) as conn:
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

# --- Keep-Alive Web Server (For Render/Vercel) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot Engine Active | Status: Healthy 🟢"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()

# --- Master Downloader Engine (Crash-Proof & Safe) ---
def download_and_send(chat_id, user_id, raw_url):
    msg = bot.send_message(chat_id, "⚡ *Processing & downloading HD video, please wait...*", parse_mode="Markdown")
    
    unique_id = uuid.uuid4().hex[:10]
    file_prefix = os.path.join(DOWNLOAD_DIR, f"dl_{user_id}_{unique_id}")
    final_file = f"{file_prefix}.mp4"
    
    success = False
    refund_needed = True  # Assume failure until upload completely succeeds

    try:
        # Native yt-dlp Engine Setup
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
            
            # Find the actual downloaded file (ignoring temp chunks)
            downloaded = glob.glob(f"{file_prefix}*")
            valid = [f for f in downloaded if not f.endswith('.part') and not f.endswith('.ytdl')]
            
            if valid:
                final_file = valid[0]
                file_size = os.path.getsize(final_file)
                
                # Check if file is valid (larger than 100KB to avoid corrupt 0-byte files)
                if 100_000 < file_size <= MAX_FILE_SIZE_BYTES:
                    success = True
                else:
                    bot.edit_message_text("❌ File is either empty or larger than Telegram's 50MB limit.", chat_id, msg.message_id)
        except Exception as e:
            bot.edit_message_text(f"❌ **Download Failed:** Could not extract media. \n\n*Make sure the post/account is public.*", chat_id, msg.message_id, parse_mode="Markdown")

        # Telegram Delivery
        if success and os.path.exists(final_file):
            try:
                remaining_credits = get_credits(user_id)
                with open(final_file, 'rb') as video:
                    bot.send_video(
                        chat_id, 
                        video, 
                        caption=f"📥 *Downloaded Successfully!*\n⚡ *Credits remaining:* `{remaining_credits}`",
                        parse_mode="Markdown",
                        timeout=120  # Gives Telegram enough time to upload 48MB files
                    )
                
                bot.delete_message(chat_id, msg.message_id)
                refund_needed = False  # Upload success, DO NOT refund the credit
                
            except Exception as e:
                bot.send_message(chat_id, "❌ **Upload Failed.** Telegram server timeout. (Credit Refunded)")
                
    finally:
        # --- GUARANTEED CLEANUP & CREDIT REFUND ---
        if refund_needed:
            atomic_add_credits(user_id, 1) # Refund exactly 1 credit if failed
            try:
                bot.delete_message(chat_id, msg.message_id)
            except:
                pass

        # Destroy all temp files to save space
        for f in glob.glob(f"{file_prefix}*"):
            try:
                os.remove(f)
            except:
                pass


# --- Command & Message Handlers ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    # Handle reward token claim
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
    now = time.time()
    text = message.text.strip()

    # 1. Anti-Spam Rate Limiter (Cooldown of 3 seconds)
    if user_id in user_last_request and (now - user_last_request[user_id]) < 3:
        bot.reply_to(message, "⏳ **Please wait a few seconds before sending another link.**", parse_mode="Markdown")
        return
    user_last_request[user_id] = now

    # 2. Basic URL Validation
    if not text.startswith("http://") and not text.startswith("https://"):
        bot.reply_to(message, "⚠️ Please send a valid **video URL / link**.")
        return

    # 3. Credit Check & Background Download
    if atomic_deduct_credit(user_id):
        # Run in a background thread so the bot doesn't freeze for other users
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
    bot.remove_webhook()
    time.sleep(1)
    print("[*] Bot Polling Active...🟢")
    # Increased timeouts to prevent crashes during bad network connectivity
    bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60)
