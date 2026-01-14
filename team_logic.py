from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from config import auctions, group_map, admin_map
from utils import generate_code, normalize_player_data, parse_price, format_price, get_auction_by_context, get_team_by_name
import pandas as pd
import os

ASK_NAME, ASK_PURSE, ASK_RTM_COUNT, ASK_FILE = range(4)

# --- SETUP ---
async def start_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        await update.message.reply_text("⚠️ Setup must be done in DM!")
        return ConversationHandler.END
    context.user_data['setup'] = {"admins": [update.effective_user.id]}
    await update.message.reply_text("🛠 <strong>Auction Setup</strong>\n1. Enter Auction Name:", parse_mode='HTML')
    return ASK_NAME

async def ask_purse(update, context):
    context.user_data['setup']['name'] = update.message.text
    await update.message.reply_text("2. Enter Default Purse (e.g., 100C):")
    return ASK_PURSE

async def ask_rtm(update, context):
    context.user_data['setup']['purse'] = parse_price(update.message.text)
    await update.message.reply_text("3. RTMs per team? (0 for none):")
    return ASK_RTM_COUNT

async def ask_file(update, context):
    try: context.user_data['setup']['rtm_limit'] = int(update.message.text)
    except: context.user_data['setup']['rtm_limit'] = 0
    await update.message.reply_text("4. Upload CSV/Excel:")
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
            "rtm_state": None, "rtm_data": {}, "timer_task": None, "last_kb": None
        }
        admin_map[user_id] = rid
        if os.path.exists(path): os.remove(path)
        await update.message.reply_text(f"✅ Ready!\n🆔 Room ID: <code>{rid}</code>\n\n1. Group: <code>/init {rid}</code>\n2. DM: <code>/createteam</code>", parse_mode='HTML')
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
        return ConversationHandler.END

async def cancel_setup(update, context):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# --- TEAM COMMANDS ---
async def create_team(update, context):
    uid = update.effective_user.id
    if uid not in admin_map: return
    auc = auctions[admin_map[uid]]
    name = " ".join(context.args)
    code = generate_code(4)
    auc['teams'][code] = {
        'name': name, 'owner': None, 'owner_name': "Vacant", 
        'sec_owner': None, 'sec_owner_name': "None", 'sub_code': None,
        'purse': auc['default_purse'], 'squad': [], 'rtms_used': 0
    }
    await update.message.reply_text(f"✅ Team: {name}\nCode: <code>{code}</code>", parse_mode='HTML')

async def register(update, context):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    code = context.args[0] if context.args else ""
    uid = update.effective_user.id
    name = update.effective_user.first_name
    
    if uid in auc['admins']: return await update.message.reply_text("Admins cannot join!")
    for t in auc['teams'].values():
        if t['owner'] == uid or t['sec_owner'] == uid: return await update.message.reply_text("Already joined!")
        
    if code in auc['teams']:
        if auc['teams'][code]['owner']: return await update.message.reply_text("Taken!")
        auc['teams'][code]['owner'] = uid
        auc['teams'][code]['owner_name'] = name
        await update.message.reply_text(f"🎉 Joined {auc['teams'][code]['name']}")
    else:
        # Check sub code
        for t in auc['teams'].values():
            if t.get('sub_code') == code:
                if t['sec_owner']: return await update.message.reply_text("Taken!")
                t['sec_owner'] = uid
                t['sec_owner_name'] = name
                await update.message.reply_text(f"🎉 Joined {t['name']} as 2nd Owner")
                return
        await update.message.reply_text("Invalid Code")

async def second_owner_cmd(update, context):
    uid = update.effective_user.id
    if uid not in admin_map: return
    auc = auctions[admin_map[uid]]
    code = context.args[0] if context.args else ""
    if code in auc['teams']:
        sub = code + "X"
        auc['teams'][code]['sub_code'] = sub
        await update.message.reply_text(f"2nd Owner Code: <code>{sub}</code>", parse_mode='HTML')

async def transfer_team(update, context):
    uid = update.effective_user.id
    if uid not in admin_map: return
    auc = auctions[admin_map[uid]]
    old = context.args[0]
    if old in auc['teams']:
        data = auc['teams'].pop(old)
        data['owner'] = None
        new_code = generate_code(4)
        auc['teams'][new_code] = data
        await update.message.reply_text(f"Transferred. New Code: <code>{new_code}</code>", parse_mode='HTML')

async def retain_player(update, context):
    uid = update.effective_user.id
    if uid not in admin_map: return
    auc = auctions[admin_map[uid]]
    try:
        # Parse: /retain CODE Player Name - Price
        full = " ".join(context.args)
        code = full.split(" ")[0]
        rest = full[len(code):].strip()
        
        if "-" in rest: name, price_str = rest.rsplit("-", 1)
        else: name = " ".join(context.args[1:-1]); price_str = context.args[-1]
        
        price = parse_price(price_str)
        if code in auc['teams']:
            auc['teams'][code]['purse'] -= price
            auc['teams'][code]['squad'].append({'name': name.strip(), 'price': price, 'type': 'retained'})
            # Remove from auction
            auc['players'] = [p for p in auc['players'] if p['Name'].lower().strip() != name.strip().lower()]
            await update.message.reply_text(f"✅ Retained {name} for {format_price(price)}")
    except: await update.message.reply_text("Usage: /retain CODE Name - Price")

async def edit_rtm_count(update, context):
    uid = update.effective_user.id
    if uid not in admin_map: return
    auc = auctions[admin_map[uid]]
    try:
        code, count = context.args[0], int(context.args[1])
        if code in auc['teams']:
            auc['teams'][code]['rtms_used'] = auc['rtm_limit'] - count
            await update.message.reply_text(f"✅ RTMs set to {count}")
    except: pass

async def full_summary_cmd(update, context):
    auc = get_auction_by_context(update)
    if not auc: return
    report = f"🏆 <strong>{auc['name']} SUMMARY</strong>\n\n"
    for t in auc['teams'].values():
        report += f"🛡 {t['name']} ({format_price(t['purse'])})\n"
        for p in t['squad']:
            tag = " (RTM)" if p.get('rtm') else ""
            report += f"- {p['name']} {format_price(p['price'])}{tag}\n"
        report += "\n"
    if len(report) > 4000:
        await update.message.reply_text(report[:4000], parse_mode='HTML')
    else:
        await update.message.reply_text(report, parse_mode='HTML')

async def team_stats(update, context):
    auc = get_auction_by_context(update)
    if not auc: return
    msg = "📊 <strong>TEAMS</strong>\n"
    for t in auc['teams'].values():
        rtm = auc['rtm_limit'] - t['rtms_used']
        msg += f"🛡 {t['name']}: {format_price(t['purse'])} | ✋ {rtm}\n"
    await update.message.reply_text(msg, parse_mode='HTML')