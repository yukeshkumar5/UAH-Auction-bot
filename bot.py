import logging
import asyncio
import pandas as pd
import random
import string
import os
import re
import sys
import psycopg2
from threading import Thread
from flask import Flask
# 1. IMPORT IMAGE SEARCH
from duckduckgo_search import DDGS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ConversationHandler
)

# --- CONFIGURATION ---
TOKEN = "8555822248:AAE76zDM4g-e_Ti3Zwg3k4TTEico-Ewyas0"
DB_URL = "postgresql://postgres:%4005052007Yukesh@db.axkdujpwqgsbvpotwvzu.supabase.co:5432/postgres"

# --- LOGGING ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

# --- STATES ---
ASK_NAME, ASK_PURSE, ASK_RTM_COUNT, ASK_FILE = range(4)

# --- GLOBAL DATA ---
auctions = {}   
group_map = {}  
admin_map = {}  

# --- DATABASE ---
def init_db():
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS auctions (room_id TEXT PRIMARY KEY, data TEXT);")
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"DB Error: {e}")

init_db()

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
    # Convert all columns to lowercase string for easier matching
    df.columns = [str(c).strip().lower() for c in df.columns]
    data = df.to_dict('records')
    cleaned = []
    
    for p in data:
        # 1. FIND NAME
        name = p.get('player name') or p.get('name') or p.get('player') or 'Unknown'
        
        # 2. FIND ROLE/POSITION
        role = p.get('position') or p.get('role') or p.get('type') or 'Player'
        
        # 3. FIND COUNTRY
        country = p.get('country') or p.get('team') or 'Unknown'
        
        # 4. FIND BASE PRICE
        raw_price = p.get('base price') or p.get('baseprice') or p.get('price') or '20L'
        base = parse_price(str(raw_price))
        
        cleaned.append({
            'Name': name,
            'Role': role,
            'Country': country,
            'BasePrice': base,
            'Status': 'Upcoming', 
            'SoldPrice': 0,
            'SoldTo': 'None'
        })
    return cleaned

def generate_code(length=5):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

# --- AUTOMATIC IMAGE SEARCH ---
def get_player_image(player_name):
    try:
        with DDGS() as ddgs:
            # Search for "Player Name Cricketer" to get accurate results
            results = list(ddgs.images(keywords=f"{player_name} cricketer", region="in-en", safesearch="on", max_results=1))
            if results: 
                return results[0]['image']
    except Exception as e:
        logger.error(f"Image search failed for {player_name}: {e}")
    
    # Default Fallback Image
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
        if t['name'].lower() == name.lower(): return code, t
    return None, None

# ==============================================================================
# 1. SETUP (DM ONLY)
# ==============================================================================

async def start_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        await update.message.reply_text("⚠️ Setup must be done in DM!")
        return ConversationHandler.END
    
    uid = update.effective_user.id
    if uid in admin_map:
        await update.message.reply_text("🚫 Active Auction exists! Use /end_auction first.")
        return ConversationHandler.END
        
    context.user_data['setup'] = {"admins": [uid]}
    await update.message.reply_text("🛠 <strong>Auction Setup</strong>\n1. Enter Auction Name:", parse_mode='HTML')
    return ASK_NAME

async def ask_purse(update, context):
    context.user_data['setup']['name'] = update.message.text
    await update.message.reply_text("2. Enter Budget (e.g. 100C). Min 1C, Max 2000C.")
    return ASK_PURSE

async def ask_rtm(update, context):
    val = parse_price(update.message.text)
    if val < 100 or val > 200000:
        await update.message.reply_text("⚠️ Budget must be between 1C and 2000C. Try again:")
        return ASK_PURSE
    context.user_data['setup']['purse'] = val
    await update.message.reply_text("3. RTMs per team? (0 for none):")
    return ASK_RTM_COUNT

async def ask_file(update, context):
    try: context.user_data['setup']['rtm_limit'] = int(update.message.text)
    except: context.user_data['setup']['rtm_limit'] = 0
    await update.message.reply_text("4. Upload Player CSV/Excel:")
    return ASK_FILE

async def finish_setup(update, context):
    user_id = update.effective_user.id
    file = await update.message.document.get_file()
    fname = update.message.document.file_name
    path = f"temp_{user_id}_{fname}"
    await file.download_to_drive(path)
    
    try:
        if fname.endswith('.csv'):
            try: df = pd.read_csv(path)
            except: df = pd.read_csv(path, encoding='latin1')
        else: df = pd.read_excel(path)
        
        players = normalize_player_data(df)
        rid = generate_code(6)
        
        auctions[rid] = {
            "room_id": rid,
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
            "rtm_state": None, "rtm_data": {}, "rtm_claimants": {}, "timer_task": None, "last_kb": None
        }
        admin_map[user_id] = rid
        if os.path.exists(path): os.remove(path)
        
        await update.message.reply_text(f"✅ Ready! Players: {len(players)}\n🆔 ID: <code>{rid}</code>\n\n1. Group: <code>/init {rid}</code>\n2. DM: <code>/createteam</code>", parse_mode='HTML')
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
        return ConversationHandler.END

async def cancel_setup(update, context):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# ==============================================================================
# 2. GROUP & TEAMS
# ==============================================================================

async def create_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private': return 
    uid = update.effective_user.id
    if uid not in admin_map: return await update.message.reply_text("❌ No active auction.")
    auc = auctions[admin_map[uid]]
    name = " ".join(context.args)
    if not name: return await update.message.reply_text("Usage: `/createteam Name`")
    
    code = generate_code(4)
    auc['teams'][code] = {
        'name': name, 'owner': None, 'owner_name': "Vacant", 
        'sec_owner': None, 'sec_owner_name': "None", 'sub_code': None,
        'purse': auc['default_purse'], 'squad': [], 'rtms_used': 0
    }
    await update.message.reply_text(f"✅ Team: <strong>{name}</strong>\nCode: <code>{code}</code>", parse_mode='HTML')

async def init_group(update, context):
    chat_id = update.effective_chat.id
    if not context.args: return
    rid = context.args[0]
    if chat_id in group_map: return await update.message.reply_text("🚫 Group busy!")
    if rid in auctions:
        if auctions[rid]['connected_group'] and auctions[rid]['connected_group'] != chat_id:
             return await update.message.reply_text("❌ Code active elsewhere!")
        auctions[rid]['connected_group'] = chat_id
        group_map[chat_id] = rid
        await update.message.reply_text(f"✅ Connected: {auctions[rid]['name']}")

async def register(update, context):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    code = context.args[0] if context.args else ""
    uid = update.effective_user.id
    name = update.effective_user.first_name
    
    if uid in auc['admins']: return await update.message.reply_text("Admins cannot join!")
    for t in auc['teams'].values():
        if t['owner'] == uid: return await update.message.reply_text("You have a team!")
        
    if code in auc['teams']:
        if auc['teams'][code]['owner']: return await update.message.reply_text("Taken!")
        auc['teams'][code]['owner'] = uid
        auc['teams'][code]['owner_name'] = name
        await update.message.reply_text(f"🎉 Joined {auc['teams'][code]['name']}")
    else:
        for t in auc['teams'].values():
            if t.get('sub_code') == code:
                if t['sec_owner']: return await update.message.reply_text("Taken!")
                t['sec_owner'] = uid
                t['sec_owner_name'] = name
                await update.message.reply_text(f"🎉 Joined {t['name']} as 2nd Owner")
                return
        await update.message.reply_text("Invalid Code")

async def retain_player(update, context):
    uid = update.effective_user.id
    if uid not in admin_map: return
    auc = auctions[admin_map[uid]]
    try:
        full = " ".join(context.args)
        code = full.split(" ")[0]
        rest = full[len(code):].strip()
        if "-" in rest: name, price_str = rest.rsplit("-", 1)
        else: return await update.message.reply_text("Format: /retain CODE Name - Price")
        
        price = parse_price(price_str)
        name = name.strip()
        
        if code in auc['teams']:
            auc['teams'][code]['purse'] -= price
            auc['teams'][code]['squad'].append({'name': name, 'price': price, 'type': 'retained'})
            # REMOVE FROM LIST
            original_len = len(auc['players'])
            auc['players'] = [p for p in auc['players'] if p['Name'].lower().strip() != name.lower()]
            msg = f"✅ Retained {name} for {format_price(price)}"
            if len(auc['players']) < original_len: msg += "\n(Removed from List)"
            await update.message.reply_text(msg)
    except: await update.message.reply_text("Usage: /retain CODE Name - Price")

async def team_stats(update, context):
    auc = get_auction_by_context(update)
    if not auc: return await update.message.reply_text("No active auction.")
    
    args = context.args
    if not args:
        msg = "📊 <strong>TEAMS</strong>\n"
        for t in auc['teams'].values():
            rtm = auc['rtm_limit'] - t['rtms_used']
            msg += f"🛡 {t['name']}: {format_price(t['purse'])} | ✋ {rtm}\n"
        await update.message.reply_text(msg, parse_mode='HTML')
        return
        
    name = " ".join(args).lower()
    code, team = get_team_by_name(auc, name)
    if not team: return await update.message.reply_text("Team not found")
    
    rtms = auc['rtm_limit'] - team['rtms_used']
    owners = f"{team['owner_name']} & {team['sec_owner_name']}"
    msg = f"🛡 <strong>{team['name']}</strong>\n💰 {format_price(team['purse'])}\n👤 {owners}\n✋ RTM: {rtms}\n\n"
    
    ret = [p for p in team['squad'] if p.get('type') == 'retained']
    auc_p = [p for p in team['squad'] if p.get('type') == 'auction']
    
    if ret:
        msg += "🔹 <strong>Retained:</strong>\n"
        for p in ret: msg += f"- {p['name']} ({format_price(p['price'])})\n"
    if auc_p:
        msg += "🔨 <strong>Auction:</strong>\n"
        for p in auc_p: 
            tag = " (RTM)" if p.get('rtm') else ""
            msg += f"- {p['name']} ({format_price(p['price'])}){tag}\n"
    
    await update.message.reply_text(msg, parse_mode='HTML')

# ==============================================================================
# 4. AUCTION CORE (AUTO IMAGE SEARCH)
# ==============================================================================

async def start_auction(update, context):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    if auc['is_active']: return await update.message.reply_text("⚠️ Running!")
    auc['is_active'] = True
    await update.message.reply_text("📢 <strong>AUCTION STARTING!</strong>", parse_mode='HTML')
    await asyncio.sleep(2)
    await show_next_player(context, chat_id)

async def show_next_player(context, chat_id, random_pick=False):
    auc = auctions[group_map[chat_id]]
    if auc.get('timer_task'): auc['timer_task'].cancel()
    
    if not random_pick:
        auc['current_index'] += 1
    else:
        indices = [i for i, p in enumerate(auc['players']) if p['Status'] == 'Upcoming']
        if not indices: return await context.bot.send_message(chat_id, "🏁 End.")
        auc['current_index'] = random.choice(indices)

    if auc['current_index'] >= len(auc['players']):
        return await context.bot.send_message(chat_id, "🏁 End of List.")

    p = auc['players'][auc['current_index']]
    base = p.get('BasePrice', 20)
    auc['current_bid'] = {"amount": base, "holder": None, "holder_team": None}
    
    # ⚠️ AUTO IMAGE SEARCH ⚠️
    loop = asyncio.get_event_loop()
    img_url = None
    try:
        # Search DuckDuckGo using the Player Name + 'cricketer'
        img_url = await loop.run_in_executor(None, get_player_image, p['Name'])
    except Exception as e:
        logger.error(f"Image search failed: {e}")

    caption = f"💎 <strong>{p['Name']}</strong>\n💰 Base: {format_price(base)}\n⏳ <strong>30s Clock</strong>"
    kb = [[InlineKeyboardButton(f"BID {format_price(base)}", callback_data="BID")], 
          [InlineKeyboardButton("SKIP", callback_data="SKIP")]]
    auc['last_kb'] = InlineKeyboardMarkup(kb)
    
    # Fallback if image search fails or is None
    if img_url:
        try:
            msg = await context.bot.send_photo(chat_id, photo=img_url, caption=caption, reply_markup=auc['last_kb'], parse_mode='HTML')
        except:
            msg = await context.bot.send_message(chat_id, text=caption, reply_markup=auc['last_kb'], parse_mode='HTML')
    else:
        msg = await context.bot.send_message(chat_id, text=caption, reply_markup=auc['last_kb'], parse_mode='HTML')
    
    auc['msg_id'] = msg.message_id
    auc['timer_task'] = asyncio.create_task(auction_timer(context, chat_id))

async def auction_timer(context, chat_id):
    try:
        await asyncio.sleep(22)
        await update_ui(context, chat_id, "⚠️ 8 Seconds!")
        await asyncio.sleep(3)
        await update_ui(context, chat_id, "⚠️ 5 Seconds!")
        await asyncio.sleep(3)
        await update_ui(context, chat_id, "⚠️ 2 Seconds!")
        await asyncio.sleep(2)
        
        auc = auctions[group_map[chat_id]]
        if auc['current_bid']['holder']:
            await handle_result(context, chat_id, sold=True)
        else:
            await handle_result(context, chat_id, sold=False)
    except asyncio.CancelledError: pass

async def update_ui(context, chat_id, text):
    auc = auctions[group_map[chat_id]]
    p = auc['players'][auc['current_index']]
    b = auc['current_bid']
    info = f"🔨 <strong>{format_price(b['amount'])}</strong> ({b['holder_team']})" if b['holder'] else f"💰 Base: {format_price(p['BasePrice'])}"
    try: await context.bot.edit_message_caption(chat_id, auc['msg_id'], caption=f"💎 <strong>{p['Name']}</strong>\n{info}\n{text}", reply_markup=auc.get('last_kb'), parse_mode='HTML')
    except: pass

async def handle_result(context, chat_id, sold):
    auc = auctions[group_map[chat_id]]
    p = auc['players'][auc['current_index']]
    
    # Check RTM
    if sold:
        total_rtms = sum([(auc['rtm_limit'] - t['rtms_used']) for t in auc['teams'].values()])
        if total_rtms > 0:
            await context.bot.send_message(chat_id, "🔴 <strong>SOLD! RTM Window (10s)</strong>", parse_mode='HTML')
            # Trigger RTM Logic (Buttons)
            kb = [[InlineKeyboardButton("✋ CLAIM RTM", callback_data="CLAIM_RTM"), InlineKeyboardButton("REBID 🔄", callback_data="REBID")]]
            try: await context.bot.edit_message_reply_markup(chat_id, auc["msg_id"], reply_markup=InlineKeyboardMarkup(kb))
            except: pass
            
            # Start RTM Timer
            auc['rtm_admin_msg_id'] = (await context.bot.send_message(chat_id, "👀 Waiting for Claims...", parse_mode='HTML')).message_id
            auc['auto_next_task'] = asyncio.create_task(rtm_window_timer(context, chat_id))
            return

    # Finalize if no RTM or Unsold
    kb = [[InlineKeyboardButton("REBID 🔄", callback_data="REBID"), InlineKeyboardButton("NEXT ⏭️", callback_data="NEXT")],
          [InlineKeyboardButton("🔀 RANDOM", callback_data="RANDOM")]]
    
    if not sold:
        cap = f"❌ <strong>UNSOLD</strong>\n\n🏏 <strong>{p['Name']}</strong>"
    else:
        b = auc['current_bid']
        w_team = None
        for t in auc['teams'].values():
            if t['name'] == b['holder_team']: w_team = t; break
            
        if w_team:
            w_team['purse'] -= b['amount']
            w_team['squad'].append({'name': p['Name'], 'price': b['amount'], 'type': 'auction', 'rtm': False})
            p['Status'] = 'Sold'; p['SoldPrice'] = b['amount']; p['SoldTo'] = w_team['name']
            
        cap = f"🔴 <strong>SOLD TO {w_team['name']}</strong>\n💸 {format_price(b['amount'])}"

    try: await context.bot.edit_message_caption(chat_id, auc['msg_id'], caption=cap, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    except: await context.bot.send_message(chat_id, text=cap, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def rtm_window_timer(context, chat_id):
    try:
        await asyncio.sleep(10)
        auc = auctions[group_map[chat_id]]
        if not auc.get("rtm_claimants"):
             # No claims -> Show Sold
             kb = [[InlineKeyboardButton("✅ MARK SOLD", callback_data="CONFIRM_SOLD"), InlineKeyboardButton("REBID 🔄", callback_data="REBID")]]
             await context.bot.send_message(chat_id, "⏳ Time Up! Confirm Sale.", reply_markup=InlineKeyboardMarkup(kb))
    except asyncio.CancelledError: pass

async def bid_handler(update, context):
    query = update.callback_query
    chat_id = update.effective_chat.id
    user_id = query.from_user.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    
    if query.data == "BID":
        my_team = None
        for t in auc['teams'].values():
            if t['owner'] == user_id: my_team = t; break
        if not my_team: return await query.answer("No Team!", show_alert=True)
        if auc['current_bid']['holder'] == user_id: return await query.answer("Winning!", show_alert=True)

        curr = auc['current_bid']['amount']
        new_amt = curr if auc['current_bid']['holder'] is None else curr + get_increment(curr)
        if my_team['purse'] < new_amt: return await query.answer("Low Funds!", show_alert=True)

        # KILL OLD TIMER
        if auc.get('timer_task'): auc['timer_task'].cancel()
        auc['current_bid'] = {"amount": new_amt, "holder": user_id, "holder_team": my_team['name']}
        auc['timer_task'] = asyncio.create_task(auction_timer(context, chat_id))
        
        await query.answer(f"Bid: {format_price(new_amt)}")
        next_bid = new_amt + get_increment(new_amt)
        kb = [[InlineKeyboardButton(f"BID {format_price(next_bid)}", callback_data="BID")], [InlineKeyboardButton("SKIP", callback_data="SKIP")]]
        auc['last_kb'] = InlineKeyboardMarkup(kb)
        await update_ui(context, chat_id, "⏳ <strong>Timer Reset!</strong>")

    elif query.data == "NEXT":
        if user_id in auc['admins']: await show_next_player(context, chat_id)
        
    elif query.data == "RANDOM":
        if user_id in auc['admins']: await show_next_player(context, chat_id, random_pick=True)
    
    elif query.data == "REBID":
        if user_id in auc['admins']: 
            auc['current_index'] -= 1
            await show_next_player(context, chat_id)
            
    elif query.data == "CONFIRM_SOLD":
        if user_id in auc['admins']:
            # Officially finalize sale logic here if coming from RTM timeout
             b = auc['current_bid']
             w_team = None
             for t in auc['teams'].values():
                 if t['name'] == b['holder_team']: w_team = t; break
             if w_team:
                 p = auc['players'][auc['current_index']]
                 w_team['purse'] -= b['amount']
                 w_team['squad'].append({'name': p['Name'], 'price': b['amount'], 'type': 'auction', 'rtm': False})
                 
             kb = [[InlineKeyboardButton("NEXT ⏭️", callback_data="NEXT")]]
             await context.bot.send_message(chat_id, f"✅ Finalized to {w_team['name']}", reply_markup=InlineKeyboardMarkup(kb))
    
    elif query.data == "CLAIM_RTM":
        my_team_code = None
        for c, t in auc['teams'].items():
            if t['owner'] == user_id: my_team_code = c; break
        if not my_team_code: return await query.answer("No team")
        
        auc.setdefault('rtm_claimants', {})[user_id] = my_team_code
        await query.answer("Claimed!")
        # Update Admin text
        names = ", ".join([auc['teams'][c]['name'] for c in auc['rtm_claimants'].values()])
        try: await context.bot.edit_message_text(chat_id, auc['rtm_admin_msg_id'], text=f"👀 Claims: {names}")
        except: pass

    # RTM HIKE LOGIC
    if query.data == "DO_HIKE":
         auc['rtm_state'] = "WAITING_HIKE"
         await context.bot.send_message(chat_id, "Type new price:")
    
    if query.data == "NO_HIKE":
         # Logic: Transfer player to RTM team at sold price
         rtm_team = auc['teams'][auc['rtm_data']['code']]
         sold_p = auc['players'][auc['current_index']]
         price = sold_p['SoldPrice'] # Need to ensure this was saved
         # Refund winner, Charge RTM team
         pass # Logic similar to manual RTM handler

# --- MANUAL RTM ---
async def manual_rtm(update, context):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    t_name = " ".join(context.args)
    code, team = get_team_by_name(auc, t_name)
    if not team: return await update.message.reply_text("Team not found")
    
    auc['rtm_state'] = "HIKE_DECISION"
    auc['rtm_data'] = {'team': team, 'code': code}
    kb = [[InlineKeyboardButton("HIKE", callback_data="DO_HIKE"), InlineKeyboardButton("NO HIKE", callback_data="NO_HIKE")]]
    await context.bot.send_message(chat_id, f"✋ <strong>RTM by {team['name']}</strong>\nWinner: Hike?", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def message_handler(update, context):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    
    if auc.get('rtm_state') == "WAITING_HIKE":
        try:
            new_p = parse_price(update.message.text)
            # Logic to process hike:
            # 1. Update Bid to new_p
            # 2. Ask RTM Team (Match/Quit)
            auc['rtm_state'] = "MATCH_DECISION"
            kb = [[InlineKeyboardButton("MATCH", callback_data="RTM_MATCH"), InlineKeyboardButton("QUIT", callback_data="RTM_QUIT")]]
            await context.bot.send_message(chat_id, f"📈 Bid: {format_price(new_p)}. Match?", reply_markup=InlineKeyboardMarkup(kb))
        except: pass

async def end_auction_btn(update, context):
    kb = [[InlineKeyboardButton("CONFIRM END", callback_data="CONFIRM_END")]]
    await update.message.reply_text("End Auction?", reply_markup=InlineKeyboardMarkup(kb))

# --- SERVER ---
app = Flask(__name__)
@app.route('/')
def index(): return "Bot Alive"
def run_web(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

if __name__ == '__main__':
    Thread(target=run_web).start()
    bot = ApplicationBuilder().token(TOKEN).build()
    
    setup = ConversationHandler(
        entry_points=[CommandHandler('start', start_setup)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT, ask_purse)],
            ASK_PURSE: [MessageHandler(filters.TEXT, ask_rtm)],
            ASK_RTM_COUNT: [MessageHandler(filters.TEXT, ask_file)],
            ASK_FILE: [MessageHandler(filters.Document.ALL, finish_setup)]
        },
        fallbacks=[CommandHandler('cancel', cancel_setup)]
    )
    
    bot.add_handler(setup)
    bot.add_handler(CommandHandler('createteam', create_team))
    bot.add_handler(CommandHandler('init', init_group))
    bot.add_handler(CommandHandler('promote', promote_admin))
    bot.add_handler(CommandHandler('secondowner', second_owner_cmd))
    bot.add_handler(CommandHandler('register', register))
    bot.add_handler(CommandHandler('start_auction', start_auction))
    bot.add_handler(CommandHandler('rtm', manual_rtm))
    bot.add_handler(CommandHandler('end_auction', end_auction_btn))
    bot.add_handler(CommandHandler('transfer', transfer_team))
    bot.add_handler(CommandHandler('retain', retain_player))
    bot.add_handler(CommandHandler('rtmedit', edit_rtm))
    bot.add_handler(CommandHandler('summary', summary_cmd))
    bot.add_handler(CommandHandler(['stats', 'team'], team_stats))
    
    bot.add_handler(CallbackQueryHandler(bid_handler))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    print("Bot Running...")
    bot.run_polling()