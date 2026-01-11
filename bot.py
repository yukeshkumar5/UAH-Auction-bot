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

# --- CONVERSATION STATES ---
ASK_NAME, ASK_PURSE, ASK_RTM_COUNT, ASK_FILE = range(4)

# --- MULTI-GROUP DATA STORE ---
# pending_setups = { 'ROOM_CODE': { ...setup data... } }
pending_setups = {} 

# active_auctions = { GROUP_CHAT_ID: { ...all game data... } }
active_auctions = {}

# user_setup_state = { ADMIN_USER_ID: { ...temp setup data... } }
user_setup_state = {}

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

def generate_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def generate_team_code():
    return ''.join(random.choices(string.ascii_uppercase, k=4))

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

# --- SETUP HANDLERS (DM) ---

async def start_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        await update.message.reply_text("⚠️ DM me to start setup!")
        return ConversationHandler.END
    
    user_id = update.effective_user.id
    user_setup_state[user_id] = {"admin_id": user_id}
    
    await update.message.reply_text("🛠 <strong>Auction Setup</strong>\n\n1. Enter Auction Name:", parse_mode='HTML')
    return ASK_NAME

async def ask_purse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_setup_state[user_id]["name"] = update.message.text
    await update.message.reply_text("2. Enter Default Purse (e.g., <code>100 C</code>):", parse_mode='HTML')
    return ASK_PURSE

async def ask_rtm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_setup_state[user_id]["purse"] = parse_price(update.message.text)
    await update.message.reply_text("3. <strong>How many RTMs per team?</strong> (0 for none)", parse_mode='HTML')
    return ASK_RTM_COUNT

async def ask_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try: val = int(update.message.text)
    except: val = 0
    user_setup_state[user_id]["rtm_limit"] = val
    await update.message.reply_text(f"✅ RTM Limit: {val}\n4. <strong>Upload CSV/Excel file.</strong>", parse_mode='HTML')
    return ASK_FILE

async def finish_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # File Processing
    file_obj = await update.message.document.get_file()
    file_name = update.message.document.file_name
    temp_path = f"temp_{user_id}.xlsx"
    
    if file_name.endswith('.csv'):
        temp_path = f"temp_{user_id}.csv"
        await file_obj.download_to_drive(temp_path)
        try: df = pd.read_csv(temp_path)
        except: df = pd.read_csv(temp_path, encoding='latin1')
    else:
        await file_obj.download_to_drive(temp_path)
        df = pd.read_excel(temp_path)
    
    # Cleanup file immediately
    if os.path.exists(temp_path): os.remove(temp_path)

    try:
        players = normalize_player_data(df)
        room_id = generate_code()
        
        # Save to Pending Setups
        pending_setups[room_id] = {
            "admin_id": user_id,
            "auction_name": user_setup_state[user_id]["name"],
            "default_purse": user_setup_state[user_id]["purse"],
            "rtm_limit": user_setup_state[user_id]["rtm_limit"],
            "players": players
        }
        
        # Clear temp user state
        del user_setup_state[user_id]
        
        await update.message.reply_text(
            f"✅ <strong>Ready!</strong>\n🆔 Room ID: <code>{room_id}</code>\n\nGo to Group -> <code>/init {room_id}</code>",
            parse_mode='HTML'
        )
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        return ConversationHandler.END

async def cancel_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# --- GROUP MANAGEMENT ---

async def init_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    args = context.args
    
    if not args: return await update.message.reply_text("Usage: <code>/init ROOM_ID</code>", parse_mode='HTML')
    room_id = args[0]
    
    if room_id not in pending_setups:
        return await update.message.reply_text("❌ Invalid Room ID.")
        
    setup_data = pending_setups[room_id]
    
    if setup_data["admin_id"] != user_id:
        return await update.message.reply_text("❌ Only the Creator can init.")
        
    # INITIALIZE NEW AUCTION INSTANCE
    active_auctions[chat_id] = {
        "admin_id": setup_data["admin_id"],
        "auction_name": setup_data["auction_name"],
        "default_purse": setup_data["default_purse"],
        "rtm_limit": setup_data["rtm_limit"],
        "players": setup_data["players"],
        "teams": {},
        "is_active": False,
        "is_paused": False,
        "current_index": -1,
        "current_bid": {"amount": 0, "holder": None, "holder_team": None},
        "skip_voters": set(),
        "rtm_state": None,
        "rtm_claimants": {},
        "msg_id": None,
        "timer_task": None,
        "auto_next_task": None
    }
    
    # Remove from pending to free space
    del pending_setups[room_id]
    
    await update.message.reply_text(f"✅ <strong>Connected: {setup_data['auction_name']}</strong>", parse_mode='HTML')

async def create_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in active_auctions: return
    auction = active_auctions[chat_id]
    
    if update.effective_user.id != auction["admin_id"]: return
    
    name = " ".join(context.args)
    if not name: return
    
    code = generate_team_code()
    auction['teams'][code] = {
        'name': name, 
        'owner': None, 
        'owner_name': "Vacant", 
        'purse': auction['default_purse'], 
        'squad': [], 
        'rtms_used': 0
    }
    await update.message.reply_text(f"✅ Team: <strong>{name}</strong>\nCode: <code>{code}</code>", parse_mode='HTML')

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in active_auctions: return
    auction = active_auctions[chat_id]
    
    if not context.args: return
    code = context.args[0]
    user_id = update.effective_user.id
    
    if code not in auction['teams']: return await update.message.reply_text("❌ Invalid Code")
    if auction['teams'][code]['owner']: return await update.message.reply_text("⚠️ Taken!")
    
    # Check duplicate
    for t in auction['teams'].values():
        if t['owner'] == user_id:
            return await update.message.reply_text("🚫 You already have a team!")

    auction['teams'][code]['owner'] = user_id
    auction['teams'][code]['owner_name'] = update.effective_user.first_name
    await update.message.reply_text(f"🎉 Joined <strong>{auction['teams'][code]['name']}</strong>!", parse_mode='HTML')

# --- AUCTION LOGIC (MULTI-INSTANCE) ---

async def start_auction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in active_auctions: return
    auction = active_auctions[chat_id]
    
    if update.effective_user.id != auction["admin_id"]: return
    
    auction["is_active"] = True
    await update.message.reply_text("📢 <strong>AUCTION STARTING!</strong>", parse_mode='HTML')
    await asyncio.sleep(2)
    await show_next_player(context, chat_id)

async def show_next_player(context, chat_id):
    if chat_id not in active_auctions: return
    auction = active_auctions[chat_id]
    
    try:
        # Cancel tasks if running
        if auction.get('timer_task'): auction['timer_task'].cancel()
        if auction.get('auto_next_task'): auction['auto_next_task'].cancel()
        
        if auction["is_paused"]: return

        auction["current_index"] += 1
        auction["skip_voters"] = set()
        auction["rtm_state"] = None
        auction["rtm_claimants"] = {}
        
        if auction["current_index"] >= len(auction["players"]):
            await context.bot.send_message(chat_id, "🏁 <strong>Auction Finished!</strong>", parse_mode='HTML')
            await end_auction_logic(context, chat_id)
            return

        player = auction["players"][auction["current_index"]]
        base_price = player.get('BasePrice', 20)
        auction["current_bid"] = {"amount": base_price, "holder": None, "holder_team": None}
        
        loop = asyncio.get_event_loop()
        img_url = await loop.run_in_executor(None, get_player_image, player['Name'])
        
        caption = (
            f"💎 <strong>LOT #{auction['current_index']+1}</strong>\n"
            f"🏏 <strong>{player['Name']}</strong>\n"
            f"🌍 {player.get('Country','')} | 🏏 {player.get('Role','')}\n\n"
            f"💰 <strong>Base Price:</strong> {format_price(base_price)}\n"
            f"⏳ <strong>30 Seconds Clock</strong>"
        )
        
        kb = [[InlineKeyboardButton(f"BID {format_price(base_price)}", callback_data="BID")], [InlineKeyboardButton("SKIP", callback_data="SKIP")]]
        
        msg = await context.bot.send_photo(chat_id, photo=img_url, caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        auction["msg_id"] = msg.message_id
        auction['timer_task'] = asyncio.create_task(auction_timer(context, chat_id))
    
    except Exception as e:
        print(f"Error: {e}")

async def auction_timer(context, chat_id):
    try:
        await asyncio.sleep(22)
        await update_timer(context, chat_id, "⚠️ <strong>8 Seconds!</strong>")
        await asyncio.sleep(3)
        await update_timer(context, chat_id, "⚠️ <strong>5 Seconds!</strong>")
        await asyncio.sleep(3)
        await update_timer(context, chat_id, "⚠️ <strong>2 Seconds!</strong>")
        await asyncio.sleep(2)
        
        if chat_id in active_auctions:
            auction = active_auctions[chat_id]
            if auction['current_bid']['holder'] is None:
                await handle_sold_result(context, chat_id, sold=False)
            else:
                await trigger_rtm_phase(context, chat_id)
    except asyncio.CancelledError: pass

async def update_timer(context, chat_id, text):
    if chat_id not in active_auctions: return
    auction = active_auctions[chat_id]
    try:
        # Reconstruct caption
        p = auction["players"][auction["current_index"]]
        b = auction["current_bid"]
        info = f"🔨 <strong>Current:</strong> {format_price(b['amount'])} ({b['holder_team']})" if b['holder'] else f"💰 <strong>Base:</strong> {format_price(p['BasePrice'])}"
        
        await context.bot.edit_message_caption(chat_id, auction["msg_id"], caption=f"💎 <strong>{p['Name']}</strong>\n{info}\n{text}", reply_markup=auction.get('last_kb'), parse_mode='HTML')
    except: pass

async def trigger_rtm_phase(context, chat_id):
    auction = active_auctions[chat_id]
    auction["rtm_state"] = "CLAIMING"
    
    await context.bot.send_message(chat_id, "🔴 <strong>SOLD! But RTM Window Open (10s)!</strong>", parse_mode='HTML')
    kb = [[InlineKeyboardButton("✋ CLAIM RTM", callback_data="CLAIM_RTM"), InlineKeyboardButton("REBID 🔄", callback_data="REBID")]]
    
    try: await context.bot.edit_message_reply_markup(chat_id, auction["msg_id"], reply_markup=InlineKeyboardMarkup(kb))
    except: pass
    
    rtm_msg = await context.bot.send_message(chat_id, "👀 <strong>Claimants:</strong> None", parse_mode='HTML')
    auction["rtm_admin_msg_id"] = rtm_msg.message_id
    
    auction['auto_next_task'] = asyncio.create_task(rtm_window_timer(context, chat_id))

async def rtm_window_timer(context, chat_id):
    try:
        await asyncio.sleep(10)
        if chat_id in active_auctions:
            auction = active_auctions[chat_id]
            # Hide Buttons
            try: await context.bot.edit_message_reply_markup(chat_id, auction["msg_id"], reply_markup=None)
            except: pass
            
            if not auction["rtm_claimants"]:
                # Auto Finalize
                try: await context.bot.edit_message_text(chat_id, auction["rtm_admin_msg_id"], text="❌ No RTM Claims.")
                except: pass
                await handle_sold_result(context, chat_id, sold=True)
            else:
                await context.bot.send_message(chat_id, "⏳ <strong>Time Up!</strong> Admin, select RTM.", parse_mode='HTML')
                auction["rtm_state"] = "SELECTING"
    except asyncio.CancelledError: pass

async def handle_sold_result(context, chat_id, sold):
    if chat_id not in active_auctions: return
    auction = active_auctions[chat_id]
    player = auction["players"][auction["current_index"]]
    
    kb = [[InlineKeyboardButton("REBID 🔄", callback_data="REBID")]]
    
    if not sold:
        player['Status'] = 'Unsold'
        caption = f"❌ <strong>UNSOLD</strong>\n\n🏏 <strong>{player['Name']}</strong>\n💰 Base: {format_price(player['BasePrice'])}\n\n<i>Next in 10s...</i>"
    else:
        holder = auction['current_bid']['holder']
        amt = auction['current_bid']['amount']
        
        # Find winner team object
        winner_team = None
        for t in auction['teams'].values():
            if t['owner'] == holder:
                winner_team = t
                break
        
        if winner_team:
            winner_team['purse'] -= amt
            winner_team['squad'].append({'name': player['Name'], 'price': amt})
            player['Status'] = 'Sold'
            player['SoldPrice'] = amt
            player['SoldTo'] = winner_team['name']
            
            caption = f"🔴 <strong>SOLD TO {winner_team['name']}</strong> 🔴\n\n👤 <strong>{player['Name']}</strong>\n💸 {format_price(amt)}\n💰 Bal: {format_price(winner_team['purse'])}\n\n<i>Next in 10s...</i>"

    try: 
        await context.bot.edit_message_caption(chat_id, auction["msg_id"], caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        # Clean Admin Panel
        if auction["rtm_admin_msg_id"]: await context.bot.delete_message(chat_id, auction["rtm_admin_msg_id"])
    except: pass
    
    auction['auto_next_task'] = asyncio.create_task(auto_advance(context, chat_id))

async def auto_advance(context, chat_id):
    await asyncio.sleep(10)
    await show_next_player(context, chat_id)

# --- BID HANDLER (ROUTING) ---

async def bid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = update.effective_chat.id
    user_id = query.from_user.id
    
    if chat_id not in active_auctions:
        return await query.answer("Auction Expired")
    
    auction = active_auctions[chat_id]
    
    # RTM / ADMIN ACTIONS
    if "RTM" in data or "GRANT" in data or data == "NO_HIKE":
        await handle_rtm_logic(update, context, chat_id)
        return

    if data == "REBID":
        if user_id != auction["admin_id"]: return await query.answer("Admin Only")
        if auction.get('auto_next_task'): auction['auto_next_task'].cancel()
        
        # Refund Logic
        p = auction["players"][auction["current_index"]]
        if p['Status'] == 'Sold':
            for t in auction['teams'].values():
                if t['name'] == p['SoldTo']:
                    t['purse'] += p['SoldPrice']
                    t['squad'] = [x for x in t['squad'] if x['name'] != p['Name']]
                    break
        
        auction["current_index"] -= 1
        await query.answer("Rebidding...")
        await show_next_player(context, chat_id)
        return

    # SKIP LOGIC
    if data == "SKIP":
        my_team = None
        for t in auction['teams'].values():
            if t['owner'] == user_id: my_team = t; break
            
        if not my_team: return await query.answer("No Team")
        if auction['current_bid']['holder'] == user_id: return await query.answer("Leader can't skip", show_alert=True)
        if user_id in auction["skip_voters"]: return await query.answer("Voted")
        
        auction["skip_voters"].add(user_id)
        active_teams = len([t for t in auction['teams'].values() if t['owner']])
        if len(auction["skip_voters"]) >= active_teams:
            if auction.get('timer_task'): auction['timer_task'].cancel()
            await handle_sold_result(context, chat_id, sold=False)
        else:
            await query.answer(f"Skip: {len(auction['skip_voters'])}/{active_teams}")
        return

    # BID LOGIC
    if data == "BID":
        my_team = None
        for t in auction['teams'].values():
            if t['owner'] == user_id: my_team = t; break
            
        if not my_team: return await query.answer("No Team")
        if auction['current_bid']['holder'] == user_id: return await query.answer("Wait!", show_alert=True)
        
        curr = auction['current_bid']['amount']
        new_amt = curr + get_increment(curr) if auction['current_bid']['holder'] else curr
        
        if my_team['purse'] < new_amt: return await query.answer("Low Funds!", show_alert=True)
        
        auction['current_bid'] = {"amount": new_amt, "holder": user_id, "holder_team": my_team['name']}
        auction["skip_voters"] = set()
        await query.answer(f"Bid {format_price(new_amt)}")
        
        if auction.get('timer_task'): auction['timer_task'].cancel()
        auction['timer_task'] = asyncio.create_task(auction_timer(context, chat_id))
        
        p = auction["players"][auction["current_index"]]
        kb = [[InlineKeyboardButton(f"BID {format_price(new_amt + get_increment(new_amt))}", callback_data="BID")], [InlineKeyboardButton("SKIP", callback_data="SKIP")]]
        cap = f"💎 <strong>{p['Name']}</strong>\n🔨 <strong>Current:</strong> {format_price(new_amt)} ({my_team['name']})\n⏳ <strong>Reset 30s</strong>"
        
        auction['last_kb'] = InlineKeyboardMarkup(kb)
        try: await context.bot.edit_message_caption(chat_id, auction["msg_id"], caption=cap, reply_markup=auction['last_kb'], parse_mode='HTML')
        except: pass

async def handle_rtm_logic(update, context, chat_id):
    auction = active_auctions[chat_id]
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    
    if data == "CLAIM_RTM":
        my_team_code = None
        my_team = None
        for code, t in auction['teams'].items():
            if t['owner'] == user_id:
                my_team_code = code
                my_team = t
                break
        
        if not my_team: return await query.answer("No Team")
        if user_id == auction['current_bid']['holder']: return await query.answer("Winner cannot RTM!", show_alert=True)
        if my_team['rtms_used'] >= auction['rtm_limit']: return await query.answer("No RTMs left!", show_alert=True)
        
        if auction.get('auto_next_task'): auction['auto_next_task'].cancel()
        
        auction["rtm_claimants"][user_id] = my_team_code
        await query.answer("Claimed!")
        
        # Update Admin Panel
        names = ", ".join([auction['teams'][c]['name'] for c in auction['rtm_claimants'].values()])
        kb = []
        row = []
        for uid, c in auction['rtm_claimants'].items():
            row.append(InlineKeyboardButton(f"Grant {auction['teams'][c]['name']}", callback_data=f"GRANT_{c}"))
            if len(row)==2: kb.append(row); row=[]
        if row: kb.append(row)
        kb.append([InlineKeyboardButton("❌ Reject All", callback_data="RTM_REJECT")])
        
        try: await context.bot.edit_message_text(chat_id=chat_id, message_id=auction["rtm_admin_msg_id"], text=f"👀 <strong>Claimants:</strong> {names}", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        except: pass
        return

    if "GRANT_" in data:
        if user_id != auction["admin_id"]: return await query.answer("Admin Only")
        code = data.split("_")[1]
        auction["selected_rtm_team"] = code
        auction["rtm_state"] = "WAITING_HIKE"
        winner = auction['current_bid']['holder_team']
        await context.bot.send_message(chat_id, f"✅ <strong>{auction['teams'][code]['name']}</strong> selected!\n👑 <strong>{winner}</strong>, Type price or:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("No Hike", callback_data="NO_HIKE")]]), parse_mode='HTML')
        return

    if data == "RTM_REJECT":
        if user_id != auction["admin_id"]: return await query.answer("Admin Only")
        await context.bot.send_message(chat_id, "❌ RTM Rejected.")
        await handle_sold_result(context, chat_id, sold=True)
        return

    if data == "NO_HIKE":
        if user_id != auction['current_bid']['holder']: return await query.answer("Not Winner")
        rtm_t = auction['teams'][auction['selected_rtm_team']]
        sold_p = auction['current_bid']['amount']
        rtm_t['rtms_used'] += 1
        await context.bot.send_message(chat_id, f"📉 No Hike.\n<strong>{rtm_t['name']}</strong> wins via RTM @ {format_price(sold_p)}!", parse_mode='HTML')
        auction['current_bid']['holder'] = rtm_t['owner']
        auction['current_bid']['holder_team'] = rtm_t['name']
        await handle_sold_result(context, chat_id, sold=True)
        return

    if data in ["RTM_MATCH", "RTM_QUIT"]:
        rtm_t = auction['teams'][auction['selected_rtm_team']]
        if user_id != rtm_t['owner']: return await query.answer("Not RTM Team")
        
        if data == "RTM_QUIT":
            await context.bot.send_message(chat_id, f"🏳️ <strong>{rtm_t['name']}</strong> QUITS!", parse_mode='HTML')
            await handle_sold_result(context, chat_id, sold=True)
        else:
            p = auction['current_bid']['amount']
            if rtm_t['purse'] < p: return await query.answer("Low Funds!", show_alert=True)
            rtm_t['rtms_used'] += 1
            auction['current_bid']['holder'] = user_id
            auction['current_bid']['holder_team'] = rtm_t['name']
            await context.bot.send_message(chat_id, f"✅ <strong>{rtm_t['name']}</strong> MATCHED!", parse_mode='HTML')
            await handle_sold_result(context, chat_id, sold=True)

# --- TEXT HANDLER ---
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in active_auctions: return
    auction = active_auctions[chat_id]
    
    if auction.get("rtm_state") != "WAITING_HIKE": return
    if update.effective_user.id != auction['current_bid']['holder']: return
    
    try:
        new_p = parse_price(update.message.text)
        curr = auction['current_bid']['amount']
        if new_p <= curr: return await update.message.reply_text(f"⚠️ Must be > {format_price(curr)}")
        
        # Check winner balance
        winner_team = None
        for t in auction['teams'].values():
            if t['owner'] == update.effective_user.id: winner_team = t; break
            
        if winner_team['purse'] < new_p: return await update.message.reply_text("❌ Not enough funds")
        
        auction['current_bid']['amount'] = new_p
        auction["rtm_state"] = "WAITING_MATCH"
        
        rtm_name = auction['teams'][auction['selected_rtm_team']]['name']
        await context.bot.send_message(chat_id, f"📈 Bid Raised to {format_price(new_p)}!\n🚨 <strong>{rtm_name}</strong>, MATCH or QUIT?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Match", callback_data="RTM_MATCH"), InlineKeyboardButton("Quit", callback_data="RTM_QUIT")]]), parse_mode='HTML')
    except: pass

# --- CLEANUP ---
async def end_auction_logic(context, chat_id):
    if chat_id not in active_auctions: return
    auction = active_auctions[chat_id]
    
    # Generate Report
    report = f"🏆 <strong>{auction['auction_name']} SUMMARY</strong> 🏆\n\n"
    for code, t in auction['teams'].items():
        report += f"🛡 <strong>{t['name']}</strong>\n💰 Bal: {format_price(t['purse'])}\n📜 Players:\n"
        for p in t['squad']:
            report += f"   • {p['name']} ({format_price(p['price'])})\n"
        report += "\n"
        
    # Send to Admin
    try: await context.bot.send_message(auction["admin_id"], report, parse_mode='HTML')
    except: await context.bot.send_message(chat_id, "❌ Could not DM Admin.")
    
    await context.bot.send_message(chat_id, "🛑 Auction Ended. Data cleared.", parse_mode='HTML')
    
    # Delete from memory
    del active_auctions[chat_id]

async def end_auction_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in active_auctions:
        if update.effective_user.id == active_auctions[chat_id]["admin_id"]:
            await end_auction_logic(context, chat_id)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "📚 <strong>COMMANDS</strong>\n\n/start - Setup (DM)\n/init [ID] - Connect Group\n/createteam [Name]\n/register [Code]\n/start_auction\n/end_auction\n/stats /teams /team\n/check [Name]\n/completed"
    await update.message.reply_text(msg, parse_mode='HTML')

# --- SERVER ---
app = Flask(__name__)
@app.route('/')
def index(): return "Multi-Instance Bot Active"
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
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("init", init_group))
    app.add_handler(CommandHandler("createteam", create_team))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("start_auction", start_auction))
    app.add_handler(CommandHandler("end_auction", end_auction_command))
    
    # Stats Aliases
    app.add_handler(CommandHandler(["stats", "teams", "team"], lambda u,c: team_stats_logic(u,c)))
    
    app.add_handler(CallbackQueryHandler(bid_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    print("Multi-Group Bot is Live...")
    app.run_polling()

# Need to define team_stats_logic separately for aliases to work
async def team_stats_logic(update, context):
    chat_id = update.effective_chat.id
    if chat_id not in active_auctions: return
    auction = active_auctions[chat_id]
    
    msg = "📊 <strong>TEAMS</strong>\n\n"
    for t in auction['teams'].values():
        msg += f"🛡 <strong>{t['name']}</strong>: {format_price(t['purse'])} | 👥 {len(t['squad'])}\n"
    await update.message.reply_text(msg, parse_mode='HTML')