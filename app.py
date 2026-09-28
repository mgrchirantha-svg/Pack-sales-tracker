import libsql
import streamlit as st

# Page Configuration
st.set_page_config(
    page_title="Bottle Shop Dashboard", page_icon="🍾", layout="wide"
)

# --- TURSO CLOUD CONNECTION ---
TURSO_URL = "libsql://pack-sales-record-chirantha.aws-ap-south-1.turso.io"
TURSO_TOKEN = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3OTA1Nzk1NTAsImlkIjoiMDFhMGU2N2ItMmEwMS03MjFjLWJhNTYtMTI2MTMwY2VlNDA5Iiwia2lkIjoiZ3dhSGsyaTV0TXlMWDRWS05KQ1BzQkx2Z01abFpNOG5yOFVVUmhmdzBKWSIsInJpZCI6IjcwMTUxZjI3LTlhZWItNDQzNS04NDZiLWJlMWU4MjQwMjk0NiJ9.UivYy_canij3-i5YvPsdujnIrxkc0nEcjNNf69q8ucCZLi2ZGTNU6QOJstTIua4bOCsYtDYLglU7Zttv0sYtDQ"


@st.cache_resource
def get_connection():
  conn = libsql.connect(
      "/tmp/bottle_sales.db", sync_url=TURSO_URL, auth_token=TURSO_TOKEN
  )
  conn.sync()
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
    CREATE TABLE IF NOT EXISTS bank_deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deposit_type TEXT,
        amount REAL,
        notes TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
""")
conn.commit()

# Sync latest cloud data
try:
  conn.sync()
except:
  pass

# Initialize session state for temporary multi-borrowers list & authentication
if "temp_borrowers" not in st.session_state:
  st.session_state.temp_borrowers = []

if "authenticated" not in st.session_state:
  st.session_state.authenticated = False

# --- DASHBOARD HEADER ---
st.title("🍾 Bottle Shop Web Dashboard")
st.markdown("Track packs, cash on hand, bank balances, and customer debts.")

# --- SECURE FINANCIAL METRICS SUMMARY ---
with st.expander("🔒 View Financial Summary (Password Required)", expanded=False):
  if not st.session_state.authenticated:
    entered_password = st.text_input(
        "Enter Dashboard Password", type="password", key="pwd_input"
    )
    if st.button("Unlock Balances"):
      # Use password from Streamlit secrets if available, else default to 'admin123'
      correct_password = st.secrets.get("DASHBOARD_PASSWORD", "admin123")
      if entered_password == correct_password:
        st.session_state.authenticated = True
        st.success("Unlocked successfully!")
        st.rerun()
      else:
        st.error("Incorrect password! Please try again.")
  else:
    if st.button("Lock Balances"):
      st.session_state.authenticated = False
      st.rerun()

    # Calculate metrics only when unlocked
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

    cash_on_hand = total_cash_collected - cash_drawer_to_bank_deposits
    total_bank_balance = sales_bank_deposits + total_standalone_deposits

    col1, col2 = st.columns(2)
    col1.metric("💵 Cash on Hand", f"Rs. {cash_on_hand:,.2f}")
    col2.metric("🏦 Bank Account Balance", f"Rs. {total_bank_balance:,.2f}")

st.divider()

# --- TABS FOR WEB APP ACTIONS ---
tab1, tab2, tab3, tab4 = st.tabs(
    ["📦 Record Sales", "🏦 Bank Deposits", "⚠️ Manage Debts", "📊 Sales History"]
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
      step=10.0,
      key="bank_dep_in",
  )

  st.markdown("### Add Borrowers for This Pack")
  b_name = st.text_input("Borrower Name", key="b_name_input")
  b_amt = st.number_input(
      "Borrow Amount (Rs.)", min_value=0.0, step=10.0, key="b_amt_input"
  )

  if st.button("+ Add Borrower to List"):
    if b_name.strip() and b_amt > 0:
      st.session_state.temp_borrowers.append(
          {"name": b_name.strip(), "amount": b_amt}
      )
      st.success(f"Added {b_name.strip()} (Rs. {b_amt:,.2f}) to this pack.")
    else:
      st.error("Please enter a valid borrower name and amount.")

  if st.session_state.temp_borrowers:
    st.markdown("**Queue of Borrowers for This Pack:**")
    for idx, b in enumerate(st.session_state.temp_borrowers):
      col_a, col_b = st.columns([4, 1])
      col_a.text(f"{b['name']} : Rs. {b['amount']:,.2f}")
      if col_b.button("Remove", key=f"rm_borrower_{idx}"):
        st.session_state.temp_borrowers.pop(idx)
        st.rerun()

  if st.button("Save Pack & Move to Next", type="primary"):
    cursor.execute(
        """
            INSERT INTO pack_sales (pack_number, cash_payment, cash_deposit)
            VALUES (?, ?, ?)
        """,
        (current_pack, cash_pay, bank_dep),
    )
    sale_id = cursor.lastrowid

    for b in st.session_state.temp_borrowers:
      cursor.execute(
          """
                INSERT INTO pack_borrows (sale_id, borrower_name, borrow_amount, status)
                VALUES (?, ?, ?, 'Pending')
            """,
          (sale_id, b["name"], b["amount"]),
      )

    conn.commit()
    conn.sync()

    st.session_state.temp_borrowers = []
    if "cash_pay_in" in st.session_state:
      del st.session_state["cash_pay_in"]
    if "bank_dep_in" in st.session_state:
      del st.session_state["bank_dep_in"]

    st.success(f"Pack {current_pack} saved and synced successfully!")
    st.rerun()

with tab2:
  st.subheader("Make Standalone Bank Deposits")
  with st.form("bank_form"):
    dep_type = st.selectbox(
        "Deposit Type",
        [
            "Deposit Cash on Hand to Bank",
            "Personal Money Investment to Bank",
        ],
    )
    dep_amt = st.number_input(
        "Amount (Rs.)", min_value=0.0, step=100.0, key="bank_amt"
    )
    dep_notes = st.text_input("Notes / Description")

    bank_submitted = st.form_submit_button("Submit Deposit")
    if bank_submitted:
      if dep_amt <= 0:
        st.error("Please enter a valid amount.")
      else:
        cursor.execute(
            """
                    INSERT INTO bank_deposits (deposit_type, amount, notes)
                    VALUES (?, ?, ?)
                """,
            (dep_type, dep_amt, dep_notes),
        )
        conn.commit()
        conn.sync()
        st.success("Bank deposit recorded and synced!")
        st.rerun()

with tab3:
  st.subheader("Outstanding Customer Borrows")
  cursor.execute("""
        SELECT pb.id, ps.pack_number, pb.borrower_name, pb.borrow_amount 
        FROM pack_borrows pb
        JOIN pack_sales ps ON pb.sale_id = ps.id
        WHERE pb.status = 'Pending'
    """)
  pending_debts = cursor.fetchall()

  if pending_debts:
    for debt in pending_debts:
      d_id, pack_no, borrower, amount = debt
      cols = st.columns([3, 2, 2, 2])
      cols[0].text(f"Customer: {borrower}")
      cols[1].text(f"Pack #{pack_no}")
      cols[2].text(f"Rs. {amount:,.2f}")
      if cols[3].button("Mark Paid", key=f"pay_{d_id}"):
        cursor.execute(
            "UPDATE pack_borrows SET status = 'Paid' WHERE id = ?", (d_id,)
        )
        conn.commit()
        conn.sync()
        st.success(f"Cleared debt for {borrower}!")
        st.rerun()
  else:
    st.info("No pending customer debts found.")

with tab4:
  st.subheader("Sales History")
  cursor.execute("""
        SELECT 
            ps.id, 
            ps.pack_number, 
            (ps.cash_payment + COALESCE(SUM(CASE WHEN pb.status = 'Paid' THEN pb.borrow_amount ELSE 0 END), 0)),
            ps.cash_deposit,
            COALESCE(SUM(CASE WHEN pb.status = 'Pending' THEN pb.borrow_amount ELSE 0 END), 0),
            (ps.cash_payment + COALESCE(SUM(CASE WHEN pb.status = 'Paid' THEN pb.borrow_amount ELSE 0 END), 0) + ps.cash_deposit)
        FROM pack_sales ps
        LEFT JOIN pack_borrows pb ON ps.id = pb.sale_id
        GROUP BY ps.id
        ORDER BY ps.id DESC
    """)
  history_data = cursor.fetchall()
  if history_data:
    st.table(
        [
            {
                "Sale ID": r[0],
                "Pack #": r[1],
                "Cash Payment": f"Rs. {r[2]:,.2f}",
                "Bank Deposit": f"Rs. {r[3]:,.2f}",
                "Money to Receive": f"Rs. {r[4]:,.2f}",
                "Total Received": f"Rs. {r[5]:,.2f}",
            }
            for r in history_data
        ]
    )
  else:
    st.info("No sales recorded yet.")
