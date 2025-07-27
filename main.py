import os
import logging
import asyncio
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import yt_dlp
import instaloader
import requests
from supabase import create_client, Client
from urllib.parse import urlparse
import tempfile
import re

# Logging sozlamalari
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', '0'))
DEFAULT_DAILY_LIMIT = int(os.getenv('DEFAULT_DAILY_LIMIT', '10'))

# Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

class MediaDownloaderBot:
    def __init__(self):
        self.instagram_loader = instaloader.Instaloader()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command handler"""
        user = update.effective_user

        # Foydalanuvchini database ga qo'shish
        await self.register_user(user.id, user.username, user.first_name)

        keyboard = [
            [InlineKeyboardButton("🎬 YouTube", callback_data='platform_youtube')],
            [InlineKeyboardButton("📸 Instagram", callback_data='platform_instagram')],
            [InlineKeyboardButton("📌 Pinterest", callback_data='platform_pinterest')],
            [InlineKeyboardButton("📊 Statistika", callback_data='stats')],
            [InlineKeyboardButton("ℹ️ Yordam", callback_data='help')]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        welcome_text = f"""
🎉 Xush kelibsiz, {user.first_name}!

🤖 Men Media Downloader Bot man.
Men sizga quyidagi platformalardan media fayllarni yuklab beraman:

• YouTube - videolar va audio
• Instagram - postlar, reels, stories
• Pinterest - rasmlar

📝 Linkni yuboring yoki tugmani tanlang!
        """

        await update.message.reply_text(welcome_text, reply_markup=reply_markup)

    async def register_user(self, user_id: int, username: str, first_name: str):
        """Foydalanuvchini database ga ro'yxatdan o'tkazish"""
        try:
            # Foydalanuvchi mavjudligini tekshirish
            result = supabase.table('users').select('*').eq('user_id', user_id).execute()

            if not result.data:
                # Yangi foydalanuvchi qo'shish
                supabase.table('users').insert({
                    'user_id': user_id,
                    'username': username,
                    'first_name': first_name,
                    'daily_limit': DEFAULT_DAILY_LIMIT,
                    'used_today': 0,
                    'last_reset': str(date.today())
                }).execute()
                logger.info(f"Yangi foydalanuvchi ro'yxatdan o'tdi: {user_id}")
        except Exception as e:
            logger.error(f"Foydalanuvchini ro'yxatdan o'tkazishda xatolik: {e}")

    async def check_rate_limit(self, user_id: int) -> tuple[bool, int, int]:
        """Rate limit tekshirish"""
        try:
            result = supabase.table('users').select('*').eq('user_id', user_id).execute()

            if not result.data:
                return False, 0, 0

            user_data = result.data[0]
            today = str(date.today())

            # Agar yangi kun bo'lsa, resetlash
            if user_data['last_reset'] != today:
                supabase.table('users').update({
                    'used_today': 0,
                    'last_reset': today
                }).eq('user_id', user_id).execute()
                user_data['used_today'] = 0

            can_download = user_data['used_today'] < user_data['daily_limit']
            return can_download, user_data['used_today'], user_data['daily_limit']

        except Exception as e:
            logger.error(f"Rate limit tekshirishda xatolik: {e}")
            return True, 0, DEFAULT_DAILY_LIMIT

    async def increment_usage(self, user_id: int):
        """Foydalanish sonini oshirish"""
        try:
            result = supabase.table('users').select('used_today').eq('user_id', user_id).execute()
            if result.data:
                current_usage = result.data[0]['used_today']
                supabase.table('users').update({
                    'used_today': current_usage + 1
                }).eq('user_id', user_id).execute()
        except Exception as e:
            logger.error(f"Foydalanish sonini oshirishda xatolik: {e}")

    def detect_platform(self, url: str) -> str:
        """URL dan platformani aniqlash"""
        if 'youtube.com' in url or 'youtu.be' in url:
            return 'youtube'
        elif 'instagram.com' in url:
            return 'instagram'
        elif 'pinterest.com' in url or 'pin.it' in url:
            return 'pinterest'
        return 'unknown'

    async def download_youtube(self, url: str, update: Update) -> str:
        """YouTube video yuklab olish"""
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                ydl_opts = {
                    'format': 'best[filesize<50M]/worst',
                    'outtmpl': f'{temp_dir}/%(title)s.%(ext)s',
                    'noplaylist': True,
                }

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)

                    if os.path.exists(filename):
                        return filename
                    else:
                        # Fayl nomini qidirish
                        for file in os.listdir(temp_dir):
                            return os.path.join(temp_dir, file)

        except Exception as e:
            logger.error(f"YouTube yuklab olishda xatolik: {e}")
            return None

    async def download_instagram(self, url: str) -> str:
        """Instagram post yuklab olish"""
        try:
            # Instagram shortcode ni olish
            shortcode = re.search(r'instagram\.com/p/([^/?]+)', url)
            if not shortcode:
                shortcode = re.search(r'instagram\.com/reel/([^/?]+)', url)

            if shortcode:
                shortcode = shortcode.group(1)

                with tempfile.TemporaryDirectory() as temp_dir:
                    post = instaloader.Post.from_shortcode(self.instagram_loader.context, shortcode)

                    self.instagram_loader.download_post(post, target=temp_dir)

                    # Yuklab olingan faylni topish
                    for file in os.listdir(temp_dir):
                        if file.endswith(('.jpg', '.mp4', '.png')):
                            return os.path.join(temp_dir, file)

        except Exception as e:
            logger.error(f"Instagram yuklab olishda xatolik: {e}")
            return None

    async def download_pinterest(self, url: str) -> str:
        """Pinterest rasm yuklab olish"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            response = requests.get(url, headers=headers)

            # Pinterest rasm URL ini topish
            img_pattern = r'"url":"(https://i\.pinimg\.com/[^"]+)"'
            matches = re.findall(img_pattern, response.text)

            if matches:
                img_url = matches[0].replace('\\u002F', '/')

                img_response = requests.get(img_url, headers=headers)

                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_file:
                    temp_file.write(img_response.content)
                    return temp_file.name

        except Exception as e:
            logger.error(f"Pinterest yuklab olishda xatolik: {e}")
            return None

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Link xabarlarini qayta ishlash"""
        user_id = update.effective_user.id
        text = update.message.text

        # Rate limit tekshirish
        can_download, used, limit = await self.check_rate_limit(user_id)

        if not can_download:
            await update.message.reply_text(
                f"❌ Kunlik limitga yetdingiz!\n"
                f"📊 Bugun: {used}/{limit}\n"
                f"🔄 Ertaga yangi limitlar beriladi."
            )
            return

        # URL tekshirish
        if not any(domain in text for domain in ['youtube.com', 'youtu.be', 'instagram.com', 'pinterest.com', 'pin.it']):
            await update.message.reply_text(
                "❌ Noto'g'ri link!\n\n"
                "✅ Qo'llab-quvvatlanadigan platformalar:\n"
                "• YouTube\n"
                "• Instagram\n"
                "• Pinterest"
            )
            return

        platform = self.detect_platform(text)

        # Yuklab olish jarayoni
        await update.message.reply_text("⏳ Yuklab olinmoqda...")

        file_path = None

        if platform == 'youtube':
            file_path = await self.download_youtube(text, update)
        elif platform == 'instagram':
            file_path = await self.download_instagram(text)
        elif platform == 'pinterest':
            file_path = await self.download_pinterest(text)

        if file_path and os.path.exists(file_path):
            try:
                # Faylni yuborish
                if file_path.endswith('.mp4'):
                    await update.message.reply_video(
                        video=open(file_path, 'rb'),
                        caption=f"✅ {platform.title()} dan yuklab olindi!"
                    )
                else:
                    await update.message.reply_photo(
                        photo=open(file_path, 'rb'),
                        caption=f"✅ {platform.title()} dan yuklab olindi!"
                    )

                # Foydalanish sonini oshirish
                await self.increment_usage(user_id)

                # Faylni o'chirish
                os.unlink(file_path)

            except Exception as e:
                logger.error(f"Fayl yuborishda xatolik: {e}")
                await update.message.reply_text("❌ Fayl yuborishda xatolik yuz berdi.")
        else:
            await update.message.reply_text("❌ Yuklab olishda xatolik yuz berdi. Iltimos boshqa link bilan urinib ko'ring.")

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Inline keyboard tugmalari"""
        query = update.callback_query
        await query.answer()

        if query.data == 'stats':
            user_id = update.effective_user.id
            can_download, used, limit = await self.check_rate_limit(user_id)

            stats_text = f"""
📊 **Sizning statistikangiz:**

👤 Foydalanuvchi: {update.effective_user.first_name}
📅 Bugun: {used}/{limit}
⏰ So'nggi yangilanish: {datetime.now().strftime('%H:%M')}

💡 **Maslahat:** Link yuboring yoki platformani tanlang!
            """

            await query.edit_message_text(stats_text, parse_mode='Markdown')

        elif query.data == 'help':
            help_text = """
ℹ️ **Yordam:**

**Qo'llab-quvvatlanadigan platformalar:**
• YouTube - video/audio
• Instagram - post/reel/story
• Pinterest - rasmlar

**Foydalanish:**
1. Linkni yuboring
2. Yoki platforma tugmasini bosing
3. Kutib turing!

**Cheklovlar:**
• Kuniga 10 ta yuklab olish
• Maksimal 50MB fayl hajmi

**Yordam:** @your_support_username
            """

            await query.edit_message_text(help_text, parse_mode='Markdown')

def main():
    """Botni ishga tushirish"""
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN topilmadi!")
        return

    bot = MediaDownloaderBot()

    application = Application.builder().token(BOT_TOKEN).build()

    # Handlerlar
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    application.add_handler(CallbackQueryHandler(bot.button_handler))

    # Botni ishga tushirish
    logger.info("Bot ishga tushirildi!")
    application.run_polling()

if __name__ == '__main__':
    main()
