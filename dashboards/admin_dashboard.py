import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # `streamlit run` executes this file directly; make project packages importable
import streamlit as st
import psycopg
import pandas as pd
import os

DB_CONN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

st.set_page_config(page_title="Madden Franchise Admin", layout="wide")
st.title("🏈 Madden Franchise Backend Admin")

tables = ["league_state", "teams", "team_seasons", "games", "players", "player_seasons", "team_stats", "player_stats", "season_stats", "awards", "agent_memories"]
selected_table = st.sidebar.selectbox("Select Table to Manage", tables)

def get_primary_key(table):
    return "id"

def load_data(table):
    with psycopg.connect(DB_CONN) as conn:
        if table == "games":
            query = """
            SELECT 
                g.id, g.season, g.week, g.game_time,
                ht.name AS home_team, g.home_score, 
                at.name AS away_team, g.away_score, 
                g.status, g.completed_at, g.game_type, g.excitement_index
            FROM games g
            LEFT JOIN teams ht ON g.home_team_id = ht.id
            LEFT JOIN teams at ON g.away_team_id = at.id
            ORDER BY g.week ASC, g.id ASC
            """
            df = pd.read_sql(query, conn)
        elif table == "players":
            query = """
            SELECT p.id, p.player_name, p.full_name, p.height, p.weight, ps.season, t.name AS team_name, ps.position, ps.age, ps.overall_rating
            FROM players p
            LEFT JOIN player_seasons ps ON p.id = ps.player_id
            LEFT JOIN teams t ON ps.team_id = t.id
            ORDER BY p.id ASC
            """
            df = pd.read_sql(query, conn)
        elif table == "teams":
            query = """
            SELECT t.id, t.name, t.city, t.abbreviation, t.team_color, t.stadium_name, t.conference, t.division, ts.season, ts.wins, ts.losses
            FROM teams t
            LEFT JOIN team_seasons ts ON t.id = ts.team_id
            ORDER BY t.name ASC
            """
            df = pd.read_sql(query, conn)
        elif table == "season_stats":
            query = """
            SELECT s.id, s.player_id, s.season, s.player_name, s.position, t.name AS team_name, 
                   s.completions, s.pass_attempts, s.pass_yards, s.pass_tds, s.interceptions, 
                   s.rush_attempts, s.rush_yards, s.rush_tds, 
                   s.receptions, s.rec_yards, s.rec_tds, s.fumb
            FROM season_stats s
            LEFT JOIN teams t ON s.team_id = t.id
            ORDER BY s.id ASC
            """
            df = pd.read_sql(query, conn)
        elif table == "player_stats":
            query = """
            SELECT ps.id, g.week, ps.game_id, ps.player_name, t.name AS team_name,
                   ps.completions, ps.pass_attempts, ps.pass_yards, ps.pass_tds, ps.interceptions,
                   ps.rush_attempts, ps.rush_yards, ps.rush_tds, ps.receptions, ps.rec_yards, ps.rec_tds, ps.fumb
            FROM player_stats ps
            LEFT JOIN teams t ON ps.team_id = t.id
            LEFT JOIN games g ON ps.game_id = g.id
            ORDER BY g.week ASC, ps.id ASC
            """
            df = pd.read_sql(query, conn)
        elif table == "team_stats":
            query = """
            SELECT ts.id, g.week, ts.game_id, ts.team_name, ts.score, ts.total_offense,
                   ts.passing_yards, ts.rushing_yards, ts.turnovers,
                   ts.passing_completions, ts.passing_attempts, ts.passing_tds,
                   ts.rushing_attempts, ts.rushing_tds, ts.first_downs, ts.total_plays,
                   ts.third_down_attempts, ts.third_down_conversions,
                   ts.fourth_down_attempts, ts.fourth_down_conversions
            FROM team_stats ts
            LEFT JOIN games g ON ts.game_id = g.id
            ORDER BY g.week ASC, ts.id ASC
            """
            df = pd.read_sql(query, conn)
        elif table == "awards":
            query = """
            SELECT a.id, a.player_id, a.season, a.award_type, a.player_name, a.position, p.full_name AS db_player_name, t.name AS team_name, a.coach_name
            FROM awards a
            LEFT JOIN players p ON a.player_id = p.id
            LEFT JOIN teams t ON a.team_id = t.id
            ORDER BY a.season DESC, a.award_type ASC
            """
            df = pd.read_sql(query, conn)
        else:
            df = pd.read_sql(f"SELECT * FROM {table} ORDER BY id ASC", conn)
    return df

st.subheader(f"Managing: {selected_table.upper()}")

try:
    df = load_data(selected_table)
except Exception as e:
    st.error(f"Failed to load table: {e}")
    st.stop()

# --- DYNAMIC FILTERING LOGIC ---
st.write("### 🔍 Filters")
search_term = st.text_input("Global Search (Search across all columns)", "")

filtered_df = df.copy()

if search_term:
    mask = filtered_df.astype(str).apply(lambda x: x.str.contains(search_term, case=False, na=False)).any(axis=1)
    filtered_df = filtered_df[mask]

if "full_name" in filtered_df.columns:
    missing_only = st.checkbox("Show only players missing 'full_name'", value=False)
    if missing_only:
        filtered_df = filtered_df[filtered_df["full_name"].isna() | (filtered_df["full_name"] == "")]

filter_columns = st.multiselect("Select columns to filter by:", options=filtered_df.columns.tolist())
if filter_columns:
    # Create a nice multi-column layout for the filters
    filter_cols = st.columns(len(filter_columns))
    for i, col in enumerate(filter_columns):
        col_type = filtered_df[col].dtype
        
        with filter_cols[i]:
            # Numeric columns get a range slider
            if pd.api.types.is_numeric_dtype(col_type) and not pd.api.types.is_bool_dtype(col_type):
                is_float = pd.api.types.is_float_dtype(col_type)
                _min = float(df[col].min()) if is_float else int(df[col].min())
                _max = float(df[col].max()) if is_float else int(df[col].max())
                
                # Handle edge cases for NaNs inside integers
                if pd.isna(_min): _min = 0.0 if is_float else 0
                if pd.isna(_max): _max = 0.0 if is_float else 0
                
                if _min == _max:
                    st.write(f"{col}: All values are {_min}")
                    step = 1.0 if is_float else 1
                    user_num_input = st.slider(
                        f"{col}",
                        min_value=_min,
                        max_value=_max,
                        value=(_min, _max),
                        step=step,
                    )
                    filtered_df = filtered_df[filtered_df[col].between(*user_num_input)]
            # Text/Categorical columns get a multiselect dropdown
                unique_vals = sorted(df[col].dropna().astype(str).unique().tolist())
                user_cat_input = st.multiselect(f"{col}", options=unique_vals, default=None)
                if user_cat_input:
                    filtered_df = filtered_df[filtered_df[col].astype(str).isin(user_cat_input)]

st.write(f"Showing {len(filtered_df)} of {len(df)} total rows.")

# --- DATA EDITOR ---
enable_row_edits = st.checkbox("Enable Adding/Deleting Rows (Note: This temporarily disables native column sorting)", value=False)
row_mode = "dynamic" if enable_row_edits else "fixed"

editor_key = f"editor_{selected_table}"

# Configure dropdowns for foreign keys so users can't make typos
col_config = {}
with psycopg.connect(DB_CONN) as conn:
    with conn.cursor() as cur:
        if "team_name" in filtered_df.columns:
            cur.execute("SELECT name FROM teams ORDER BY name")
            team_names = [row[0] for row in cur.fetchall()]
            col_config["team_name"] = st.column_config.SelectboxColumn("Team", options=team_names, required=True)
        if "home_team" in filtered_df.columns:
            cur.execute("SELECT name FROM teams ORDER BY name")
            team_names = [row[0] for row in cur.fetchall()]
            col_config["home_team"] = st.column_config.SelectboxColumn("Home Team", options=team_names, required=True)
            col_config["away_team"] = st.column_config.SelectboxColumn("Away Team", options=team_names, required=True)
        if "week" in filtered_df.columns and selected_table in ["team_stats", "player_stats"]:
            col_config["week"] = st.column_config.Column("Week", disabled=True)

edited_df = st.data_editor(
    filtered_df, 
    num_rows=row_mode, 
    use_container_width=True, 
    key=editor_key,
    column_config=col_config
)

if st.button("💾 Commit Changes to Database", type="primary"):
    changes = st.session_state[editor_key]
    added_rows = changes.get("added_rows", [])
    edited_rows = changes.get("edited_rows", {})
    deleted_rows = changes.get("deleted_rows", [])
    
    pk_col = get_primary_key(selected_table)
    
    try:
        with psycopg.connect(DB_CONN) as conn:
            with conn.cursor() as cur:
                # 1. Handle Deletions
                for row_idx in deleted_rows:
                    pk_val = filtered_df.iloc[int(row_idx)][pk_col]
                    cur.execute(f"DELETE FROM {selected_table} WHERE {pk_col} = %s", (pk_val,))
                    
                # 2. Handle Updates
                for row_idx, edits in edited_rows.items():
                    pk_val = filtered_df.iloc[int(row_idx)][pk_col]
                    
                    if selected_table == "players":
                        player_edits, player_vals = [], []
                        season_edits, season_vals = [], []
                        
                        for col, new_val in edits.items():
                            if col == "full_name":
                                player_edits.append(f"{col} = %s")
                                player_vals.append(new_val)
                            elif col in ["height", "weight"]:
                                player_edits.append(f"{col} = %s")
                                player_vals.append(new_val)
                            elif col in ["position", "age", "overall_rating", "years_of_experience"]:
                                season_edits.append(f"{col} = %s")
                                season_vals.append(new_val)
                            elif col == "team_name":
                                cur.execute("SELECT id FROM teams WHERE name = %s", (new_val,))
                                row = cur.fetchone()
                                if row:
                                    season_edits.append("team_id = %s")
                                    season_vals.append(row[0])
                                    
                        if player_edits:
                            player_vals.append(pk_val)
                            cur.execute(f"UPDATE players SET {', '.join(player_edits)} WHERE id = %s", player_vals)
                        if season_edits:
                            season = int(filtered_df.iloc[int(row_idx)]["season"])
                            season_vals.extend([pk_val, season])
                            cur.execute(f"UPDATE player_seasons SET {', '.join(season_edits)} WHERE player_id = %s AND season = %s", season_vals)
                        continue

                    set_clauses = []
                    values = []
                    for col, new_val in edits.items():
                        if col == "week" and selected_table in ["team_stats", "player_stats"]:
                            continue
                        elif selected_table == "games" and col in ["home_team", "away_team"]:
                            cur.execute("SELECT id FROM teams WHERE name = %s", (new_val,))
                            row = cur.fetchone()
                            if row:
                                db_col = "home_team_id" if col == "home_team" else "away_team_id"
                                set_clauses.append(f"{db_col} = %s")
                                values.append(row[0])
                        elif selected_table in ["season_stats", "player_stats", "player_seasons"] and col == "team_name":
                            cur.execute("SELECT id FROM teams WHERE name ILIKE %s", (new_val,))
                            row = cur.fetchone()
                            if row:
                                set_clauses.append("team_id = %s")
                                values.append(row[0])
                        elif selected_table == "teams" and col == "wins":
                            cur.execute(f"UPDATE team_seasons SET wins = %s WHERE team_id = %s", (new_val, pk_val))
                        elif selected_table == "teams" and col == "losses":
                            cur.execute(f"UPDATE team_seasons SET losses = %s WHERE team_id = %s", (new_val, pk_val))
                        else:
                            set_clauses.append(f"{col} = %s")
                            values.append(new_val)
                    
                    if set_clauses:
                        query = f"UPDATE {selected_table} SET {', '.join(set_clauses)} WHERE {pk_col} = %s"
                        values.append(pk_val)
                        cur.execute(query, values)
                        
                # 3. Handle Additions
                for new_row in added_rows:
                    cols_list = []
                    vals_list = []
                    for k, v in new_row.items():
                        # Intercept games table team name insertions to reverse lookup the ID
                        if selected_table == "games" and k in ["home_team", "away_team"]:
                            cur.execute("SELECT id FROM teams WHERE name = %s", (v,))
                            row = cur.fetchone()
                            if row:
                                cols_list.append("home_team_id" if k == "home_team" else "away_team_id")
                                vals_list.append(row[0])
                            cols_list.append(k)
                            vals_list.append(v)
                            
                    if cols_list:
                        placeholders = ", ".join(["%s"] * len(vals_list))
                        col_names = ", ".join(cols_list)
                        query = f"INSERT INTO {selected_table} ({col_names}) VALUES ({placeholders})"
                        cur.execute(query, vals_list)
                    
            conn.commit()
        if selected_table == "season_stats" and (edited_rows or deleted_rows or added_rows):
            try:
                from engines.game_stat_generator import generate_team_and_player_stats
                generate_team_and_player_stats(season_year=2035)
                st.success("Successfully synchronized League Game Stats with your manual edits!")
            except Exception as ex:
                st.error(f"Failed to run generator: {ex}")
                
        st.success("Successfully committed changes to PostgreSQL!")
        st.rerun()
        
    except Exception as e:
        import traceback
        st.error(f"Database Error: {e}")
        st.code(traceback.format_exc())
