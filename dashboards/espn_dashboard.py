import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # `streamlit run` executes this file directly; make project packages importable
import streamlit as st
import psycopg
import pandas as pd
from db.db_handler import DatabaseHandler
import time

st.set_page_config(page_title="Mu Sports Network", layout="wide", initial_sidebar_state="expanded")

db = DatabaseHandler()

# --- CUSTOM CSS ---
st.markdown("""
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .block-container { padding-top: 1rem; padding-bottom: 0rem; max-width: 1400px; }
    
    /* ESPN ARTICLE CSS */
    .espn-navbar { background-color: #CC0000; color: white; padding: 15px 20px; font-weight: bold; font-size: 24px; letter-spacing: 1px; display: flex; justify-content: space-between; align-items: center; border-radius: 5px; margin-bottom: 20px; }
    .hero-article { background-color: white; padding: 30px; border-radius: 4px; border-bottom: 4px solid #CC0000; margin-bottom: 25px; }
    .hero-headline { font-family: 'Georgia', serif; font-size: 42px; font-weight: 900; color: #000; line-height: 1.1; margin-bottom: 10px; }
    .hero-author { font-family: 'Helvetica', sans-serif; font-size: 13px; font-weight: bold; text-transform: uppercase; color: #CC0000; margin-bottom: 15px; }
    .sub-article { border-top: 1px solid #ddd; padding-top: 15px; margin-bottom: 15px; }
    .sub-headline { font-family: 'Georgia', serif; font-size: 22px; font-weight: bold; color: #111; line-height: 1.2; margin-bottom: 8px; }
    
    /* TWITTER CSS */
    .twitter-layout { display: flex; justify-content: center; }
    .twitter-feed { max-width: 600px; width: 100%; border-left: 1px solid #eff3f4; border-right: 1px solid #eff3f4; padding-bottom: 100px; }
    .tweet-card { padding: 16px; border-bottom: 1px solid #eff3f4; transition: background-color 0.2s; cursor: pointer; }
    .tweet-card:hover { background-color: #f7f9f9; }
    .tweet-avatar { width: 48px; height: 48px; border-radius: 50%; background-color: #1da1f2; margin-right: 12px; display: flex; justify-content: center; align-items: center; color: white; font-weight: bold; font-size: 20px; flex-shrink: 0; }
    .tweet-header { display: flex; align-items: center; margin-bottom: 4px; }
    .tweet-name { font-weight: 800; color: #0f1419; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; font-size: 15px; }
    .tweet-handle { color: #536471; font-size: 15px; margin-left: 5px; }
    .tweet-text { color: #0f1419; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; font-size: 15px; line-height: 1.5; margin-top: 4px; }
    .twitter-header { position: sticky; top: 0; background-color: rgba(255,255,255,0.9); backdrop-filter: blur(12px); padding: 16px; border-bottom: 1px solid #eff3f4; font-weight: 800; font-size: 20px; z-index: 10; }
</style>
""", unsafe_allow_html=True)

def fetch_teams():
    with psycopg.connect(db.db_conn_info) as conn:
        return pd.read_sql("SELECT id, name FROM teams ORDER BY name", conn)

def render_tweet(display_name, handle, content, color="#1da1f2"):
    initial = display_name[0] if display_name else "U"
    return f"""
    <div class="tweet-card">
        <div style="display: flex;">
            <div class="tweet-avatar" style="background-color: {color};">{initial}</div>
            <div style="flex-grow: 1;">
                <div class="tweet-header">
                    <span class="tweet-name">{display_name}</span>
                    <span class="tweet-handle">{handle}</span>
                    <span class="tweet-handle" style="margin-left: 5px;">· 2h</span>
                </div>
                <div class="tweet-text">{content}</div>
                <div style="display: flex; justify-content: space-between; color: #536471; margin-top: 12px; max-width: 425px;">
                    <span>💬 124</span><span>🔄 45</span><span>❤️ 892</span><span>📊 12.5K</span>
                </div>
            </div>
        </div>
    </div>
    """

def render_hero_article(headline, author, content):
    st.markdown(f"""
    <div class="hero-article">
        <div class="hero-headline">{headline}</div>
        <div class="hero-author">By {author} | SENIOR NFL WRITER</div>
        <div style="font-family: 'Georgia', serif; font-size: 19px; color: #333; line-height: 1.6;">
            {content}
        </div>
    </div>
    """, unsafe_allow_html=True)

def render_sub_article(headline, author, content):
    st.markdown(f"""
    <div class="sub-article">
        <div style="font-size: 12px; color: #CC0000; text-transform: uppercase; font-weight: 800; margin-bottom: 4px;">{author}</div>
        <div class="sub-headline">{headline}</div>
        <div style="font-family: 'Georgia', serif; font-size: 15px; color: #555; line-height: 1.5;">
            {content[:200]}...
        </div>
    </div>
    """, unsafe_allow_html=True)

def main():
    teams_df = fetch_teams()
    team_options = {"National Front Page": None}
    for _, row in teams_df.iterrows():
        team_options[row['name']] = row['id']
        
    st.sidebar.markdown("<h2 style='margin-bottom: 5px;'>Platform</h2>", unsafe_allow_html=True)
    app_mode = st.sidebar.radio("", ["📰 ESPN Newsroom", "🐦 Twitter Timeline"])
    
    st.sidebar.markdown("<hr style='margin: 10px 0;'>", unsafe_allow_html=True)
    
    st.sidebar.markdown("<h2 style='margin-bottom: 5px;'>Filter Team</h2>", unsafe_allow_html=True)
    selection = st.sidebar.radio("", list(team_options.keys()))
    selected_team_id = team_options[selection]
    
    if app_mode == "📰 ESPN Newsroom":
        # --- ESPN MODE ---
        st.markdown("""
        <div class="espn-navbar">
            <div>ESPN NFL</div>
            <div style="font-size: 14px; font-weight: normal; letter-spacing: 0px;">Home | Scores | Schedule | Standings | Stats | Teams</div>
        </div>
        """, unsafe_allow_html=True)
        
        if selected_team_id is None:
            col1, col2, col3 = st.columns([5, 3, 2])
            with col1:
                render_hero_article("Is George Crawford's 4-TD Performance Sustainable?", "Bill Barnwell", "The Carolina Panthers walked away with a thrilling 35-34 victory over the Falcons in the Game of the Week. But before we crown him the inevitable MVP, we need to look at the underlying Expected Points Added and the Offensive Pace metric. Three of those touchdowns came against a broken Cover-2 shell that Atlanta won't play again.")
                render_sub_article("Anonymous GMs Weigh In: The Purgatory Penalty", "Mike Sando", "I spoke to three NFC executives this week, and the consensus is clear: if your roster is over 27 years old and you don't have a top-tier quarterback, you need to blow it up.")
            with col2:
                render_sub_article("Watchability Index: Watch the Panthers", "Gregg Rosenthal", "Stop watching boring, high-gravity teams just because they play in New York. The sandbox chaos in Carolina is the best show on television.")
                render_sub_article("The 2035 Rookie Class is Historic", "Kirk Herbstreit", "Looking at the Blue-Chip Pedigree Index, it's obvious why these young receivers are separating instantly against veteran corners.")
                render_sub_article("Fantasy Adjustments: Do Not Panic on Drake Maye", "Matthew Berry", "Yes, he scored 4 points. Yes, you lost your matchup. But the SpikeIndex indicates a massive positive regression is coming.")
            with col3:
                st.markdown("### Top Headlines")
                st.markdown("- **Schefter:** Panthers extend Crawford")
                st.markdown("- **Rapoport:** Falcons OC on hot seat")
                st.markdown("- **Injury Report:** Week 4 Updates")
        else:
            col1, col2 = st.columns([2, 1])
            with col1:
                render_hero_article(f"Locker Room Report: Tension Building After Brutal {selection} Loss", "Sarah Smith", f"The {selection} facility was eerily quiet on Monday morning. After a performance that completely lacked discipline and situational awareness, the coaching staff is facing intense scrutiny from the local media.")
            with col2:
                render_sub_article(f"Positional Battle: Starting Job?", "Mike Johnson", "Breaking down the snap counts from Sunday reveals a disturbing trend for the highly-paid veterans.")
                render_sub_article(f"Time to Panic in Front Office?", "Jason Williams", "The Blue-Chip draft picks aren't developing, and the window is closing rapidly.")
                
    else:
        # --- TWITTER MODE ---
        col_left, col_mid, col_right = st.columns([1, 2, 1])
        
        with col_left:
            st.markdown("### 🐦 Home\n### 🔍 Explore\n### 🔔 Notifications\n### ✉️ Messages", unsafe_allow_html=True)
            
        with col_mid:
            feed_html = '<div class="twitter-feed">'
            feed_html += f'<div class="twitter-header">{"For You" if selected_team_id is None else f"{selection} Timeline"}</div>'
            
            if selected_team_id is None:
                feed_html += render_tweet("Skip Bayless", "@RealSkipBayless", "George Crawford threw 4 TDs but they were all GARBAGE TIME against a soft zone. Drake Maye would NEVER. No clutch gene! 😤", "#FF0000")
                feed_html += render_tweet("Adam Schefter", "@AdamSchefter", "Breaking: The Panthers have extended George Crawford to a 4-year, $240M deal following his MVP campaign.")
                feed_html += render_tweet("Josh 😤", "@JoshTakes87", "OMG I am officially a Panthers fan now 🔥🚀 Give me all the George Crawford stock rn. We are winning the Super Bowl.", "#000000")
                feed_html += render_tweet("Mina Kimes", "@minakimes", "The way the Panthers' OC is using motion to manipulate the weak-side linebacker's read step is literally art. Look at this gap 🤯")
                feed_html += render_tweet("Matthew Berry", "@MatthewBerryTMR", "If you benched Jalen McMillan because of the 'matchup', you deserve to lose your fantasy week.", "#FF8C00")
            else:
                feed_html += render_tweet("Tom 🤠", "@DC4L_Tom52", f"I don't care what the national media says. The {selection} are the greatest franchise on earth and the refs stole that game from us. 😤", "#002244")
                feed_html += render_tweet("Dan 🤠", "@CowboysNation_Dan81", f"Fire the defensive coordinator. Unacceptable performance at home. You can't waste this legacy.", "#002244")
                feed_html += render_tweet("Skip Bayless", "@RealSkipBayless", f"Just watched the {selection} tape. Absolute embarrassment. They lack the clutch gene entirely.", "#FF0000")
                
            feed_html += "</div>"
            st.markdown(feed_html, unsafe_allow_html=True)
            
        with col_right:
            st.markdown("""
            <div style="background-color: #f7f9f9; padding: 15px; border-radius: 16px;">
                <h4>Trending</h4>
                <p style="color: #536471; margin-bottom:0;">1 · Sports · Trending</p>
                <strong>#NFL</strong>
                <p style="color: #536471; margin-bottom:10px;">34.2K Tweets</p>
                <p style="color: #536471; margin-bottom:0;">2 · Trending</p>
                <strong>George Crawford</strong>
                <p style="color: #536471;">12.1K Tweets</p>
            </div>
            """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
