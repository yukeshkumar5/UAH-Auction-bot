from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from config import auctions, group_map, admin_map
from utils import generate_code, normalize_player_data, parse_price, format_price
import pandas as pd
import os

ASK_NAME, ASK_PURSE, ASK_RTM_COUNT, ASK_FILE = range(4)

async def start_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        await update.message.reply_text("⚠️ DM me for setup!")
        return ConversationHandler.END
    context.user_data['setup'] = {"admins": [update.effective_user.id]}
    await update.message.reply_text("🛠 <strong>Auction Setup</strong>\n1. Enter Auction Name:", parse_mode='HTML')
    return ASK_NAME

async def ask_purse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['setup']['name'] = update.message.text
    await update.message.reply_text("2. Enter Default Purse (e.g. 100C):")
    return ASK_PURSE

async def ask_rtm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['setup']['purse'] = parse_price(update.message.text)
    await update.message.reply_text("3. RTMs per team? (0 for none):")
    return ASK_RTM_COUNT

async def ask_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: context.user_data['setup']['rtm_limit'] = int(update.message.text)
    except: context.user_data['setup']['rtm_limit'] = 0
    await update.message.reply_text("4. Upload Player CSV/Excel:")
    return ASK_FILE

async def finish_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            "rtm_state": None, "rtm_claimants": {}, "timer_task": None, "last_kb": None
        }
        admin_map[user_id] = rid
        if os.path.exists(path): os.remove(path)
        await update.message.reply_text(f"✅ Created! Room ID: <code>{rid}</code>\nGo to group -> /init {rid}", parse_mode='HTML')
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
        return ConversationHandler.END

async def cancel_setup(update, context):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

async def create_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in admin_map: return
    auc = auctions[admin_map[uid]]
    name = " ".join(context.args)
    if not name: return await update.message.reply_text("Usage: /createteam Name")
    
    code = generate_code(4)
    auc['teams'][code] = {
        'name': name, 'owner': None, 'owner_name': "Vacant", 
        'sec_owner': None, 'sec_owner_name': "None", 'sub_code': None,
        'purse': auc['default_purse'], 'squad': [], 'rtms_used': 0
    }
    await update.message.reply_text(f"✅ Team: <strong>{name}</strong>\nCode: <code>{code}</code>", parse_mode='HTML')

async def init_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args: return
    rid = context.args[0]
    if rid in auctions:
        if auctions[rid]['connected_group'] and auctions[rid]['connected_group'] != chat_id:
             return await update.message.reply_text("❌ Code active elsewhere!")
        auctions[rid]['connected_group'] = chat_id
        group_map[chat_id] = rid
        await update.message.reply_text(f"✅ Connected: {auctions[rid]['name']}")

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    code = context.args[0] if context.args else ""
    uid = update.effective_user.id
    
    # Check Admin
    if uid in auc['admins']: return await update.message.reply_text("Admins cannot join!")
    
    # Check Duplicate
    for t in auc['teams'].values():
        if t['owner'] == uid: return await update.message.reply_text("You have a team!")
        
    if code in auc['teams']:
        if auc['teams'][code]['owner']: return await update.message.reply_text("Taken!")
        auc['teams'][code]['owner'] = uid
        auc['teams'][code]['owner_name'] = update.effective_user.first_name
        await update.message.reply_text(f"✅ Joined {auc['teams'][code]['name']}")
    else:
        await update.message.reply_text("Invalid Code")

async def team_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    
    msg = "📊 <strong>TEAMS</strong>\n"
    for t in auc['teams'].values():
        msg += f"🛡 {t['name']}: {format_price(t['purse'])} | 👥 {len(t['squad'])}\n"
    await update.message.reply_text(msg, parse_mode='HTML')