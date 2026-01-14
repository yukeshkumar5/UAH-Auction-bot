import random
import string

def generate_code(prefix):
    return prefix + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def parse_price_to_lakhs(price_str):
    """Parses Excel values like '1C', '50L'."""
    if not price_str: return 0
    s = str(price_str).upper().replace(" ", "").strip()
    try:
        if "C" in s:
            return int(float(s.replace("C", "")) * 100)
        elif "L" in s:
            return int(float(s.replace("L", "")))
        else:
            return int(float(s)) # Default fallback
    except:
        return 0

def format_price(lakhs):
    """Converts 150 -> 1.5C, 50 -> 50L."""
    if not lakhs: return "0L"
    if lakhs >= 100:
        cr = lakhs / 100
        return f"{int(cr)}C" if cr.is_integer() else f"{cr:.2f}C"
    else:
        return f"{lakhs}L"