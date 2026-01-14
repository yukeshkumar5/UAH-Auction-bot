from duckduckgo_search import DDGS
import random
import string
import re

def format_price(value):
    try:
        value = float(value)
        if value >= 100:
            cr_val = value / 100
            if cr_val.is_integer(): return f"{int(cr_val)}C"
            return f"{round(cr_val, 2)}C"
        else:
            return f"{int(value)}L"
    except: return "0L"

def parse_price(value):
    try:
        s = str(value).upper().strip().replace(" ", "")
        nums = re.findall(r"[\d\.]+", s)
        if not nums: return 0 
        num = float(nums[0])
        if "C" in s: return int(num * 100)
        elif "L" in s: return int(num)
        else: return int(num)
    except: return 0

def normalize_player_data(df):
    df.columns = [str(c).strip().lower() for c in df.columns]
    data = df.to_dict('records')
    cleaned = []
    for p in data:
        cleaned.append({
            'Name': p.get('name', 'Unknown'),
            'Role': p.get('role', 'Player'),
            'Country': p.get('country', 'Unknown'),
            'BasePrice': parse_price(str(p.get('baseprice', '20L'))),
            'Status': 'Upcoming', 
            'SoldPrice': 0,
            'SoldTo': 'None'
        })
    return cleaned

def generate_code(length=5):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def get_player_image(player_name):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(keywords=f"{player_name} cricketer", region="in-en", safesearch="on", max_results=1))
            if results: return results[0]['image']
    except: pass
    return "https://upload.wikimedia.org/wikipedia/commons/7/7a/Pollock_to_Hussey.jpg"

def get_increment(price):
    if price < 100: return 5
    elif price < 200: return 10
    elif price < 500: return 20
    else: return 50