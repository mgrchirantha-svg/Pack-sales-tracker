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

# Sync latest cloud data
try:
  conn.sync()
except:
  pass

# Initialize session state variables
if "show_password_prompt" not in st.session_state:
  st.session_state.show_password_prompt = False

if "authenticated" not in st.session_state:
  st.session_state.authenticated = False

# --- DASHBOARD HEADER ---
st.title("🍾 Bottle Shop Web Dashboard")
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
      cursor.execute(
          """
                INSERT INTO temp_staged_borrows (pack_number, borrower_name, borrow_amount)
                VALUES (?, ?, ?)
            """,
          (current_pack, b_name.strip(), b_amt),
      )
      conn.commit()
      conn.sync()
      st.success(f"Added {b_name.strip()} (Rs. {b_amt:,.2f}) securely.")
      st.rerun()
    else:
      st.error("Please enter a valid borrower name and amount.")

  cursor.execute(
      "SELECT id, borrower_name, borrow_amount FROM temp_staged_borrows WHERE"
      " pack_number = ?",
      (current_pack,),
  )
  staged_borrowers = cursor.fetchall()

  if staged_borrowers:
    st.markdown("**Queue of Borrowers for This Pack:**")
    for row_item in staged_borrowers:
      b_id, name, amount = row_item
      col_a, col_b = st.columns([4, 1])
      col_a.text(f"{name} : Rs. {amount:,.2f}")
      if col_b.button("Remove", key=f"rm_staged_{b_id}"):
        cursor.execute("DELETE FROM temp_staged_borrows WHERE id = ?", (b_id,))
        conn.commit()
        conn.sync()
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

    for row_item in staged_borrowers:
      b_id, name, amount = row_item
      cursor.execute(
          """
                INSERT INTO pack_borrows (sale_id, borrower_name, borrow_amount, status)
                VALUES (?, ?, ?, 'Pending')
            """,
          (sale_id, name, amount),
      )

    cursor.execute(
        "DELETE FROM temp_staged_borrows WHERE pack_number = ?", (current_pack,)
    )
    conn.commit()
    conn.sync()

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
  st.subheader("Record Bank Account Expenses (Purchases)")
  with st.form("expense_form"):
    exp_desc = st.text_input("Expense Description / Note")
    exp_amt = st.number_input(
        "Amount (Rs.)", min_value=0.0, step=100.0, key="exp_amt_input"
    )

    exp_submitted = st.form_submit_button("Submit Expense")
    if exp_submitted:
      if not exp_desc.strip():
        st.error("Please enter a description for the expense.")
      elif exp_amt <= 0:
        st.error("Please enter a valid expense amount.")
      else:
        cursor.execute(
            """
                    INSERT INTO bank_expenses (expense_category, amount, notes)
                    VALUES (?, ?, ?)
                """,
            ("Bank Expense", exp_amt, exp_desc.strip()),
        )
        conn.commit()
        conn.sync()
        st.success("Bank expense recorded and balance updated!")
        st.rerun()

  st.markdown("### Recent Bank Expenses History")
  cursor.execute(
      "SELECT id, notes, amount, timestamp FROM bank_expenses ORDER BY id DESC"
      " LIMIT 10"
  )
  expense_data = cursor.fetchall()
  if expense_data:
    st.table(
        [
            {
                "ID": r[0],
                "Expense Description": r[1],
                "Amount": f"Rs. {r[2]:,.2f}",
                "Date/Time": r[3],
            }
            for r in expense_data
        ]
    )
  else:
    st.info("No bank expenses recorded yet.")

with tab4:
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

with tab5:
  st.subheader("Sales History (Grouped by Sets of 10 Packs)")

  # Fetch all sales data ordered chronologically/by ID
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
        ORDER BY ps.id ASC
    """)
  history_data = cursor.fetchall()

  if history_data:
    # Group rows into batches of 10 based on position or total sales count
    batches = {}
    for i, r in enumerate(history_data):
      # Calculate batch block (e.g., Sales 1-10 -> Group 1, 11-20 -> Group 2)
      batch_num = (i // 10) + 1
      start_pack = ((batch_num - 1) * 10) + 1
      end_pack = batch_num * 10
      batch_key = f"Packs {start_pack} to {end_pack} (Batch {batch_num})"

      if batch_key not in batches:
        batches[batch_key] = []
      batches[batch_key].append(r)

    # Display each batch in reverse order so the newest set of 10 is at the top
    for batch_title, rows in sorted(batches.items(), reverse=True):
      # Default latest batch to expanded, older batches collapsed
      is_expanded = batch_title == list(batches.keys())[-1]

      with st.expander(batch_title, expanded=is_expanded):
        table_rows = []
        batch_total_received = 0.0

        for r in rows:
          total_received_val = r[5]
          batch_total_received += total_received_val

          table_rows.append({
              "Sale ID": r[0],
              "Pack #": r[1],
              "Cash Payment": f"Rs. {r[2]:,.2f}",
              "Bank Deposit": f"Rs. {r[3]:,.2f}",
              "Money to Receive": f"Rs. {r[4]:,.2f}",
              "Total Received": f"Rs. {r[5]:,.2f}",
          })

        st.table(table_rows)
        st.markdown(
            f"**Total Received for this Group of Packs:** Rs."
            f" {batch_total_received:,.2f}"
        )
  else:
    st.info("No sales recorded yet.")
