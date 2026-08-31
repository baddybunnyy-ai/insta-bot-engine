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


# =========================================================
# FFMPEG
# =========================================================

try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = None


# =========================================================
# CONFIG
# =========================================================

# Put your NEW token in Render Environment Variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")

WEB_APP_URL = "https://insta-reel-ad.vercel.app"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")

bot = telebot.TeleBot(BOT_TOKEN)


# =========================================================
# SQLITE DATABASE
# =========================================================

def init_db():
    conn = sqlite3.connect("users.db")
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_credits (
            user_id INTEGER PRIMARY KEY,
            credits INTEGER
        )
    """)

    conn.commit()
    conn.close()


def get_credits(user_id):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()

    c.execute(
        "SELECT credits FROM user_credits WHERE user_id = ?",
        (user_id,)
    )

    row = c.fetchone()

    if row is None:
        c.execute(
            "INSERT INTO user_credits VALUES (?, ?)",
            (user_id, 2)
        )

        conn.commit()
        credits = 2

    else:
        credits = row[0]

    conn.close()

    return credits


def deduct_credit(user_id):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()

    c.execute(
        """
        UPDATE user_credits
        SET credits = credits - 1
        WHERE user_id = ?
        AND credits > 0
        """,
        (user_id,)
    )

    conn.commit()
    conn.close()


def add_credits(user_id, amount=3):
    current = get_credits(user_id)

    conn = sqlite3.connect("users.db")
    c = conn.cursor()

    c.execute(
        """
        UPDATE user_credits
        SET credits = ?
        WHERE user_id = ?
        """,
        (current + amount, user_id)
    )

    conn.commit()
    conn.close()


init_db()


# =========================================================
# FLASK KEEP ALIVE
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot Engine 24/7 Active!"


def run_flask():
    app.run(
        host="0.0.0.0",
        port=8080
    )


def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()


# =========================================================
# URL HELPERS
# =========================================================

def is_youtube_url(url):
    return bool(
        re.search(
            r"(youtube\.com|youtu\.be)",
            url,
            re.IGNORECASE
        )
    )


# =========================================================
# /START
# =========================================================

@bot.message_handler(commands=["start"])
def send_welcome(message):

    user_id = message.from_user.id
    text = message.text.strip()

    # -----------------------------------------
    # AD REWARD
    # -----------------------------------------

    if "reward_" in text:

        add_credits(user_id, 3)

        total_credits = get_credits(user_id)

        bot.reply_to(
            message,
            (
                "🎉 **+3 Download Credits Added!**\n\n"
                f"⚡ Total Available Balance: **{total_credits} Downloads**\n\n"
                "📥 **Send your video link now to download!**"
            ),
            parse_mode="Markdown"
        )

        return

    # -----------------------------------------
    # NORMAL START
    # -----------------------------------------

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

    bot.reply_to(
        message,
        welcome_text,
        parse_mode="Markdown"
    )


# =========================================================
# YOUTUBE DOWNLOADER
# =========================================================

def download_youtube(url, file_prefix):

    """
    Downloads YouTube videos / Shorts using yt-dlp.

    Returns:
        downloaded filename on success
        None on failure
    """

    output_template = f"{file_prefix}.%(ext)s"

    ydl_opts = {
        # Prefer MP4.
        # 1080p maximum keeps files manageable.
        "format": (
            "best[ext=mp4][height<=1080]/"
            "bestvideo[ext=mp4][height<=1080]+bestaudio/"
            "best[height<=1080]/"
            "best"
        ),

        "outtmpl": output_template,

        "noplaylist": True,

        # Network reliability
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,

        # 25 MB application limit
        "max_filesize": 25 * 1024 * 1024,

        # Browser-like headers
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },

        # Shorts and normal YouTube URLs
        "extractor_args": {
            "youtube": {
                "player_client": ["mweb"]
            }
        },

        # Don't dump huge logs into Render normally
        "quiet": True,
        "no_warnings": True,
    }

    # FFmpeg
    if FFMPEG_PATH:

        ydl_opts["ffmpeg_location"] = FFMPEG_PATH
        ydl_opts["merge_output_format"] = "mp4"

    try:

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

            prepared_filename = ydl.prepare_filename(info)

            base = os.path.splitext(prepared_filename)[0]

            possible_files = [
                prepared_filename,
                base + ".mp4",
                base + ".mkv",
                base + ".webm"
            ]

            for filename in possible_files:

                if os.path.exists(filename):

                    if os.path.getsize(filename) > 10_000:
                        return filename

    except Exception as e:

        print("\n====================================")
        print("YOUTUBE DOWNLOAD ERROR")
        print("====================================")
        print(str(e))
        print("====================================\n")

    return None


# =========================================================
# UNIVERSAL DOWNLOAD HANDLER
# =========================================================

def download_and_send(
    chat_id,
    user_id,
    url,
    remaining_credits
):

    msg = bot.send_message(
        chat_id,
        "⚡ *Processing & downloading HD video, please wait...*",
        parse_mode="Markdown"
    )

    file_prefix = (
        f"dl_{user_id}_{int(time.time())}"
    )

    downloaded_file = None

    # =====================================================
    # YOUTUBE / YOUTUBE SHORTS
    # =====================================================

    if is_youtube_url(url):

        downloaded_file = download_youtube(
            url,
            file_prefix
        )

    # =====================================================
    # ALL OTHER PLATFORMS
    # Instagram
    # Pinterest
    # X / Twitter
    # Facebook
    # Reddit
    # etc.
    # =====================================================

    else:

        ydl_opts = {

            "format": (
                "best[ext=mp4]/"
                "bestvideo[ext=mp4]+bestaudio/"
                "best"
            ),

            "outtmpl":
                f"{file_prefix}.%(ext)s",

            "quiet": True,
            "no_warnings": True,

            "noplaylist": True,

            "nocheckcertificate": True,

            "max_filesize":
                25 * 1024 * 1024,

            "socket_timeout": 30,

            "retries": 3,

            "fragment_retries": 3,
        }

        if FFMPEG_PATH:

            ydl_opts["ffmpeg_location"] = FFMPEG_PATH

            ydl_opts["merge_output_format"] = "mp4"

        try:

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:

                ydl.download([url])

            downloaded = glob.glob(
                f"{file_prefix}*"
            )

            valid = [
                f for f in downloaded
                if not f.endswith(".part")
                and not f.endswith(".ytdl")
            ]

            if valid:

                # Prefer largest valid file
                valid.sort(
                    key=lambda x: os.path.getsize(x),
                    reverse=True
                )

                downloaded_file = valid[0]

        except Exception as e:

            print("\n====================================")
            print("YT-DLP ERROR")
            print("====================================")
            print(str(e))
            print("====================================\n")

    # =====================================================
    # SUCCESS
    # =====================================================

    if (
        downloaded_file
        and os.path.exists(downloaded_file)
        and os.path.getsize(downloaded_file) > 0
    ):

        try:

            file_size = os.path.getsize(
                downloaded_file
            )

            # ---------------------------------------------
            # 25 MB LIMIT
            # ---------------------------------------------

            if file_size > 25 * 1024 * 1024:

                raise Exception(
                    "Downloaded file exceeds 25 MB limit."
                )

            # ---------------------------------------------
            # SEND TO TELEGRAM
            # ---------------------------------------------

            with open(
                downloaded_file,
                "rb"
            ) as video:

                bot.send_video(
                    chat_id,
                    video,
                    caption=(
                        "📥 **Downloaded via "
                        "All-in-One Saver Bot**\n"
                        f"⚡ *Credits remaining:* "
                        f"`{remaining_credits}`"
                    ),
                    parse_mode="Markdown"
                )

            # ---------------------------------------------
            # DELETE PROCESSING MESSAGE
            # ---------------------------------------------

            try:

                bot.delete_message(
                    chat_id,
                    msg.message_id
                )

            except Exception:
                pass

        except Exception as e:

            print("\n====================================")
            print("TELEGRAM UPLOAD ERROR")
            print("====================================")
            print(str(e))
            print("====================================\n")

            # Refund because download succeeded
            # but sending failed
            add_credits(user_id, 1)

            try:

                bot.delete_message(
                    chat_id,
                    msg.message_id
                )

            except Exception:
                pass

            bot.send_message(
                chat_id,
                (
                    "❌ **Upload failed.**\n\n"
                    "Your download credit has been "
                    "refunded."
                ),
                parse_mode="Markdown"
            )

    # =====================================================
    # FAILURE
    # =====================================================

    else:

        # Refund credit
        add_credits(user_id, 1)

        # Delete processing message
        try:

            bot.delete_message(
                chat_id,
                msg.message_id
            )

        except Exception:
            pass

        bot.send_message(
            chat_id,
            (
                "❌ **Download Failed.**\n\n"
                "Please make sure:\n"
                "1. The link is public.\n"
                "2. The video is under 25MB.\n"
                "3. The video is still available.\n\n"
                "💳 **Credit refunded.**"
            ),
            parse_mode="Markdown"
        )

    # =====================================================
    # CLEANUP
    # =====================================================

    for f in glob.glob(
        f"{file_prefix}*"
    ):

        try:
            os.remove(f)
        except Exception:
            pass


# =========================================================
# HANDLE ALL INCOMING LINKS
# =========================================================

@bot.message_handler(
    func=lambda message: True
)
def handle_message(message):

    user_id = message.from_user.id

    # Prevent crashes from non-text messages
    if not message.text:

        bot.reply_to(
            message,
            "⚠️ Please send a valid **video URL / link**.",
            parse_mode="Markdown"
        )

        return

    text = message.text.strip()

    # =====================================================
    # URL CHECK
    # =====================================================

    if not (
        text.startswith("http://")
        or text.startswith("https://")
    ):

        bot.reply_to(
            message,
            "⚠️ Please send a valid **video URL / link**.",
            parse_mode="Markdown"
        )

        return

    # =====================================================
    # CREDIT CHECK
    # =====================================================

    credits = get_credits(user_id)

    # =====================================================
    # HAS CREDIT
    # =====================================================

    if credits > 0:

        deduct_credit(user_id)

        remaining = credits - 1

        download_and_send(
            message.chat.id,
            user_id,
            text,
            remaining
        )

    # =====================================================
    # NO CREDIT
    # =====================================================

    else:

        cache_bypass_url = (
            f"{WEB_APP_URL}/"
            f"?uid={user_id}"
            f"&v={int(time.time())}"
        )

        markup = types.InlineKeyboardMarkup()

        web_app_info = types.WebAppInfo(
            url=cache_bypass_url
        )

        ad_button = types.InlineKeyboardButton(
            text="⚡ Watch Ad (+3 Downloads)",
            web_app=web_app_info
        )

        markup.add(ad_button)

        bot.send_message(
            message.chat.id,
            (
                "🔒 **Out of Download Credits!**\n\n"
                "You have used all your free downloads.\n\n"
                "Tap below to watch a quick ad and "
                "get **+3 HD Downloads** instantly!"
            ),
            reply_markup=markup,
            parse_mode="Markdown"
        )


# =========================================================
# START BOT
# =========================================================

if __name__ == "__main__":

    keep_alive()

    bot.remove_webhook()

    time.sleep(1)

    print("====================================")
    print("BOT STARTED")
    print("====================================")

    bot.infinity_polling(
        skip_pending=True
    )
