import telebot
from telebot import types
import threading
import time
import sqlite3
import os
from datetime import datetime, timedelta

# --- CONFIGURATION ---
API_TOKEN = os.environ.get('BOT_TOKEN')
TON_WALLET = os.environ.get('TON_WALLET')
SUPPORT_ADMIN = '@mdrifat021u'
ADSTERRA_LINK = os.environ.get('ADSTERRA_LINK')

bot = telebot.TeleBot(API_TOKEN)

# --- DATABASE SETUP (Enhanced) ---
def init_db():
    conn = sqlite3.connect('adprofit_v2.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (user_id INTEGER PRIMARY KEY, premium_until TEXT, group_limit INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS groups 
                 (group_id INTEGER PRIMARY KEY, owner_id INTEGER, ad_msg TEXT, interval INTEGER)''')
    conn.commit()
    conn.close()

# --- HELPERS ---
def get_user_info(user_id):
    conn = sqlite3.connect('adprofit_v2.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    conn.close()
    return user

def is_premium(user_id):
    user = get_user_info(user_id)
    if user and user[1]:
        expiry = datetime.strptime(user[1], '%Y-%m-%d %H:%M:%S')
        if expiry > datetime.now():
            return True, expiry
    return False, None

# --- COMMANDS ---
@bot.message_handler(commands=['start'])
def welcome_professional(message):
    if message.chat.id < 0: return # Groups ignore start
    
    # Register User
    conn = sqlite3.connect('adprofit_v2.db')
    c = conn.cursor()
    if not get_user_info(message.chat.id):
        c.execute("INSERT INTO users (user_id) VALUES (?)", (message.chat.id,))
    conn.commit()
    conn.close()

    text = (
        "🚀 *Welcome to AdProfit Master V2*\n\n"
        "The most powerful automation tool for Telegram Group Admins.\n\n"
        "📜 *Rules & Setup Guide:*\n"
        "1️⃣ Add me to your group as **Administrator**.\n"
        "2️⃣ Use `/setup` inside the group to link it to your account.\n"
        "3️⃣ Set your ad message and interval from this private chat.\n\n"
        "⚠️ *Limits:* Free users can only link **1 group**."
    )
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📋 My Groups", callback_data='my_groups'),
        types.InlineKeyboardButton("💎 Go Premium", callback_data='premium'),
        types.InlineKeyboardButton("🛠 Support", callback_data='support'),
        types.InlineKeyboardButton("⚙️ How to use", callback_data='guide')
    )
    bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=markup)

@bot.message_handler(commands=['setup'])
def setup_group(message):
    if message.chat.id > 0:
        return bot.reply_to(message, "❌ Use this command *inside* a group!")
    
    user_id = message.from_user.id
    group_id = message.chat.id
    
    # Check Admin Status
    member = bot.get_chat_member(group_id, user_id)
    if member.status not in ['administrator', 'creator']:
        return bot.reply_to(message, "❌ Only group admins can use this!")

    # Check Limits
    user = get_user_info(user_id)
    conn = sqlite3.connect('adprofit_v2.db')
    c = conn.cursor()
    c.execute("SELECT count(*) FROM groups WHERE owner_id=?", (user_id,))
    count = c.fetchone()[0]
    
    premium, _ = is_premium(user_id)
    limit = 1 if not premium else 50 # Premium gets 50 groups
    
    if count >= limit:
        return bot.send_message(user_id, f"❌ Limit reached! You can only manage {limit} group(s). Upgrade to Premium.")

    c.execute("INSERT OR REPLACE INTO groups (group_id, owner_id) VALUES (?, ?)", (group_id, user_id))
    conn.commit()
    conn.close()
    
    bot.reply_to(message, "✅ *Group Linked Successfully!*\nGo to my private chat to set up ads.")
    bot.send_message(user_id, f"🔗 Linked new group: *{message.chat.title}*\nNow set your Ad Message and Interval.")

# --- AD ENGINE UPDATE ---
def ad_cycle_v2(group_id, owner_id):
    while True:
        conn = sqlite3.connect('adprofit_v2.db')
        c = conn.cursor()
        c.execute("SELECT ad_msg, interval FROM groups WHERE group_id=?", (group_id,))
        group = c.fetchone()
        conn.close()
        
        if not group or not group[0]: break # Stop if no message
        
        time.sleep(group[1] if group[1] else 600)
        
        try:
            bot.send_message(group_id, f"📢 *SPONSORED*\n\n{group[0]}", parse_mode='Markdown')
            
            # Dev Ad for Free users
            premium, _ = is_premium(owner_id)
            if not premium:
                time.sleep(30)
                btn = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🎁 Claim Bonus", url=ADSTERRA_LINK))
                bot.send_message(group_id, "🌟 *Exclusive Offer!*", reply_markup=btn)
        except:
            break # Bot kicked or group deleted

# বটের অন্যান্য হ্যান্ডলার আগের মতোই থাকবে...

if __name__ == "__main__":
    init_db()
    print("V2 Master is LIVE! 🚀")
    bot.polling(none_stop=True)
