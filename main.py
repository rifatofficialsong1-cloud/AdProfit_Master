"""
Telegram Ad Automation Bot
A professional, scalable bot for automated advertising in Telegram groups and channels
Author: Professional Bot Developer
Version: 1.0.0
"""

import os
import sqlite3
import threading
import time
import logging
import schedule
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from contextlib import contextmanager
import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException
import re
import hashlib
from enum import Enum

# ==================== Configuration ====================
import os
from dotenv import load_dotenv

load_dotenv()

# Bot Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
TON_WALLET = os.getenv('TON_WALLET')
ADSTERRA_LINK = os.getenv('ADSTERRA_LINK')
ADMIN_IDS = [int(id) for id in os.getenv('ADMIN_IDS', '').split(',') if id]

# Bot Settings
BOT_USERNAME = "YourBotUsername"
DEVELOPER_AD_DELAY = 30  # seconds
FREE_USER_LIMIT = 1
PREMIUM_CHECK_INTERVAL = 3600  # 1 hour

# Logging Configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN)

# ==================== Database Models ====================

class UserTier(Enum):
    FREE = "free"
    PREMIUM = "premium"

@dataclass
class User:
    id: int
    username: Optional[str]
    first_name: str
    tier: UserTier
    premium_until: Optional[datetime]
    joined_at: datetime
    
@dataclass
class LinkedChat:
    chat_id: int
    chat_type: str  # 'group' or 'channel'
    chat_title: str
    user_id: int
    is_active: bool
    welcome_enabled: bool
    welcome_message: Optional[str]
    welcome_media: Optional[str]  # file_id
    welcome_media_type: Optional[str]  # 'photo' or 'video'
    added_at: datetime

@dataclass
class AdMessage:
    id: int
    chat_id: int
    content: str
    media_type: Optional[str]  # 'photo', 'video', or None
    media_file_id: Optional[str]
    interval_minutes: int
    is_active: bool
    created_at: datetime

# ==================== Database Manager ====================

class DatabaseManager:
    def __init__(self, db_path='bot_database.db'):
        self.db_path = db_path
        self.init_database()
        
    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def init_database(self):
        """Initialize database tables"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    tier TEXT DEFAULT 'free',
                    premium_until TIMESTAMP,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Linked chats table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS linked_chats (
                    chat_id INTEGER PRIMARY KEY,
                    chat_type TEXT,
                    chat_title TEXT,
                    user_id INTEGER,
                    is_active BOOLEAN DEFAULT 1,
                    welcome_enabled BOOLEAN DEFAULT 0,
                    welcome_message TEXT,
                    welcome_media TEXT,
                    welcome_media_type TEXT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            
            # Ad messages table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ad_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    content TEXT,
                    media_type TEXT,
                    media_file_id TEXT,
                    interval_minutes INTEGER,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (chat_id) REFERENCES linked_chats(chat_id)
                )
            ''')
            
            # Ad logs table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ad_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    ad_id INTEGER,
                    posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT,
                    FOREIGN KEY (chat_id) REFERENCES linked_chats(chat_id),
                    FOREIGN KEY (ad_id) REFERENCES ad_messages(id)
                )
            ''')
            
            conn.commit()
    
    # User methods
    def get_user(self, user_id: int) -> Optional[User]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
            if row:
                return User(
                    id=row['user_id'],
                    username=row['username'],
                    first_name=row['first_name'],
                    tier=UserTier(row['tier']),
                    premium_until=datetime.fromisoformat(row['premium_until']) if row['premium_until'] else None,
                    joined_at=datetime.fromisoformat(row['joined_at'])
                )
            return None
    
    def create_or_update_user(self, user_id: int, username: str, first_name: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, username, first_name, tier)
                VALUES (?, ?, ?, COALESCE((SELECT tier FROM users WHERE user_id = ?), 'free'))
            ''', (user_id, username, first_name, user_id))
            conn.commit()
    
    def update_user_tier(self, user_id: int, tier: UserTier, days: int = 0):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if tier == UserTier.PREMIUM and days > 0:
                premium_until = datetime.now() + timedelta(days=days)
                cursor.execute('''
                    UPDATE users 
                    SET tier = ?, premium_until = ?
                    WHERE user_id = ?
                ''', (tier.value, premium_until.isoformat(), user_id))
            else:
                cursor.execute('''
                    UPDATE users 
                    SET tier = ?, premium_until = NULL
                    WHERE user_id = ?
                ''', (tier.value, user_id))
            conn.commit()
    
    def is_premium(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        if not user or user.tier == UserTier.FREE:
            return False
        if user.premium_until and user.premium_until < datetime.now():
            # Premium expired
            self.update_user_tier(user_id, UserTier.FREE)
            return False
        return True
    
    # Chat methods
    def add_linked_chat(self, chat_id: int, chat_type: str, chat_title: str, user_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO linked_chats 
                (chat_id, chat_type, chat_title, user_id, is_active)
                VALUES (?, ?, ?, ?, 1)
            ''', (chat_id, chat_type, chat_title, user_id))
            conn.commit()
    
    def remove_linked_chat(self, chat_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE linked_chats SET is_active = 0 WHERE chat_id = ?', (chat_id,))
            cursor.execute('UPDATE ad_messages SET is_active = 0 WHERE chat_id = ?', (chat_id,))
            conn.commit()
    
    def get_user_chats(self, user_id: int) -> List[LinkedChat]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM linked_chats 
                WHERE user_id = ? AND is_active = 1
            ''', (user_id,))
            rows = cursor.fetchall()
            return [
                LinkedChat(
                    chat_id=row['chat_id'],
                    chat_type=row['chat_type'],
                    chat_title=row['chat_title'],
                    user_id=row['user_id'],
                    is_active=bool(row['is_active']),
                    welcome_enabled=bool(row['welcome_enabled']),
                    welcome_message=row['welcome_message'],
                    welcome_media=row['welcome_media'],
                    welcome_media_type=row['welcome_media_type'],
                    added_at=datetime.fromisoformat(row['added_at'])
                ) for row in rows
            ]
    
    def update_welcome_settings(self, chat_id: int, enabled: bool, message: str = None, 
                               media: str = None, media_type: str = None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE linked_chats 
                SET welcome_enabled = ?,
                    welcome_message = ?,
                    welcome_media = ?,
                    welcome_media_type = ?
                WHERE chat_id = ?
            ''', (enabled, message, media, media_type, chat_id))
            conn.commit()
    
    # Ad methods
    def add_ad(self, chat_id: int, content: str, interval_minutes: int, 
               media_type: str = None, media_file_id: str = None) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO ad_messages 
                (chat_id, content, media_type, media_file_id, interval_minutes, is_active)
                VALUES (?, ?, ?, ?, ?, 1)
            ''', (chat_id, content, media_type, media_file_id, interval_minutes))
            conn.commit()
            return cursor.lastrowid
    
    def get_chat_ads(self, chat_id: int) -> List[AdMessage]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM ad_messages 
                WHERE chat_id = ? AND is_active = 1
                ORDER BY created_at DESC
            ''', (chat_id,))
            rows = cursor.fetchall()
            return [
                AdMessage(
                    id=row['id'],
                    chat_id=row['chat_id'],
                    content=row['content'],
                    media_type=row['media_type'],
                    media_file_id=row['media_file_id'],
                    interval_minutes=row['interval_minutes'],
                    is_active=bool(row['is_active']),
                    created_at=datetime.fromisoformat(row['created_at'])
                ) for row in rows
            ]
    
    def delete_ad(self, ad_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE ad_messages SET is_active = 0 WHERE id = ?', (ad_id,))
            conn.commit()
    
    def log_ad_post(self, chat_id: int, ad_id: int, status: str = 'success'):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO ad_logs (chat_id, ad_id, status)
                VALUES (?, ?, ?)
            ''', (chat_id, ad_id, status))
            conn.commit()

# Initialize database
db = DatabaseManager()

# ==================== Ad Engine ====================

class AdEngine:
    def __init__(self, bot_instance):
        self.bot = bot_instance
        self.running = True
        self.threads = []
        self.ad_queue = {}
        self.lock = threading.Lock()
        
    def start(self):
        """Start the ad engine in separate threads"""
        # Main scheduler thread
        scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        scheduler_thread.start()
        self.threads.append(scheduler_thread)
        
        # Worker threads for posting ads
        for i in range(3):  # 3 concurrent workers
            worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            worker_thread.start()
            self.threads.append(worker_thread)
            
        logger.info("Ad Engine started with {} threads".format(len(self.threads)))
    
    def stop(self):
        """Stop the ad engine"""
        self.running = False
        
    def _scheduler_loop(self):
        """Main scheduler loop - checks for ads to post"""
        while self.running:
            try:
                self._check_and_queue_ads()
                time.sleep(10)  # Check every 10 seconds
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
    
    def _check_and_queue_ads(self):
        """Check all active chats and queue ads that need to be posted"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get all active ads with their last post time
            cursor.execute('''
                SELECT a.*, l.chat_type, l.user_id,
                       MAX(al.posted_at) as last_posted
                FROM ad_messages a
                JOIN linked_chats l ON a.chat_id = l.chat_id
                LEFT JOIN ad_logs al ON a.id = al.ad_id
                WHERE a.is_active = 1 AND l.is_active = 1
                GROUP BY a.id
            ''')
            
            for row in cursor.fetchall():
                chat_id = row['chat_id']
                ad_id = row['id']
                interval = row['interval_minutes']
                last_posted = row['last_posted']
                
                # Calculate when next post should be
                if last_posted:
                    next_post = datetime.fromisoformat(last_posted) + timedelta(minutes=interval)
                else:
                    next_post = datetime.now()  # Post immediately if never posted
                
                # If it's time to post, add to queue
                if datetime.now() >= next_post:
                    with self.lock:
                        if chat_id not in self.ad_queue:
                            self.ad_queue[chat_id] = []
                        self.ad_queue[chat_id].append({
                            'ad_id': ad_id,
                            'content': row['content'],
                            'media_type': row['media_type'],
                            'media_file_id': row['media_file_id'],
                            'chat_type': row['chat_type'],
                            'user_id': row['user_id']
                        })
    
    def _worker_loop(self):
        """Worker thread that posts ads from queue"""
        while self.running:
            try:
                chat_to_process = None
                ad_to_process = None
                
                with self.lock:
                    for chat_id, ads in self.ad_queue.items():
                        if ads:
                            chat_to_process = chat_id
                            ad_to_process = ads.pop(0)
                            if not ads:
                                del self.ad_queue[chat_id]
                            break
                
                if chat_to_process and ad_to_process:
                    self._post_ad(chat_to_process, ad_to_process)
                
                time.sleep(1)  # Small delay between posts
                
            except Exception as e:
                logger.error(f"Worker error: {e}")
    
    def _post_ad(self, chat_id: int, ad_data: dict):
        """Post an ad to a chat"""
        try:
            user_id = ad_data['user_id']
            is_premium = db.is_premium(user_id)
            
            # Post user's ad
            if ad_data['media_type'] == 'photo':
                self.bot.send_photo(
                    chat_id, 
                    ad_data['media_file_id'],
                    caption=ad_data['content'],
                    parse_mode='HTML'
                )
            elif ad_data['media_type'] == 'video':
                self.bot.send_video(
                    chat_id,
                    ad_data['media_file_id'],
                    caption=ad_data['content'],
                    parse_mode='HTML'
                )
            else:
                self.bot.send_message(
                    chat_id,
                    ad_data['content'],
                    parse_mode='HTML'
                )
            
            # Log the post
            db.log_ad_post(chat_id, ad_data['ad_id'], 'success')
            
            # Post developer ad if user is free
            if not is_premium and ADSTERRA_LINK:
                time.sleep(DEVELOPER_AD_DELAY)
                self._post_developer_ad(chat_id)
                
            logger.info(f"Posted ad {ad_data['ad_id']} to chat {chat_id}")
            
        except ApiTelegramException as e:
            if e.error_code == 403:  # Bot was kicked
                logger.warning(f"Bot kicked from chat {chat_id}, removing...")
                db.remove_linked_chat(chat_id)
            else:
                logger.error(f"Failed to post ad to {chat_id}: {e}")
                db.log_ad_post(chat_id, ad_data['ad_id'], f'failed: {e}')
    
    def _post_developer_ad(self, chat_id: int):
        """Post developer's ad"""
        try:
            # You can customize this ad format
            ad_text = f"""
🔥 <b>Sponsored Message</b> 🔥

<a href='{ADSTERRA_LINK}'>Click here to learn more!</a>

<em>This ad supports the bot's development</em>
            """
            self.bot.send_message(chat_id, ad_text, parse_mode='HTML', disable_web_page_preview=True)
        except Exception as e:
            logger.error(f"Failed to post developer ad to {chat_id}: {e}")

# ==================== UI Components ====================

class UIBuilder:
    @staticmethod
    def main_menu(user_id: int) -> types.InlineKeyboardMarkup:
        """Build main menu keyboard"""
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        
        is_premium = db.is_premium(user_id)
        
        buttons = [
            types.InlineKeyboardButton("📊 My Chats", callback_data="my_chats"),
            types.InlineKeyboardButton("➕ Add Chat", callback_data="add_chat"),
            types.InlineKeyboardButton("📝 Manage Ads", callback_data="manage_ads"),
            types.InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
            types.InlineKeyboardButton("👤 My Profile", callback_data="profile"),
            types.InlineKeyboardButton("💎 Upgrade", callback_data="upgrade") if not is_premium else 
                     types.InlineKeyboardButton("✨ Premium", callback_data="premium_info")
        ]
        
        keyboard.add(*buttons)
        return keyboard
    
    @staticmethod
    def chat_management_menu(chat_id: int) -> types.InlineKeyboardMarkup:
        """Build chat management keyboard"""
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        
        buttons = [
            types.InlineKeyboardButton("📝 Welcome Message", callback_data=f"welcome_{chat_id}"),
            types.InlineKeyboardButton("📢 Add Ad", callback_data=f"add_ad_{chat_id}"),
            types.InlineKeyboardButton("📋 View Ads", callback_data=f"view_ads_{chat_id}"),
            types.InlineKeyboardButton("❌ Remove Chat", callback_data=f"remove_{chat_id}"),
            types.InlineKeyboardButton("◀️ Back", callback_data="back_to_chats")
        ]
        
        keyboard.add(*buttons)
        return keyboard
    
    @staticmethod
    def welcome_settings_menu(chat_id: int, enabled: bool) -> types.InlineKeyboardMarkup:
        """Build welcome message settings keyboard"""
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        
        status = "✅ Enabled" if enabled else "❌ Disabled"
        toggle_text = "❌ Disable" if enabled else "✅ Enable"
        
        buttons = [
            types.InlineKeyboardButton(f"Status: {status}", callback_data="noop"),
            types.InlineKeyboardButton(toggle_text, callback_data=f"toggle_welcome_{chat_id}"),
            types.InlineKeyboardButton("✏️ Edit Text", callback_data=f"edit_welcome_text_{chat_id}"),
            types.InlineKeyboardButton("🖼 Add Media", callback_data=f"welcome_media_{chat_id}"),
            types.InlineKeyboardButton("◀️ Back", callback_data=f"chat_menu_{chat_id}")
        ]
        
        keyboard.add(*buttons)
        return keyboard
    
    @staticmethod
    def ads_menu(chat_id: int, ads: List[AdMessage]) -> types.InlineKeyboardMarkup:
        """Build ads management keyboard"""
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        
        for ad in ads[:5]:  # Show first 5 ads
            btn_text = f"📌 {ad.content[:30]}... ({ad.interval_minutes} min)"
            keyboard.add(types.InlineKeyboardButton(
                btn_text, 
                callback_data=f"edit_ad_{ad.id}"
            ))
        
        keyboard.add(types.InlineKeyboardButton(
            "➕ Add New Ad", 
            callback_data=f"add_ad_{chat_id}"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "◀️ Back", 
            callback_data=f"chat_menu_{chat_id}"
        ))
        
        return keyboard
    
    @staticmethod
    def upgrade_menu() -> types.InlineKeyboardMarkup:
        """Build premium upgrade keyboard"""
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        
        buttons = [
            types.InlineKeyboardButton("💎 1 Month - 10 TON", callback_data="buy_30"),
            types.InlineKeyboardButton("💎 3 Months - 25 TON", callback_data="buy_90"),
            types.InlineKeyboardButton("💎 6 Months - 45 TON", callback_data="buy_180"),
            types.InlineKeyboardButton("💎 1 Year - 80 TON", callback_data="buy_365"),
            types.InlineKeyboardButton("📞 Contact Support", url="https://t.me/support"),
            types.InlineKeyboardButton("◀️ Back", callback_data="back_to_main")
        ]
        
        keyboard.add(*buttons)
        return keyboard

# ==================== Message Handlers ====================

@bot.message_handler(commands=['start'])
def start_command(message):
    """Handle /start command"""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    # Save user to database
    db.create_or_update_user(user_id, username, first_name)
    
    welcome_text = f"""
🌟 <b>Welcome to Ad Automation Bot, {first_name}!</b> 🌟

I help you automate advertisements in your Telegram groups and channels.

<b>✨ Features:</b>
• Schedule multiple ads with custom intervals
• Custom welcome messages with media
• Premium tier with unlimited chats & no developer ads
• TON Wallet payments

<b>📖 Quick Start:</b>
1. Add me to your group/channel as admin
2. Use /setup in groups or I'll auto-detect in channels
3. Configure your ads and welcome messages

<b>Your current plan:</b> {'💎 PREMIUM' if db.is_premium(user_id) else '🆓 FREE'}
<b>Active chats limit:</b> {'Unlimited' if db.is_premium(user_id) else '1'}
    """
    
    bot.send_message(
        message.chat.id,
        welcome_text,
        parse_mode='HTML',
        reply_markup=UIBuilder.main_menu(user_id)
    )

@bot.message_handler(commands=['setup'])
def setup_command(message):
    """Handle /setup command for groups"""
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    # Check if in a group
    if message.chat.type not in ['group', 'supergroup']:
        bot.reply_to(message, "❌ This command only works in groups!")
        return
    
    # Check if user has reached limit (for free users)
    user_chats = db.get_user_chats(user_id)
    if not db.is_premium(user_id) and len(user_chats) >= FREE_USER_LIMIT:
        bot.reply_to(
            message,
            f"❌ You've reached the free limit of {FREE_USER_LIMIT} chat.\n"
            f"Upgrade to premium for unlimited chats! 💎",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("💎 Upgrade Now", callback_data="upgrade")
            )
        )
        return
    
    # Save the chat
    db.add_linked_chat(
        chat_id,
        'group',
        message.chat.title,
        user_id
    )
    
    success_text = f"""
✅ <b>Group successfully linked!</b>

<b>Group:</b> {message.chat.title}
<b>ID:</b> <code>{chat_id}</code>

Now you can:
• Set welcome messages with /welcome
• Add ads via the menu below
• Configure intervals and media

What would you like to do?
    """
    
    bot.reply_to(
        message,
        success_text,
        parse_mode='HTML',
        reply_markup=UIBuilder.chat_management_menu(chat_id)
    )

@bot.channel_post_handler(func=lambda message: True)
def handle_channel_add(message):
    """Handle bot being added to channel"""
    chat_id = message.chat.id
    
    # Try to find who added the bot
    try:
        admins = bot.get_chat_administrators(chat_id)
        for admin in admins:
            if admin.status == 'creator':
                user_id = admin.user.id
                break
        else:
            # If no creator found, try to get from message
            user_id = message.from_user.id if message.from_user else None
    except:
        user_id = message.from_user.id if message.from_user else None
    
    if not user_id:
        logger.warning(f"Could not determine who added bot to channel {chat_id}")
        return
    
    # Check user's limit
    user_chats = db.get_user_chats(user_id)
    if not db.is_premium(user_id) and len(user_chats) >= FREE_USER_LIMIT:
        bot.send_message(
            user_id,
            f"❌ You've reached the free limit of {FREE_USER_LIMIT} chat.\n"
            f"The channel @{message.chat.username if message.chat.username else 'channel'} was not linked.\n"
            f"Upgrade to premium for unlimited chats! 💎"
        )
        return
    
    # Save the channel
    db.add_linked_chat(
        chat_id,
        'channel',
        message.chat.title,
        user_id
    )
    
    bot.send_message(
        user_id,
        f"""
✅ <b>Channel successfully linked!</b>

<b>Channel:</b> {message.chat.title}
<b>ID:</b> <code>{chat_id}</code>

Now you can manage ads from here!
        """,
        parse_mode='HTML',
        reply_markup=UIBuilder.chat_management_menu(chat_id)
    )

@bot.message_handler(commands=['welcome'])
def welcome_command(message):
    """Handle /welcome command"""
    chat_id = message.chat.id
    
    # Check if this chat is linked
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM linked_chats WHERE chat_id = ?', (chat_id,))
        chat = cursor.fetchone()
    
    if not chat:
        bot.reply_to(message, "❌ This chat is not linked! Use /setup first.")
        return
    
    welcome_enabled = bool(chat['welcome_enabled'])
    
    bot.reply_to(
        message,
        f"""
🖋 <b>Welcome Message Settings</b>

Current status: {'✅ Enabled' if welcome_enabled else '❌ Disabled'}

Configure how new members are welcomed!
        """,
        parse_mode='HTML',
        reply_markup=UIBuilder.welcome_settings_menu(chat_id, welcome_enabled)
    )

@bot.message_handler(func=lambda message: True, content_types=['new_chat_members'])
def handle_new_members(message):
    """Handle new members joining groups"""
    chat_id = message.chat.id
    
    # Check if welcome is enabled for this chat
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT welcome_enabled, welcome_message, welcome_media, welcome_media_type 
            FROM linked_chats WHERE chat_id = ?
        ''', (chat_id,))
        chat = cursor.fetchone()
    
    if not chat or not chat['welcome_enabled']:
        return
    
    # Get new members (excluding the bot itself)
    new_members = [user for user in message.new_chat_members if user.id != bot.get_me().id]
    
    for user in new_members:
        welcome_text = chat['welcome_message'] or f"Welcome {user.first_name} to the group! 🎉"
        # Replace placeholders
        welcome_text = welcome_text.replace("{name}", user.first_name)
        welcome_text = welcome_text.replace("{username}", f"@{user.username}" if user.username else user.first_name)
        
        try:
            if chat['welcome_media_type'] == 'photo':
                bot.send_photo(
                    chat_id,
                    chat['welcome_media'],
                    caption=welcome_text,
                    parse_mode='HTML'
                )
            elif chat['welcome_media_type'] == 'video':
                bot.send_video(
                    chat_id,
                    chat['welcome_media'],
                    caption=welcome_text,
                    parse_mode='HTML'
                )
            else:
                bot.send_message(chat_id, welcome_text, parse_mode='HTML')
        except Exception as e:
            logger.error(f"Failed to send welcome message: {e}")

# ==================== Callback Handlers ====================

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    """Handle all inline keyboard callbacks"""
    user_id = call.from_user.id
    data = call.data
    
    try:
        if data == "my_chats":
            show_my_chats(call)
        
        elif data == "add_chat":
            bot.answer_callback_query(call.id, "📝 Add me to a group and use /setup, or add me to a channel as admin!")
            bot.edit_message_text(
                "📝 <b>How to add a chat:</b>\n\n"
                "• <b>For Groups:</b> Add me to the group, make me admin, then use /setup\n"
                "• <b>For Channels:</b> Add me as an admin and I'll auto-detect!\n\n"
                "After adding, you can manage it here.",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='HTML',
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("◀️ Back", callback_data="back_to_main")
                )
            )
        
        elif data == "manage_ads":
            show_manage_ads(call)
        
        elif data == "settings":
            show_settings(call)
        
        elif data == "profile":
            show_profile(call)
        
        elif data == "upgrade":
            show_upgrade(call)
        
        elif data == "premium_info":
            show_premium_info(call)
        
        elif data == "back_to_main":
            go_back_to_main(call)
        
        elif data == "back_to_chats":
            show_my_chats(call)
        
        elif data.startswith("chat_menu_"):
            chat_id = int(data.split("_")[2])
            show_chat_menu(call, chat_id)
        
        elif data.startswith("welcome_"):
            chat_id = int(data.split("_")[1])
            show_welcome_menu(call, chat_id)
        
        elif data.startswith("toggle_welcome_"):
            chat_id = int(data.split("_")[2])
            toggle_welcome(call, chat_id)
        
        elif data.startswith("edit_welcome_text_"):
            chat_id = int(data.split("_")[3])
            request_welcome_text(call, chat_id)
        
        elif data.startswith("welcome_media_"):
            chat_id = int(data.split("_")[2])
            request_welcome_media(call, chat_id)
        
        elif data.startswith("add_ad_"):
            chat_id = int(data.split("_")[2])
            request_ad_content(call, chat_id)
        
        elif data.startswith("view_ads_"):
            chat_id = int(data.split("_")[2])
            show_ads(call, chat_id)
        
        elif data.startswith("edit_ad_"):
            ad_id = int(data.split("_")[2])
            show_ad_options(call, ad_id)
        
        elif data.startswith("delete_ad_"):
            ad_id = int(data.split("_")[2])
            delete_ad(call, ad_id)
        
        elif data.startswith("remove_"):
            chat_id = int(data.split("_")[1])
            confirm_remove_chat(call, chat_id)
        
        elif data.startswith("confirm_remove_"):
            chat_id = int(data.split("_")[2])
            remove_chat(call, chat_id)
        
        elif data.startswith("buy_"):
            days = int(data.split("_")[1])
            process_payment(call, days)
        
        elif data == "noop":
            bot.answer_callback_query(call.id, "ℹ️ No action available")
        
        else:
            bot.answer_callback_query(call.id, "❓ Unknown command")
    
    except Exception as e:
        logger.error(f"Callback error: {e}")
        bot.answer_callback_query(call.id, "❌ An error occurred")

def show_my_chats(call):
    """Show user's linked chats"""
    user_id = call.from_user.id
    chats = db.get_user_chats(user_id)
    
    if not chats:
        bot.edit_message_text(
            "📭 <b>No chats linked yet!</b>\n\n"
            "Add me to a group and use /setup, or add me to a channel as admin.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='HTML',
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("➕ Add Chat", callback_data="add_chat"),
                types.InlineKeyboardButton("◀️ Back", callback_data="back_to_main")
            )
        )
        return
    
    text = "📊 <b>Your Linked Chats</b>\n\n"
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    
    for chat in chats:
        icon = "👥" if chat.chat_type == 'group' else "📢"
        text += f"{icon} <b>{chat.chat_title}</b>\n"
        text += f"  • Type: {chat.chat_type}\n"
        text += f"  • Welcome: {'✅' if chat.welcome_enabled else '❌'}\n\n"
        
        keyboard.add(types.InlineKeyboardButton(
            f"⚙️ Manage {chat.chat_title[:20]}",
            callback_data=f"chat_menu_{chat.chat_id}"
        ))
    
    keyboard.add(types.InlineKeyboardButton("➕ Add New Chat", callback_data="add_chat"))
    keyboard.add(types.InlineKeyboardButton("◀️ Back", callback_data="back_to_main"))
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode='HTML',
        reply_markup=keyboard
    )

def show_chat_menu(call, chat_id):
    """Show management menu for a specific chat"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM linked_chats WHERE chat_id = ?', (chat_id,))
        chat = cursor.fetchone()
    
    if not chat:
        bot.answer_callback_query(call.id, "❌ Chat not found!")
        return
    
    text = f"""
⚙️ <b>Managing: {chat['chat_title']}</b>

<b>Type:</b> {'👥 Group' if chat['chat_type'] == 'group' else '📢 Channel'}
<b>Welcome Message:</b> {'✅ Enabled' if chat['welcome_enabled'] else '❌ Disabled'}
<b>Active Ads:</b> {len(db.get_chat_ads(chat_id))}

Select an option below:
    """
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode='HTML',
        reply_markup=UIBuilder.chat_management_menu(chat_id)
    )

def show_welcome_menu(call, chat_id):
    """Show welcome message settings"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT welcome_enabled FROM linked_chats WHERE chat_id = ?', (chat_id,))
        chat = cursor.fetchone()
    
    if not chat:
        bot.answer_callback_query(call.id, "❌ Chat not found!")
        return
    
    bot.edit_message_text(
        f"""
🖋 <b>Welcome Message Settings</b>

Configure how new members are welcomed.

Current status: {'✅ Enabled' if chat['welcome_enabled'] else '❌ Disabled'}

You can set a text message and optionally add a photo or video.
Use {{name}} to mention the new member's name.
        """,
        call.message.chat.id,
        call.message.message_id,
        parse_mode='HTML',
        reply_markup=UIBuilder.welcome_settings_menu(chat_id, chat['welcome_enabled'])
    )

def toggle_welcome(call, chat_id):
    """Toggle welcome message on/off"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT welcome_enabled FROM linked_chats WHERE chat_id = ?', (chat_id,))
        chat = cursor.fetchone()
        
        if chat:
            new_status = not chat['welcome_enabled']
            db.update_welcome_settings(chat_id, new_status)
            bot.answer_callback_query(call.id, f"Welcome {'enabled' if new_status else 'disabled'}!")
    
    show_welcome_menu(call, chat_id)

def request_welcome_text(call, chat_id):
    """Request welcome text from user"""
    bot.answer_callback_query(call.id, "✏️ Send me the new welcome text!")
    
    msg = bot.send_message(
        call.message.chat.id,
        "📝 <b>Send your welcome message</b>\n\n"
        "You can use <code>{{name}}</code> to mention the new member.\n"
        "Example: <i>Welcome {{name}} to our group! 🎉</i>\n\n"
        "Send /cancel to abort.",
        parse_mode='HTML'
    )
    
    bot.register_next_step_handler(msg, process_welcome_text, chat_id)

def process_welcome_text(message, chat_id):
    """Process received welcome text"""
    if message.text == '/cancel':
        bot.reply_to(message, "❌ Welcome text setup cancelled.")
        return
    
    db.update_welcome_settings(chat_id, True, message.text)
    
    bot.reply_to(
        message,
        "✅ Welcome message saved and enabled!",
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("◀️ Back to Welcome", callback_data=f"welcome_{chat_id}")
        )
    )

def request_welcome_media(call, chat_id):
    """Request welcome media from user"""
    bot.answer_callback_query(call.id, "🖼 Send me a photo or video!")
    
    msg = bot.send_message(
        call.message.chat.id,
        "🖼 <b>Send welcome media</b>\n\n"
        "Send a photo or video to use as welcome media.\n"
        "The current text will be used as caption.\n\n"
        "Send /cancel to abort.",
        parse_mode='HTML'
    )
    
    bot.register_next_step_handler(msg, process_welcome_media, chat_id)

def process_welcome_media(message, chat_id):
    """Process received welcome media"""
    if message.text == '/cancel':
        bot.reply_to(message, "❌ Welcome media setup cancelled.")
        return
    
    media_type = None
    media_file_id = None
    
    if message.photo:
        media_type = 'photo'
        media_file_id = message.photo[-1].file_id
    elif message.video:
        media_type = 'video'
        media_file_id = message.video.file_id
    else:
        bot.reply_to(message, "❌ Please send a photo or video!")
        return
    
    db.update_welcome_settings(chat_id, True, None, media_file_id, media_type)
    
    bot.reply_to(
        message,
        "✅ Welcome media saved and enabled!",
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("◀️ Back to Welcome", callback_data=f"welcome_{chat_id}")
        )
    )

def request_ad_content(call, chat_id):
    """Request ad content from user"""
    bot.answer_callback_query(call.id, "📢 Send me your ad!")
    
    msg = bot.send_message(
        call.message.chat.id,
        "📢 <b>Create New Ad</b>\n\n"
        "Send me your ad message. You can also add a photo or video.\n"
        "After sending, I'll ask for the posting interval.\n\n"
        "Send /cancel to abort.",
        parse_mode='HTML'
    )
    
    bot.register_next_step_handler(msg, process_ad_content, chat_id)

def process_ad_content(message, chat_id):
    """Process received ad content"""
    if message.text == '/cancel':
        bot.reply_to(message, "❌ Ad creation cancelled.")
        return
    
    # Store ad data temporarily
    ad_data = {
        'content': message.text or message.caption,
        'media_type': None,
        'media_file_id': None
    }
    
    if message.photo:
        ad_data['media_type'] = 'photo'
        ad_data['media_file_id'] = message.photo[-1].file_id
    elif message.video:
        ad_data['media_type'] = 'video'
        ad_data['media_file_id'] = message.video.file_id
    
    # Ask for interval
    msg = bot.reply_to(
        message,
        "⏱ <b>Set posting interval</b>\n\n"
        "How often should this ad be posted? (in minutes)\n"
        "Send a number (e.g., 30 for every 30 minutes)\n\n"
        "Minimum: 5 minutes",
        parse_mode='HTML'
    )
    
    bot.register_next_step_handler(msg, process_ad_interval, chat_id, ad_data)

def process_ad_interval(message, chat_id, ad_data):
    """Process ad interval and save ad"""
    if message.text == '/cancel':
        bot.reply_to(message, "❌ Ad creation cancelled.")
        return
    
    try:
        interval = int(message.text)
        if interval < 5:
            raise ValueError("Interval too short")
        
        # Save ad to database
        ad_id = db.add_ad(
            chat_id,
            ad_data['content'],
            interval,
            ad_data['media_type'],
            ad_data['media_file_id']
        )
        
        bot.reply_to(
            message,
            f"✅ Ad created successfully!\n\n"
            f"<b>Ad ID:</b> {ad_id}\n"
            f"<b>Interval:</b> Every {interval} minutes\n"
            f"It will start posting automatically.",
            parse_mode='HTML',
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("📋 View All Ads", callback_data=f"view_ads_{chat_id}")
            )
        )
        
    except ValueError:
        msg = bot.reply_to(
            message,
            "❌ Invalid interval! Please send a number (minimum 5 minutes):"
        )
        bot.register_next_step_handler(msg, process_ad_interval, chat_id, ad_data)

def show_ads(call, chat_id):
    """Show all ads for a chat"""
    ads = db.get_chat_ads(chat_id)
    
    if not ads:
        bot.edit_message_text(
            "📭 <b>No ads yet!</b>\n\n"
            "Create your first ad to start automating!",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='HTML',
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("➕ Add First Ad", callback_data=f"add_ad_{chat_id}"),
                types.InlineKeyboardButton("◀️ Back", callback_data=f"chat_menu_{chat_id}")
            )
        )
        return
    
    text = f"📋 <b>Ads for Chat</b>\n\n"
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    
    for i, ad in enumerate(ads, 1):
        text += f"{i}. <b>Ad #{ad.id}</b>\n"
        text += f"   • Interval: Every {ad.interval_minutes} min\n"
        text += f"   • Content: {ad.content[:50]}...\n\n"
        
        keyboard.add(types.InlineKeyboardButton(
            f"✏️ Edit Ad #{ad.id}",
            callback_data=f"edit_ad_{ad.id}"
        ))
    
    keyboard.add(types.InlineKeyboardButton("➕ Add New Ad", callback_data=f"add_ad_{chat_id}"))
    keyboard.add(types.InlineKeyboardButton("◀️ Back", callback_data=f"chat_menu_{chat_id}"))
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode='HTML',
        reply_markup=keyboard
    )

def show_ad_options(call, ad_id):
    """Show options for a specific ad"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT a.*, l.chat_id, l.chat_title 
            FROM ad_messages a
            JOIN linked_chats l ON a.chat_id = l.chat_id
            WHERE a.id = ?
        ''', (ad_id,))
        ad = cursor.fetchone()
    
    if not ad:
        bot.answer_callback_query(call.id, "❌ Ad not found!")
        return
    
    text = f"""
📌 <b>Ad #{ad_id}</b>

<b>Chat:</b> {ad['chat_title']}
<b>Content:</b> {ad['content'][:100]}...
<b>Interval:</b> Every {ad['interval_minutes']} minutes
<b>Media:</b> {'✅' if ad['media_type'] else '❌'}
<b>Status:</b> {'🟢 Active' if ad['is_active'] else '🔴 Inactive'}
    """
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("🔄 Change Interval", callback_data=f"change_interval_{ad_id}"),
        types.InlineKeyboardButton("✏️ Edit Content", callback_data=f"edit_content_{ad_id}"),
        types.InlineKeyboardButton("🖼 Change Media", callback_data=f"change_media_{ad_id}"),
        types.InlineKeyboardButton("❌ Delete", callback_data=f"delete_ad_{ad_id}"),
        types.InlineKeyboardButton("◀️ Back", callback_data=f"view_ads_{ad['chat_id']}")
    )
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode='HTML',
        reply_markup=keyboard
    )

def delete_ad(call, ad_id):
    """Delete an ad"""
    db.delete_ad(ad_id)
    bot.answer_callback_query(call.id, "✅ Ad deleted!")
    
    # Go back to ads list
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id FROM ad_messages WHERE id = ?', (ad_id,))
        ad = cursor.fetchone()
    
    if ad:
        show_ads(call, ad['chat_id'])

def confirm_remove_chat(call, chat_id):
    """Confirm chat removal"""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("✅ Yes, Remove", callback_data=f"confirm_remove_{chat_id}"),
        types.InlineKeyboardButton("❌ No, Keep", callback_data=f"chat_menu_{chat_id}")
    )
    
    bot.edit_message_text(
        "⚠️ <b>Are you sure?</b>\n\n"
        "Removing this chat will stop all ads and delete all settings.\n"
        "This action cannot be undone!",
        call.message.chat.id,
        call.message.message_id,
        parse_mode='HTML',
        reply_markup=keyboard
    )

def remove_chat(call, chat_id):
    """Remove a chat"""
    db.remove_linked_chat(chat_id)
    bot.answer_callback_query(call.id, "✅ Chat removed!")
    show_my_chats(call)

def show_manage_ads(call):
    """Show ads management overview"""
    user_id = call.from_user.id
    chats = db.get_user_chats(user_id)
    
    if not chats:
        bot.answer_callback_query(call.id, "📭 No chats linked yet!")
        return
    
    text = "📢 <b>Manage Ads</b>\n\nSelect a chat to manage its ads:"
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    
    for chat in chats:
        ad_count = len(db.get_chat_ads(chat.chat_id))
        keyboard.add(types.InlineKeyboardButton(
            f"{'👥' if chat.chat_type == 'group' else '📢'} {chat.chat_title} ({ad_count} ads)",
            callback_data=f"view_ads_{chat.chat_id}"
        ))
    
    keyboard.add(types.InlineKeyboardButton("◀️ Back", callback_data="back_to_main"))
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode='HTML',
        reply_markup=keyboard
    )

def show_settings(call):
    """Show settings menu"""
    text = """
⚙️ <b>Settings</b>

Configure your bot preferences:

• <b>Notification Settings</b> - Coming soon
• <b>Language</b> - English only for now
• <b>Time Zone</b> - Coming soon
    """
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("🔔 Notifications", callback_data="noop"),
        types.InlineKeyboardButton("🌐 Language", callback_data="noop"),
        types.InlineKeyboardButton("🕐 Time Zone", callback_data="noop"),
        types.InlineKeyboardButton("◀️ Back", callback_data="back_to_main")
    )
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode='HTML',
        reply_markup=keyboard
    )

def show_profile(call):
    """Show user profile"""
    user_id = call.from_user.id
    user = db.get_user(user_id)
    chats = db.get_user_chats(user_id)
    
    if not user:
        bot.answer_callback_query(call.id, "❌ User not found!")
        return
    
    # Check premium status
    is_premium = db.is_premium(user_id)
    
    # Calculate time left if premium
    time_left = ""
    if is_premium and user.premium_until:
        delta = user.premium_until - datetime.now()
        days = delta.days
        hours = delta.seconds // 3600
        time_left = f"\n⏳ <b>Time Left:</b> {days}d {hours}h"
    
    text = f"""
👤 <b>Your Profile</b>

<b>User ID:</b> <code>{user_id}</code>
<b>Username:</b> @{user.username if user.username else 'N/A'}
<b>Plan:</b> {'💎 PREMIUM' if is_premium else '🆓 FREE'}{time_left}
<b>Joined:</b> {user.joined_at.strftime('%Y-%m-%d')}

<b>📊 Statistics:</b>
• Linked Chats: {len(chats)}/{'∞' if is_premium else FREE_USER_LIMIT}
• Total Ads: {sum(len(db.get_chat_ads(chat.chat_id)) for chat in chats)}

<b>💳 Payment Info:</b>
TON Wallet: <code>{TON_WALLET}</code>
    """
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    if not is_premium:
        keyboard.add(types.InlineKeyboardButton("💎 Upgrade to Premium", callback_data="upgrade"))
    keyboard.add(types.InlineKeyboardButton("◀️ Back", callback_data="back_to_main"))
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode='HTML',
        reply_markup=keyboard
    )

def show_upgrade(call):
    """Show upgrade options"""
    text = f"""
💎 <b>Upgrade to Premium</b>

<b>✨ Premium Benefits:</b>
• 📊 Unlimited chats & channels
• 🚫 No developer ads
• ⚡ Priority ad posting
• 🎨 Custom welcome messages with media
• 📈 Advanced analytics (coming soon)

<b>💰 Pricing:</b>
• 1 Month - 10 TON
• 3 Months - 25 TON (save 17%)
• 6 Months - 45 TON (save 25%)
• 1 Year - 80 TON (save 33%)

<b>💳 Payment:</b>
Send exact TON amount to:
<code>{TON_WALLET}</code>

After payment, contact @support with transaction ID.
    """
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode='HTML',
        reply_markup=UIBuilder.upgrade_menu()
    )

def show_premium_info(call):
    """Show premium info for premium users"""
    user_id = call.from_user.id
    user = db.get_user(user_id)
    
    if user.premium_until:
        delta = user.premium_until - datetime.now()
        days = delta.days
        hours = delta.seconds // 3600
        
        text = f"""
✨ <b>Premium Status</b>

✅ You are a premium member!

⏳ <b>Valid until:</b> {user.premium_until.strftime('%Y-%m-%d %H:%M')}
⏰ <b>Time left:</b> {days} days, {hours} hours

<b>Active benefits:</b>
• Unlimited chats ✓
• No developer ads ✓
• Priority posting ✓
• Priority support ✓
        """
    else:
        text = """
✨ <b>Premium Status</b>

⚠️ Your premium status is active but no expiry date is set.
Please contact support.
        """
    
    keyboard = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("◀️ Back", callback_data="back_to_main")
    )
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode='HTML',
        reply_markup=keyboard
    )

def process_payment(call, days):
    """Process payment request"""
    prices = {30: 10, 90: 25, 180: 45, 365: 80}
    amount = prices.get(days, 10)
    
    text = f"""
💳 <b>Payment Request</b>

<b>Plan:</b> {days} days Premium
<b>Amount:</b> {amount} TON

<b>Send exactly:</b> <code>{amount}</code> TON to:
<code>{TON_WALLET}</code>

<b>Important:</b>
1. Send the exact amount
2. Keep your transaction ID
3. Contact @support with:
   • Transaction ID
   • Your User ID: <code>{call.from_user.id}</code>

Your premium will be activated within 24 hours.
    """
    
    keyboard = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("✅ I've Sent Payment", url="https://t.me/support"),
        types.InlineKeyboardButton("◀️ Back", callback_data="upgrade")
    )
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode='HTML',
        reply_markup=keyboard
    )

def go_back_to_main(call):
    """Go back to main menu"""
    user_id = call.from_user.id
    user = db.get_user(user_id)
    
    text = f"""
🌟 <b>Welcome back, {user.first_name if user else 'User'}!</b> 🌟

<b>Your current plan:</b> {'💎 PREMIUM' if db.is_premium(user_id) else '🆓 FREE'}
<b>Active chats:</b> {len(db.get_user_chats(user_id))}/{('∞' if db.is_premium(user_id) else FREE_USER_LIMIT)}

What would you like to do today?
    """
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode='HTML',
        reply_markup=UIBuilder.main_menu(user_id)
    )

# ==================== Admin Commands ====================

@bot.message_handler(commands=['admin'])
def admin_command(message):
    """Admin panel"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    text = """
👑 <b>Admin Panel</b>

Available commands:
• <code>/activate user_id days</code> - Activate premium
• <code>/deactivate user_id</code> - Deactivate premium
• <code>/stats</code> - View bot statistics
• <code>/broadcast message</code> - Send broadcast
• <code>/users</code> - List all users
    """
    
    bot.reply_to(message, text, parse_mode='HTML')

@bot.message_handler(commands=['activate'])
def activate_premium(message):
    """Activate premium for a user"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 3:
            bot.reply_to(message, "Usage: /activate user_id days")
            return
        
        user_id = int(parts[1])
        days = int(parts[2])
        
        db.update_user_tier(user_id, UserTier.PREMIUM, days)
        
        # Notify user
        try:
            bot.send_message(
                user_id,
                f"🎉 <b>Congratulations!</b>\n\n"
                f"You are now a premium member for {days} days!\n"
                f"Enjoy unlimited chats and ad-free experience!",
                parse_mode='HTML'
            )
        except:
            pass
        
        bot.reply_to(message, f"✅ Premium activated for user {user_id} for {days} days!")
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

@bot.message_handler(commands=['deactivate'])
def deactivate_premium(message):
    """Deactivate premium for a user"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            bot.reply_to(message, "Usage: /deactivate user_id")
            return
        
        user_id = int(parts[1])
        
        db.update_user_tier(user_id, UserTier.FREE)
        
        # Notify user
        try:
            bot.send_message(
                user_id,
                f"ℹ️ Your premium membership has ended.\n"
                f"Thank you for your support! You can renew anytime.",
                parse_mode='HTML'
            )
        except:
            pass
        
        bot.reply_to(message, f"✅ Premium deactivated for user {user_id}")
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

@bot.message_handler(commands=['stats'])
def bot_stats(message):
    """Show bot statistics"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Total users
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        
        # Premium users
        cursor.execute('SELECT COUNT(*) FROM users WHERE tier = "premium"')
        premium_users = cursor.fetchone()[0]
        
        # Total chats
        cursor.execute('SELECT COUNT(*) FROM linked_chats WHERE is_active = 1')
        total_chats = cursor.fetchone()[0]
        
        # Total ads
        cursor.execute('SELECT COUNT(*) FROM ad_messages WHERE is_active = 1')
        total_ads = cursor.fetchone()[0]
        
        # Today's posts
        cursor.execute('''
            SELECT COUNT(*) FROM ad_logs 
            WHERE date(posted_at) = date('now')
        ''')
        today_posts = cursor.fetchone()[0]
    
    text = f"""
📊 <b>Bot Statistics</b>

<b>Users:</b>
• Total: {total_users}
• Premium: {premium_users}
• Free: {total_users - premium_users}

<b>Content:</b>
• Active Chats: {total_chats}
• Active Ads: {total_ads}
• Posts Today: {today_posts}

<b>System:</b>
• Bot Version: 1.0.0
• Uptime: {time.time() - start_time:.0f} seconds
    """
    
    bot.reply_to(message, text, parse_mode='HTML')

@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    """Broadcast message to all users"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    broadcast_text = message.text.replace('/broadcast', '', 1).strip()
    if not broadcast_text:
        bot.reply_to(message, "Usage: /broadcast Your message here")
        return
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users')
        users = cursor.fetchall()
    
    sent = 0
    failed = 0
    
    for user in users:
        try:
            bot.send_message(
                user[0],
                f"📢 <b>Broadcast Message</b>\n\n{broadcast_text}",
                parse_mode='HTML'
            )
            sent += 1
            time.sleep(0.05)  # Avoid flood limits
        except:
            failed += 1
    
    bot.reply_to(
        message,
        f"✅ Broadcast sent!\n"
        f"Sent: {sent}\n"
        f"Failed: {failed}"
    )

@bot.message_handler(commands=['users'])
def list_users(message):
    """List all users"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT user_id, username, first_name, tier, premium_until 
            FROM users ORDER BY joined_at DESC LIMIT 50
        ''')
        users = cursor.fetchall()
    
    text = "👥 <b>Recent Users</b>\n\n"
    
    for user in users:
        user_id, username, first_name, tier, premium_until = user
        username = f"@{username}" if username else "No username"
        premium = f" until {premium_until[:10]}" if premium_until else ""
        
        text += f"• <b>{first_name}</b> ({username})\n"
        text += f"  ID: <code>{user_id}</code> | {tier.upper()}{premium}\n\n"
        
        if len(text) > 3500:  # Telegram message limit
            text += "... and more"
            break
    
    bot.reply_to(message, text, parse_mode='HTML')

# ==================== Error Handlers ====================

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Handle all other messages"""
    # Ignore commands
    if message.text and message.text.startswith('/'):
        return
    
    # Check if this is a response to a setup request
    if message.chat.type in ['group', 'supergroup']:
        # Check if this chat is linked
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM linked_chats WHERE chat_id = ?', (message.chat.id,))
            if not cursor.fetchone():
                bot.reply_to(
                    message,
                    "ℹ️ This group is not set up for ads.\n"
                    "Use /setup to begin!"
                )

# ==================== Main Bot Initialization ====================

def check_premium_expiry():
    """Background task to check for expired premium users"""
    while True:
        try:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT user_id FROM users 
                    WHERE tier = 'premium' 
                    AND premium_until < datetime('now')
                ''')
                expired = cursor.fetchall()
                
                for user in expired:
                    db.update_user_tier(user[0], UserTier.FREE)
                    try:
                        bot.send_message(
                            user[0],
                            "ℹ️ Your premium membership has expired.\n"
                            "Renew anytime to continue enjoying premium benefits!"
                        )
                    except:
                        pass
                        
        except Exception as e:
            logger.error(f"Premium check error: {e}")
        
        time.sleep(PREMIUM_CHECK_INTERVAL)

def cleanup_inactive_chats():
    """Background task to clean up inactive chats"""
    while True:
        try:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Check chats where bot might have been removed
                cursor.execute('SELECT chat_id, chat_type FROM linked_chats WHERE is_active = 1')
                chats = cursor.fetchall()
                
                for chat in chats:
                    try:
                        if chat['chat_type'] == 'group':
                            bot.get_chat(chat['chat_id'])
                        else:
                            # For channels, try to send a test message
                            bot.send_chat_action(chat['chat_id'], 'typing')
                    except ApiTelegramException as e:
                        if e.error_code in [403, 400]:  # Bot was kicked or chat not found
                            logger.info(f"Removing inactive chat {chat['chat_id']}")
                            db.remove_linked_chat(chat['chat_id'])
                    except Exception as e:
                        logger.error(f"Error checking chat {chat['chat_id']}: {e}")
                        
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        
        time.sleep(3600)  # Check every hour

# Start time for uptime tracking
start_time = time.time()

if __name__ == '__main__':
    logger.info("Starting Ad Automation Bot...")
    
    # Check required environment variables
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        exit(1)
    
    # Start ad engine
    ad_engine = AdEngine(bot)
    ad_engine.start()
    
    # Start background tasks
    premium_thread = threading.Thread(target=check_premium_expiry, daemon=True)
    premium_thread.start()
    
    cleanup_thread = threading.Thread(target=cleanup_inactive_chats, daemon=True)
    cleanup_thread.start()
    
    logger.info("Bot is running...")
    
    # Start bot
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
    finally:
        ad_engine.stop()