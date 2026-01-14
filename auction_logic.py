from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from config import auctions, group_map
from utils import format_price, get_player_image, get_increment, parse_price
import asyncio

async def start_auction(update: Update, context):
    chat_id = update.effective_chat.id
    if chat_id not in group_map: return
    auc = auctions[group_map[chat_id]]
    auc['is_active'] = True
    await update.message.reply_text("📢 <strong>AUCTION STARTING!</strong>", parse_mode='HTML')
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
    
    # Send Image
    loop = asyncio.get_event_loop()
    img = None
    try:
        img = await loop.run_in_executor(None, get_player_image, p['Name'])
    except: pass
    
    kb = [[InlineKeyboardButton(f"BID {format_price(base)}", callback_data="BID")], 
          [InlineKeyboardButton("SKIP", callback_data="SKIP")]]
    auc['last_kb'] = InlineKeyboardMarkup(kb)
    
    caption = f"💎 <strong>{p['Name']}</strong>\n💰 Base: {format_price(base)}\n⏳ <strong>30s Clock</strong>"
    
    if img:
        msg = await context.bot.send_photo(chat_id, photo=img, caption=caption, reply_markup=auc['last_kb'], parse_mode='HTML')
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
            await trigger_rtm(context, chat_id)
        else:
            await handle_unsold(context, chat_id)
            
    except asyncio.CancelledError:
        pass

async def update_ui(context, chat_id, text):
    auc = auctions[group_map[chat_id]]
    p = auc['players'][auc['current_index']]
    b = auc['current_bid']
    
    info = f"🔨 <strong>{format_price(b['amount'])}</strong> ({b['holder_team']})" if b['holder'] else f"💰 Base: {format_price(p['BasePrice'])}"
    
    try: 
        await context.bot.edit_message_caption(
            chat_id, auc['msg_id'], 
            caption=f"💎 <strong>{p['Name']}</strong>\n{info}\n{text}", 
            reply_markup=auc['last_kb'], 
            parse_mode='HTML'
        )
    except: pass

async def bid_handler(update: Update, context):
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
        if auc['current_bid']['holder'] == user_id: return await query.answer("You hold the bid!", show_alert=True)

        curr = auc['current_bid']['amount']
        new_amt = curr if auc['current_bid']['holder'] is None else curr + get_increment(curr)
        
        if my_team['purse'] < new_amt: return await query.answer("Low Funds!", show_alert=True)

        # KILL OLD TIMER FIRST
        if auc.get('timer_task'): auc['timer_task'].cancel()
        
        auc['current_bid'] = {"amount": new_amt, "holder": user_id, "holder_team": my_team['name']}
        
        # RESTART TIMER
        auc['timer_task'] = asyncio.create_task(auction_timer(context, chat_id))
        
        await query.answer(f"Bid Placed: {format_price(new_amt)}")
        
        # UPDATE UI
        next_bid = new_amt + get_increment(new_amt)
        kb = [[InlineKeyboardButton(f"BID {format_price(next_bid)}", callback_data="BID")], 
              [InlineKeyboardButton("SKIP", callback_data="SKIP")]]
        auc['last_kb'] = InlineKeyboardMarkup(kb)
        
        await update_ui(context, chat_id, "⏳ <strong>Timer Reset!</strong>")

    elif query.data == "NEXT":
        if user_id not in auc["admins"]: return await query.answer("Admin Only")
        await show_next_player(context, chat_id)

async def handle_unsold(context, chat_id):
    auc = auctions[group_map[chat_id]]
    kb = [[InlineKeyboardButton("NEXT ⏭️", callback_data="NEXT")]]
    await context.bot.send_message(chat_id, "❌ UNSOLD", reply_markup=InlineKeyboardMarkup(kb))

async def trigger_rtm(context, chat_id):
    auc = auctions[group_map[chat_id]]
    b = auc['current_bid']
    # Sell immediately for now (RTM expansion logic can go here)
    await context.bot.send_message(chat_id, f"🔴 <strong>SOLD to {b['holder_team']}</strong> for {format_price(b['amount'])}", parse_mode='HTML')
    
    for t in auc['teams'].values():
        if t['name'] == b['holder_team']:
            t['purse'] -= b['amount']
            t['squad'].append(auc['players'][auc['current_index']]['Name'])
            break
            
    kb = [[InlineKeyboardButton("NEXT ⏭️", callback_data="NEXT")]]
    await context.bot.send_message(chat_id, "Click Next.", reply_markup=InlineKeyboardMarkup(kb))