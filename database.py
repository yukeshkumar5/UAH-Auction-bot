import os
import psycopg2

def get_connection():
    """Connects to PostgreSQL using the DATABASE_URL env variable."""
    url = os.getenv('postgresql://postgres:%4005052007Yukesh@db.axkdujpwqgsbvpotwvzu.supabase.co:5432/postgres')
    if not url:
        raise Exception("❌ DATABASE_URL is missing! Add it to Render Environment Variables.")
    return psycopg2.connect(url)

def init_db():
    """Initializes tables in PostgreSQL."""
    try:
        conn = get_connection()
        c = conn.cursor()
        
        # Auctions Table
        c.execute('''CREATE TABLE IF NOT EXISTS auctions (
            room_id TEXT PRIMARY KEY,
            owner_id BIGINT,
            group_id BIGINT,
            name TEXT,
            budget_lakhs INTEGER, 
            state TEXT DEFAULT 'SETUP',
            current_player_id INTEGER,
            current_bid_lakhs INTEGER DEFAULT 0,
            current_bidder_id BIGINT
        )''')

        # Admins Table
        c.execute('''CREATE TABLE IF NOT EXISTS admins (
            room_id TEXT,
            user_id BIGINT,
            PRIMARY KEY (room_id, user_id)
        )''')

        # Teams Table
        c.execute('''CREATE TABLE IF NOT EXISTS teams (
            id SERIAL PRIMARY KEY,
            room_id TEXT,
            name TEXT,
            code TEXT UNIQUE,
            sub_code TEXT UNIQUE,
            owner_id BIGINT,
            owner_name TEXT,
            co_owner_id BIGINT,
            purse_spent_lakhs INTEGER DEFAULT 0,
            rtm_count INTEGER DEFAULT 0
        )''')

        # Players Table
        c.execute('''CREATE TABLE IF NOT EXISTS players (
            id SERIAL PRIMARY KEY,
            room_id TEXT,
            name TEXT,
            country TEXT,
            role TEXT,
            base_price_lakhs INTEGER,
            status TEXT DEFAULT 'UNSOLD',
            sold_price_lakhs INTEGER DEFAULT 0,
            owner_team_id INTEGER
        )''')
        
        conn.commit()
        conn.close()
        print("✅ Database Tables Initialized Successfully!")
    except Exception as e:
        print(f"❌ Database Error: {e}")

if __name__ == "__main__":
    init_db()