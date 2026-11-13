import os
import threading
from collections import OrderedDict

import libsql
import streamlit as st


# ============================================================
# PAGE CONFIGURATION
# ============================================================
st.set_page_config(
    page_title="Dashboard",
    page_icon="🍾",
    layout="wide",
)

DB_PATH = os.environ.get("TURSO_LOCAL_DB_PATH", "/tmp/bottle_sales.db")


# ============================================================
# DATABASE CONNECTION
# ============================================================
def get_secret(name: str) -> str:
    """Read a required value from Streamlit secrets."""
    value = st.secrets.get(name)
    if not value:
        st.error(
            f"Missing Streamlit secret: `{name}`. "
            "Add it to your Streamlit secrets configuration."
        )
        st.stop()
    return str(value)


@st.cache_resource
def get_database():
    """
    Create one shared embedded-replica connection for this app process.

    The lock prevents simultaneous Streamlit sessions from using the same
    connection/cursor at the same time.
    """
    turso_url = get_secret("TURSO_DATABASE_URL")
    turso_token = get_secret("TURSO_AUTH_TOKEN")

    connection = libsql.connect(
        DB_PATH,
        sync_url=turso_url,
        auth_token=turso_token,
    )
    lock = threading.RLock()

    with lock:
        connection.sync()

    return connection, lock


conn, db_lock = get_database()


def fetch_one(sql: str, params=()):
    """Run a read query safely and return one row."""
    with db_lock:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchone()


def fetch_all(sql: str, params=()):
    """Run a read query safely and return all rows."""
    with db_lock:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchall()


def write_transaction(operation):
    """
    Execute all related writes in one transaction, then sync once to Turso.

    `operation` receives a cursor and should return any value needed by
    the caller. A sync error is reported separately because the local
    transaction may already have committed successfully.
    """
    with db_lock:
        cursor = conn.cursor()
        cursor.execute("BEGIN")

        try:
            result = operation(cursor)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        try:
            conn.sync()
            sync_error = None
        except Exception as exc:
            sync_error = str(exc)

        return result, sync_error


def show_write_result(success_message: str, sync_error):
    """Show an accurate result after a committed local write."""
    if sync_error:
        st.warning(
            f"{success_message} The change was saved locally, but syncing "
            "with Turso failed. Check your internet connection and refresh "
            f"the app before making the same entry again. Details: {sync_error}"
        )
    else:
        st.success(success_message)


# ============================================================
# DATABASE TABLES AND INDEXES
# ============================================================
def initialize_database():
    def create_schema(cursor):
        statements = [
            """
            CREATE TABLE IF NOT EXISTS pack_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pack_number INTEGER,
                cash_payment REAL,
                cash_deposit REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS pack_borrows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_id INTEGER,
                borrower_name TEXT,
                borrow_amount REAL,
                status TEXT DEFAULT 'Pending',
                FOREIGN KEY(sale_id) REFERENCES pack_sales(id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS temp_staged_borrows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pack_number INTEGER,
                borrower_name TEXT,
                borrow_amount REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS bank_deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deposit_type TEXT,
                amount REAL,
                notes TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS bank_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expense_category TEXT,
                amount REAL,
                notes TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_pack_sales_id ON pack_sales(id)",
            "CREATE INDEX IF NOT EXISTS idx_pack_borrows_sale_id ON pack_borrows(sale_id)",
            "CREATE INDEX IF NOT EXISTS idx_pack_borrows_status ON pack_borrows(status)",
            "CREATE INDEX IF NOT EXISTS idx_staged_pack_number ON temp_staged_borrows(pack_number)",
            "CREATE INDEX IF NOT EXISTS idx_bank_expenses_id ON bank_expenses(id)",
        ]

        for statement in statements:
            cursor.execute(statement)

    _, sync_error = write_transaction(create_schema)
    return sync_error


try:
    schema_sync_error = initialize_database()
except Exception as exc:
    st.error(f"Could not initialize the Turso database: {exc}")
    st.stop()


# ============================================================
# SMALL HELPERS
# ============================================================
def money(value) -> str:
    return f"Rs. {float(value or 0):,.2f}"


def get_current_pack() -> int:
    row = fetch_one(
        "SELECT pack_number FROM pack_sales ORDER BY id DESC LIMIT 1"
    )
    if not row or row[0] is None:
        return 1
    return (int(row[0]) % 10) + 1


def get_financial_balances():
    """
    Preserve the original balance rules:
    - Cash on hand = cash payments + paid debts - cash-on-hand deposits.
    - Bank = sales bank deposits + standalone deposits - bank expenses.
    - Personal investments increase bank balance but not cash on hand or sales.
    """
    row = fetch_one(
        """
        SELECT
            (
                SELECT COALESCE(SUM(cash_payment), 0)
                FROM pack_sales
            ) +
            (
                SELECT COALESCE(SUM(borrow_amount), 0)
                FROM pack_borrows
                WHERE status = 'Paid'
            ) AS total_cash_collected,

            (
                SELECT COALESCE(SUM(cash_deposit), 0)
                FROM pack_sales
            ) AS sales_bank_deposits,

            (
                SELECT COALESCE(SUM(amount), 0)
                FROM bank_deposits
            ) AS standalone_deposits,

            (
                SELECT COALESCE(SUM(amount), 0)
                FROM bank_deposits
                WHERE deposit_type LIKE '%Cash on Hand%'
            ) AS cash_drawer_deposits,

            (
                SELECT COALESCE(SUM(amount), 0)
                FROM bank_expenses
            ) AS bank_expenses
        """
    )

    total_cash_collected = float(row[0] or 0)
    sales_bank_deposits = float(row[1] or 0)
    standalone_deposits = float(row[2] or 0)
    cash_drawer_deposits = float(row[3] or 0)
    bank_expenses = float(row[4] or 0)

    cash_on_hand = total_cash_collected - cash_drawer_deposits
    bank_balance = (
        sales_bank_deposits + standalone_deposits - bank_expenses
    )
    return cash_on_hand, bank_balance


def set_flash(message: str, kind: str = "success"):
    st.session_state["dashboard_flash"] = (message, kind)


def display_flash():
    flash = st.session_state.pop("dashboard_flash", None)
    if not flash:
        return

    message, kind = flash
    if kind == "error":
        st.error(message)
    elif kind == "warning":
        st.warning(message)
    else:
        st.success(message)


# ============================================================
# SESSION STATE AND HEADER
# ============================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if "show_password_prompt" not in st.session_state:
    st.session_state.show_password_prompt = False

st.title("Web Dashboard")
st.caption("Track packs, cash on hand, bank balances, and customer debts.")

display_flash()

if schema_sync_error:
    st.warning(
        "Database tables are ready locally, but the initial sync with Turso "
        f"reported an error: {schema_sync_error}"
    )


# ============================================================
# PASSWORD-PROTECTED FINANCIAL SUMMARY
# ============================================================
st.subheader("Financial Summary")

if not st.session_state.authenticated:
    if st.button("🔒 Click to View Cash & Bank Balances"):
        st.session_state.show_password_prompt = (
            not st.session_state.show_password_prompt
        )

    if st.session_state.show_password_prompt:
        with st.form("dashboard_unlock_form"):
            entered_password = st.text_input(
                "Dashboard Password",
                type="password",
            )
            unlock_submitted = st.form_submit_button("Unlock")

        if unlock_submitted:
            correct_password = get_secret("DASHBOARD_PASSWORD")
            if entered_password == correct_password:
                st.session_state.authenticated = True
                st.session_state.show_password_prompt = False
                st.rerun()
            else:
                st.error("Incorrect password.")
else:
    if st.button("🔒 Lock Balances"):
        st.session_state.authenticated = False
        st.rerun()

    cash_on_hand, bank_balance = get_financial_balances()
    balance_col1, balance_col2 = st.columns(2)
    balance_col1.metric("💵 Cash on Hand", money(cash_on_hand))
    balance_col2.metric("🏦 Bank Account Balance", money(bank_balance))

st.divider()


# ============================================================
# NAVIGATION
# Using radio navigation instead of st.tabs means only the selected
# section's queries and UI are executed on each rerun.
# ============================================================
sections = [
    "📦 Record Sales",
    "🏦 Bank Deposits",
    "💸 Bank Expenses",
    "⚠️ Manage Debts",
    "📊 Sales History",
]

selected_section = st.radio(
    "Dashboard section",
    sections,
    horizontal=True,
    label_visibility="collapsed",
)


# ============================================================
# 1. RECORD SALES
# ============================================================
if selected_section == "📦 Record Sales":
    current_pack = get_current_pack()
    st.subheader(f"Recording Pack: {current_pack} of 10")

    # Form fields do not trigger a complete script rerun for every keystroke.
    with st.form("pack_sales_form", clear_on_submit=False):
        cash_payment = st.number_input(
            "Cash Payment Received (Rs.)",
            min_value=0.0,
            step=10.0,
            key="cash_pay_in",
        )
        bank_transfer = st.number_input(
            "Direct Customer Bank Transfer Amount (Rs.)",
            min_value=0.0,
            step=10.0,
            key="bank_dep_in",
        )
        save_pack_submitted = st.form_submit_button(
            "Save Pack & Move to Next",
            type="primary",
        )

    st.markdown("### Add Borrowers for This Pack")

    with st.form("add_borrower_form", clear_on_submit=True):
        borrower_name = st.text_input("Borrower Name")
        borrower_amount = st.number_input(
            "Borrow Amount (Rs.)",
            min_value=0.0,
            step=10.0,
        )
        add_borrower_submitted = st.form_submit_button(
            "+ Add Borrower to List"
        )

    if add_borrower_submitted:
        if borrower_name.strip() and borrower_amount > 0:
            def add_borrower(cursor):
                cursor.execute(
                    """
                    INSERT INTO temp_staged_borrows
                        (pack_number, borrower_name, borrow_amount)
                    VALUES (?, ?, ?)
                    """,
                    (
                        current_pack,
                        borrower_name.strip(),
                        borrower_amount,
                    ),
                )

            try:
                _, sync_error = write_transaction(add_borrower)
                show_write_result(
                    f"Added {borrower_name.strip()} ({money(borrower_amount)}).",
                    sync_error,
                )
            except Exception as exc:
                st.error(f"Could not add borrower: {exc}")
        else:
            st.error("Please enter a valid borrower name and amount.")

    staged_borrowers = fetch_all(
        """
        SELECT id, borrower_name, borrow_amount
        FROM temp_staged_borrows
        WHERE pack_number = ?
        ORDER BY id
        """,
        (current_pack,),
    )

    st.markdown("### Borrowers Queued for This Pack")

    if staged_borrowers:
        for staged_id, name, amount in staged_borrowers:
            col_name, col_amount, col_remove = st.columns([4, 2, 1])
            col_name.write(name)
            col_amount.write(money(amount))

            if col_remove.button("Remove", key=f"remove_staged_{staged_id}"):
                try:
                    def remove_borrower(cursor):
                        cursor.execute(
                            "DELETE FROM temp_staged_borrows WHERE id = ?",
                            (staged_id,),
                        )

                    _, sync_error = write_transaction(remove_borrower)
                    show_write_result("Borrower removed from the queue.", sync_error)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not remove borrower: {exc}")
    else:
        st.info("No borrowers added to this pack yet.")

    if save_pack_submitted:
        # Re-read the queue immediately before saving so the transaction
        # uses the latest staged borrowers.
        latest_staged = fetch_all(
            """
            SELECT id, borrower_name, borrow_amount
            FROM temp_staged_borrows
            WHERE pack_number = ?
            ORDER BY id
            """,
            (current_pack,),
        )

        def save_pack(cursor):
            cursor.execute(
                """
                INSERT INTO pack_sales
                    (pack_number, cash_payment, cash_deposit)
                VALUES (?, ?, ?)
                """,
                (current_pack, cash_payment, bank_transfer),
            )
            sale_id = cursor.lastrowid

            for _, name, amount in latest_staged:
                cursor.execute(
                    """
                    INSERT INTO pack_borrows
                        (sale_id, borrower_name, borrow_amount, status)
                    VALUES (?, ?, ?, 'Pending')
                    """,
                    (sale_id, name, amount),
                )

            cursor.execute(
                "DELETE FROM temp_staged_borrows WHERE pack_number = ?",
                (current_pack,),
            )
            return sale_id

        try:
            sale_id, sync_error = write_transaction(save_pack)

            # Clear the two pack-entry values only after the transaction commits.
            st.session_state["cash_pay_in"] = 0.0
            st.session_state["bank_dep_in"] = 0.0

            if sync_error:
                set_flash(
                    f"Pack {current_pack} (Sale ID {sale_id}) was saved locally, "
                    "but syncing with Turso failed. Check the connection before "
                    "re-entering this pack.",
                    "warning",
                )
            else:
                set_flash(
                    f"Pack {current_pack} saved and synced successfully!"
                )
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save pack: {exc}")


# ============================================================
# 2. BANK DEPOSITS
# ============================================================
elif selected_section == "🏦 Bank Deposits":
    st.subheader("Make Standalone Bank Deposits")

    with st.form("bank_deposit_form", clear_on_submit=True):
        deposit_type = st.selectbox(
            "Deposit Type",
            [
                "Deposit Cash on Hand to Bank",
                "Personal Money Investment to Bank",
            ],
        )
        deposit_amount = st.number_input(
            "Amount (Rs.)",
            min_value=0.0,
            step=100.0,
        )
        deposit_notes = st.text_input("Notes / Description")
        deposit_submitted = st.form_submit_button("Submit Deposit")

    if deposit_submitted:
        if deposit_amount <= 0:
            st.error("Please enter a valid amount.")
        else:
            def save_deposit(cursor):
                cursor.execute(
                    """
                    INSERT INTO bank_deposits
                        (deposit_type, amount, notes)
                    VALUES (?, ?, ?)
                    """,
                    (deposit_type, deposit_amount, deposit_notes.strip()),
                )

            try:
                _, sync_error = write_transaction(save_deposit)
                show_write_result("Bank deposit recorded.", sync_error)
            except Exception as exc:
                st.error(f"Could not record bank deposit: {exc}")


# ============================================================
# 3. BANK EXPENSES
# ============================================================
elif selected_section == "💸 Bank Expenses":
    st.subheader("Record Bank Account Expenses (Purchases)")

    with st.form("bank_expense_form", clear_on_submit=True):
        expense_description = st.text_input("Expense Description / Note")
        expense_amount = st.number_input(
            "Amount (Rs.)",
            min_value=0.0,
            step=100.0,
        )
        expense_submitted = st.form_submit_button("Submit Expense")

    if expense_submitted:
        if not expense_description.strip():
            st.error("Please enter a description for the expense.")
        elif expense_amount <= 0:
            st.error("Please enter a valid expense amount.")
        else:
            def save_expense(cursor):
                cursor.execute(
                    """
                    INSERT INTO bank_expenses
                        (expense_category, amount, notes)
                    VALUES (?, ?, ?)
                    """,
                    ("Bank Expense", expense_amount, expense_description.strip()),
                )

            try:
                _, sync_error = write_transaction(save_expense)
                show_write_result(
                    "Bank expense recorded and balance updated.",
                    sync_error,
                )
            except Exception as exc:
                st.error(f"Could not record bank expense: {exc}")

    st.markdown("### Recent Bank Expenses History")
    expense_rows = fetch_all(
        """
        SELECT id, notes, amount, timestamp
        FROM bank_expenses
        ORDER BY id DESC
        LIMIT 10
        """
    )

    if expense_rows:
        st.dataframe(
            [
                {
                    "ID": row[0],
                    "Expense Description": row[1],
                    "Amount": money(row[2]),
                    "Date/Time": row[3],
                }
                for row in expense_rows
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No bank expenses recorded yet.")


# ============================================================
# 4. MANAGE DEBTS
# ============================================================
elif selected_section == "⚠️ Manage Debts":
    st.subheader("Outstanding Customer Borrows")

    pending_debts = fetch_all(
        """
        SELECT
            pb.id,
            ps.pack_number,
            pb.borrower_name,
            pb.borrow_amount
        FROM pack_borrows AS pb
        INNER JOIN pack_sales AS ps ON pb.sale_id = ps.id
        WHERE pb.status = 'Pending'
        ORDER BY pb.id
        """
    )

    if pending_debts:
        for debt_id, pack_number, borrower, amount in pending_debts:
            col_customer, col_pack, col_amount, col_action = st.columns(
                [3, 1.5, 2, 1.5]
            )
            col_customer.write(f"**Customer:** {borrower}")
            col_pack.write(f"Pack #{pack_number}")
            col_amount.write(money(amount))

            if col_action.button("Mark Paid", key=f"mark_paid_{debt_id}"):
                def mark_debt_paid(cursor):
                    # The status condition makes this operation idempotent:
                    # a second click cannot mark or count the same debt twice.
                    cursor.execute(
                        """
                        UPDATE pack_borrows
                        SET status = 'Paid'
                        WHERE id = ? AND status = 'Pending'
                        """,
                        (debt_id,),
                    )
                    return cursor.rowcount

                try:
                    updated_rows, sync_error = write_transaction(mark_debt_paid)

                    if updated_rows:
                        if sync_error:
                            set_flash(
                                f"Debt for {borrower} was marked paid locally, "
                                "but Turso sync failed. Check the connection.",
                                "warning",
                            )
                        else:
                            set_flash(f"Cleared debt for {borrower}!")
                    else:
                        set_flash(
                            f"This debt for {borrower} was already marked paid.",
                            "warning",
                        )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not update payment status: {exc}")
    else:
        st.info("No pending customer debts found.")


# ============================================================
# 5. SALES HISTORY
# ============================================================
elif selected_section == "📊 Sales History":
    st.subheader("Sales History (Grouped by Sets of 10 Packs)")

    history_data = fetch_all(
        """
        SELECT
            ps.id,
            ps.pack_number,
            (
                COALESCE(ps.cash_payment, 0) +
                COALESCE(
                    SUM(
                        CASE
                            WHEN pb.status = 'Paid'
                            THEN pb.borrow_amount
                            ELSE 0
                        END
                    ),
                    0
                )
            ) AS cash_received,
            COALESCE(ps.cash_deposit, 0) AS bank_deposit,
            COALESCE(
                SUM(
                    CASE
                        WHEN pb.status = 'Pending'
                        THEN pb.borrow_amount
                        ELSE 0
                    END
                ),
                0
            ) AS money_to_receive,
            (
                COALESCE(ps.cash_payment, 0) +
                COALESCE(
                    SUM(
                        CASE
                            WHEN pb.status = 'Paid'
                            THEN pb.borrow_amount
                            ELSE 0
                        END
                    ),
                    0
                ) +
                COALESCE(ps.cash_deposit, 0)
            ) AS total_received
        FROM pack_sales AS ps
        LEFT JOIN pack_borrows AS pb ON ps.id = pb.sale_id
        GROUP BY
            ps.id,
            ps.pack_number,
            ps.cash_payment,
            ps.cash_deposit
        ORDER BY ps.id ASC
        """
    )

    if history_data:
        batches = OrderedDict()

        for index, row in enumerate(history_data):
            batch_number = (index // 10) + 1
            first_pack = ((batch_number - 1) * 10) + 1
            last_pack = batch_number * 10
            batch_title = (
                f"Packs {first_pack} to {last_pack} "
                f"(Batch {batch_number})"
            )
            batches.setdefault(batch_title, []).append(row)

        # Display newest batch first, matching the previous dashboard.
        batch_items = list(batches.items())
        batch_items.reverse()

        for batch_index, (batch_title, rows) in enumerate(batch_items):
            with st.expander(
                batch_title,
                expanded=(batch_index == 0),
            ):
                table_rows = []
                batch_total_received = 0.0

                for row in rows:
                    total_received = float(row[5] or 0)
                    batch_total_received += total_received

                    table_rows.append(
                        {
                            "Sale ID": row[0],
                            "Pack #": row[1],
                            "Cash Payment": money(row[2]),
                            "Bank Deposit": money(row[3]),
                            "Money to Receive": money(row[4]),
                            "Total Received": money(row[5]),
                        }
                    )

                st.dataframe(
                    table_rows,
                    use_container_width=True,
                    hide_index=True,
                )
                st.markdown(
                    "**Total Received for this Group of Packs:** "
                    f"{money(batch_total_received)}"
                )
    else:
        st.info("No sales recorded yet.")


# ============================================================
# END OF DASHBOARD
# ============================================================
