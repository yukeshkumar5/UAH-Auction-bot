from supabase import create_client
from datetime import datetime
import os

SUPABASE_URL = "https://wvhrohaissdtfnwrobiy.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Ind2aHJvaGFpc3NkdGZud3JvYml5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2ODgzMDQ0OCwiZXhwIjoyMDg0NDA2NDQ4fQ.4_onCWr0wBWieTctpLTgEU5dFNVPwByqGvQnyyFuKwo"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def save_last_auction(auc):
    supabase.table("auctions").insert({
        "auction_name": auc["name"],
        "ended_at": datetime.utcnow().isoformat(),
        "data": auc
    }).execute()

def load_last_auction():
    res = supabase.table("auctions") \
        .select("*") \
        .order("ended_at", desc=True) \
        .limit(1) \
        .execute()

    if res.data:
        return res.data[0]["data"]
    return None
