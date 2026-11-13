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

DB_PATH = os.environ.get(
    "TURSO_LOCAL_DB_PATH",
    "/tmp/bottle_sales.db"
)


# ============================================================
# DATABASE MANAGER
# ============================================================

class DatabaseManager:

    def __init__(self, connection):

        self.connection = connection
        self.lock = threading.RLock()

        self.last_sync_error = None

    # --------------------------------------------------------
    # READ OPERATIONS
    # --------------------------------------------------------

    def fetch_one(self, sql, params=()):

        with self.lock:

            cursor = self.connection.cursor()

            cursor.execute(sql, params)

            return cursor.fetchone()

    def fetch_all(self, sql, params=()):

        with self.lock:

            cursor = self.connection.cursor()

            cursor.execute(sql, params)

            return cursor.fetchall()

    # --------------------------------------------------------
    # WRITE TRANSACTIONS
    # --------------------------------------------------------

    def write_transaction(self, operation):

        with self.lock:

            cursor = self.connection.cursor()

            try:

                cursor.execute("BEGIN")

                result = operation(cursor)

                self.connection.commit()

            except Exception:

                self.connection.rollback()

                raise

            # The transaction has committed locally.
            # Synchronize only after the transaction is finished.

            try:

                self.connection.sync()

                self.last_sync_error = None

            except Exception as exc:

                self.last_sync_error = str(exc)

                st.warning(
                    "The change was saved locally, but synchronization "
                    "with Turso failed. Check your internet connection. "
                    "Avoid entering the same transaction again."
                )

            return result


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def get_secret(name):

    value = st.secrets.get(name)

    if not value:

        st.error(
            f"Missing Streamlit secret: {name}"
        )

        st.stop()

    return str(value)


@st.cache_resource
def get_database():

    turso_url = get_secret("TURSO_DATABASE_URL")

    turso_token = get_secret("TURSO_AUTH_TOKEN")

    connection = libsql.connect(
        DB_PATH,
        sync_url=turso_url,
        auth_token=turso_token
    )

    # Initial synchronization.
    connection.sync()

    cursor = connection.cursor()

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

            FOREIGN KEY(sale_id)
                REFERENCES pack_sales(id)

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

        """
        CREATE INDEX IF NOT EXISTS idx_borrows_sale_id
        ON pack_borrows(sale_id)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_borrows_status
        ON pack_borrows(status)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_staged_pack
        ON temp_staged_borrows(pack_number)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_deposit_type
        ON bank_deposits(deposit_type)
        """,

    ]

    cursor.execute("BEGIN")

    try:

        for statement in statements:

            cursor.execute(statement)

        connection.commit()

    except Exception:

        connection.rollback()

        raise

    # Synchronize schema changes.
    connection.sync()

    return DatabaseManager(connection)


try:

    db = get_database()

except Exception as exc:

    st.error(
        f"Database connection or initialization failed: {exc}"
    )

    st.stop()


# ============================================================
# DATABASE HELPER FUNCTIONS
# ============================================================

def fetch_one(sql, params=()):

    return db.fetch_one(sql, params)


def fetch_all(sql, params=()):

    return db.fetch_all(sql, params)


def write_transaction(operation):

    return db.write_transaction(operation)


# ============================================================
# SMALL HELPER FUNCTIONS
# ============================================================

def money(value):

    return f"Rs. {float(value or 0):,.2f}"


def set_flash(message, kind="success"):

    st.session_state["dashboard_flash"] = (
        message,
        kind
    )


def display_flash():

    flash = st.session_state.pop(
        "dashboard_flash",
        None
    )

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
# CURRENT PACK
# ============================================================

def get_current_pack():

    row = fetch_one(
        """
        SELECT pack_number

        FROM pack_sales

        ORDER BY id DESC

        LIMIT 1
        """
    )

    if not row or row[0] is None:

        return 1

    return (int(row[0]) % 10) + 1


# ============================================================
# FINANCIAL BALANCES
# ============================================================

def get_financial_balances():

    row = fetch_one(
        """
        SELECT

            (
                SELECT COALESCE(SUM(cash_payment), 0)
                FROM pack_sales
            )

            +

            (
                SELECT COALESCE(SUM(borrow_amount), 0)
                FROM pack_borrows
                WHERE status = 'Paid'
            ),

            (
                SELECT COALESCE(SUM(cash_deposit), 0)
                FROM pack_sales
            ),

            (
                SELECT COALESCE(SUM(amount), 0)
                FROM bank_deposits
            ),

            (
                SELECT COALESCE(SUM(amount), 0)
                FROM bank_deposits
                WHERE deposit_type LIKE '%Cash on Hand%'
            ),

            (
                SELECT COALESCE(SUM(amount), 0)
                FROM bank_expenses
            )
        """
    )

    total_cash_collected = float(row[0] or 0)

    sales_bank_deposits = float(row[1] or 0)

    standalone_deposits = float(row[2] or 0)

    cash_drawer_deposits = float(row[3] or 0)

    bank_expenses = float(row[4] or 0)

    cash_on_hand = (
        total_cash_collected - cash_drawer_deposits
    )

    bank_balance = (
        sales_bank_deposits
        + standalone_deposits
        - bank_expenses
    )

    return cash_on_hand, bank_balance


# ============================================================
# DASHBOARD HEADER
# ============================================================

st.title("Web Dashboard")

st.caption(
    "Track packs, cash on hand, bank balances, "
    "and customer debts."
)

display_flash()

st.caption(
    "☁️ Database connected. Changes synchronize after saving."
)

st.divider()


# ============================================================
# FINANCIAL SUMMARY
# ============================================================

st.subheader("Financial Summary")

cash_on_hand, bank_balance = get_financial_balances()

col1, col2 = st.columns(2)

col1.metric(
    "💵 Cash on Hand",
    money(cash_on_hand)
)

col2.metric(
    "🏦 Bank Account Balance",
    money(bank_balance)
)

st.divider()


# ============================================================
# NAVIGATION
# ============================================================

sections = [

    "📦 Record Sales",

    "🏦 Bank Deposits",

    "💸 Bank Expenses",

    "⚠️ Manage Debts",

    "📊 Sales History"

]

selected_section = st.radio(
    "Dashboard Section",
    sections,
    horizontal=True,
    label_visibility="collapsed"
)


# ============================================================
# 1. RECORD SALES
# ============================================================

if selected_section == "📦 Record Sales":

    current_pack = get_current_pack()

    st.subheader(
        f"Recording Pack: {current_pack} of 10"
    )

    # --------------------------------------------------------
    # ADD BORROWER
    # --------------------------------------------------------

    st.markdown("### Add Borrowers for This Pack")

    with st.form(
        "add_borrower_form",
        clear_on_submit=True
    ):

        borrower_name = st.text_input(
            "Borrower Name"
        )

        borrower_amount = st.number_input(
            "Borrow Amount (Rs.)",
            min_value=0.0,
            step=10.0
        )

        add_borrower = st.form_submit_button(
            "+ Add Borrower to List"
        )

    if add_borrower:

        if not borrower_name.strip():

            st.error(
                "Please enter a borrower name."
            )

        elif borrower_amount <= 0:

            st.error(
                "Please enter a valid amount."
            )

        else:

            def insert_borrower(cursor):

                cursor.execute(
                    """
                    INSERT INTO temp_staged_borrows
                    (
                        pack_number,
                        borrower_name,
                        borrow_amount
                    )
                    VALUES (?, ?, ?)
                    """,

                    (
                        current_pack,
                        borrower_name.strip(),
                        borrower_amount
                    )
                )

            try:

                write_transaction(insert_borrower)

                set_flash(
                    f"Added {borrower_name.strip()} successfully."
                )

                st.rerun()

            except Exception as exc:

                st.error(
                    f"Could not add borrower: {exc}"
                )

    # --------------------------------------------------------
    # STAGED BORROWERS
    # --------------------------------------------------------

    staged_borrowers = fetch_all(
        """
        SELECT

            id,

            borrower_name,

            borrow_amount

        FROM temp_staged_borrows

        WHERE pack_number = ?

        ORDER BY id
        """,

        (current_pack,)
    )

    st.markdown("### Borrowers Queued for This Pack")

    if staged_borrowers:

        for row in staged_borrowers:

            borrower_id, name, amount = row

            col_a, col_b, col_c = st.columns(
                [4, 2, 1]
            )

            col_a.write(name)

            col_b.write(money(amount))

            if col_c.button(
                "Remove",
                key=f"remove_borrower_{borrower_id}"
            ):

                def remove_borrower(cursor):

                    cursor.execute(
                        """
                        DELETE FROM temp_staged_borrows

                        WHERE id = ?
                        """,

                        (borrower_id,)
                    )

                try:

                    write_transaction(remove_borrower)

                    set_flash(
                        "Borrower removed from the queue."
                    )

                    st.rerun()

                except Exception as exc:

                    st.error(
                        f"Could not remove borrower: {exc}"
                    )

    else:

        st.info(
            "No borrowers added to this pack yet."
        )

    st.divider()

    # --------------------------------------------------------
    # SAVE PACK
    # --------------------------------------------------------

    st.markdown("### Pack Payment Details")

    with st.form(
        "save_pack_form",
        clear_on_submit=True
    ):

        cash_payment = st.number_input(
            "Cash Payment Received (Rs.)",
            min_value=0.0,
            step=10.0
        )

        bank_transfer = st.number_input(
            "Direct Customer Bank Transfer Amount (Rs.)",
            min_value=0.0,
            step=10.0
        )

        save_pack = st.form_submit_button(
            "Save Pack & Move to Next",
            type="primary"
        )

    if save_pack:

        latest_borrowers = fetch_all(
            """
            SELECT

                id,

                borrower_name,

                borrow_amount

            FROM temp_staged_borrows

            WHERE pack_number = ?

            ORDER BY id
            """,

            (current_pack,)
        )

        def save_pack_transaction(cursor):

            cursor.execute(
                """
                INSERT INTO pack_sales
                (
                    pack_number,
                    cash_payment,
                    cash_deposit
                )
                VALUES (?, ?, ?)
                """,

                (
                    current_pack,
                    cash_payment,
                    bank_transfer
                )
            )

            sale_id = cursor.lastrowid

            for borrower in latest_borrowers:

                _, name, amount = borrower

                cursor.execute(
                    """
                    INSERT INTO pack_borrows
                    (
                        sale_id,
                        borrower_name,
                        borrow_amount,
                        status
                    )
                    VALUES (?, ?, ?, 'Pending')
                    """,

                    (
                        sale_id,
                        name,
                        amount
                    )
                )

            cursor.execute(
                """
                DELETE FROM temp_staged_borrows

                WHERE pack_number = ?
                """,

                (current_pack,)
            )

            return sale_id

        try:

            sale_id = write_transaction(
                save_pack_transaction
            )

            set_flash(
                f"Pack {current_pack} saved successfully."
            )

            st.rerun()

        except Exception as exc:

            st.error(
                f"Could not save pack: {exc}"
            )


# ============================================================
# 2. BANK DEPOSITS
# ============================================================

elif selected_section == "🏦 Bank Deposits":

    st.subheader("Make Standalone Bank Deposits")

    with st.form(
        "bank_deposit_form",
        clear_on_submit=True
    ):

        deposit_type = st.selectbox(
            "Deposit Type",

            [
                "Deposit Cash on Hand to Bank",

                "Personal Money Investment to Bank"
            ]
        )

        deposit_amount = st.number_input(
            "Amount (Rs.)",
            min_value=0.0,
            step=100.0
        )

        deposit_notes = st.text_input(
            "Notes / Description"
        )

        submit_deposit = st.form_submit_button(
            "Submit Deposit"
        )

    if submit_deposit:

        if deposit_amount <= 0:

            st.error(
                "Please enter a valid deposit amount."
            )

        else:

            def insert_deposit(cursor):

                cursor.execute(
                    """
                    INSERT INTO bank_deposits
                    (
                        deposit_type,
                        amount,
                        notes
                    )
                    VALUES (?, ?, ?)
                    """,

                    (
                        deposit_type,
                        deposit_amount,
                        deposit_notes.strip()
                    )
                )

            try:

                write_transaction(insert_deposit)

                set_flash(
                    "Bank deposit recorded successfully."
                )

                st.rerun()

            except Exception as exc:

                st.error(
                    f"Could not record deposit: {exc}"
                )


# ============================================================
# 3. BANK EXPENSES
# ============================================================

elif selected_section == "💸 Bank Expenses":

    st.subheader(
        "Record Bank Account Expenses (Purchases)"
    )

    with st.form(
        "bank_expense_form",
        clear_on_submit=True
    ):

        expense_description = st.text_input(
            "Expense Description / Note"
        )

        expense_amount = st.number_input(
            "Amount (Rs.)",
            min_value=0.0,
            step=100.0
        )

        submit_expense = st.form_submit_button(
            "Submit Expense"
        )

    if submit_expense:

        if not expense_description.strip():

            st.error(
                "Please enter an expense description."
            )

        elif expense_amount <= 0:

            st.error(
                "Please enter a valid expense amount."
            )

        else:

            def insert_expense(cursor):

                cursor.execute(
                    """
                    INSERT INTO bank_expenses
                    (
                        expense_category,
                        amount,
                        notes
                    )
                    VALUES (?, ?, ?)
                    """,

                    (
                        "Bank Expense",
                        expense_amount,
                        expense_description.strip()
                    )
                )

            try:

                write_transaction(insert_expense)

                set_flash(
                    "Bank expense recorded successfully."
                )

                st.rerun()

            except Exception as exc:

                st.error(
                    f"Could not record expense: {exc}"
                )

    # --------------------------------------------------------
    # EXPENSE HISTORY
    # --------------------------------------------------------

    st.markdown("### Recent Bank Expenses History")

    expense_data = fetch_all(
        """
        SELECT

            id,

            notes,

            amount,

            timestamp

        FROM bank_expenses

        ORDER BY id DESC

        LIMIT 10
        """
    )

    if expense_data:

        expense_table = [

            {
                "ID": row[0],

                "Expense Description": row[1],

                "Amount": money(row[2]),

                "Date/Time": row[3]
            }

            for row in expense_data

        ]

        st.dataframe(
            expense_table,
            use_container_width=True,
            hide_index=True
        )

    else:

        st.info(
            "No bank expenses recorded yet."
        )


# ============================================================
# 4. MANAGE DEBTS
# ============================================================

elif selected_section == "⚠️ Manage Debts":

    st.subheader(
        "Outstanding Customer Borrows"
    )

    pending_debts = fetch_all(
        """
        SELECT

            pb.id,

            ps.pack_number,

            pb.borrower_name,

            pb.borrow_amount

        FROM pack_borrows pb

        INNER JOIN pack_sales ps

            ON pb.sale_id = ps.id

        WHERE pb.status = 'Pending'

        ORDER BY pb.id
        """
    )

    if pending_debts:

        for debt in pending_debts:

            debt_id, pack_number, borrower, amount = debt

            cols = st.columns(
                [3, 1.5, 2, 1.5]
            )

            cols[0].write(
                f"**Customer:** {borrower}"
            )

            cols[1].write(
                f"Pack #{pack_number}"
            )

            cols[2].write(
                money(amount)
            )

            if cols[3].button(
                "Mark Paid",
                key=f"mark_paid_{debt_id}"
            ):

                def mark_paid(cursor):

                    cursor.execute(
                        """
                        UPDATE pack_borrows

                        SET status = 'Paid'

                        WHERE id = ?

                        AND status = 'Pending'
                        """,

                        (debt_id,)
                    )

                    return cursor.rowcount

                try:

                    updated = write_transaction(
                        mark_paid
                    )

                    if updated:

                        set_flash(
                            f"Cleared debt for {borrower}."
                        )

                    else:

                        set_flash(
                            "This debt has already been marked as paid.",
                            "warning"
                        )

                    st.rerun()

                except Exception as exc:

                    st.error(
                        f"Could not update payment: {exc}"
                    )

    else:

        st.info(
            "No pending customer debts found."
        )


# ============================================================
# 5. SALES HISTORY
# ============================================================

elif selected_section == "📊 Sales History":

    st.subheader(
        "Sales History (Grouped by Sets of 10 Packs)"
    )

    history_data = fetch_all(
        """
        SELECT

            ps.id,

            ps.pack_number,

            (
                COALESCE(ps.cash_payment, 0)

                +

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

            COALESCE(ps.cash_deposit, 0),

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
                COALESCE(ps.cash_payment, 0)

                +

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

                +

                COALESCE(ps.cash_deposit, 0)
            ) AS total_received

        FROM pack_sales ps

        LEFT JOIN pack_borrows pb

            ON ps.id = pb.sale_id

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

            first_pack = (
                (batch_number - 1) * 10
            ) + 1

            last_pack = batch_number * 10

            batch_title = (
                f"Packs {first_pack} to {last_pack} "
                f"(Batch {batch_number})"
            )

            batches.setdefault(
                batch_title,
                []
            ).append(row)

        batch_items = list(batches.items())

        batch_items.reverse()

        for batch_index, (batch_title, rows) in enumerate(
            batch_items
        ):

            with st.expander(
                batch_title,
                expanded=(batch_index == 0)
            ):

                table_rows = []

                batch_total_received = 0.0

                for row in rows:

                    batch_total_received += float(
                        row[5] or 0
                    )

                    table_rows.append(
                        {
                            "Sale ID": row[0],

                            "Pack #": row[1],

                            "Cash Payment": money(row[2]),

                            "Bank Deposit": money(row[3]),

                            "Money to Receive": money(row[4]),

                            "Total Received": money(row[5])
                        }
                    )

                st.dataframe(
                    table_rows,
                    use_container_width=True,
                    hide_index=True
                )

                st.markdown(
                    "**Total Received for this Group of Packs:** "
                    f"{money(batch_total_received)}"
                )

    else:

        st.info(
            "No sales recorded yet."
        )


# ============================================================
# END OF DASHBOARD
# ============================================================
