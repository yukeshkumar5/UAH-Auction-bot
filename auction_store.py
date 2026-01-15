import json
from datetime import datetime

FILE = "last_auction.json"

def save_last_auction(auc):
    data = {
        "name": auc["name"],
        "room_id": auc["room_id"],
        "ended_at": datetime.utcnow().isoformat(),
        "teams": []
    }

    for t in auc["teams"].values():
        team_data = {
            "name": t["name"],
            "purse": t["purse"],
            "players": t["squad"]
        }
        data["teams"].append(team_data)

    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_last_auction():
    try:
        with open(FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None
