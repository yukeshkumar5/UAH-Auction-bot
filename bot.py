import logging
import asyncio
import pandas as pd
import random
import string
import os
import re
import sys
from threading import Thread
from flask import Flask
from duckduckgo_search import DDGS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ConversationHandler
)

# --- CONFIGURATION ---
TOKEN = "8555822248:AAE76zDM4g-e_Ti3Zwg3k4TTEico-Ewyas0"

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- STATES ---
ASK_NAME, ASK_PURSE, ASK_RTM_COUNT, ASK_FILE = range(4)

# --- DATA STORAGE ---
auctions = {}   
group_map = {}  # { group_chat_id: 'ROOM_ID' } -> Used for Locking
admin_map = {}  

# --- HELPER FUNCTIONS ---

def format_price(value):
    try:
        value = float(value)
        if value >= 100:
            cr_val = value / 100
            if cr_val.is_integer(): return f"{int(cr_val)}C"
            return f"{round(cr_val, 2)}C"
        else:
            return f"{int(value)}L"
    except: return "0L"

def parse_price(value):
    try:
        s = str(value).upper().strip().replace(" ", "")
        nums = re.findall(r"[\d\.]+", s)
        if not nums: return 0 
        num = float(nums[0])
        if "C" in s: return int(num * 100)
        elif "L" in s: return int(num)
        else: return int(num)
    except: return 0

def normalize_player_data(df):
    df.columns = [str(c).strip().lower() for c in df.columns]
    column_map = {}
    for col in df.columns:
        if 'name' in col: column_map[col] = 'Name'
        elif 'role' in col: column_map[col] = 'Role'
        elif 'country' in col: column_map[col] = 'Country'
        elif 'price' in col: column_map[col] = 'BasePrice'
    
    df.rename(columns=column_map, inplace=True)
    data = df.to_dict('records')
    cleaned = []
    for p in data:
        cleaned.append({
            'Name': p.get('Name', 'Unknown'),
            'Role': p.get('Role', 'Player'),
            'Country': p.get('Country', 'Unknown'),
            'BasePrice': parse_price(p.get('BasePrice', '20L')),
            'Status': 'Upcoming', 
            'SoldPrice': 0,
            'SoldTo': 'None'
        })
    return cleaned

def generate_code(length=5):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def get_player_image(player_name):
    try:
        results = DDGS().images(keywords=f"{player_name} cricketer", region="in-en", safesearch="on", max_results=1)
        if results: return results[0]['image']
    except: pass
    return "https://upload.wikimedia.org/wikipedia/commons/7/7a/Pollock_to_Hussey.jpg"

def get_increment(price):
    if price < 100: return 5
    elif price < 200: return 10
    elif price < 500: return 20
    else: return 50

def get_auction_by_context(update):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if chat_id in group_map: return auctions[group_map[chat_id]]
    if update.effective_chat.type == 'private':
        for auc in auctions.values():
            if user_id in auc['admins']: return auc
    return None

def get_team_by_name(auc, name):
    for code, t in auc['teams'].items():
        if t['name'].lower() == name.lower():
            return code, t
    return None, None

# ==============================================================================
# 1. SETUP (DM ONLY)
# ==============================================================================

async def start_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        await update.message.reply_text("⚠️ Setup must be done in DM!")
        return ConversationHandler.END
    
    user_id = update.effective_user.id
    if user_id in admin_map:
        await update.message.reply_text("🚫 <strong>Active Auction Exists!</strong>\nFinish it first.", parse_mode='HTML')
        return ConversationHandler.END
    
    context.user_data['setup'] = {"admins": [user_id]}
    await update.message.reply_text("🛠 <strong>Auction Setup</strong>\n\n1. Enter Auction Name:", parse_mode='HTML')
    return ASK_NAME

async def ask_purse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['setup']['name'] = update.message.text
    await update.message.reply_text("2. Enter Default Purse (e.g., <code>100 C</code>):", parse_mode='HTML')
    return ASK_PURSE

async def ask_rtm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['setup']['purse'] = parse_price(update.message.text)
    await update.message.reply_text("3. <strong>How many RTMs per team?</strong> (0 for none)", parse_mode='HTML')
    return ASK_RTM_COUNT

async def ask_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: val = int(update.message.text)
    except: val = 0
    context.user_data['setup']['rtm_limit'] = val
    await update.message.reply_text(f"✅ RTM Limit: {val}\n4. <strong>Upload CSV/Excel file.</strong>", parse_mode='HTML')
    return ASK_FILE

async def finish_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    file_obj = await update.message.document.get_file()
    file_name = update.message.document.file_name
    path = f"temp_{user_id}_{file_name}"
    
    if file_name.endswith('.csv'):
        await file_obj.download_to_drive(path)
        try: df = pd.read_csv(path)
        except: df = pd.read_csv(path, encoding='latin1')
    else:
        await file_obj.download_to_drive(path)
        df = pd.read_excel(path)
        
    if os.path.exists(path): os.remove(path)

    try:
        players = normalize_player_data(df)
        room_id = generate_code(6)
        
        auctions[room_id] = {
            "room_id": room_id,
            "admins": context.user_data['setup']['admins'],
            "name": context.user_data['setup']['name'],
            "default_purse": context.user_data['setup']['purse'],
            "rtm_limit": context.user_data['setup']['rtm_limit'],
            "players": players,
            "teams": {},
            "connected_group": None,
            "is_active": False,
            "is_paused": False,
            "current_index": -1,
            "current_bid": {"amount": 0, "holder": None},
            "skip_voters": set(),
            "rtm_state": None, # 'HIKE_DECISION', 'WAITING_HIKE', 'MATCH_DECISION'
            "rtm_data": {},    # Stores temp RTM data
            "timer_task": None,
            "last_kb": None
        }
        
        admin_map[user_id] = room_id
        
        await update.message.reply_text(
            f"✅ <strong>Ready!</strong>\n🆔 Room ID: <code>{room_id}</code>\n\n"
            f"1. Go to Group -> <code>/init {room_id}</code>\n"
            f"2. Come back to DM -> <code>/createteam</code>",
            parse_mode='HTML'
        )
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        return ConversationHandler.END

async def cancel_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# ==============================================================================
# 2. GROUP & ADMIN
# ==============================================================================

async def init_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_chat.type == 'private': return
    
    if not context.args: return await update.message.reply_text("Usage: <code>/init ROOM_ID</code>", parse_mode='HTML')
    rid = context.args[0]
    
    if rid not in auctions: return await update.message.reply_text("❌ Invalid ID.")
    auc = auctions[rid]
    
    # 🔒 LOCKING MECHANISM
    if chat_id in group_map:
        return await update.message.reply_text("🚫 This group is already connected to an active auction! End it first.")
        
    if auc['connected_group'] and auc['connected_group'] != chat_id:
        return await update.message.reply_text("❌ Code used elsewhere!")
        
    auc['connected_group'] = chat_id
    group_map[chat_id] = rid
    await update.message.reply_text(f"✅ <strong>Connected: {auc['name']}</strong>", parse_mode='HTML')

async def promote_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    
    if update.effective_user.id not in auc['admins']: return
    
    try:
        new_admin = update.message.reply_to_message.from_user
        if new_admin.id not in auc['admins']:
            auc['admins'].append(new_admin.id)
            admin_map[new_admin.id] = group_map[chat_id]
            await update.message.reply_text(f"✅ <strong>{new_admin.first_name}</strong> is now an Admin.", parse_mode='HTML')
    except:
        await update.message.reply_text("Reply to a user with /promote")

# ==============================================================================
# 3. TEAM MANAGEMENT
# ==============================================================================

async def create_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return 
    uid = update.effective_user.id
    if uid not in admin_map: return await update.message.reply_text("❌ No active auction.")
    
    rid = admin_map[uid]
    auc = auctions[rid]
    
    name = " ".join(context.args)
    if not name: return await update.message.reply_text("Usage: `/createteam TeamName`")
    
    code = generate_code(4)
    auc['teams'][code] = {
        'name': name, 'owner': None, 'owner_name': "Vacant", 
        'sec_owner': None, 'sec_owner_name': "None", 'sub_code': None,
        'purse': auc['default_purse'], 'squad': [], 'rtms_used': 0
    }
    
    await update.message.reply_text(f"✅ Team: <strong>{name}</strong>\nCode: <code>{code}</code>", parse_mode='HTML')

async def second_owner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    uid = update.effective_user.id
    if uid not in admin_map: return
    
    if not context.args: return await update.message.reply_text("Usage: `/secondowner TEAM_CODE`")
    code = context.args[0]
    auc = auctions[admin_map[uid]]
    
    if code not in auc['teams']: return await update.message.reply_text("❌ Invalid Team Code")
    
    sub_code = code + "X"
    auc['teams'][code]['sub_code'] = sub_code
    
    await update.message.reply_text(f"👥 2nd Owner Code: <code>{sub_code}</code>", parse_mode='HTML')

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    
    if not context.args: return
    input_code = context.args[0]
    uid = update.effective_user.id
    name = update.effective_user.first_name
    
    if uid in auc['admins']: return await update.message.reply_text("🚫 Admins cannot join!")

    for t in auc['teams'].values():
        if t['owner'] == uid or t['sec_owner'] == uid:
            return await update.message.reply_text("🚫 You already have a team!")

    if input_code in auc['teams']:
        if auc['teams'][input_code]['owner']: return await update.message.reply_text("⚠️ Taken!")
        auc['teams'][input_code]['owner'] = uid
        auc['teams'][input_code]['owner_name'] = name
        await update.message.reply_text(f"🎉 <strong>{name}</strong> joined <strong>{auc['teams'][input_code]['name']}</strong>!", parse_mode='HTML')
        return

    for code, t in auc['teams'].items():
        if t.get('sub_code') == input_code:
            if t['sec_owner']: return await update.message.reply_text("⚠️ 2nd Owner Taken!")
            t['sec_owner'] = uid
            t['sec_owner_name'] = name
            await update.message.reply_text(f"🎉 <strong>{name}</strong> joined as 2nd Owner!", parse_mode='HTML')
            return

    await update.message.reply_text("❌ Invalid Code.")

async def transfer_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    uid = update.effective_user.id
    if uid not in admin_map: return
    auc = auctions[admin_map[uid]]
    
    if not context.args: return await update.message.reply_text("Usage: `/transfer OLD_CODE`")
    old = context.args[0]
    
    if old not in auc['teams']: return await update.message.reply_text("❌ Invalid Code")
    
    data = auc['teams'].pop(old)
    data['owner'] = None; data['owner_name'] = "Vacant"
    data['sec_owner'] = None; data['sec_owner_name'] = "None"
    data['sub_code'] = None
    
    new_code = generate_code(4)
    auc['teams'][new_code] = data
    
    await update.message.reply_text(f"🔄 Transferred.\nNew Code: <code>{new_code}</code>", parse_mode='HTML')

async def retain_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    uid = update.effective_user.id
    if uid not in admin_map: return
    auc = auctions[admin_map[uid]]
    
    try:
        full_args = " ".join(context.args)
        code = full_args.split(" ")[0]
        rest = full_args[len(code):].strip()
        if "-" in rest:
            p_name, p_price_str = rest.rsplit("-", 1)
        else:
            p_name = " ".join(context.args[1:-1])
            p_price_str = context.args[-1]
            
        price = parse_price(p_price_str)
        p_name = p_name.strip()
        
        if code not in auc['teams']: return await update.message.reply_text("❌ Invalid Team Code")
        
        t = auc['teams'][code]
        t['purse'] -= price
        t['squad'].append({'name': p_name, 'price': price, 'type': 'retained'})
        
        original_len = len(auc['players'])
        auc['players'] = [p for p in auc['players'] if p['Name'].lower().strip() != p_name.lower().strip()]
        
        msg = f"✅ Retained <strong>{p_name}</strong> for {format_price(price)}"
        if len(auc['players']) < original_len: 
            msg += "\n🗑 <strong>Removed from Auction List!</strong>"
        
        await update.message.reply_text(msg, parse_mode='HTML')
    except:
        await update.message.reply_text("Usage: <code>/retain CODE Player Name - Price</code>", parse_mode='HTML')

async def edit_rtm_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    uid = update.effective_user.id
    if uid not in admin_map: return
    auc = auctions[admin_map[uid]]
    
    try:
        code = context.args[0]
        count = int(context.args[1])
        if code not in auc['teams']: return await update.message.reply_text("❌ Invalid Team Code")
        
        # We adjust rtms_used based on limit to set "Remaining"
        # RTM Left = Limit - Used
        # Used = Limit - Target
        new_used = auc['rtm_limit'] - count
        if new_used < 0: new_used = 0 
        
        auc['teams'][code]['rtms_used'] = new_used
        await update.message.reply_text(f"✅ <strong>{auc['teams'][code]['name']}</strong> RTMs set to {count}.", parse_mode='HTML')
    except:
        await update.message.reply_text("Usage: `/rtmedit TEAM_CODE NewCount`")

# --- STATS ---

async def team_stats_logic(update, context):
    auc = get_auction_by_context(update)
    if not auc: return await update.message.reply_text("❌ No active auction.")
    
    args = context.args
    if not args:
        msg = "📊 <strong>TEAMS SUMMARY</strong>\n\n"
        for t in auc['teams'].values():
            rtms = auc['rtm_limit'] - t['rtms_used']
            msg += f"🛡 <strong>{t['name']}</strong>: {format_price(t['purse'])} | 👥 {len(t['squad'])} | ✋ {rtms}\n"
        await update.message.reply_text(msg, parse_mode='HTML')
        return

    query = " ".join(args).lower()
    found_team = None
    for t in auc['teams'].values():
        if query in t['name'].lower(): found_team = t; break
    
    if not found_team: return await update.message.reply_text("❌ Team not found.")
    
    t = found_team
    rtms = auc['rtm_limit'] - t['rtms_used']
    owners = t['owner_name']
    if t['sec_owner_name'] != "None": owners += f" & {t['sec_owner_name']}"
    
    msg = f"🛡 <strong>{t['name']}</strong>\n"
    msg += f"💰 Balance: <strong>{format_price(t['purse'])}</strong>\n"
    msg += f"👤 Owners: {owners}\n"
    msg += f"✋ RTM Left: {rtms}\n\n"
    
    retained = [p for p in t['squad'] if p.get('type') == 'retained']
    auction_buy = [p for p in t['squad'] if p.get('type') == 'auction']
    
    if retained:
        msg += "🔹 <strong>Retained Players</strong>\n"
        for i, p in enumerate(retained):
            msg += f"{i+1}. {p['name']} - {format_price(p['price'])}\n"
        msg += "\n"
        
    if auction_buy:
        msg += "🔨 <strong>Auction Players</strong>\n"
        for i, p in enumerate(auction_buy):
            tag = " (RTM)" if p.get('rtm') else ""
            msg += f"{i+1}. {p['name']} - {format_price(p['price'])}{tag}\n"
            
    await update.message.reply_text(msg, parse_mode='HTML')

# ==============================================================================
# 4. AUCTION CORE
# ==============================================================================

async def start_auction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    if update.effective_user.id not in auc['admins']: return
    
    if auc['is_active']:
        return await update.message.reply_text("⚠️ Auction is already running!")
    
    auc['is_active'] = True
    await update.message.reply_text("📢 <strong>AUCTION STARTING!</strong>", parse_mode='HTML')
    await asyncio.sleep(2)
    await show_next_player(context, chat_id)

async def show_next_player(context, chat_id):
    auc = auctions[group_map[chat_id]]
    try:
        if auc.get('timer_task'): auc['timer_task'].cancel()
        if auc['is_paused']: return

        auc['current_index'] += 1
        auc['skip_voters'] = set()
        auc['rtm_state'] = None
        auc['rtm_data'] = {}
        
        if auc['current_index'] >= len(auc['players']):
            await context.bot.send_message(chat_id, "🏁 <strong>Auction Finished!</strong>", parse_mode='HTML')
            # Trigger end flow? No, wait for command.
            return

        p = auc['players'][auc['current_index']]
        base = p.get('BasePrice', 20)
        auc['current_bid'] = {"amount": base, "holder": None, "holder_team": None}
        
        loop = asyncio.get_event_loop()
        img_url = None
        try:
            img_url = await loop.run_in_executor(None, get_player_image, p['Name'])
        except: pass
        
        caption = (
            f"💎 <strong>LOT #{auc['current_index']+1}</strong>\n"
            f"🏏 <strong>{p['Name']}</strong>\n"
            f"🌍 {p.get('Country','')} | 🏏 {p.get('Role','')}\n\n"
            f"💰 <strong>Base Price:</strong> {format_price(base)}\n"
            f"⏳ <strong>30 Seconds Clock</strong>"
        )
        kb = [
            [InlineKeyboardButton(f"BID {format_price(base)}", callback_data="BID")],
            [
                InlineKeyboardButton("SKIP", callback_data="SKIP"),
                InlineKeyboardButton("RANDOM 🎲", callback_data="RANDOM")
            ]
        ]

        
        auc['last_kb'] = InlineKeyboardMarkup(kb)
        
        if img_url:
            msg = await context.bot.send_photo(chat_id, photo=img_url, caption=caption, reply_markup=auc['last_kb'], parse_mode='HTML')
        else:
            msg = await context.bot.send_message(chat_id, text=caption, reply_markup=auc['last_kb'], parse_mode='HTML')
            
        auc["msg_id"] = msg.message_id
        auc['timer_task'] = asyncio.create_task(auction_timer(context, chat_id))
    except Exception as e:
        await context.bot.send_message(chat_id, f"⚠️ Error: {e}\nSkipping player...")
        await show_next_player(context, chat_id)

async def auction_timer(context, chat_id):
    try:
        await asyncio.sleep(22)
        await update_caption(context, chat_id, "⚠️ <strong>8 Seconds!</strong>")
        await asyncio.sleep(3)
        await update_caption(context, chat_id, "⚠️ <strong>5 Seconds!</strong>")
        await asyncio.sleep(3)
        await update_caption(context, chat_id, "⚠️ <strong>2 Seconds!</strong>")
        await asyncio.sleep(2)
        
        auc = auctions[group_map[chat_id]]
        if auc['current_bid']['holder'] is None:
            await handle_result(context, chat_id, sold=False)
        else:
            await handle_result(context, chat_id, sold=True)
    except asyncio.CancelledError: pass

async def update_caption(context, chat_id, text):
    auc = auctions[group_map[chat_id]]
    p = auc['players'][auc['current_index']]
    b = auc['current_bid']
    info = f"🔨 <strong>Current:</strong> {format_price(b['amount'])} ({b['holder_team']})" if b['holder'] else f"💰 <strong>Base:</strong> {format_price(p['BasePrice'])}"
    try: await context.bot.edit_message_caption(chat_id, auc["msg_id"], caption=f"💎 <strong>{p['Name']}</strong>\n{info}\n{text}", reply_markup=auc.get('last_kb'), parse_mode='HTML')
    except: pass

async def handle_result(context, chat_id, sold):
    auc = auctions[group_map[chat_id]]
    p = auc['players'][auc['current_index']]
    
    # ⚠️ NO AUTO NEXT. ADMIN MUST CLICK NEXT ⚠️
    kb = [[InlineKeyboardButton("REBID 🔄", callback_data="REBID"), InlineKeyboardButton("NEXT ⏭️", callback_data="NEXT")]]
    
    if not sold:
        p['Status'] = 'Unsold'
        cap = f"❌ <strong>UNSOLD</strong>\n\n🏏 <strong>{p['Name']}</strong>\n💰 Base: {format_price(p['BasePrice'])}"
        try: await context.bot.edit_message_caption(chat_id, auc["msg_id"], caption=cap, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        except: pass
        return

    # IF SOLD, CHECK RTM FLAG OR JUST NORMAL SALE
    # NOTE: Since we changed RTM logic to manual trigger by Admin, 
    # we just mark it sold here. The admin can trigger RTM *after* this message 
    # if they want (via /rtm command reply), OR we consider this the final sale state
    # until admin intervenes.
    
    amt = auc['current_bid']['amount']
    holder = auc['current_bid']['holder']
    rtm_used = p.get('rtm_flag', False)
    
    w_team = None
    for t in auc['teams'].values():
        if t['owner'] == holder or t['sec_owner'] == holder: w_team = t; break
        
    if w_team:
        w_team['purse'] -= amt
        w_team['squad'].append({'name': p['Name'], 'price': amt, 'type': 'auction', 'rtm': rtm_used})
        p['Status'] = 'Sold'; p['SoldPrice'] = amt; p['SoldTo'] = w_team['name']
        cap = f"🔴 <strong>SOLD TO {w_team['name']}</strong> 🔴\n\n👤 <strong>{p['Name']}</strong>\n💸 {format_price(amt)}\n💰 Bal: {format_price(w_team['purse'])}"

    try: 
        await context.bot.edit_message_caption(chat_id, auc["msg_id"], caption=cap, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    except: pass

# --- MANUAL RTM TRIGGER COMMAND ---
async def manual_rtm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    
    if update.effective_user.id not in auc['admins']: return
    if not update.message.reply_to_message: return await update.message.reply_text("Reply to the Sold message!")
    
    # Check if a team name was provided
    if not context.args: return await update.message.reply_text("Usage: `/rtm TeamName`")
    team_name = " ".join(context.args)
    
    rtm_team_code, rtm_team = get_team_by_name(auc, team_name)
    if not rtm_team: return await update.message.reply_text("❌ Team not found.")
    
    # Check RTM Count
    if rtm_team['rtms_used'] >= auc['rtm_limit']:
        return await update.message.reply_text(f"🚫 {rtm_team['name']} has no RTMs left!")
        
    # Valid RTM Trigger
    auc["rtm_state"] = "RTM_HIKE_DECISION"
    auc["rtm_data"] = {"rtm_team_code": rtm_team_code, "rtm_team_name": rtm_team['name']}
    
    sold_team_name = auc['players'][auc['current_index']]['SoldTo']
    
    kb = [[InlineKeyboardButton("HIKE 📈", callback_data="DO_HIKE"), InlineKeyboardButton("NO HIKE 📉", callback_data="NO_HIKE")]]
    
    await context.bot.send_message(
        chat_id,
        f"✋ <strong>RTM TRIGGERED by {rtm_team['name']}!</strong>\n\n"
        f"👑 <strong>{sold_team_name}</strong>, do you want to HIKE the price?",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='HTML'
    )

# --- BUTTON HANDLER ---

async def bid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = update.effective_chat.id
    user_id = query.from_user.id
    
    if chat_id not in group_map: return await query.answer("Expired")
    auc = auctions[group_map[chat_id]]
    
    # --- RTM FLOW LOGIC ---
    
    if data == "NO_HIKE":
        # Winner chose no hike -> RTM Team takes player at current price
        # We need to verify user is the winner (SoldTo owner)
        p = auc['players'][auc['current_index']]
        winner_code, winner_t = get_team_by_name(auc, p['SoldTo'])
        
        if user_id != winner_t['owner'] and user_id != winner_t['sec_owner']: 
            return await query.answer("Not your decision!", show_alert=True)
            
        # Execute Transfer
        rtm_code = auc['rtm_data']['rtm_team_code']
        rtm_t = auc['teams'][rtm_code]
        price = p['SoldPrice']
        
        # 1. Refund Winner
        winner_t['purse'] += price
        winner_t['squad'] = [x for x in winner_t['squad'] if x['name'] != p['Name']]
        
        # 2. Charge RTM Team
        if rtm_t['purse'] < price:
            return await context.bot.send_message(chat_id, f"❌ {rtm_t['name']} doesn't have funds! RTM Failed.")
            
        rtm_t['purse'] -= price
        rtm_t['rtms_used'] += 1
        rtm_t['squad'].append({'name': p['Name'], 'price': price, 'type': 'auction', 'rtm': True})
        
        p['SoldTo'] = rtm_t['name']
        p['rtm_flag'] = True
        
        await context.bot.send_message(chat_id, f"✅ <strong>{rtm_t['name']}</strong> uses RTM @ {format_price(price)}!", parse_mode='HTML')
        await query.message.delete()
        return

    if data == "DO_HIKE":
        # Winner wants to hike -> Ask for text input
        p = auc['players'][auc['current_index']]
        winner_code, winner_t = get_team_by_name(auc, p['SoldTo'])
        
        if user_id != winner_t['owner'] and user_id != winner_t['sec_owner']: 
            return await query.answer("Not your decision!", show_alert=True)
            
        auc['rtm_state'] = "RTM_WAITING_HIKE_PRICE"
        await context.bot.send_message(chat_id, f"🔢 <strong>{winner_t['name']}</strong>, type the new price now:", parse_mode='HTML')
        await query.message.delete()
        return

    if data == "RTM_MATCH":
        # RTM Team matches hiked price
        rtm_code = auc['rtm_data']['rtm_team_code']
        rtm_t = auc['teams'][rtm_code]
        
        if user_id != rtm_t['owner'] and user_id != rtm_t['sec_owner']:
            return await query.answer("Not RTM Team!", show_alert=True)
            
        new_price = auc['rtm_data']['hike_price']
        
        if rtm_t['purse'] < new_price:
             return await query.answer("Insufficient Funds!", show_alert=True)
             
        # Refund Original Winner (Sold Price)
        p = auc['players'][auc['current_index']]
        old_winner_code, old_winner_t = get_team_by_name(auc, p['SoldTo'])
        old_price = p['SoldPrice']
        
        old_winner_t['purse'] += old_price
        old_winner_t['squad'] = [x for x in old_winner_t['squad'] if x['name'] != p['Name']]
        
        # Charge RTM Team (New Price)
        rtm_t['purse'] -= new_price
        rtm_t['rtms_used'] += 1
        rtm_t['squad'].append({'name': p['Name'], 'price': new_price, 'type': 'auction', 'rtm': True})
        
        p['SoldTo'] = rtm_t['name']
        p['SoldPrice'] = new_price
        p['rtm_flag'] = True
        
        await context.bot.send_message(chat_id, f"✅ <strong>{rtm_t['name']}</strong> MATCHED @ {format_price(new_price)}!", parse_mode='HTML')
        await query.message.delete()
        return

    if data == "RTM_QUIT":
        rtm_code = auc['rtm_data']['rtm_team_code']
        rtm_t = auc['teams'][rtm_code]
        
        if user_id != rtm_t['owner'] and user_id != rtm_t['sec_owner']:
            return await query.answer("Not RTM Team!", show_alert=True)
            
        # Original Winner Keeps Player but at HIKED PRICE
        p = auc['players'][auc['current_index']]
        old_winner_code, old_winner_t = get_team_by_name(auc, p['SoldTo'])
        old_price = p['SoldPrice']
        new_price = auc['rtm_data']['hike_price']
        
        # Adjust difference
        diff = new_price - old_price
        if old_winner_t['purse'] < diff:
             await context.bot.send_message(chat_id, "⚠️ Winner doesn't have funds for hike! Reverting...")
             # Logic to handle this edge case (rare)
        else:
             old_winner_t['purse'] -= diff # Pay extra
             # Update squad price
             for sq_p in old_winner_t['squad']:
                 if sq_p['name'] == p['Name']:
                     sq_p['price'] = new_price
             p['SoldPrice'] = new_price
             
        await context.bot.send_message(chat_id, f"🏳️ RTM Quit. <strong>{old_winner_t['name']}</strong> keeps @ {format_price(new_price)}.", parse_mode='HTML')
        await query.message.delete()
        return

    # --- END AUCTION CONFIRMATION ---
    if data == "CONFIRM_END":
        if user_id not in auc["admins"]: return await query.answer("Admin Only")
        await end_auction_logic(context, chat_id)
        await query.message.delete()
        return
        
    if data == "CANCEL_END":
        if user_id not in auc["admins"]: return await query.answer("Admin Only")
        await query.message.delete()
        return

    # --- NORMAL FLOW ---

    if data == "NEXT":
        if user_id not in auc["admins"]: return await query.answer("Admin Only")
        await show_next_player(context, chat_id)
        return

    if data == "REBID":
        if user_id not in auc["admins"]: return await query.answer("Admin Only")
        if auc.get('timer_task'): auc['timer_task'].cancel()
        
        p = auc['players'][auc['current_index']]
        if p.get('Status') == 'Sold':
            for t in auc['teams'].values():
                if t['name'] == p['SoldTo']:
                    t['purse'] += p['SoldPrice']
                    if p.get('rtm_flag'): t['rtms_used'] -= 1
                    t['squad'] = [x for x in t['squad'] if x['name'] != p['Name']]
                    break
        
        auc["current_index"] -= 1
        await query.answer("Rebidding...")
        await show_next_player(context, chat_id)
        return

    if data == "SKIP":
        my_team = None
        for t in auc['teams'].values():
            if t['owner'] == user_id or t['sec_owner'] == user_id: my_team = t; break
        
        if not my_team: return await query.answer("No Team")
        if auc['current_bid']['holder'] == user_id: return await query.answer("Leader can't skip", show_alert=True)
        if user_id in auc["skip_voters"]: return await query.answer("Voted")
        
        auc["skip_voters"].add(user_id)
        active = len([t for t in auc['teams'].values() if t['owner']])
        if len(auc["skip_voters"]) >= active:
            if auc.get('timer_task'): auc['timer_task'].cancel()
            await handle_result(context, chat_id, sold=False)
        else: await query.answer(f"Skip: {len(auc['skip_voters'])}/{active}")
        return

    if data == "BID":
        my_team = None
        for t in auc['teams'].values():
            if t['owner'] == user_id or t['sec_owner'] == user_id: my_team = t; break
            
        if not my_team: return await query.answer("No Team")
        if auc['current_bid']['holder'] == user_id: return await query.answer("Wait!", show_alert=True)
        
        curr = auc['current_bid']['amount']
        if auc['current_bid']['holder'] is None:
            new_amt = curr
        else:
            new_amt = curr + get_increment(curr)

        
        if my_team['purse'] < new_amt: return await query.answer("Low Funds!", show_alert=True)
        
        auc['current_bid'] = {"amount": new_amt, "holder": user_id, "holder_team": my_team['name']}
        auc['skip_voters'] = set()
        await query.answer(f"Bid {format_price(new_amt)}")
        
        if auc.get('timer_task'): auc['timer_task'].cancel()
        auc['timer_task'] = asyncio.create_task(auction_timer(context, chat_id))
        
        p = auc['players'][auc['current_index']]
        kb = [[InlineKeyboardButton(f"BID {format_price(new_amt + get_increment(new_amt))}", callback_data="BID")], [InlineKeyboardButton("SKIP", callback_data="SKIP")]]
        cap = f"💎 <strong>{p['Name']}</strong>\n🔨 <strong>Current:</strong> {format_price(new_amt)} ({my_team['name']})\n⏳ <strong>Reset 30s</strong>"
        
        auc['last_kb'] = InlineKeyboardMarkup(kb)
        try: await context.bot.edit_message_caption(chat_id, auc["msg_id"], caption=cap, reply_markup=auc['last_kb'], parse_mode='HTML')
        except: pass

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    user_id = update.effective_user.id
    
    # RTM HIKE INPUT
    if auc.get("rtm_state") == "RTM_WAITING_HIKE_PRICE":
        # Check if user is the winner
        p = auc['players'][auc['current_index']]
        code, t = get_team_by_name(auc, p['SoldTo'])
        
        if user_id != t['owner'] and user_id != t['sec_owner']: return
        
        try:
            hike_price = parse_price(update.message.text)
            sold_price = p['SoldPrice']
            
            if hike_price <= sold_price:
                return await update.message.reply_text(f"⚠️ Hike must be > {format_price(sold_price)}")
                
            if t['purse'] < hike_price:
                return await update.message.reply_text(f"❌ Low Funds! Max: {format_price(t['purse'])}")
            
            auc['rtm_data']['hike_price'] = hike_price
            auc['rtm_state'] = "RTM_MATCH_DECISION"
            
            rtm_code = auc['rtm_data']['rtm_team_code']
            rtm_name = auc['rtm_data']['rtm_team_name']
            
            kb = [[InlineKeyboardButton(f"MATCH @ {format_price(hike_price)}", callback_data="RTM_MATCH"), InlineKeyboardButton("QUIT", callback_data="RTM_QUIT")]]
            
            await context.bot.send_message(chat_id, f"📈 Price Hiked to <strong>{format_price(hike_price)}</strong>!\n\n🚨 <strong>{rtm_name}</strong>, decision?", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
            
        except: pass
        return

async def end_auction_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Sends confirmation button
    chat_id = update.effective_chat.id
    if chat_id in group_map:
        if update.effective_user.id in auctions[group_map[chat_id]]['admins']:
            kb = [[InlineKeyboardButton("✅ YES, END IT", callback_data="CONFIRM_END"), InlineKeyboardButton("❌ CANCEL", callback_data="CANCEL_END")]]
            await update.message.reply_text("🛑 <strong>Are you sure you want to end?</strong>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def end_auction_logic(context, chat_id):
    auc = auctions[group_map[chat_id]]
    report = f"🏆 <strong>{auc['name']} RESULTS</strong> 🏆\n\n"
    for t in auc['teams'].values():
        report += f"🛡 <strong>{t['name']}</strong>\n💰 Rem: {format_price(t['purse'])}\n"
    
    for admin_id in auc['admins']:
        try: await context.bot.send_message(admin_id, report, parse_mode='HTML')
        except: pass
        if admin_id in admin_map: del admin_map[admin_id] 
    
    await context.bot.send_message(chat_id, "🛑 Auction Ended. Data Cleared.", parse_mode='HTML')
    # CLEANUP
    if auc['connected_group'] in group_map: del group_map[auc['connected_group']]
    if auc['room_id'] in auctions: del auctions[auc['room_id']]

async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in group_map:
        if update.effective_user.id in auctions[group_map[chat_id]]['admins']:
            auctions[group_map[chat_id]]['is_paused'] = True
            await update.message.reply_text("⏸ Paused")

async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in group_map:
        if update.effective_user.id in auctions[group_map[chat_id]]['admins']:
            auctions[group_map[chat_id]]['is_paused'] = False
            await update.message.reply_text("▶️ Resumed")
            await show_next_player(context, chat_id)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """📚 <strong>FULL COMMAND LIST</strong>

<strong>👑 ADMIN COMMANDS (DM)</strong>
<code>/start</code> - Start Setup
<code>/createteam TeamName</code> - Generate Team Code
<code>/secondowner TEAM_CODE</code> - Generate Sub Code
<code>/transfer OLD_CODE</code> - Transfer team
<code>/retain TEAM_CODE Player Name - Price</code> - Add retained player
<code>/summary</code> - Get Full Report
<code>/rtmedit TEAM_CODE COUNT</code> - Edit RTMs

<strong>📢 GROUP ADMIN</strong>
<code>/init ROOM_ID</code> - Connect Group
<code>/promote</code> (Reply) - Add Admin
<code>/start_auction</code> - Begin
<code>/end_auction</code> - Stop & Send Report
<code>/pause</code> / <code>/resume</code> - Control
<code>/now PlayerName</code> - Fast track player
<code>/rtm TeamName</code> (Reply to Sold) - Trigger RTM

<strong>👤 TEAM OWNERS</strong>
<code>/register CODE</code> - Claim Team
<code>/team TeamName</code> - View Squad
<code>/stats</code> - View All Teams
<code>/check PlayerName</code> - Check status
<code>/upcoming</code> - Next 10 Players
"""
    await update.message.reply_text(msg, parse_mode='HTML')

async def check_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auc = get_auction_by_context(update)
    if not auc: return await update.message.reply_text("❌ No active auction.")
    
    q = " ".join(context.args).lower()
    msg = "❌ Not found"
    for p in auc['players']:
        if q in p['Name'].lower():
            if p.get('Status') == 'Sold': msg = f"🔴 <strong>{p['Name']}</strong>: Sold to {p['SoldTo']} ({format_price(p['SoldPrice'])})"
            elif p.get('Status') == 'Unsold': msg = f"❌ <strong>{p['Name']}</strong>: Unsold"
            else: msg = f"⏳ <strong>{p['Name']}</strong>: Upcoming"
    await update.message.reply_text(msg, parse_mode='HTML')

async def upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    start = auc['current_index'] + 1
    ls = auc['players'][start:start+10]
    msg = "📋 <strong>UPCOMING</strong>\n" + "\n".join([f"{p['Name']} - {format_price(p['BasePrice'])}" for p in ls])
    await update.message.reply_text(msg, parse_mode='HTML')

async def completed_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    msg = "📜 <strong>SOLD</strong>\n" + "\n".join([f"{p['Name']} -> {p['SoldTo']} ({format_price(p['SoldPrice'])})" for p in auc['players'] if p.get('Status') == 'Sold'])
    await update.message.reply_text(msg, parse_mode='HTML')

async def fast_track_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    
    if update.effective_user.id not in auc['admins']: return
    if not context.args: return await update.message.reply_text("Usage: `/now PlayerName`")
    
    name_query = " ".join(context.args).lower()
    target_idx = -1
    found_player = None
    
    for i, p in enumerate(auc['players']):
        if name_query in p['Name'].lower() and p.get('Status') == 'Upcoming':
            target_idx = i
            found_player = p
            break
            
    if target_idx == -1: return await update.message.reply_text("❌ Not found/Sold.")
        
    auc['players'].pop(target_idx)
    auc['players'].insert(auc['current_index'] + 1, found_player)
    
    await update.message.reply_text(f"🚀 <strong>{found_player['Name']}</strong> is next!", parse_mode='HTML')

async def full_summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    
    auc = None
    for a in auctions.values():
        if update.effective_user.id in a['admins']:
            auc = a
            break
            
    if not auc: return await update.message.reply_text("❌ No active auction.")
    
    report = f"🏆 <strong>{auc['name']} FULL REPORT</strong> 🏆\n\n"
    for t in auc['teams'].values():
        owners = t['owner_name']
        if t['sec_owner_name'] != "None": owners += f" & {t['sec_owner_name']}"
        report += f"🛡 <strong>{t['name']}</strong> ({owners})\n💰 Rem: {format_price(t['purse'])}\n"
        
        for p in t['squad']:
            tag = " (RTM)" if p.get('rtm') else ""
            p_type = "🔹" if p.get('type') == 'retained' else "🔨"
            report += f"   {p_type} {p['name']} - {format_price(p['price'])}{tag}\n"
        report += "\n"
        
    if len(report) > 4000:
        for x in range(0, len(report), 4000):
            await update.message.reply_text(report[x:x+4000], parse_mode='HTML')
    else:
        await update.message.reply_text(report, parse_mode='HTML')

# --- SERVER ---
app = Flask(__name__)
@app.route('/')
def index(): return "Bot Active"
def run_web_server(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

if __name__ == '__main__':
    Thread(target=run_web_server).start()
    bot_app = ApplicationBuilder().token(TOKEN).build()
    
    setup = ConversationHandler(
        entry_points=[CommandHandler('start', start_setup)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_purse)],
            ASK_PURSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_rtm)],
            ASK_RTM_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_file)],
            ASK_FILE: [MessageHandler(filters.Document.ALL, finish_setup)]
        },
        fallbacks=[CommandHandler('cancel', cancel_setup)]
    )
    
    bot_app.add_handler(setup)
    bot_app.add_handler(CommandHandler("help", help_cmd))
    bot_app.add_handler(CommandHandler("init", init_group))
    bot_app.add_handler(CommandHandler("promote", promote_admin))
    bot_app.add_handler(CommandHandler("createteam", create_team))
    bot_app.add_handler(CommandHandler("secondowner", second_owner_cmd))
    bot_app.add_handler(CommandHandler("register", register))
    bot_app.add_handler(CommandHandler("start_auction", start_auction))
    bot_app.add_handler(CommandHandler("end_auction", end_auction_btn))
    bot_app.add_handler(CommandHandler(["team", "teams", "stats"], team_stats_logic))
    bot_app.add_handler(CommandHandler("retain", retain_player))
    bot_app.add_handler(CommandHandler("transfer", transfer_team))
    bot_app.add_handler(CommandHandler("check", check_player))
    bot_app.add_handler(CommandHandler("upcoming", upcoming))
    bot_app.add_handler(CommandHandler("completed", completed_list))
    bot_app.add_handler(CommandHandler("pause", pause_cmd))
    bot_app.add_handler(CommandHandler("resume", resume_cmd))
    bot_app.add_handler(CommandHandler("now", fast_track_player))
    bot_app.add_handler(CommandHandler("summary", full_summary_cmd))
    bot_app.add_handler(CommandHandler("rtm", manual_rtm_command))
    bot_app.add_handler(CommandHandler("rtmedit", edit_rtm_count))
    
    bot_app.add_handler(CallbackQueryHandler(bid_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    print("Ultra Advanced Bot is Live...")
    bot_app.run_polling()