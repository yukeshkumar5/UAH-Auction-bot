import logging
import sys

# REPLACE WITH YOUR TOKEN
TOKEN = "8555822248:AAE76zDM4g-e_Ti3Zwg3k4TTEico-Ewyas0"

# GLOBAL DATABASES
auctions = {}   
group_map = {}  
admin_map = {}

# LOGGING
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)