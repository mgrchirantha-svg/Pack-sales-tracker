import os
import libsql
import streamlit as st

# Page Configuration
st.set_page_config(
    page_title="Dashboard", page_icon="🍾", layout="wide"
)

# --- TURSO CLOUD CONNECTION ---
TURSO_URL = "libsql://pack-sales-record-chirantha.aws-ap-south-1.turso.io"
TURSO_TOKEN = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3OTA1Nzk1NTAsImlkIjoiMDFhMGU2N2ItMmEwMS03MjFjLWJhNTYtMTI2MTMwY2VlNDA5Iiwia2lkIjoiZ3dhSGsyaTV0TXlMWDRWS05KQ1BzQkx2Z01abFpNOG5yOFVVUmhmdzBKWSIsInJpZCI6IjcwMTUxZjI3LTlhZWItNDQzNS04NDZiLWJlMWU4MjQwMjk0NiJ9.UivYy_canij3-i5YvPsdujnIrxkc0nEcjNNf69q8ucCZLi2ZGTNU6QOJstTIua4bOCsYtDYLglU7Zttv0sYtDQ"


@st.cache_resource
def get_connection():
    db_path = "/tmp/bottle_sales.db"
    
    # If the local replica file is empty, remove it
    if os.path.exists(db_path) and os.path.getsize(db_path) == 0:
        os.remove(db_path)
        
    try:
        conn = libsql.connect(
            db_path, sync_url=TURSO_URL, auth_token=TURSO_TOKEN
        )
    except ValueError as e:
        if "file is not a database" in str(e):
            if os.path.exists(db_path):
                os.remove(db_path)
            # Clear Streamlit's cache to force a fresh connection
            st.cache_resource.clear()
            conn = libsql.connect(
                db_path, sync_url=TURSO_URL, auth_token=TURSO_TOKEN
            )
        else:
            raise e
            
    conn.sync()  # Sync once on initial boot
    return conn


conn = get_connection()
cursor = conn.cursor()

# Ensure tables exist
cursor.execute("""
    CREATE TABLE IF NOT EXISTS pack_sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pack_number INTEGER,
        cash_payment REAL,
        cash_deposit REAL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS pack_borrows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_id INTEGER,
        borrower_name TEXT,
        borrow_amount REAL,
        status TEXT DEFAULT 'Pending',
        FOREIGN KEY(sale_id) REFERENCES pack_sales(id)
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS temp_staged_borrows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pack_number INTEGER,
        borrower_name TEXT,
        borrow_amount REAL
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS bank_deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deposit_type TEXT,
        amount REAL,
        notes TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS bank_expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        expense_category TEXT,
        amount REAL,
        notes TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
""")
conn.commit()

# Initialize session state variables
if "show_password_prompt" not in st.session_state:
    st.session_state.show_password_prompt = False

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

# --- DASHBOARD HEADER ---
st.title("Web Dashboard")
st.markdown("Track packs, cash on hand, bank balances, and customer debts.")

# --- SECURE FINANCIAL BALANCES SECTION ---
if not st.session_state.authenticated:
    if st.button("🔒 Click to View Cash & Bank Balances"):
        st.session_state.show_password_prompt = (
            not st.session_state.show_password_prompt
        )

    if st.session_state.show_password_prompt:
        entered_pwd = st.text_input(
            "Enter Dashboard Password", type="password", key="header_pwd_input"
        )
        if st.button("Unlock"):
            correct_pwd = st.secrets.get("DASHBOARD_PASSWORD", "admin123")
            if entered_pwd == correct_pwd:
                st.session_state.authenticated = True
                st.session_state.show_password_prompt = False
                st.rerun()
            else:
                st.error("Incorrect password!")
else:
    if st.button("🔒 Lock Balances"):
        st.session_state.authenticated = False
        st.rerun()

    # Calculate and display balances only when unlocked
    cursor.execute("""
        SELECT 
            (SELECT COALESCE(SUM(cash_payment), 0) FROM pack_sales) +
            (SELECT COALESCE(SUM(borrow_amount), 0) FROM pack_borrows WHERE status = 'Paid')
    """)
    total_cash_collected = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(cash_deposit), 0) FROM pack_sales")
    sales_bank_deposits = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM bank_deposits")
    total_standalone_deposits = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0) FROM bank_deposits 
        WHERE deposit_type LIKE '%Cash on Hand%'
    """)
    cash_drawer_to_bank_deposits = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM bank_expenses")
    total_bank_expenses = cursor.fetchone()[0]

    cash_on_hand = total_cash_collected - cash_drawer_to_bank_deposits
    total_bank_balance = (
        sales_bank_deposits + total_standalone_deposits - total_bank_expenses
    )

    col1, col2 = st.columns(2)
    col1.metric("💵 Cash on Hand", f"Rs. {cash_on_hand:,.2f}")
    col2.metric("🏦 Bank Account Balance", f"Rs. {total_bank_balance:,.2f}")

st.divider()

# --- TABS FOR WEB APP ACTIONS ---
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    [
        "📦 Record Sales",
        "🏦 Bank Deposits",
        "💸 Bank Expenses",
        "⚠️ Manage Debts",
        "📊 Sales History",
    ]
)

# Determine current pack state
cursor.execute("SELECT pack_number FROM pack_sales ORDER BY id DESC LIMIT 1")
row = cursor.fetchone()
current_pack = (row[0] % 10) + 1 if row else 1

with tab1:
    st.subheader(f"Recording Pack: {current_pack} of 10")

    cash_pay = st.number_input(
        "Cash Payment Received (Rs.)", min_value=0.0, step=10.0, key="cash_pay_in"
    )
    bank_dep = st.number_input(
        "Direct Customer Bank Transfer Amount (Rs.)",
        min_value=0.0,
        step=1
    )
