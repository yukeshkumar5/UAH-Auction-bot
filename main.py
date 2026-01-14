import os
from threading import Thread
from flask import Flask
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from config import TOKEN
from team_logic import start_setup, ask_purse, ask_rtm, ask_file, finish_setup, cancel_setup, create_team, init_group, register, team_stats, ASK_NAME, ASK_PURSE, ASK_RTM_COUNT, ASK_FILE
from auction_logic import start_auction, bid_handler

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
    bot.add_handler(CommandHandler('register', register))
    bot.add_handler(CommandHandler('stats', team_stats))
    bot.add_handler(CommandHandler('start_auction', start_auction))
    bot.add_handler(CallbackQueryHandler(bid_handler))
    
    print("Bot Running...")
    bot.run_polling()