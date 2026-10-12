import psycopg
CONN_INFO = "host=localhost dbname=madden user=postgres password=postgres"
try:
    with psycopg.connect(CONN_INFO) as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE players ADD COLUMN player_number INTEGER DEFAULT 0;")
        conn.commit()
    print("Success")
except Exception as e:
    print("Error:", e)
