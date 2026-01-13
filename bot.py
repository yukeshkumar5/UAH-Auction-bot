import logging
import asyncio
import pandas as pd
import random
import string
import os
import re
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
group_map = {}  
admin_map = {}  # Stores {user_id: room_id} to prevent multiple auctions

# --- HELPER FUNCTIONS ---

def format_price(value):
    if value >= 100:
        cr_val = value / 100
        if cr_val.is_integer(): return f"{int(cr_val)}C"
        return f"{round(cr_val, 2)}C"
    else:
        return f"{value}L"

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

def get_team_by_owner(user_id):
    for code, data in state['teams'].items(): # Will be accessed via auction object usually
        pass 
    # Helper to find team in a specific auction instance
    return None

# ==============================================================================
# 1. SETUP (DM ONLY) - WITH SINGLE AUCTION CHECK
# ==============================================================================

async def start_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        await update.message.reply_text("⚠️ Setup must be done in DM!")
        return ConversationHandler.END
    
    user_id = update.effective_user.id
    
    # CHECK: Does this user already have an active auction?
    if user_id in admin_map:
        await update.message.reply_text("🚫 <strong>You already have an active auction!</strong>\n\nPlease finish that one using <code>/end_auction</code> in the group before starting a new one.", parse_mode='HTML')
        return ConversationHandler.END
    
    context.user_data['setup'] = {"admins": [user_id]}
    await update.message.reply_text("🛠 <strong>Auction Setup</strong>\n\n1. Enter Auction Name (e.g. IPL 2026):", parse_mode='HTML')
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
            "rtm_state": None,
            "rtm_claimants": {},
            "timer_task": None,
            "auto_next_task": None
        }
        
        # Map Admin to Room
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
# 2. GROUP COMMANDS & ADMIN
# ==============================================================================

async def init_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_chat.type == 'private': return
    
    if not context.args: return await update.message.reply_text("Usage: <code>/init ROOM_ID</code>", parse_mode='HTML')
    rid = context.args[0]
    
    if rid not in auctions: return await update.message.reply_text("❌ Invalid ID.")
    auc = auctions[rid]
    
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
    
    # Check duplicate
    for t in auc['teams'].values():
        if t['owner'] == uid or t['sec_owner'] == uid:
            return await update.message.reply_text("🚫 You already have a team!")

    # Check Main Code
    if input_code in auc['teams']:
        if auc['teams'][input_code]['owner']: return await update.message.reply_text("⚠️ Main Owner Exists!")
        auc['teams'][input_code]['owner'] = uid
        auc['teams'][input_code]['owner_name'] = name
        await update.message.reply_text(f"🎉 <strong>{name}</strong> is Owner of <strong>{auc['teams'][input_code]['name']}</strong>!", parse_mode='HTML')
        return

    # Check Sub Code
    for code, t in auc['teams'].items():
        if t.get('sub_code') == input_code:
            if t['sec_owner']: return await update.message.reply_text("⚠️ 2nd Owner Exists!")
            t['sec_owner'] = uid
            t['sec_owner_name'] = name
            await update.message.reply_text(f"🎉 <strong>{name}</strong> joined <strong>{t['name']}</strong> as 2nd Owner!", parse_mode='HTML')
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
    
    await update.message.reply_text(f"🔄 Team Transferred.\nNew Code: <code>{new_code}</code>", parse_mode='HTML')

async def retain_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return
    uid = update.effective_user.id
    if uid not in admin_map: return
    auc = auctions[admin_map[uid]]
    
    try:
        code = context.args[0]
        price = parse_price(context.args[-1])
        p_name = " ".join(context.args[1:-1])
        
        if code not in auc['teams']: return await update.message.reply_text("❌ Invalid Team")
        
        t = auc['teams'][code]
        t['purse'] -= price
        t['squad'].append({'name': p_name, 'price': price, 'type': 'retained'})
        
        # SMART RETENTION: Remove from Auction Pool
        original_len = len(auc['players'])
        auc['players'] = [p for p in auc['players'] if p['Name'].lower() != p_name.lower()]
        
        msg_extra = ""
        if len(auc['players']) < original_len: msg_extra = "\n(Removed from Auction List)"
        
        await update.message.reply_text(f"✅ Retained <strong>{p_name}</strong> for {format_price(price)}{msg_extra}", parse_mode='HTML')
    except:
        await update.message.reply_text("Usage: <code>/retain CODE Name Price</code>", parse_mode='HTML')

async def team_stats_logic(update, context):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    
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
    
    auc['is_active'] = True
    await update.message.reply_text("📢 <strong>AUCTION STARTING!</strong>", parse_mode='HTML')
    await asyncio.sleep(2)
    await show_next_player(context, chat_id)

async def show_next_player(context, chat_id):
    auc = auctions[group_map[chat_id]]
    try:
        if auc.get('timer_task'): auc['timer_task'].cancel()
        if auc.get('auto_next_task'): auc['auto_next_task'].cancel()
        if auc['is_paused']: return

        auc['current_index'] += 1
        auc['skip_voters'] = set()
        auc['rtm_state'] = None
        auc['rtm_claimants'] = {}
        
        if auc['current_index'] >= len(auc['players']):
            await context.bot.send_message(chat_id, "🏁 <strong>Auction Finished!</strong>", parse_mode='HTML')
            await end_auction_logic(context, chat_id)
            return

        p = auc['players'][auc['current_index']]
        base = p.get('BasePrice', 20)
        auc['current_bid'] = {"amount": base, "holder": None, "holder_team": None}
        
        loop = asyncio.get_event_loop()
        img_url = await loop.run_in_executor(None, get_player_image, p['Name'])
        
        caption = (
            f"💎 <strong>LOT #{auc['current_index']+1}</strong>\n"
            f"🏏 <strong>{p['Name']}</strong>\n"
            f"🌍 {p.get('Country','')} | 🏏 {p.get('Role','')}\n\n"
            f"💰 <strong>Base Price:</strong> {format_price(base)}\n"
            f"⏳ <strong>30 Seconds Clock</strong>"
        )
        kb = [[InlineKeyboardButton(f"BID {format_price(base)}", callback_data="BID")], [InlineKeyboardButton("SKIP", callback_data="SKIP")]]
        msg = await context.bot.send_photo(chat_id, photo=img_url, caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        auc["msg_id"] = msg.message_id
        auc['timer_task'] = asyncio.create_task(auction_timer(context, chat_id))
    except: pass

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
            await trigger_rtm_phase(context, chat_id)
    except asyncio.CancelledError: pass

async def update_caption(context, chat_id, text):
    auc = auctions[group_map[chat_id]]
    p = auc['players'][auc['current_index']]
    b = auc['current_bid']
    info = f"🔨 <strong>Current:</strong> {format_price(b['amount'])} ({b['holder_team']})" if b['holder'] else f"💰 <strong>Base:</strong> {format_price(p['BasePrice'])}"
    try: await context.bot.edit_message_caption(chat_id, auc["msg_id"], caption=f"💎 <strong>{p['Name']}</strong>\n{info}\n{text}", reply_markup=auc.get('last_kb'), parse_mode='HTML')
    except: pass

# --- RTM & RESULT ---

async def trigger_rtm_phase(context, chat_id):
    auc = auctions[group_map[chat_id]]
    auc["rtm_state"] = "CLAIMING"
    await context.bot.send_message(chat_id, "🔴 <strong>SOLD! RTM Window (10s)</strong>", parse_mode='HTML')
    
    kb = [[InlineKeyboardButton("✋ CLAIM RTM", callback_data="CLAIM_RTM"), InlineKeyboardButton("REBID 🔄", callback_data="REBID")],
          [InlineKeyboardButton("NEXT PLAYER ⏭️", callback_data="NEXT")]]
    
    try: await context.bot.edit_message_reply_markup(chat_id, auc["msg_id"], reply_markup=InlineKeyboardMarkup(kb))
    except: pass
    
    msg = await context.bot.send_message(chat_id, "👀 <strong>Claims:</strong> None", parse_mode='HTML')
    auc["rtm_admin_msg_id"] = msg.message_id
    auc['auto_next_task'] = asyncio.create_task(rtm_window_timer(context, chat_id))

async def rtm_window_timer(context, chat_id):
    try:
        await asyncio.sleep(10)
        auc = auctions[group_map[chat_id]]
        try: await context.bot.edit_message_reply_markup(chat_id, auc["msg_id"], reply_markup=None)
        except: pass
        
        if not auc["rtm_claimants"]:
            try: await context.bot.edit_message_text(chat_id, auc["rtm_admin_msg_id"], text="❌ No RTM Claims.")
            except: pass
            await handle_result(context, chat_id, sold=True)
        else:
            await context.bot.send_message(chat_id, "⏳ <strong>Time Up!</strong> Admin, select RTM.", parse_mode='HTML')
            auc["rtm_state"] = "SELECTING"
    except asyncio.CancelledError: pass

async def handle_result(context, chat_id, sold):
    auc = auctions[group_map[chat_id]]
    p = auc['players'][auc['current_index']]
    kb = [[InlineKeyboardButton("REBID 🔄", callback_data="REBID"), InlineKeyboardButton("NEXT ⏭️", callback_data="NEXT")]]
    
    if not sold:
        p['Status'] = 'Unsold'
        cap = f"❌ <strong>UNSOLD</strong>\n\n🏏 <strong>{p['Name']}</strong>\n💰 Base: {format_price(p['BasePrice'])}\n\n<i>Next in 10s...</i>"
    else:
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
            cap = f"🔴 <strong>SOLD TO {w_team['name']}</strong> 🔴\n\n👤 <strong>{p['Name']}</strong>\n💸 {format_price(amt)}\n💰 Bal: {format_price(w_team['purse'])}\n\n<i>Next in 10s...</i>"

    try: 
        await context.bot.edit_message_caption(chat_id, auc["msg_id"], caption=cap, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        if auc["rtm_admin_msg_id"]: await context.bot.delete_message(chat_id, auc["rtm_admin_msg_id"])
    except: pass
    
    auc['auto_next_task'] = asyncio.create_task(auto_advance(context, chat_id))

async def auto_advance(context, chat_id):
    await asyncio.sleep(10)
    await show_next_player(context, chat_id)

async def end_auction_logic(context, chat_id):
    auc = auctions[group_map[chat_id]]
    
    # Generate Full Report
    report = f"🏆 <strong>{auc['name']} FULL REPORT</strong> 🏆\n\n"
    for code, t in auc['teams'].items():
        owners = t['owner_name']
        if t['sec_owner_name'] != "None": owners += f" & {t['sec_owner_name']}"
        
        report += f"🛡 <strong>{t['name']}</strong>\n"
        report += f"👤 Owners: {owners}\n"
        report += f"💰 Rem. Purse: {format_price(t['purse'])}\n"
        report += f"👥 Squad Size: {len(t['squad'])}\n"
        
        retained = [p for p in t['squad'] if p.get('type') == 'retained']
        auction = [p for p in t['squad'] if p.get('type') == 'auction']
        
        if retained:
            report += "🔹 <strong>Retained:</strong>\n"
            for p in retained: report += f"   • {p['name']} ({format_price(p['price'])})\n"
            
        if auction:
            report += "🔨 <strong>Auction Buys:</strong>\n"
            for p in auction: 
                tag = " (RTM)" if p.get('rtm') else ""
                report += f"   • {p['name']} ({format_price(p['price'])}){tag}\n"
        
        report += "\n----------------------\n\n"
    
    # Send to ALL Admins
    for admin_id in auc['admins']:
        try: 
            # Split if too long
            if len(report) > 4000:
                for x in range(0, len(report), 4000):
                    await context.bot.send_message(admin_id, report[x:x+4000], parse_mode='HTML')
            else:
                await context.bot.send_message(admin_id, report, parse_mode='HTML')
                
            # Remove Admin from admin_map to allow them to create NEW auction
            if admin_id in admin_map: del admin_map[admin_id]
        except: pass
    
    await context.bot.send_message(chat_id, "🛑 <strong>Auction Ended.</strong> Check DM for report.", parse_mode='HTML')
    del auctions[group_map[chat_id]]
    del group_map[chat_id]

# --- BUTTON HANDLER ---

async def bid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = update.effective_chat.id
    user_id = query.from_user.id
    
    if chat_id not in group_map: return await query.answer("Expired")
    auc = auctions[group_map[chat_id]]
    
    if "RTM" in data or "GRANT" in data or data == "NO_HIKE":
        await handle_rtm_logic(update, context, chat_id)
        return

    if data == "NEXT":
        if user_id not in auc["admins"]: return await query.answer("Admin Only")
        await show_next_player(context, chat_id)
        return

    if data == "REBID":
        if user_id not in auc["admins"]: return await query.answer("Admin Only")
        if auc.get('auto_next_task'): auc['auto_next_task'].cancel()
        
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
        new_amt = curr + get_increment(curr) if auc['current_bid']['holder'] else curr
        
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

async def handle_rtm_logic(update, context, chat_id):
    auc = auctions[group_map[chat_id]]
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    
    if data == "CLAIM_RTM":
        my_team_code = None
        my_team = None
        for c, t in auc['teams'].items():
            if t['owner'] == user_id or t['sec_owner'] == user_id: my_team_code=c; my_team=t; break
        
        if not my_team: return await query.answer("No Team")
        if user_id == auc['current_bid']['holder']: return await query.answer("Winner can't RTM", show_alert=True)
        if my_team['rtms_used'] >= auc['rtm_limit']: return await query.answer("No RTMs left", show_alert=True)
        
        if auc.get('auto_next_task'): auc['auto_next_task'].cancel()
        
        auc["rtm_claimants"][user_id] = my_team_code
        await query.answer("Claimed!")
        
        names = ", ".join([auc['teams'][c]['name'] for c in auc['rtm_claimants'].values()])
        kb = []
        row = []
        for uid, c in auc['rtm_claimants'].items():
            row.append(InlineKeyboardButton(f"Grant {auc['teams'][c]['name']}", callback_data=f"GRANT_{c}"))
            if len(row)==2: kb.append(row); row=[]
        if row: kb.append(row)
        kb.append([InlineKeyboardButton("❌ Reject All", callback_data="RTM_REJECT")])
        try: await context.bot.edit_message_text(chat_id, auc["rtm_admin_msg_id"], text=f"👀 <strong>Claims:</strong> {names}", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        except: pass
        return

    if "GRANT_" in data:
        if user_id not in auc["admins"]: return await query.answer("Admin Only")
        code = data.split("_")[1]
        auc["selected_rtm_team"] = code
        auc['teams'][code]['rtms_used'] += 1
        
        auc["rtm_state"] = "WAITING_HIKE"
        winner = auc['current_bid']['holder_team']
        await context.bot.send_message(chat_id, f"✅ <strong>{auc['teams'][code]['name']}</strong> selected!\n(RTM Deducted)\n\n👑 <strong>{winner}</strong>, Type price or:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("No Hike", callback_data="NO_HIKE")]]), parse_mode='HTML')
        return

    if data == "RTM_REJECT":
        if user_id not in auc["admins"]: return await query.answer("Admin Only")
        await context.bot.send_message(chat_id, "❌ RTM Rejected.")
        await handle_result(context, chat_id, sold=True)
        return

    if data == "NO_HIKE":
        if user_id != auc['current_bid']['holder']: return await query.answer("Not Winner")
        rtm_t = auc['teams'][auc['selected_rtm_team']]
        sold_p = auc['current_bid']['amount']
        await context.bot.send_message(chat_id, f"📉 No Hike.\n<strong>{rtm_t['name']}</strong> wins via RTM @ {format_price(sold_p)}!", parse_mode='HTML')
        auc['current_bid']['holder'] = rtm_t['owner']
        auc['current_bid']['holder_team'] = rtm_t['name']
        auc['players'][auc['current_index']]['rtm_flag'] = True
        await handle_result(context, chat_id, sold=True)
        return

    if data in ["RTM_MATCH", "RTM_QUIT"]:
        rtm_t = auc['teams'][auc['selected_rtm_team']]
        if user_id != rtm_t['owner'] and user_id != rtm_t['sec_owner']: return await query.answer("Not RTM Team")
        
        if data == "RTM_QUIT":
            await context.bot.send_message(chat_id, f"🏳️ <strong>{rtm_t['name']}</strong> QUITS!", parse_mode='HTML')
            await handle_result(context, chat_id, sold=True)
        else:
            p = auc['current_bid']['amount']
            if rtm_t['purse'] < p: return await query.answer("Low Funds!", show_alert=True)
            auc['current_bid']['holder'] = user_id
            auc['current_bid']['holder_team'] = rtm_t['name']
            auc['players'][auc['current_index']]['rtm_flag'] = True
            await context.bot.send_message(chat_id, f"✅ <strong>{rtm_t['name']}</strong> MATCHED!", parse_mode='HTML')
            await handle_result(context, chat_id, sold=True)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    
    if auc.get("rtm_state") != "WAITING_HIKE": return
    if update.effective_user.id != auc['current_bid']['holder']: return
    
    try:
        new_p = parse_price(update.message.text)
        curr = auc['current_bid']['amount']
        if new_p <= curr: return await update.message.reply_text(f"⚠️ Must be > {format_price(curr)}")
        
        winner_team = None
        for t in auc['teams'].values():
            if t['owner'] == update.effective_user.id or t['sec_owner'] == update.effective_user.id: winner_team = t; break
            
        if winner_team['purse'] < new_p: return await update.message.reply_text("❌ Not enough funds")
        
        auc['current_bid']['amount'] = new_p
        auc["rtm_state"] = "WAITING_MATCH"
        
        rtm_name = auc['teams'][auc['selected_rtm_team']]['name']
        await context.bot.send_message(chat_id, f"📈 Bid Raised to {format_price(new_p)}!\n🚨 <strong>{rtm_name}</strong>, MATCH or QUIT?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Match", callback_data="RTM_MATCH"), InlineKeyboardButton("Quit", callback_data="RTM_QUIT")]]), parse_mode='HTML')
    except: pass

async def end_auction_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in group_map:
        if update.effective_user.id in auctions[group_map[chat_id]]['admins']:
            await end_auction_logic(context, chat_id)

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
<code>/retain TEAM_CODE PlayerName Price</code> - Add retained player

<strong>📢 GROUP ADMIN</strong>
<code>/init ROOM_ID</code> - Connect Group
<code>/promote</code> (Reply) - Add Admin
<code>/start_auction</code> - Begin
<code>/end_auction</code> - Stop & Send Report
<code>/pause</code> / <code>/resume</code> - Control

<strong>👤 TEAM OWNERS</strong>
<code>/register CODE</code> - Claim Team
<code>/team TeamName</code> - View Squad
<code>/stats</code> - View All Teams
<code>/check PlayerName</code> - Check status
<code>/upcoming</code> - Next 10 Players
"""
    await update.message.reply_text(msg, parse_mode='HTML')

# --- SERVER ---
app = Flask(__name__)
@app.route('/')
def index(): return "Bot Active"
def run_web_server(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

if __name__ == '__main__':
    Thread(target=run_web_server).start()
    app = ApplicationBuilder().token(TOKEN).build()
    
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
    
    app.add_handler(setup)
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("init", init_group))
    app.add_handler(CommandHandler("promote", promote_admin))
    app.add_handler(CommandHandler("createteam", create_team))
    app.add_handler(CommandHandler("secondowner", second_owner_cmd))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("start_auction", start_auction))
    app.add_handler(CommandHandler("end_auction", end_auction_cmd))
    app.add_handler(CommandHandler(["team", "teams", "stats"], team_stats_logic))
    app.add_handler(CommandHandler("retain", retain_player))
    app.add_handler(CommandHandler("transfer", transfer_team))
    app.add_handler(CommandHandler("check", check_player))
    app.add_handler(CommandHandler("upcoming", upcoming))
    app.add_handler(CommandHandler("completed", completed_list))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))
    
    app.add_handler(CallbackQueryHandler(bid_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    print("Ultra Advanced Bot is Live...")
    app.run_polling()
