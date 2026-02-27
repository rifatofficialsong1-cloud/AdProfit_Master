import telebot
from telebot import types
import threading
import time
import sqlite3
import os
from datetime import datetime, timedelta

# --- CONFIGURATION (Safe Mode) ---
# গিটহাবে আপলোড করার জন্য টোকেন ও ওয়ালেট সরিয়ে Environment Variable ব্যবহার করা হয়েছে
API_TOKEN = os.environ.get('BOT_TOKEN')
TON_WALLET = os.environ.get('TON_WALLET')
SUPPORT_ADMIN = '@mdrifat021u'
CHANNEL_LINK = 'https://t.me/AdProfit_Master_News'
ADSTERRA_LINK = 'https://www.effectivegatecpm.com/djcfysfxz?key=6ef18d633548c970dc5f80d696319336'

bot = telebot.TeleBot(API_TOKEN)

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('adprofit.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (user_id INTEGER PRIMARY KEY, premium_until TEXT, ad_msg TEXT, interval INTEGER)''')
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect('adprofit.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    conn.close()
    return user

def update_user(user_id, premium_days=None, ad_msg=None, interval=None):
    conn = sqlite3.connect('adprofit.db')
    c = conn.cursor()
    if not get_user(user_id):
        c.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
    
    if premium_days is not None:
        expiry = (datetime.now() + timedelta(days=premium_days)).strftime('%Y-%m-%d %H:%M:%S')
        c.execute("UPDATE users SET premium_until=? WHERE user_id=?", (expiry, user_id))
    if ad_msg:
        c.execute("UPDATE users SET ad_msg=? WHERE user_id=?", (ad_msg, user_id))
    if interval:
        c.execute("UPDATE users SET interval=? WHERE user_id=?", (interval, user_id))
    conn.commit()
    conn.close()

def is_premium(user_id):
    user = get_user(user_id)
    if user and user[1]:
        expiry = datetime.strptime(user[1], '%Y-%m-%d %H:%M:%S')
        if expiry > datetime.now():
            return True, expiry
    return False, None

# --- UI HANDLERS ---
@bot.message_handler(commands=['start'])
def welcome(message):
    update_user(message.chat.id)
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📝 Set Message", callback_data='set_msg'),
        types.InlineKeyboardButton("⏰ Set Time", callback_data='set_time'),
        types.InlineKeyboardButton("🚀 Start Ads", callback_data='start_ads'),
        types.InlineKeyboardButton("💎 Premium", callback_data='premium'),
        types.InlineKeyboardButton("🛠 Support", callback_data='support')
    )
    bot.send_message(message.chat.id, "✨ *Welcome to AdProfit Master*\nAutomate your group ads and earn more!", parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    uid = call.message.chat.id
    if call.data == "premium":
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("7 Days - 4 TON", callback_data="p_4"),
            types.InlineKeyboardButton("15 Days - 9 TON", callback_data="p_9"),
            types.InlineKeyboardButton("30 Days - 14 TON", callback_data="p_14")
        )
        bot.edit_message_text("💎 *Premium Plans*\nNo developer ads & priority support.", uid, call.message.message_id, reply_markup=markup)
    
    elif call.data.startswith("p_"):
        amt = call.data.split("_")[1]
        bot.send_message(uid, f"💳 Send `{amt} TON` to:\n`{TON_WALLET}`\n\nSend proof to {SUPPORT_ADMIN} with ID: `{uid}`", parse_mode='Markdown')

    elif call.data == "support":
        premium, expiry = is_premium(uid)
        status = f"✅ Premium until {expiry.date()}" if premium else "❌ Free Plan"
        bot.send_message(uid, f"🛠 *Support Center*\nStatus: {status}\nAdmin: {SUPPORT_ADMIN}", parse_mode='Markdown')

    elif call.data == "set_msg":
        m = bot.send_message(uid, "📩 Send your Ad text:")
        bot.register_next_step_handler(m, lambda msg: [update_user(uid, ad_msg=msg.text), bot.reply_to(msg, "✅ Saved!")])

    elif call.data == "set_time":
        m = bot.send_message(uid, "🕒 Enter interval (minutes):")
        bot.register_next_step_handler(m, save_time)

    elif call.data == "start_ads":
        user = get_user(uid)
        if user and user[2] and user[3]:
            bot.send_message(uid, "🚀 *Automation Active!*")
            threading.Thread(target=ad_engine, args=(uid,)).start()
        else:
            bot.send_message(uid, "❌ Set Message & Time first!")

def save_time(message):
    try:
        t = int(message.text) * 60
        update_user(message.chat.id, interval=t)
        bot.reply_to(message, "✅ Interval Saved!")
    except:
        bot.reply_to(message, "❌ Enter a number.")

def ad_engine(uid):
    while True:
        user = get_user(uid)
        if not user or not user[2]: break
        time.sleep(user[3] if user[3] else 600)
        bot.send_message(uid, f"📢 *SPONSORED*\n\n{user[2]}", parse_mode='Markdown')
        premium, _ = is_premium(uid)
        if not premium:
            time.sleep(30)
            btn = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🎁 Claim Bonus", url=ADSTERRA_LINK))
            bot.send_message(uid, "🌟 *Special Offer!*", reply_markup=btn)

@bot.message_handler(commands=['activate'])
def manual_activate(message):
    if f"@{message.from_user.username}" == SUPPORT_ADMIN:
        try:
            _, target, days = message.text.split()
            update_user(int(target), premium_days=int(days))
            bot.reply_to(message, f"✅ Activated {target} for {days} days.")
        except:
            bot.reply_to(message, "Use: /activate ID DAYS")

if __name__ == "__main__":
    init_db()
    print("Bot starting...")
    bot.polling(none_stop=True)
