import os
import json
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

# Global variables for gspread connection
_client = None
_spreadsheet = None

def _normalize_private_key(key: str) -> str:
    """Standardize the private key format to match what cryptography expects
    through TOML/JSON/env-var storage: escaped newlines left un-decoded,
    stray CRLF, surrounding quotes/whitespace, or a missing trailing newline."""
    key = key.strip().strip('"').strip("'")
    key = key.replace("\\r\\n", "\\n").replace("\\n", "\n")
    key = key.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse double newlines which happen due to mixed physical/escaped line breaks
    while "\n\n" in key:
        key = key.replace("\n\n", "\n")
    if not key.endswith("\n"):
        key += "\n"
    return key


def _load_creds_info():
    """Return service-account credentials as a plain dict, regardless of whether
    they were stored in Streamlit secrets as a native TOML table (recommended -
    each field is its own key, so TOML/Streamlit handles the private key's
    newlines correctly) or as one big JSON string (legacy/.env style, prone to
    escaping bugs)."""
    raw = None
    try:
        import streamlit as st
        if "GOOGLE_CREDS_JSON" in st.secrets:
            raw = st.secrets["GOOGLE_CREDS_JSON"]
    except Exception:
        pass

    if raw is None:
        raw = os.getenv("GOOGLE_CREDS_JSON")

    if not raw:
        return None

    if isinstance(raw, str):
        creds_info = json.loads(raw)
    else:
        # st.secrets returns a Mapping (AttrDict) when defined as a TOML table.
        creds_info = dict(raw)

    if "private_key" in creds_info:
        creds_info["private_key"] = _normalize_private_key(creds_info["private_key"])

    return creds_info


def get_gsheet():
    global _client, _spreadsheet
    if _spreadsheet is not None:
        return _spreadsheet

    creds_file = "google_creds.json"

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    try:
        creds_info = _load_creds_info()
        if creds_info:
            try:
                creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
            except ValueError as key_err:
                pk = creds_info.get("private_key", "")
                raise ValueError(
                    "GOOGLE_CREDS_JSON has a malformed private_key (starts with "
                    f"{pk[:27]!r}, {len(pk)} chars, ends with {pk[-27:]!r}). "
                    "In Streamlit Cloud, store the service account credentials as a "
                    "native TOML table instead of one JSON string, e.g.:\n"
                    "[GOOGLE_CREDS_JSON]\n"
                    'type = "service_account"\n'
                    'private_key = """-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"""\n'
                    "...\n"
                    "This avoids the JSON/TOML double-escaping that corrupts the key."
                ) from key_err
        elif os.path.exists(creds_file):
            creds = Credentials.from_service_account_file(creds_file, scopes=scopes)
        else:
            raise FileNotFoundError("Google credentials not found. Please provide 'google_creds.json' or set GOOGLE_CREDS_JSON env var.")

        _client = gspread.authorize(creds)
        
        sheet_name = os.getenv("GOOGLE_SHEET_NAME", "Brand Tracker DB")
        if sheet_name.startswith("https://") or "docs.google.com/spreadsheets" in sheet_name:
            _spreadsheet = _client.open_by_url(sheet_name)
        else:
            _spreadsheet = _client.open(sheet_name)
            
        return _spreadsheet
    except Exception as e:
        print(f"Error connecting to Google Sheets: {e}")
        raise e

def init_db():
    sh = get_gsheet()
    
    # 1. Verify/Create 'Companies' worksheet
    try:
        w_comp = sh.worksheet("Companies")
        # Upgrade for user-scoped sessions: resize to 5 columns if it's 4
        if w_comp.col_count < 5:
            w_comp.resize(rows=w_comp.row_count, cols=5)
            w_comp.update_cell(1, 5, "user_email")
            print("Upgraded Companies worksheet with user_email column.")
    except gspread.exceptions.WorksheetNotFound:
        sh.add_worksheet(title="Companies", rows="100", cols="5")
        w = sh.worksheet("Companies")
        w.append_row(["id", "name", "region", "last_status", "user_email"])
        
    # 2. Verify/Create 'Articles' worksheet (Support 6 columns for region)
    try:
        w_art = sh.worksheet("Articles")
        if w_art.col_count < 6:
            w_art.resize(rows=w_art.row_count, cols=6)
            w_art.update_cell(1, 6, "region")
            print("Upgraded Articles worksheet with region column.")
    except gspread.exceptions.WorksheetNotFound:
        sh.add_worksheet(title="Articles", rows="2000", cols="6")
        w = sh.worksheet("Articles")
        w.append_row(["title", "link", "published_at", "source", "company_name", "region"])

        
    # 3. Verify/Create 'Status' worksheet
    try:
        sh.worksheet("Status")
    except gspread.exceptions.WorksheetNotFound:
        sh.add_worksheet(title="Status", rows="10", cols="2")
        w = sh.worksheet("Status")
        w.append_row(["key", "value"])

    print("Google Sheets database initialized successfully.")

def get_all_companies(user_email: str = None):
    try:
        sh = get_gsheet()
        w = sh.worksheet("Companies")
        rows = w.get_all_values()
        if len(rows) <= 1:
            return []
        
        companies = []
        for row in rows[1:]:
            if len(row) >= 3:
                comp_id = int(row[0]) if row[0].isdigit() else 0
                comp_name = row[1]
                region = row[2]
                last_status = row[3] if len(row) > 3 else "Pending first fetch"
                row_email = row[4].strip() if len(row) > 4 else ""
                
                # Filter by user_email if provided
                if user_email:
                    if row_email.lower() != user_email.lower():
                        continue
                
                companies.append({
                    "id": comp_id,
                    "name": comp_name,
                    "region": region,
                    "last_status": last_status,
                    "user_email": row_email
                })
        return sorted(companies, key=lambda x: x["name"])
    except Exception as e:
        print(f"Error fetching companies: {e}")
        return []

def add_company(name: str, region: str = 'Global', user_email: str = ''):
    try:
        sh = get_gsheet()
        w = sh.worksheet("Companies")
        rows = w.get_all_values()
        
        for row in rows[1:]:
            if len(row) > 1 and row[1].strip().lower() == name.strip().lower():
                row_email = row[4].strip() if len(row) > 4 else ""
                if row_email.lower() == user_email.lower():
                    return False
                
        max_id = 0
        for row in rows[1:]:
            if row[0].isdigit():
                max_id = max(max_id, int(row[0]))
        new_id = max_id + 1
        
        w.append_row([new_id, name.strip(), region, "Pending first fetch", user_email])
        return True
    except Exception as e:
        print(f"Error adding company: {e}")
        return False

def remove_company(name: str, user_email: str = ''):
    try:
        sh = get_gsheet()
        w_comp = sh.worksheet("Companies")
        rows_comp = w_comp.get_all_values()
        
        row_idx_to_delete = -1
        for idx, row in enumerate(rows_comp):
            if idx > 0 and len(row) > 1 and row[1].strip().lower() == name.strip().lower():
                row_email = row[4].strip() if len(row) > 4 else ""
                if row_email.lower() == user_email.lower():
                    row_idx_to_delete = idx + 1
                    break
                
        if row_idx_to_delete != -1:
            w_comp.delete_rows(row_idx_to_delete)
            
        # Note: Articles are stored globally now without company_name,
        # so we don't delete them from the Articles sheet to preserve history.
        return True
    except Exception as e:
        print(f"Error removing company: {e}")
        return False

def update_company_status(company_id: int, status: str):
    try:
        sh = get_gsheet()
        w = sh.worksheet("Companies")
        rows = w.get_all_values()
        
        for idx, row in enumerate(rows):
            if idx > 0 and row[0].isdigit() and int(row[0]) == company_id:
                # Do not overwrite if company is manually paused by user
                current_status = row[3] if len(row) > 3 else ""
                if "paused" in current_status.lower():
                    return
                w.update_cell(idx + 1, 4, status)
                break
    except Exception as e:
        print(f"Error updating company status: {e}")

def toggle_company_pause(company_id: int, user_email: str = ''):
    try:
        sh = get_gsheet()
        w = sh.worksheet("Companies")
        rows = w.get_all_values()
        
        for idx, row in enumerate(rows):
            if idx > 0 and row[0].isdigit() and int(row[0]) == company_id:
                row_email = row[4].strip() if len(row) > 4 else ""
                if not user_email or row_email.lower() == user_email.lower():
                    current_status = row[3] if len(row) > 3 else ""
                    new_status = "Active" if "paused" in current_status.lower() else "Paused"
                    w.update_cell(idx + 1, 4, new_status)
                    return new_status
        return None
    except Exception as e:
        print(f"Error toggling company pause: {e}")
        return None

def get_user_articles_sheet(user_email: str = None):
    sh = get_gsheet()
    sheet_name = user_email.strip() if user_email and user_email.strip() else "Articles"
    try:
        w = sh.worksheet(sheet_name)
        if w.col_count < 6:
            w.resize(rows=w.row_count, cols=6)
            w.update_cell(1, 6, "region")
            print(f"Upgraded sheet '{sheet_name}' with region column.")
    except gspread.exceptions.WorksheetNotFound:
        # Create a new sheet for the specific user
        sh.add_worksheet(title=sheet_name, rows="2000", cols="6")
        w = sh.worksheet(sheet_name)
        w.append_row(["title", "link", "published_at", "source", "company_name", "region"])
    return w

def add_article(company_id: int, title: str, link: str, published_at: str, source: str, summary: str = None, sentiment: str = None, extraction_method: str = 'summary', user_email: str = '', company_name: str = 'Event Feed', region: str = 'Global'):
    try:
        w_art = get_user_articles_sheet(user_email)
        rows = w_art.get_all_values()
        
        # Check for duplicate links or titles inside the user's sheet (Column 1 is title, Column 2 is link)
        norm_link = link.strip().lower()
        norm_title = title.strip().lower()
        
        for row in rows[1:]:
            if len(row) > 1:
                db_title = row[0].strip().lower()
                db_link = row[1].strip().lower()
                if db_link == norm_link or db_title == norm_title:
                    return False
                
        w_art.append_row([
            title,
            link,
            published_at,
            source,
            company_name,
            region
        ])
        return True
    except Exception as e:
        print(f"Error adding article to user sheet: {e}")
        return False


def get_recent_articles(limit=50, user_email: str = None):
    try:
        w = get_user_articles_sheet(user_email)
        rows = w.get_all_values()
        if len(rows) <= 1:
            return []
            
        articles = []
        for row in reversed(rows[1:]):
            if len(row) < 4:
                row = row + [""] * (4 - len(row))
            
            comp_name = row[4].strip() if len(row) > 4 and row[4].strip() else "Event Feed"
            region_val = row[5].strip() if len(row) > 5 and row[5].strip() else "Global"

            articles.append({
                "title": row[0],
                "link": row[1],
                "published_at": row[2],
                "source": row[3],
                "company_name": comp_name,
                "region": region_val
            })
            if len(articles) >= limit:
                break
        return articles
    except Exception as e:
        print(f"Error getting recent articles: {e}")
        return []

def get_articles_for_brand(company_name, user_email: str = None):
    try:
        w = get_user_articles_sheet(user_email)
        rows = w.get_all_values()
        if len(rows) <= 1:
            return []
            
        articles = []
        for row in reversed(rows[1:]):
            if len(row) < 4:
                row = row + [""] * (4 - len(row))
                
            comp_name = row[4].strip() if len(row) > 4 and row[4].strip() else "Event Feed"
            region_val = row[5].strip() if len(row) > 5 and row[5].strip() else "Global"
            if company_name and comp_name.lower() != comp_name.lower():
                continue

            articles.append({
                "title": row[0],
                "link": row[1],
                "published_at": row[2],
                "source": row[3],
                "company_name": comp_name,
                "region": region_val
            })
        return articles
    except Exception as e:
        print(f"Error getting articles: {e}")
        return []

def set_last_fetch_time(timestamp_iso: str):
    try:
        sh = get_gsheet()
        w = sh.worksheet("Status")
        rows = w.get_all_values()
        
        found = False
        for idx, row in enumerate(rows):
            if idx > 0 and len(row) > 0 and row[0] == "last_fetch_time":
                w.update_cell(idx + 1, 2, timestamp_iso)
                found = True
                break
                
        if not found:
            w.append_row(["last_fetch_time", timestamp_iso])
    except Exception as e:
        print(f"Error setting last fetch time: {e}")

def get_last_fetch_time():
    try:
        sh = get_gsheet()
        w = sh.worksheet("Status")
        rows = w.get_all_values()
        
        for row in rows[1:]:
            if len(row) > 1 and row[0] == "last_fetch_time":
                return row[1]
        return None
    except Exception as e:
        print(f"Error getting last fetch time: {e}")
        return None

def is_paused() -> bool:
    try:
        sh = get_gsheet()
        w = sh.worksheet("Status")
        rows = w.get_all_values()
        
        for row in rows[1:]:
            if len(row) > 1 and row[0] == "is_paused":
                return row[1].strip().lower() == "true"
        return False
    except Exception as e:
        print(f"Error checking pause status: {e}")
        return False

def set_paused(paused: bool):
    try:
        sh = get_gsheet()
        w = sh.worksheet("Status")
        rows = w.get_all_values()
        
        val_str = "true" if paused else "false"
        found = False
        for idx, row in enumerate(rows):
            if idx > 0 and len(row) > 0 and row[0] == "is_paused":
                w.update_cell(idx + 1, 2, val_str)
                found = True
                break
                
        if not found:
            w.append_row(["is_paused", val_str])
    except Exception as e:
        print(f"Error setting pause status: {e}")

def delete_article(title: str, user_email: str = '') -> bool:
    try:
        sh = get_gsheet()
        w = sh.worksheet("Articles")
        rows = w.get_all_values()
        
        row_idx_to_delete = -1
        for idx, row in enumerate(rows):
            if idx > 0 and len(row) > 0 and row[0].strip().lower() == title.strip().lower():
                row_email = row[4].strip() if len(row) > 4 else ""
                if row_email.lower() == user_email.lower():
                    row_idx_to_delete = idx + 1
                    break
                
        if row_idx_to_delete != -1:
            w.delete_rows(row_idx_to_delete)
            return True
        return False
    except Exception as e:
        print(f"Error deleting article: {e}")
        return False

if __name__ == "__main__":
    init_db()
    print("Database initialized.")

