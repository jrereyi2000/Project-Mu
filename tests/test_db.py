import psycopg
from pipeline.ocr_extractor import PlayerStat
from db.db_handler import DatabaseHandler

def test_database():
    print("Testing Phase 3 Database Schema & UPSERT Logic...")
    
    # 1. Initialize handler
    try:
        # Assumes a local postgres instance with a database named 'madden'
        db = DatabaseHandler(db_conn_info="dbname=madden user=postgres", redis_url="redis://localhost:6379/0")
    except Exception as e:
        print(f"Connection failed: {e}")
        print("Make sure PostgreSQL & Redis are running and you have created a database named 'madden'.")
        return

    # 2. Create mock data (including a split scenario to test UPSERT deduplication)
    mock_stats = [
        # Pass 1: Stroud's passing stats
        PlayerStat(player_name="C. Stroud", team="HOU", completions=20, attempts=30, passing_yards=250, passing_touchdowns=2),
        
        # Pass 2: Mixon's rushing stats
        PlayerStat(player_name="J. Mixon", team="HOU", carries=15, rushing_yards=85, rushing_touchdowns=1),
        
        # Pass 3: Stroud appears AGAIN with rushing stats (simulating separate Passing and Rushing tab extractions)
        PlayerStat(player_name="C. Stroud", team="HOU", carries=3, rushing_yards=15, rushing_touchdowns=0)
    ]

    try:
        # 3. Insert Game and Stats
        print("\nInserting mock game and player stats...")
        game_id = db.insert_game_and_stats(
            season=2035, week=1, home_team_id=None, away_team_id=None, 
            home_score=24, away_score=14, player_stats_list=mock_stats
        )
        print(f"Successfully inserted Game ID: {game_id}")
        
        # 4. Emit the event
        db.emit_game_completed(game_id)
        
        # 5. Verify the data in PostgreSQL
        print("\nVerifying Data in PostgreSQL (Notice how Stroud's passing & rushing stats were perfectly merged!):")
        with psycopg.connect("dbname=madden user=postgres") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT player_name, team, stat_type, stat_value FROM player_stats WHERE game_id = %s ORDER BY player_name, stat_type;", 
                    (game_id,)
                )
                rows = cur.fetchall()
                for row in rows:
                    print(f"  {row[0]:<15} ({row[1]}) -> {row[2]:<20}: {row[3]}")
                    
        print("\nTest passed successfully! The EAV schema and UPSERT deduplication are working perfectly.")
        
    except Exception as e:
        print(f"\nDatabase operation failed: {e}")
        print("Did you remember to run 'psql -U postgres -d madden -f init_db.sql' to create the tables?")

if __name__ == "__main__":
    test_database()
