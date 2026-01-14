import logging
import os
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    ContextTypes, ConversationHandler, filters
)
import database as db
import helpers
from keep_alive import keep_alive

# --- TOKEN ---
TOKEN = "8555822248:AAE76zDM4g-e_Ti3Zwg3k4TTEico-Ewyas0"

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- STATES ---
ASK_NAME, ASK_BUDGET, ASK_FILE = range(3)

# --- DB HELPERS ---
def get_room(group_id):
    conn = db.get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM auctions WHERE group_id=%s", (group_id,))
    row = c.fetchone()
    conn.close()
    return row

def is_admin(room_id, user_id):
    conn = db.get_connection()
    c = conn.cursor()
    c.execute("SELECT 1 FROM auctions WHERE room_id=%s AND owner_id=%s", (room_id, user_id))
    owner = c.fetchone()
    c.execute("SELECT 1 FROM admins WHERE room_id=%s AND user_id=%s", (room_id, user_id))
    admin = c.fetchone()
    conn.close()
    return owner or admin

# --- SETUP (DM) ---
async def start_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != 'private':
        await update.message.reply_text("⚠️ DM me to set up an auction.")
        return ConversationHandler.END
    await update.message.reply_text("👋 **Auction Setup**\n\n1. Enter **Auction Name**:")
    return ASK_NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['name'] = update.message.text
    await update.message.reply_text(f"✅ Name: {context.user_data['name']}\n\n2. Enter **Budget per Team** (in C):\nExample: `200` for 200 Crores.")
    return ASK_BUDGET

async def get_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.upper().replace("C", "").strip()
    try:
        val = float(text)
        # Assuming input 200 means 200 Crores -> 20000 Lakhs
        lakhs = int(val * 100)
    except:
        await update.message.reply_text("⚠️ Invalid number. Try again.")
        return ASK_BUDGET
        
    context.user_data['budget'] = lakhs
    await update.message.reply_text("3. Upload **Excel/CSV File**.\n(Columns: `Player Name`, `Base Price`, `Role`)")
    return ASK_FILE

async def process_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await update.message.document.get_file()
    path = f"temp_{update.message.from_user.id}.xlsx"
    await file.download_to_drive(path)
    
    try:
        df = pd.read_csv(path) if path.endswith('.csv') else pd.read_excel(path)
        df.columns = [c.strip().lower() for c in df.columns]
        
        if not any('name' in c for c in df.columns):
            await update.message.reply_text("❌ Error: Column 'Player Name' not found.")
            return ConversationHandler.END

        room_id = helpers.generate_code("ROOM")
        conn = db.get_connection()
        c = conn.cursor()
        
        # Insert Auction & Admin
        c.execute("INSERT INTO auctions (room_id, owner_id, name, budget_lakhs) VALUES (%s,%s,%s,%s)",
                  (room_id, update.message.from_user.id, context.user_data['name'], context.user_data['budget']))
        c.execute("INSERT INTO admins (room_id, user_id) VALUES (%s,%s)", (room_id, update.message.from_user.id))

        # Map Columns
        name_col = next((c for c in df.columns if 'name' in c), None)
        price_col = next((c for c in df.columns if 'price' in c), None)
        role_col = next((c for c in df.columns if 'position' in c or 'role' in c), None)
        country_col = next((c for c in df.columns if 'country' in c), None)

        count = 0
        for _, row in df.iterrows():
            name = str(row[name_col]).strip()
            price = helpers.parse_price_to_lakhs(str(row[price_col])) if price_col else 0
            role = str(row[role_col]) if role_col else "Player"
            country = str(row[country_col]) if country_col else ""
            
            c.execute("INSERT INTO players (room_id, name, base_price_lakhs, role, country) VALUES (%s,%s,%s,%s,%s)",
                      (room_id, name, price, role, country))
            count += 1
            
        conn.commit()
        conn.close()
        os.remove(path)
        
        await update.message.reply_text(
            f"✅ **Auction Ready!**\n🆔 Room ID: `{room_id}`\n👥 Players: {count}\n\n"
            f"**Next Steps:**\n1. Add me to your group.\n2. Type `/init {room_id}` in the group."
        , parse_mode='Markdown')
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
        return ConversationHandler.END

# --- ADMIN COMMANDS ---
async def create_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    name = " ".join(context.args)
    conn = db.get_connection()
    c = conn.cursor()
    c.execute("SELECT room_id FROM auctions WHERE owner_id=%s ORDER BY ctid DESC LIMIT 1", (update.message.from_user.id,))
    res = c.fetchone()
    if not res: return
    
    code = helpers.generate_code("TM")
    try:
        c.execute("INSERT INTO teams (room_id, name, code) VALUES (%s,%s,%s)", (res[0], name, code))
        conn.commit()
        await update.message.reply_text(f"✅ Team: **{name}**\nCode: `{code}`", parse_mode='Markdown')
    except:
        await update.message.reply_text("Error creating team.")
    conn.close()

async def retain_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.replace("/retain ", "")
    try:
        parts = msg.rsplit(" - ", 1)
        price = helpers.parse_price_to_lakhs(parts[1])
        details = parts[0].split(" ")
        team_code = details[0]
        p_name = " ".join(details[1:])
        
        conn = db.get_connection()
        c = conn.cursor()
        c.execute("SELECT id, room_id, purse_spent_lakhs FROM teams WHERE code=%s", (team_code,))
        team = c.fetchone()
        
        if not team:
            await update.message.reply_text("❌ Invalid Team Code")
            return
            
        # Update or Insert
        c.execute("UPDATE players SET status='RETAINED', sold_price_lakhs=%s, owner_team_id=%s WHERE room_id=%s AND name=%s", 
                  (price, team[0], team[1], p_name))
        
        if c.rowcount == 0:
            c.execute("INSERT INTO players (room_id, name, status, sold_price_lakhs, owner_team_id) VALUES (%s,%s,'RETAINED',%s,%s)",
                     (team[1], p_name, price, team[0]))
                     
        c.execute("UPDATE teams SET purse_spent_lakhs = purse_spent_lakhs + %s WHERE id=%s", (price, team[0]))
        conn.commit()
        await update.message.reply_text(f"✅ **{p_name}** retained for {helpers.format_price(price)}")
        conn.close()
    except:
        await update.message.reply_text("Format: `/retain TeamCode Player Name - Price`")

async def second_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    code = context.args[0]
    sub = helpers.generate_code("SUB")
    conn = db.get_connection()
    c = conn.cursor()
    c.execute("UPDATE teams SET sub_code=%s WHERE code=%s", (sub, code))
    conn.commit()
    await update.message.reply_text(f"✅ Sub Code: `{sub}`", parse_mode='Markdown')
    conn.close()

async def rtm_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2: return
    code = context.args[0]
    count = int(context.args[1])
    conn = db.get_connection()
    c = conn.cursor()
    c.execute("UPDATE teams SET rtm_count=%s WHERE code=%s", (count, code))
    conn.commit()
    await update.message.reply_text(f"✅ RTM Count updated to {count}")
    conn.close()

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db.get_connection()
    c = conn.cursor()
    c.execute("SELECT room_id FROM auctions WHERE owner_id=%s ORDER BY ctid DESC LIMIT 1", (update.message.from_user.id,))
    res = c.fetchone()
    if not res: return
    c.execute("SELECT name, purse_spent_lakhs, rtm_count FROM teams WHERE room_id=%s", (res[0],))
    teams = c.fetchall()
    msg = "📊 **Summary**\n"
    for t in teams:
        msg += f"{t[0]}: {helpers.format_price(t[1])} | RTM: {t[2]}\n"
    await update.message.reply_text(msg, parse_mode='Markdown')
    conn.close()

# --- GROUP COMMANDS ---
async def init_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    room_id = context.args[0]
    conn = db.get_connection()
    c = conn.cursor()
    
    c.execute("SELECT room_id FROM auctions WHERE group_id=%s AND state!='ENDED'", (update.message.chat.id,))
    if c.fetchone():
        await update.message.reply_text("❌ Group already connected.")
        conn.close()
        return

    c.execute("SELECT group_id FROM auctions WHERE room_id=%s", (room_id,))
    res = c.fetchone()
    if not res:
        await update.message.reply_text("❌ Invalid Room ID")
    elif res[0]:
        await update.message.reply_text("❌ Room Taken")
    else:
        c.execute("UPDATE auctions SET group_id=%s, state='WAITING' WHERE room_id=%s", (update.message.chat.id, room_id))
        conn.commit()
        await update.message.reply_text(f"✅ Connected! Admins use `/start_auction`")
    conn.close()

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    code = context.args[0]
    user = update.message.from_user
    row = get_room(update.message.chat.id)
    if not row: return
    
    conn = db.get_connection()
    c = conn.cursor()
    if is_admin(row[0], user.id):
        await update.message.reply_text("❌ Admins cannot claim teams.")
        conn.close()
        return

    c.execute("SELECT id, name FROM teams WHERE code=%s AND room_id=%s", (code, row[0]))
    tm = c.fetchone()
    if tm:
        c.execute("UPDATE teams SET owner_id=%s, owner_name=%s WHERE id=%s", (user.id, user.first_name, tm[0]))
        conn.commit()
        await update.message.reply_text(f"✅ Owner registered for **{tm[1]}**", parse_mode='Markdown')
        conn.close()
        return

    c.execute("SELECT id, name FROM teams WHERE sub_code=%s AND room_id=%s", (code, row[0]))
    tm = c.fetchone()
    if tm:
        c.execute("UPDATE teams SET co_owner_id=%s WHERE id=%s", (user.id, tm[0]))
        conn.commit()
        await update.message.reply_text(f"✅ Co-Owner registered for **{tm[1]}**", parse_mode='Markdown')
    conn.close()

async def team_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args) if context.args else ""
    row = get_room(update.message.chat.id)
    if not row: return
    
    conn = db.get_connection()
    c = conn.cursor()
    
    if not name:
        c.execute("SELECT name, purse_spent_lakhs, rtm_count FROM teams WHERE room_id=%s", (row[0],))
        teams = c.fetchall()
        msg = "📊 **All Teams**\n"
        for t in teams:
            rem = row[4] - t[1]
            msg += f"▫️ **{t[0]}**: {helpers.format_price(rem)} Left | RTMs: {t[2]}\n"
        await update.message.reply_text(msg, parse_mode='Markdown')
    else:
        c.execute("SELECT id, name, owner_name, purse_spent_lakhs FROM teams WHERE room_id=%s AND name ILIKE %s", (row[0], f"%{name}%"))
        tm = c.fetchone()
        if not tm:
            await update.message.reply_text("❌ Team not found.")
        else:
            c.execute("SELECT name, sold_price_lakhs, status FROM players WHERE owner_team_id=%s", (tm[0],))
            players = c.fetchall()
            msg = f"🏰 **{tm[1]}**\n👤 Owner: {tm[2]}\n💰 Purse: {helpers.format_price(row[4] - tm[3])} Left\n\n"
            for p in players:
                tag = "(R)" if p[2] == 'RETAINED' else ""
                msg += f"• {p[0]} {tag} - {helpers.format_price(p[1])}\n"
            await update.message.reply_text(msg, parse_mode='Markdown')
    conn.close()

async def upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = get_room(update.message.chat.id)
    if not row: return
    conn = db.get_connection()
    c = conn.cursor()
    c.execute("SELECT name, role, base_price_lakhs FROM players WHERE room_id=%s AND status='UNSOLD' ORDER BY id ASC LIMIT 10", (row[0],))
    players = c.fetchall()
    msg = "📋 **Upcoming Players**\n"
    for p in players:
        msg += f"• {p[0]} ({p[1]}) - {helpers.format_price(p[2])}\n"
    await update.message.reply_text(msg, parse_mode='Markdown')
    conn.close()

async def promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message: return
    row = get_room(update.message.chat.id)
    if not row: return
    if row[1] != update.message.from_user.id:
        await update.message.reply_text("❌ Only Creator can promote.")
        return
    conn = db.get_connection()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO admins (room_id, user_id) VALUES (%s,%s)", (row[0], update.message.reply_to_message.from_user.id))
    conn.commit()
    await update.message.reply_text("✅ Admin Promoted.")
    conn.close()

async def check_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    name = " ".join(context.args)
    row = get_room(update.message.chat.id)
    if not row: return
    conn = db.get_connection()
    c = conn.cursor()
    c.execute("SELECT status, sold_price_lakhs FROM players WHERE room_id=%s AND name ILIKE %s", (row[0], f"%{name}%"))
    res = c.fetchone()
    if res:
        await update.message.reply_text(f"🔎 **{name}**: {res[0]} ({helpers.format_price(res[1])})", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Not found.")
    conn.close()

# --- AUCTION FLOW ---
async def start_auction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = get_room(update.message.chat.id)
    if not row: return
    if not is_admin(row[0], update.message.from_user.id): return
    
    conn = db.get_connection()
    c = conn.cursor()
    c.execute("UPDATE auctions SET state='ACTIVE' WHERE room_id=%s", (row[0],))
    conn.commit()
    conn.close()
    
    kb = [[InlineKeyboardButton("Next ▶️", callback_data="next"), InlineKeyboardButton("Random 🎲", callback_data="random")]]
    await update.message.reply_text("🚀 **Auction Started!**", reply_markup=InlineKeyboardMarkup(kb))

async def control(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.replace("/", "")
    row = get_room(update.message.chat.id)
    if not row or not is_admin(row[0], update.message.from_user.id): return
    conn = db.get_connection()
    c = conn.cursor()
    if cmd == "pause":
        c.execute("UPDATE auctions SET state='PAUSED' WHERE room_id=%s", (row[0],))
        await update.message.reply_text("⏸ Paused")
    elif cmd == "resume":
        c.execute("UPDATE auctions SET state='ACTIVE' WHERE room_id=%s", (row[0],))
        await update.message.reply_text("▶️ Resumed")
    elif cmd == "end_auction":
        kb = [[InlineKeyboardButton("✅ End Auction", callback_data="confirm_end")]]
        await update.message.reply_text("End Auction?", reply_markup=InlineKeyboardMarkup(kb))
    conn.commit()
    conn.close()

async def fast_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    name = " ".join(context.args)
    row = get_room(update.message.chat.id)
    if not row: return
    await fetch_player(update, context, row[0], mode="specific", specific_name=name)

async def fetch_player(update: Update, context: ContextTypes.DEFAULT_TYPE, room_id, mode="next", specific_name=None):
    conn = db.get_connection()
    c = conn.cursor()
    
    query = "SELECT * FROM players WHERE room_id=%s AND status='UNSOLD'"
    params = [room_id]
    
    if mode == "random":
        query += " ORDER BY RANDOM() LIMIT 1"
    elif specific_name:
        query += " AND name ILIKE %s LIMIT 1"
        params.append(f"%{specific_name}%")
    else:
        query += " ORDER BY id ASC LIMIT 1"
        
    c.execute(query, tuple(params))
    p = c.fetchone()
    
    if not p:
        await context.bot.send_message(update.effective_chat.id, "✅ End of List.")
        conn.close()
        return

    c.execute("UPDATE auctions SET current_player_id=%s, current_bid_lakhs=%s, current_bidder_id=NULL WHERE room_id=%s", 
              (p[0], p[5], room_id))
    conn.commit()
    conn.close()
    
    txt = (f"💠 **{p[2]}**\n"
           f"🏏 {p[4]} | 🌍 {p[3]}\n"
           f"💰 Base: {helpers.format_price(p[5])}")
           
    kb = [
        [InlineKeyboardButton(f"Bid {helpers.format_price(p[5])}", callback_data=f"bid_{p[5]}")],
        [InlineKeyboardButton("🔨 SOLD", callback_data="sold"), InlineKeyboardButton("❌ UNSOLD", callback_data="unsold")]
    ]
    await context.bot.send_message(update.effective_chat.id, txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def rtm_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message: return
    rtm_team_name = " ".join(context.args)
    row = get_room(update.message.chat.id)
    if not row: return
    room_id = row[0]
    sold_bidder = row[8]
    
    conn = db.get_connection()
    c = conn.cursor()
    c.execute("SELECT id, name FROM teams WHERE room_id=%s AND name ILIKE %s", (room_id, f"%{rtm_team_name}%"))
    rtm_team = c.fetchone()
    
    c.execute("SELECT id, name FROM teams WHERE (owner_id=%s OR co_owner_id=%s) AND room_id=%s", (sold_bidder, sold_bidder, room_id))
    sold_team = c.fetchone()
    
    if rtm_team and sold_team:
        kb = [[InlineKeyboardButton("⬆️ HIKE", callback_data=f"rtmhike_{rtm_team[0]}"),
               InlineKeyboardButton("⛔ NO HIKE", callback_data=f"rtmnohike_{rtm_team[0]}")]]
        await update.message.reply_text(
            f"🚨 **RTM ALERT**\nSold Team: **{sold_team[1]}**\nRTM Team: **{rtm_team[1]}**\n\n@{sold_team[1]} Owner: Hike or No Hike?",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )
    conn.close()

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    user = q.from_user
    chat_id = q.message.chat.id
    
    row = get_room(chat_id)
    if not row: return
    room_id, _, _, _, budget, _, p_id, curr_bid, curr_bidder = row
    
    conn = db.get_connection()
    c = conn.cursor()
    
    # --- ADMIN CONTROLS ---
    if data in ["next", "random", "unsold", "confirm_end"]:
        if not is_admin(room_id, user.id): 
            await q.answer("Admin Only")
            return
            
        if data == "confirm_end":
            c.execute("UPDATE auctions SET state='ENDED' WHERE room_id=%s", (room_id,))
            conn.commit()
            await q.message.edit_text("🛑 Auction Ended.")
        elif data == "unsold":
            c.execute("UPDATE players SET status='UNSOLD' WHERE id=%s", (p_id,))
            conn.commit()
            await q.message.edit_text("❌ UNSOLD")
            kb = [[InlineKeyboardButton("Next ▶️", callback_data="next")]]
            await context.bot.send_message(chat_id, "Next?", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q.message.edit_reply_markup(None)
            await fetch_player(update, context, room_id, mode=data)

    # --- SOLD ---
    elif data == "sold":
        if not is_admin(room_id, user.id): return
        if not curr_bidder: return
        
        c.execute("SELECT id, name FROM teams WHERE (owner_id=%s OR co_owner_id=%s) AND room_id=%s", (curr_bidder, curr_bidder, room_id))
        team = c.fetchone()
        
        c.execute("UPDATE players SET status='SOLD', sold_price_lakhs=%s, owner_team_id=%s WHERE id=%s", (curr_bid, team[0], p_id))
        c.execute("UPDATE teams SET purse_spent_lakhs = purse_spent_lakhs + %s WHERE id=%s", (curr_bid, team[0]))
        conn.commit()
        
        c.execute("SELECT name FROM players WHERE id=%s", (p_id,))
        p_name = c.fetchone()[0]
        
        await q.message.edit_text(f"🔨 **SOLD** to {team[1]} for {helpers.format_price(curr_bid)}\nPlayer: {p_name}", parse_mode='Markdown')
        kb = [[InlineKeyboardButton("Next ▶️", callback_data="next")]]
        await context.bot.send_message(chat_id, "Next?", reply_markup=InlineKeyboardMarkup(kb))

    # --- RTM ---
    elif data.startswith("rtmhike_"):
        rtm_team_id = int(data.split("_")[1])
        c.execute("SELECT name FROM teams WHERE id=%s", (rtm_team_id,))
        t_name = c.fetchone()[0]
        
        # Revert Old Sale
        c.execute("SELECT owner_team_id, sold_price_lakhs FROM players WHERE id=%s", (p_id,))
        old_data = c.fetchone()
        if old_data:
            c.execute("UPDATE teams SET purse_spent_lakhs = purse_spent_lakhs - %s WHERE id=%s", (old_data[1], old_data[0]))
            
        # Apply New Sale
        c.execute("UPDATE players SET owner_team_id=%s WHERE id=%s", (rtm_team_id, p_id))
        c.execute("UPDATE teams SET purse_spent_lakhs = purse_spent_lakhs + %s, rtm_count = rtm_count - 1 WHERE id=%s", (curr_bid, rtm_team_id))
        conn.commit()
        await q.message.edit_text(f"🔄 **RTM SUCCESS**! Player taken by **{t_name}**.", parse_mode='Markdown')

    elif data.startswith("rtmnohike_"):
        await q.message.edit_text("✅ No Hike. Original Sale Stands.")

    # --- BIDDING ---
    elif data.startswith("bid_"):
        amt = int(data.split("_")[1])
        c.execute("SELECT id, name, purse_spent_lakhs FROM teams WHERE (owner_id=%s OR co_owner_id=%s) AND room_id=%s", (user.id, user.id, room_id))
        team = c.fetchone()
        
        if not team:
            await q.answer("No Team!", show_alert=True)
            return
        if team[2] + amt > budget:
            await q.answer(f"Funds Low! Left: {helpers.format_price(budget - team[2])}", show_alert=True)
            return
        if curr_bidder and amt <= curr_bid:
            await q.answer("Bid Higher!", show_alert=True)
            return

        c.execute("UPDATE auctions SET current_bid_lakhs=%s, current_bidder_id=%s WHERE room_id=%s", (amt, user.id, room_id))
        conn.commit()
        
        # Increment Logic
        inc = 5
        if amt >= 1000: inc = 50
        elif amt >= 200: inc = 20
        elif amt >= 50: inc = 10
        
        kb = [
            [InlineKeyboardButton(f"Bid {helpers.format_price(amt + inc)}", callback_data=f"bid_{amt+inc}")],
            [InlineKeyboardButton("🔨 SOLD", callback_data="sold"), InlineKeyboardButton("❌ UNSOLD", callback_data="unsold")]
        ]
        
        await q.message.edit_text(
            f"💠 **PLAYER ON AUCTION**\n💰 Price: **{helpers.format_price(amt)}**\n🙋‍♂️ Bidder: **{team[1]}**", 
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )
        await q.answer()

    conn.close()

def main():
    db.init_db()
    keep_alive()
    app = Application.builder().token(TOKEN).build()
    
    # Handlers
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('start', start_setup)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT, get_name)],
            ASK_BUDGET: [MessageHandler(filters.TEXT, get_budget)],
            ASK_FILE: [MessageHandler(filters.Document.ALL, process_file)]
        },
        fallbacks=[]
    ))
    
    app.add_handler(CommandHandler("createteam", create_team))
    app.add_handler(CommandHandler("retain", retain_player))
    app.add_handler(CommandHandler("secondowner", second_owner))
    app.add_handler(CommandHandler("rtmedit", rtm_edit))
    app.add_handler(CommandHandler("summary", summary))
    
    app.add_handler(CommandHandler("init", init_group))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("promote", promote))
    app.add_handler(CommandHandler("upcoming", upcoming))
    app.add_handler(CommandHandler("check", check_player))
    app.add_handler(CommandHandler("team", team_stats))
    app.add_handler(CommandHandler("stats", team_stats))
    app.add_handler(CommandHandler("start_auction", start_auction))
    app.add_handler(CommandHandler("pause", control))
    app.add_handler(CommandHandler("resume", control))
    app.add_handler(CommandHandler("end_auction", control))
    app.add_handler(CommandHandler("now", fast_track))
    app.add_handler(CommandHandler("rtm", rtm_trigger))
    
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    print("Bot Started...")
    app.run_polling()

if __name__ == "__main__":
    main()