from supabase import create_client
from datetime import datetime
import os

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

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
