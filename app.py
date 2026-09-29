import streamlit as st
import pandas as pd
import warnings
warnings.filterwarnings("ignore", category=SyntaxWarning)
from database import init_db, add_company, remove_company, get_all_companies, get_recent_articles, get_last_fetch_time, set_last_fetch_time, is_paused, set_paused, delete_article, toggle_company_pause
from scheduler import init_scheduler
from fetcher import fetch_all_companies
from notifier import send_notification
import datetime
import streamlit.components.v1 as components
import io

# Initialize database ONCE
@st.cache_resource
def run_db_init():
    init_db()
    return True

run_db_init()

# Start background scheduler ONCE
@st.cache_resource
def start_scheduler():
    init_scheduler()
    return True

start_scheduler()

# Read user_email from query parameters for session-based personalization
user_email = st.query_params.get("user_email", "").strip().lower()

# Cache Google Sheets API reads to prevent 429 Quota Exceeded errors
@st.cache_data(ttl=15)
def cached_get_all_companies(email=None):
    return get_all_companies(email)

@st.cache_data(ttl=15)
def cached_get_recent_articles(limit, email=None):
    return get_recent_articles(limit, email)

@st.cache_data(ttl=15)
def cached_get_last_fetch_time():
    return get_last_fetch_time()

@st.cache_data(ttl=15)
def cached_is_paused():
    return is_paused()


# App layout
st.set_page_config(page_title="Client News Tracker", layout="wide")
st.title("📰 Client News Tracker")
st.markdown("Automatically track Google News RSS feeds for specific companies. Updates every 5 minutes.")

# Sidebar for managing companies
with st.sidebar:
    st.header("Tracked Companies")
    if user_email:
        st.caption(f"👤 Connected Session: **{user_email}**")
    else:
        st.caption("👤 Connected Session: **Anonymous / Direct**")
    
    # Add company form
    with st.form("add_company_form", clear_on_submit=True):
        new_company = st.text_input("Add a Company")
        region = st.selectbox("Region", ["Global", "India", "Both"])
        submit_btn = st.form_submit_button("Add")
        if submit_btn and new_company:
            if add_company(new_company.strip(), region, user_email):
                st.cache_data.clear() # Clear cache on new write
                st.success(f"Added {new_company} ({region})")
                st.rerun()
            else:
                st.error(f"{new_company} is already tracked.")
    
    # List and remove companies
    companies = cached_get_all_companies(user_email)
    if not companies:
        st.info("No companies tracked right now. Add some above.")
    else:
        for comp in companies:
            c_status = comp.get('last_status', 'N/A')
            is_comp_paused = "paused" in c_status.lower()
            status_icon = "⏸️ Paused" if is_comp_paused else "🟢 Active"
            
            with st.expander(f"🏢 {comp['name']} ({comp.get('region', 'Global')}) - {status_icon}"):
                st.write(f"**Status:** {c_status}")
                col1, col2 = st.columns(2)
                with col1:
                    pause_btn_label = "▶️ Resume" if is_comp_paused else "⏸️ Pause"
                    if st.button(pause_btn_label, key=f"pause_{comp['id']}", use_container_width=True):
                        toggle_company_pause(comp['id'], user_email)
                        st.cache_data.clear()
                        st.rerun()
                with col2:
                    if st.button("🗑️ Remove", key=f"remove_{comp['id']}", type="secondary", use_container_width=True):
                        remove_company(comp['name'], user_email)
                        st.cache_data.clear()
                        st.rerun()
    st.markdown("---")
    st.subheader("Fetch Status")
    
    # Pause/Resume Actions
    is_p = cached_is_paused()
    if is_p:
        st.markdown(
            """
            <div style="background-color: #EF5350; color: #FFFFFF; font-weight: bold; padding: 10px; border-radius: 8px; text-align: center; margin-bottom: 10px;">
                ⏸️ SCRAPER IS PAUSED
            </div>
            """, 
            unsafe_allow_html=True
        )
        if st.button("▶️ Resume Scraper", type="primary", use_container_width=True):
            set_paused(False)
            st.cache_data.clear()
            st.rerun()
    else:
        if st.button("⏸️ Pause Scraper", type="secondary", use_container_width=True):
            set_paused(True)
            st.cache_data.clear()
            st.rerun()

    st.markdown(" ")
    last_fetch_str = cached_get_last_fetch_time()
    
    if last_fetch_str:
        try:
            # Parse the stored UTC time
            last_fetch_dt = datetime.datetime.fromisoformat(last_fetch_str).replace(tzinfo=datetime.timezone.utc)
            
            # Convert to Indian Standard Time (UTC+5:30)
            ist_offset = datetime.timedelta(hours=5, minutes=30)
            last_fetch_ist = last_fetch_dt + ist_offset
            next_fetch_ist = last_fetch_ist + datetime.timedelta(minutes=5)
            
            # Next fetch for JS (ISO format for the Date constructor)
            next_fetch_utc = last_fetch_dt + datetime.timedelta(minutes=5)
            
            st.write(f"**Last Check (IST):** {last_fetch_ist.strftime('%H:%M:%S')}")
            
            if is_p:
                timer_html = """
                <div style="font-family: 'Inter', sans-serif; background: #1E1E1E; color: #FFFFFF; padding: 15px; border-radius: 8px; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                    <div style="font-size: 11px; font-weight: 500; color: #AAAAAA; margin-bottom: 5px; text-transform: uppercase; letter-spacing: 1px;">Time Until Next Fetch</div>
                    <div id="countdown" style="font-size: 32px; font-weight: 700; font-variant-numeric: tabular-nums; color: #EF5350; text-shadow: 0 0 10px rgba(239,83,80,0.3);">PAUSED</div>
                </div>
                """
                components.html(timer_html, height=130)
            else:
                st.write(f"**Next Check (IST):** {next_fetch_ist.strftime('%H:%M:%S')}")
                
                # Classy JS Countdown Widget
                timer_html = f"""
                <div style="font-family: 'Inter', sans-serif; background: #1E1E1E; color: #FFFFFF; padding: 15px; border-radius: 8px; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                    <div style="font-size: 11px; font-weight: 500; color: #AAAAAA; margin-bottom: 5px; text-transform: uppercase; letter-spacing: 1px;">Time Until Next Fetch</div>
                    <div id="countdown" style="font-size: 32px; font-weight: 700; font-variant-numeric: tabular-nums; color: #00E676; text-shadow: 0 0 10px rgba(0,230,118,0.3);">--:--</div>
                </div>
                <script>
                    var nextFetch = new Date('{next_fetch_utc.isoformat()}').getTime();
                    var countdownEl = document.getElementById("countdown");
                    
                    var x = setInterval(function() {{
                        var now = new Date().getTime();
                        var distance = nextFetch - now;
                        
                        if (distance < 0) {{
                            clearInterval(x);
                            countdownEl.innerHTML = "REFRESHING...";
                            countdownEl.style.color = "#00E676";
                            setTimeout(function() {{ 
                                window.location.reload(); 
                            }}, 2000);
                        }} else {{
                            var minutes = Math.floor((distance % (1000 * 60 * 60)) / (1000 * 60));
                            var seconds = Math.floor((distance % (1000 * 60)) / 1000);
                            
                            var minStr = minutes < 10 ? "0" + minutes : minutes;
                            var secStr = seconds < 10 ? "0" + seconds : seconds;
                            
                            countdownEl.innerHTML = minStr + ":" + secStr;
                            if (minutes < 1) {{
                                countdownEl.style.color = "#FFCA28";
                                countdownEl.style.textShadow = "0 0 10px rgba(255,202,40,0.3)";
                            }}
                        }}
                    }}, 1000);
                </script>
                """
                components.html(timer_html, height=130)
                
        except Exception:
            st.write("Wait for first fetch...")
    else:
        st.write("Fetching soon...")

    # Manual Fetch Action
    if is_p:
        st.caption("⚠️ Resume the scraper to enable background checks.")
        st.button("Fetch Now! (Scraper Paused)", disabled=True, use_container_width=True)
    else:
        if st.button("Fetch Now! (Manual Override)", use_container_width=True):
            with st.spinner("Fetching latest news..."):
                new_arts = fetch_all_companies()
                st.cache_data.clear() # Clear cache so new articles show immediately
                if new_arts:
                    send_notification(new_arts)
                    st.success(f"Found {len(new_arts)} new articles and updated sheet.")
                else:
                    st.info("No new articles found.")
                st.rerun()


    # Brand Report Download Section (Consolidated Excel Sheet)
    st.markdown("---")
    st.subheader("📊 Download Compiled Report")
    
    # Fetch all articles from sheet
    all_articles = cached_get_recent_articles(5000, user_email)
    company_names = [comp['name'].lower() for comp in companies]
    
    if user_email and company_names:
        all_articles = [
            art for art in all_articles
            if any(name in art['title'].lower() or name in art['source'].lower() for name in company_names)
        ]
    
    if all_articles:
        report_df = pd.DataFrame(all_articles)
        
        # Format report columns: URL, Title, Agency, Time of Publishing, Region
        if 'region' not in report_df.columns:
            report_df['region'] = 'Global'
        export_df = report_df[['link', 'title', 'source', 'published_at', 'region']].copy()
        export_df.columns = ['URL', 'Title', 'Agency', 'Time of Publishing', 'Region']
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            export_df.to_excel(writer, index=False, sheet_name='News Articles')
        processed_data = output.getvalue()
        
        st.download_button(
            label="📥 Download Excel Report",
            data=processed_data,
            file_name=f"compiled_news_report_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
    else:
        st.info("No articles in database yet.")

# Main area for displaying articles
st.header("Recent Articles")

# Search Bar
search_query = st.text_input("Looking for something specific?", placeholder="Type to search within Titles or Sources...")

recent_articles = cached_get_recent_articles(500, user_email)

if not companies:
    st.info("Please add a company in the sidebar to start tracking news.")
elif not recent_articles:
    st.info("No articles found yet. Please add a company and wait for the fetcher.")
else:
    df = pd.DataFrame(recent_articles)
    
    df['parsed_date'] = pd.to_datetime(df['published_at'], format='mixed', utc=True)
    df = df.sort_values(by='parsed_date', ascending=False)
        
    if search_query:
        mask = (
            df['title'].str.contains(search_query, case=False, na=False) |
            df['source'].str.contains(search_query, case=False, na=False)
        )
        df = df[mask]
    
    if df.empty:
        st.warning(f"No results found for your search criteria.")
    else:
        def format_ist_and_relative(dt):
            ist_dt = dt + datetime.timedelta(hours=5, minutes=30)
            now = datetime.datetime.now(datetime.timezone.utc)
            diff = now - dt
            seconds = diff.total_seconds()
            
            if seconds < 0: rel = "Just now"
            elif seconds < 60: rel = f"{int(seconds)}s ago"
            elif seconds < 3600: rel = f"{int(seconds // 60)}m ago"
            elif seconds < 86400: rel = f"{int(seconds // 3600)}h ago"
            else: rel = f"{int(seconds // 86400)}d ago"
            
            return ist_dt.strftime('%d %b, %H:%M'), rel

        df[['Time (IST)', 'Relative Time']] = df.apply(
            lambda row: format_ist_and_relative(row['parsed_date']), 
            axis=1, result_type='expand'
        )

        # Render clean feed cards segregated keyword-wise via Streamlit Tabs
        tab_names = [f"🏢 {comp['name']}" for comp in companies]
        tabs = st.tabs(tab_names)
        
        for i, comp in enumerate(companies):
            comp_name = comp['name']
            with tabs[i]:
                # Segregate matching: exact sheet company name OR keyword mention in the headline/title
                comp_df = df[
                    (df['company_name'].str.lower() == comp_name.lower()) |
                    (df['title'].str.lower().str.contains(comp_name.lower(), na=False))
                ]
                
                if comp_df.empty:
                    st.info(f"No recent articles found for keyword: **{comp_name}**")
                else:
                    for idx, row in comp_df.iterrows():
                        col_card, col_del = st.columns([0.94, 0.06], vertical_alignment="center")
                        with col_card:
                            st.markdown(
                                f"""
                                <div style="background-color: #1E293B; padding: 15px; border-radius: 8px; border-left: 5px solid #3B82F6;">
                                    <div style="font-size: 16px; font-weight: bold; line-height: 1.4;">
                                        <a href="{row['link']}" target="_blank" style="text-decoration: none; color: #60A5FA;">{row['title']}</a>
                                    </div>
                                    <div style="font-size: 13px; color: #94A3B8; margin-top: 6px;">
                                        <span>📰 {row['source']}</span> &nbsp;|&nbsp; 
                                        <span>⏰ {row['Time (IST)']} ({row['Relative Time']})</span>
                                    </div>
                                </div>
                                """,
                                unsafe_allow_html=True
                            )
                        with col_del:
                            # Unique key scoped per tab/company name to prevent Streamlit key collisions
                            if st.button("🗑️", key=f"del_{comp_name}_{idx}_{row['title'][:20]}", help="Delete this article permanently from spreadsheet"):
                                with st.spinner("Deleting..."):
                                    if delete_article(row['title'], user_email):
                                        st.cache_data.clear()
                                        st.toast("Article deleted successfully!", icon="🗑️")
                                        st.rerun()
                                    else:
                                        st.error("Failed to delete.")

