import json
import os
from datetime import datetime

FILE = "last_auction.json"

def save_last_auction(auc):
    data = {
        "name": auc["name"],
        "room_id": auc["room_id"],
        "ended_at": datetime.utcnow().isoformat(),
        "teams": {}
    }

    for code, t in auc["teams"].items():
        data["teams"][code] = {
            "name": t["name"],
            "purse": t["purse"],
            "squad": t["squad"]
        }

    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_last_auction():
    if not os.path.exists(FILE):
        return None
    try:
        with open(FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return None
