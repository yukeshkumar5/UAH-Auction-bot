import os
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is Alive!"

def run():
    # Get the PORT from Render's environment variables, default to 8080 if missing
    port = int(os.environ.get("PORT", 8080))
    try:
        # 0.0.0.0 is required for Render/Cloud hosting
        app.run(host='0.0.0.0', port=port)
    except Exception as e:
        print(f"Web Server Error: {e}")

def keep_alive():
    t = Thread(target=run)
    t.start()