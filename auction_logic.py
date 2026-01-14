from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from config import auctions, group_map
from utils import format_price, get_player_image, get_increment, parse_price, get_team_by_name
import asyncio

async def start_auction(update, context):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    if update.effective_user.id not in auc['admins']: return
    
    auc['is_active'] = True
    await update.message.reply_text("📢 <strong>AUCTION STARTED!</strong>", parse_mode='HTML')
    await asyncio.sleep(2)
    await show_next_player(context, chat_id)

async def show_next_player(context, chat_id):
    auc = auctions[group_map[chat_id]]
    if auc.get('timer_task'): auc['timer_task'].cancel()
    
    auc['current_index'] += 1
    if auc['current_index'] >= len(auc['players']):
        return await context.bot.send_message(chat_id, "🏁 End of List.")

    p = auc['players'][auc['current_index']]
    base = p.get('BasePrice', 20)
    auc['current_bid'] = {"amount": base, "holder": None, "holder_team": None}
    
    # Image Fetch
    loop = asyncio.get_event_loop()
    img = None
    try: img = await loop.run_in_executor(None, get_player_image, p['Name'])
    except: pass
    
    kb = [[InlineKeyboardButton(f"BID {format_price(base)}", callback_data="BID")], 
          [InlineKeyboardButton("SKIP", callback_data="SKIP")]]
    auc['last_kb'] = InlineKeyboardMarkup(kb)
    
    caption = f"💎 <strong>{p['Name']}</strong>\n💰 Base: {format_price(base)}\n⏳ <strong>30s Clock</strong>"
    
    if img: await context.bot.send_photo(chat_id, photo=img, caption=caption, reply_markup=auc['last_kb'], parse_mode='HTML')
    else: await context.bot.send_message(chat_id, text=caption, reply_markup=auc['last_kb'], parse_mode='HTML')
        
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
            # Auto Sell logic if no RTM triggered yet
            await handle_sold(context, chat_id, False)
        else:
            await handle_unsold(context, chat_id)
    except asyncio.CancelledError: pass

async def update_ui(context, chat_id, text):
    auc = auctions[group_map[chat_id]]
    p = auc['players'][auc['current_index']]
    b = auc['current_bid']
    info = f"🔨 <strong>{format_price(b['amount'])}</strong> ({b['holder_team']})" if b['holder'] else f"💰 Base: {format_price(p['BasePrice'])}"
    
    try: await context.bot.edit_message_caption(chat_id, auc['msg_id'], caption=f"💎 <strong>{p['Name']}</strong>\n{info}\n{text}", reply_markup=auc['last_kb'], parse_mode='HTML')
    except: pass

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
        await update_ui(context, chat_id, "⏳ <strong>Reset!</strong>")

    elif query.data == "NEXT":
        if user_id in auc['admins']: await show_next_player(context, chat_id)

async def handle_sold(context, chat_id, is_rtm):
    auc = auctions[group_map[chat_id]]
    p = auc['players'][auc['current_index']]
    b = auc['current_bid']
    
    # Check RTM Count
    total_rtms = sum([(auc['rtm_limit'] - t['rtms_used']) for t in auc['teams'].values()])
    
    # If RTMs left and not yet triggered, Show RTM Window
    if total_rtms > 0 and not is_rtm:
        await context.bot.send_message(chat_id, "🔴 <strong>SOLD! RTM Window (10s)</strong>", parse_mode='HTML')
        await asyncio.sleep(10)
        # If no RTM command received in 10s, proceed to finalize
    
    # Finalize Sale
    w_team = None
    for t in auc['teams'].values():
        if t['name'] == b['holder_team']: w_team = t; break
        
    if w_team:
        w_team['purse'] -= b['amount']
        w_team['squad'].append({'name': p['Name'], 'price': b['amount']})
        p['Status'] = 'Sold'
        p['SoldTo'] = w_team['name']
        
    kb = [[InlineKeyboardButton("NEXT ⏭️", callback_data="NEXT")]]
    await context.bot.send_message(chat_id, f"🔴 <strong>SOLD to {w_team['name']}</strong> for {format_price(b['amount'])}", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def handle_unsold(context, chat_id):
    kb = [[InlineKeyboardButton("NEXT ⏭️", callback_data="NEXT")]]
    await context.bot.send_message(chat_id, "❌ UNSOLD", reply_markup=InlineKeyboardMarkup(kb))

# --- MANUAL RTM COMMAND ---
async def manual_rtm(update, context):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    if update.effective_user.id not in auc['admins']: return
    
    team_name = " ".join(context.args)
    code, team = get_team_by_name(auc, team_name)
    if not team: return await update.message.reply_text("Team not found")
    
    if team['rtms_used'] >= auc['rtm_limit']: return await update.message.reply_text("No RTMs left")
    
    # Trigger RTM Flow (Hike Logic)
    auc["rtm_state"] = "WAITING_HIKE"
    auc["rtm_data"] = {"code": code, "name": team['name']}
    
    kb = [[InlineKeyboardButton("HIKE", callback_data="DO_HIKE"), InlineKeyboardButton("NO HIKE", callback_data="NO_HIKE")]]
    await context.bot.send_message(chat_id, f"✋ <strong>RTM by {team['name']}</strong>\nWinner: Hike?", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')