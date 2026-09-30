from __future__ import annotations

import sqlite3
import hashlib
import hmac
import re
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "camp_inventory.db"
SITES = ("HQ Steppes Road", "The Hide Safaris", "Changa Safari Camp")


def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_site_name(conn: sqlite3.Connection, old_name: str, new_name: str) -> None:
    old_row = conn.execute("SELECT id FROM sites WHERE name=?", (old_name,)).fetchone()
    if not old_row:
        return
    new_row = conn.execute("SELECT id FROM sites WHERE name=?", (new_name,)).fetchone()
    old_id = old_row[0]
    if not new_row:
        conn.execute("UPDATE sites SET name=? WHERE id=?", (new_name, old_id))
        return

    new_id = new_row[0]
    old_stock = conn.execute(
        "SELECT item_id, quantity, updated_at FROM stock WHERE site_id=?", (old_id,)
    ).fetchall()
    for row in old_stock:
        existing = conn.execute(
            "SELECT quantity FROM stock WHERE item_id=? AND site_id=?",
            (row["item_id"], new_id),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE stock SET quantity=?, updated_at=?
                WHERE item_id=? AND site_id=?""",
                (existing[0] + row["quantity"], row["updated_at"], row["item_id"], new_id),
            )
        else:
            conn.execute(
                "UPDATE stock SET site_id=? WHERE item_id=? AND site_id=?",
                (new_id, row["item_id"], old_id),
            )
    conn.execute("DELETE FROM stock WHERE site_id=?", (old_id,))
    conn.execute("UPDATE movements SET from_site_id=? WHERE from_site_id=?", (new_id, old_id))
    conn.execute("UPDATE movements SET to_site_id=? WHERE to_site_id=?", (new_id, old_id))
    conn.execute("DELETE FROM sites WHERE id=?", (old_id,))


def init_db() -> None:
    conn = connect_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            unit TEXT NOT NULL,
            unit_cost REAL NOT NULL DEFAULT 0,
            minimum_level REAL NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stock (
            item_id INTEGER NOT NULL,
            site_id INTEGER NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (item_id, site_id),
            FOREIGN KEY (item_id) REFERENCES items(id),
            FOREIGN KEY (site_id) REFERENCES sites(id)
        );
        CREATE TABLE IF NOT EXISTS movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            movement_type TEXT NOT NULL,
            item_id INTEGER NOT NULL,
            from_site_id INTEGER,
            to_site_id INTEGER,
            quantity REAL NOT NULL,
            reference TEXT,
            notes TEXT,
            performed_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (item_id) REFERENCES items(id),
            FOREIGN KEY (from_site_id) REFERENCES sites(id),
            FOREIGN KEY (to_site_id) REFERENCES sites(id)
        );
        CREATE TABLE IF NOT EXISTS shipments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stv_number TEXT,
            item_id INTEGER NOT NULL,
            from_site_id INTEGER NOT NULL,
            to_site_id INTEGER NOT NULL,
            quantity_dispatched REAL NOT NULL,
            quantity_received REAL,
            status TEXT NOT NULL DEFAULT 'In Transit',
            dispatch_reference TEXT,
            receipt_reference TEXT,
            dispatch_notes TEXT,
            receipt_notes TEXT,
            dispatched_by TEXT NOT NULL,
            received_by TEXT,
            dispatched_at TEXT NOT NULL,
            received_at TEXT,
            FOREIGN KEY (item_id) REFERENCES items(id),
            FOREIGN KEY (from_site_id) REFERENCES sites(id),
            FOREIGN KEY (to_site_id) REFERENCES sites(id)
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            profile_image BLOB,
            profile_image_type TEXT,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            role TEXT NOT NULL,
            site_id INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            must_change_password INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            created_by TEXT,
            FOREIGN KEY (site_id) REFERENCES sites(id)
        );
        CREATE TABLE IF NOT EXISTS system_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_items_name ON items(name);
        CREATE INDEX IF NOT EXISTS idx_movements_created ON movements(created_at);
        CREATE INDEX IF NOT EXISTS idx_shipments_status ON shipments(status);
        """
    )
    user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "profile_image" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN profile_image BLOB")
    if "profile_image_type" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN profile_image_type TEXT")
    shipment_columns = {row["name"] for row in conn.execute("PRAGMA table_info(shipments)")}
    if "stv_number" not in shipment_columns:
        conn.execute("ALTER TABLE shipments ADD COLUMN stv_number TEXT")
    existing_shipments = conn.execute(
        "SELECT id,dispatched_at FROM shipments WHERE stv_number IS NULL"
    ).fetchall()
    for shipment in existing_shipments:
        dispatch_date = shipment["dispatched_at"][:10].replace("-", "")
        conn.execute(
            "UPDATE shipments SET stv_number=? WHERE id=?",
            (f"STV-{dispatch_date}-{shipment['id']:04d}", shipment["id"]),
        )
    default_expiry = (date.today() + timedelta(days=30)).isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO system_settings(setting_key,setting_value)
        VALUES ('license_expires_on',?)""",
        (default_expiry,),
    )
    # Preserve all stock and movement history when upgrading from the original
    # working location names to the camps' official names.
    _migrate_site_name(conn, "Hwange Camp Site", "The Hide Safaris")
    _migrate_site_name(conn, "Kariba Camp Site", "Changa Safari Camp")
    _migrate_site_name(conn, "HQ", "HQ Steppes Road")
    conn.executemany("INSERT OR IGNORE INTO sites(name) VALUES (?)", [(site,) for site in SITES])
    # Demo stock is added only when explicitly requested. A new client database
    # must open with a blank item catalogue, ready for its own opening stock.
    demo_seed = conn.execute(
        "SELECT setting_value FROM system_settings WHERE setting_key='seed_demo_data'"
    ).fetchone()
    if (
        conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
        and demo_seed
        and demo_seed[0].lower() == "true"
    ):
        seed_items = [
            ("FUEL-001", "Diesel", "Fuel", "litres", 1.45, 600),
            ("FNB-014", "Bottled water", "Food & Beverage", "cases", 9.50, 24),
            ("HK-022", "Bed linen sets", "Housekeeping", "sets", 32.00, 30),
            ("SAFE-008", "First aid kits", "Safety", "kits", 48.00, 6),
            ("FUEL-009", "Cooking gas", "Fuel", "cylinders", 28.00, 8),
            ("HK-031", "Laundry detergent", "Housekeeping", "bottles", 6.20, 25),
            ("OPS-017", "Dry firewood", "Operations", "bundles", 3.50, 40),
            ("FNB-021", "Long-life milk", "Food & Beverage", "cases", 18.00, 15),
            ("MAINT-012", "Generator oil", "Maintenance", "litres", 7.80, 20),
        ]
        now = datetime.now().isoformat(timespec="seconds")
        conn.executemany(
            """INSERT INTO items
            (sku,name,category,unit,unit_cost,minimum_level,created_at)
            VALUES (?,?,?,?,?,?,?)""",
            [(*item, now) for item in seed_items],
        )
        quantities = {
            "HQ Steppes Road": [1240, 60, 25, 12, 14, 84, 35, 24, 45],
            "The Hide Safaris": [560, 18, 34, 4, 10, 31, 72, 12, 17],
            "Changa Safari Camp": [710, 38, 46, 8, 7, 29, 51, 20, 26],
        }
        item_ids = [row[0] for row in conn.execute("SELECT id FROM items ORDER BY id")]
        for site, values in quantities.items():
            site_id = conn.execute("SELECT id FROM sites WHERE name=?", (site,)).fetchone()[0]
            conn.executemany(
                "INSERT INTO stock(item_id,site_id,quantity,updated_at) VALUES (?,?,?,?)",
                [(item_id, site_id, value, now) for item_id, value in zip(item_ids, values)],
            )
    conn.commit()
    conn.close()


def _password_hash(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 240_000
    ).hex()


def create_user(
    username: str,
    display_name: str,
    password: str,
    role: str,
    site: str | None,
    created_by: str,
    must_change_password: bool = True,
) -> None:
    username = username.strip().lower()
    if role != "Super User" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", username):
        raise ValueError("Enter a valid email address for the user's login.")
    if role == "Super User" and len(username) < 3:
        raise ValueError("The Super User login must contain at least three characters.")
    if not display_name.strip():
        raise ValueError("The user's full name is required.")
    if len(password) < 10:
        raise ValueError("Temporary password must contain at least 10 characters.")
    if role not in {"Super User", "HQ User", "Camp User"}:
        raise ValueError("Unknown user role.")
    if role == "Camp User" and site not in SITES[1:]:
        raise ValueError("Camp users must be assigned to The Hide or Changa.")
    if role in {"Super User", "HQ User"}:
        site = "HQ Steppes Road"
    conn = connect_db()
    site_id = conn.execute("SELECT id FROM sites WHERE name=?", (site,)).fetchone()[0]
    salt = secrets.token_hex(16)
    try:
        conn.execute(
            """INSERT INTO users
            (username,display_name,password_hash,password_salt,role,site_id,active,
             must_change_password,created_at,created_by)
            VALUES (?,?,?,?,?,?,1,?,?,?)""",
            (
                username, display_name.strip(), _password_hash(password, salt), salt,
                role, site_id, int(must_change_password),
                datetime.now().isoformat(timespec="seconds"), created_by,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as error:
        raise ValueError("That username already exists.") from error
    finally:
        conn.close()


def ensure_superuser(password: str) -> bool:
    conn = connect_db()
    exists = conn.execute("SELECT 1 FROM users WHERE role='Super User' LIMIT 1").fetchone()
    conn.close()
    if exists:
        return False
    create_user(
        "superuser", "Super User", password, "Super User",
        "HQ Steppes Road", "System", True,
    )
    return True


def authenticate(username: str, password: str) -> dict | None:
    conn = connect_db()
    row = conn.execute(
        """SELECT u.*,s.name AS site FROM users u
        LEFT JOIN sites s ON s.id=u.site_id
        WHERE LOWER(u.username)=LOWER(?) AND u.active=1""",
        (username.strip(),),
    ).fetchone()
    conn.close()
    if not row:
        return None
    candidate = _password_hash(password, row["password_salt"])
    if not hmac.compare_digest(candidate, row["password_hash"]):
        return None
    return {
        "id": row["id"], "username": row["username"],
        "display_name": row["display_name"], "role": row["role"],
        "site": row["site"],
        "profile_image": row["profile_image"],
        "profile_image_type": row["profile_image_type"],
        "must_change_password": bool(row["must_change_password"]),
    }


def change_password(user_id: int, current_password: str, new_password: str) -> None:
    if len(new_password) < 10:
        raise ValueError("New password must contain at least 10 characters.")
    conn = connect_db()
    row = conn.execute(
        "SELECT password_hash,password_salt FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if not row or not hmac.compare_digest(
        _password_hash(current_password, row["password_salt"]), row["password_hash"]
    ):
        conn.close()
        raise ValueError("Current password is incorrect.")
    salt = secrets.token_hex(16)
    conn.execute(
        """UPDATE users SET password_hash=?,password_salt=?,must_change_password=0
        WHERE id=?""",
        (_password_hash(new_password, salt), salt, user_id),
    )
    conn.commit()
    conn.close()


def update_profile(
    user_id: int,
    display_name: str,
    profile_image: bytes | None = None,
    profile_image_type: str | None = None,
    remove_image: bool = False,
) -> None:
    display_name = display_name.strip()
    if len(display_name) < 2:
        raise ValueError("Please enter your full name.")
    conn = connect_db()
    if remove_image:
        conn.execute(
            """UPDATE users
            SET display_name=?,profile_image=NULL,profile_image_type=NULL
            WHERE id=?""",
            (display_name, user_id),
        )
    elif profile_image is not None:
        conn.execute(
            """UPDATE users
            SET display_name=?,profile_image=?,profile_image_type=?
            WHERE id=?""",
            (display_name, profile_image, profile_image_type, user_id),
        )
    else:
        conn.execute(
            "UPDATE users SET display_name=? WHERE id=?",
            (display_name, user_id),
        )
    conn.commit()
    conn.close()


def list_users() -> pd.DataFrame:
    conn = connect_db()
    df = pd.read_sql_query(
        """SELECT u.id,u.username,u.display_name,u.role,s.name AS site,
        CASE WHEN u.active=1 THEN 'Active' ELSE 'Inactive' END AS status,
        u.created_at,u.created_by
        FROM users u LEFT JOIN sites s ON s.id=u.site_id ORDER BY u.id""",
        conn,
    )
    conn.close()
    return df


def set_user_active(user_id: int, active: bool, acting_user_id: int) -> None:
    if user_id == acting_user_id and not active:
        raise ValueError("You cannot deactivate your own account.")
    conn = connect_db()
    conn.execute("UPDATE users SET active=? WHERE id=?", (int(active), user_id))
    conn.commit()
    conn.close()


def set_user_display_name(user_id: int, display_name: str) -> None:
    display_name = display_name.strip()
    if len(display_name) < 2:
        raise ValueError("Please enter the user's full name.")
    conn = connect_db()
    conn.execute(
        "UPDATE users SET display_name=? WHERE id=?",
        (display_name, user_id),
    )
    conn.commit()
    conn.close()


def license_status() -> dict:
    conn = connect_db()
    row = conn.execute(
        "SELECT setting_value FROM system_settings WHERE setting_key='license_expires_on'"
    ).fetchone()
    conn.close()
    expires_on = date.fromisoformat(row["setting_value"])
    days_remaining = max((expires_on - date.today()).days, 0)
    return {
        "expires_on": expires_on,
        "days_remaining": days_remaining,
        "expired": date.today() > expires_on,
    }


def inventory(site: str = "All sites") -> pd.DataFrame:
    conn = connect_db()
    params: tuple = ()
    where = ""
    if site != "All sites":
        where = "WHERE s.name = ?"
        params = (site,)
    df = pd.read_sql_query(
        f"""
        SELECT i.id, i.sku, i.name, i.category, i.unit, i.unit_cost,
               i.minimum_level, s.name AS site, st.quantity,
               ROUND(st.quantity * i.unit_cost, 2) AS stock_value,
               CASE WHEN st.quantity < i.minimum_level THEN 'Low stock' ELSE 'Healthy' END AS status,
               st.updated_at
        FROM stock st
        JOIN items i ON i.id = st.item_id
        JOIN sites s ON s.id = st.site_id
        {where}
        ORDER BY i.name, s.name
        """,
        conn,
        params=params,
    )
    conn.close()
    return df


def item_options() -> dict[str, int]:
    conn = connect_db()
    rows = conn.execute("SELECT id, sku, name FROM items WHERE active=1 ORDER BY name").fetchall()
    conn.close()
    return {f"{r['name']} ({r['sku']})": r["id"] for r in rows}


def add_item(sku: str, name: str, category: str, unit: str, unit_cost: float, minimum: float) -> None:
    conn = connect_db()
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """INSERT INTO items(sku,name,category,unit,unit_cost,minimum_level,created_at)
        VALUES (?,?,?,?,?,?,?)""",
        (sku.strip().upper(), name.strip(), category, unit.strip(), unit_cost, minimum, now),
    )
    site_ids = [r[0] for r in conn.execute("SELECT id FROM sites")]
    conn.executemany(
        "INSERT INTO stock(item_id,site_id,quantity,updated_at) VALUES (?,?,0,?)",
        [(cur.lastrowid, site_id, now) for site_id in site_ids],
    )
    conn.commit()
    conn.close()


def bulk_add_items(items: list[dict], performed_by: str) -> int:
    """Create items and optional opening balances in one database transaction."""
    if not items:
        raise ValueError("There are no items to import.")
    conn = connect_db()
    now = datetime.now().isoformat(timespec="seconds")
    site_rows = conn.execute("SELECT id,name FROM sites").fetchall()
    site_ids = {row["name"]: row["id"] for row in site_rows}
    try:
        conn.execute("BEGIN")
        for item in items:
            cur = conn.execute(
                """INSERT INTO items
                (sku,name,category,unit,unit_cost,minimum_level,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    item["sku"].strip().upper(), item["name"].strip(),
                    item["category"].strip(), item["unit"].strip(),
                    float(item["unit_cost"]), int(item["minimum_level"]), now,
                ),
            )
            item_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO stock(item_id,site_id,quantity,updated_at) VALUES (?,?,?,?)",
                [
                    (item_id, site_id, int(item["opening_stock"].get(site_name, 0)), now)
                    for site_name, site_id in site_ids.items()
                ],
            )
            for site_name, quantity in item["opening_stock"].items():
                quantity = int(quantity)
                if quantity <= 0:
                    continue
                conn.execute(
                    """INSERT INTO movements
                    (movement_type,item_id,from_site_id,to_site_id,quantity,reference,
                     notes,performed_by,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        "Opening Balance", item_id, None, site_ids[site_name], quantity,
                        "BULK-OPENING", "Opening stock from bulk item import",
                        performed_by.strip(), now,
                    ),
                )
        conn.commit()
        return len(items)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_movement(
    movement_type: str,
    item_id: int,
    quantity: float,
    site: str | None,
    destination: str | None,
    reference: str,
    notes: str,
    performed_by: str,
) -> None:
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")
    if not float(quantity).is_integer():
        raise ValueError("Quantity must be a whole number.")
    conn = connect_db()
    now = datetime.now().isoformat(timespec="seconds")
    site_id = conn.execute("SELECT id FROM sites WHERE name=?", (site,)).fetchone()[0] if site else None
    destination_id = conn.execute("SELECT id FROM sites WHERE name=?", (destination,)).fetchone()[0] if destination else None
    try:
        conn.execute("BEGIN")
        if movement_type in ("Receive", "Purchase Receipt"):
            conn.execute("UPDATE stock SET quantity=quantity+?, updated_at=? WHERE item_id=? AND site_id=?", (quantity, now, item_id, site_id))
        elif movement_type in ("Issue", "Guest Consumption", "Staff Consumption"):
            available = conn.execute("SELECT quantity FROM stock WHERE item_id=? AND site_id=?", (item_id, site_id)).fetchone()[0]
            if available < quantity:
                raise ValueError(f"Only {available:.0f} units are available at this site.")
            conn.execute("UPDATE stock SET quantity=quantity-?, updated_at=? WHERE item_id=? AND site_id=?", (quantity, now, item_id, site_id))
        elif movement_type == "Transfer":
            if site_id == destination_id:
                raise ValueError("Origin and destination must be different.")
            available = conn.execute("SELECT quantity FROM stock WHERE item_id=? AND site_id=?", (item_id, site_id)).fetchone()[0]
            if available < quantity:
                raise ValueError(f"Only {available:.0f} units are available at the origin site.")
            conn.execute("UPDATE stock SET quantity=quantity-?, updated_at=? WHERE item_id=? AND site_id=?", (quantity, now, item_id, site_id))
            conn.execute("UPDATE stock SET quantity=quantity+?, updated_at=? WHERE item_id=? AND site_id=?", (quantity, now, item_id, destination_id))
        else:
            raise ValueError("Unknown movement type.")
        conn.execute(
            """INSERT INTO movements
            (movement_type,item_id,from_site_id,to_site_id,quantity,reference,notes,performed_by,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                movement_type, item_id,
                site_id if movement_type in ("Issue", "Guest Consumption", "Staff Consumption", "Transfer") else None,
                site_id if movement_type in ("Receive", "Purchase Receipt") else destination_id,
                quantity, reference.strip(), notes.strip(), performed_by.strip(), now,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def purchase_receipt(
    item_id: int,
    quantity: float,
    reference: str,
    notes: str,
    performed_by: str,
) -> None:
    record_movement(
        "Purchase Receipt", item_id, quantity, "HQ Steppes Road", None,
        reference, notes, performed_by,
    )


def guest_consumption(
    item_id: int,
    quantity: float,
    camp: str,
    reference: str,
    notes: str,
    performed_by: str,
) -> None:
    if camp == "HQ Steppes Road":
        raise ValueError("Guest consumption must be recorded against a camp.")
    record_movement(
        "Guest Consumption", item_id, quantity, camp, None,
        reference, notes, performed_by,
    )


def staff_consumption(
    item_id: int,
    quantity: float,
    camp: str,
    reference: str,
    notes: str,
    performed_by: str,
) -> None:
    if camp == "HQ Steppes Road":
        raise ValueError("Staff consumption must be recorded against a camp.")
    record_movement(
        "Staff Consumption", item_id, quantity, camp, None,
        reference, notes, performed_by,
    )


def dispatch_stock(
    item_id: int,
    quantity: float,
    destination: str,
    reference: str,
    notes: str,
    performed_by: str,
) -> str:
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")
    if not float(quantity).is_integer():
        raise ValueError("Quantity must be a whole number.")
    if destination == "HQ Steppes Road":
        raise ValueError("Dispatch destination must be a camp.")
    conn = connect_db()
    now = datetime.now().isoformat(timespec="seconds")
    hq_id = conn.execute("SELECT id FROM sites WHERE name='HQ Steppes Road'").fetchone()[0]
    destination_id = conn.execute("SELECT id FROM sites WHERE name=?", (destination,)).fetchone()[0]
    try:
        conn.execute("BEGIN")
        available = conn.execute(
            "SELECT quantity FROM stock WHERE item_id=? AND site_id=?",
            (item_id, hq_id),
        ).fetchone()[0]
        if available < quantity:
            raise ValueError(f"Only {available:.0f} units are available at HQ Steppes Road.")
        conn.execute(
            "UPDATE stock SET quantity=quantity-?, updated_at=? WHERE item_id=? AND site_id=?",
            (quantity, now, item_id, hq_id),
        )
        shipment_cursor = conn.execute(
            """INSERT INTO shipments
            (item_id,from_site_id,to_site_id,quantity_dispatched,status,
             dispatch_reference,dispatch_notes,dispatched_by,dispatched_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                item_id, hq_id, destination_id, quantity, "In Transit",
                reference.strip(), notes.strip(), performed_by.strip(), now,
            ),
        )
        stv_number = f"STV-{datetime.now().strftime('%Y%m%d')}-{shipment_cursor.lastrowid:04d}"
        conn.execute(
            "UPDATE shipments SET stv_number=? WHERE id=?",
            (stv_number, shipment_cursor.lastrowid),
        )
        conn.execute(
            """INSERT INTO movements
            (movement_type,item_id,from_site_id,to_site_id,quantity,reference,notes,performed_by,created_at)
            VALUES ('Dispatch',?,?,?,?,?,?,?,?)""",
            (
                item_id, hq_id, destination_id, quantity, reference.strip(),
                notes.strip(), performed_by.strip(), now,
            ),
        )
        conn.commit()
        return stv_number
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def open_shipments(site: str | None = None) -> pd.DataFrame:
    conn = connect_db()
    site_filter = " AND ts.name=?" if site else ""
    df = pd.read_sql_query(
        f"""
        SELECT sh.id, sh.dispatched_at, i.sku, i.name AS item, i.unit,
               fs.name AS from_site, ts.name AS to_site,
               sh.quantity_dispatched, sh.dispatch_reference,
               sh.stv_number, sh.dispatched_by, sh.dispatch_notes, sh.status
        FROM shipments sh
        JOIN items i ON i.id=sh.item_id
        JOIN sites fs ON fs.id=sh.from_site_id
        JOIN sites ts ON ts.id=sh.to_site_id
        WHERE sh.status='In Transit' {site_filter}
        ORDER BY sh.id DESC
        """,
        conn,
        params=(site,) if site else (),
    )
    conn.close()
    return df


def shipments(site: str | None = None) -> pd.DataFrame:
    conn = connect_db()
    site_filter = " WHERE ts.name=?" if site else ""
    df = pd.read_sql_query(
        f"""
        SELECT sh.id, sh.stv_number, sh.dispatched_at, sh.received_at, sh.status,
               i.sku, i.name AS item, i.unit, fs.name AS from_site,
               ts.name AS to_site, sh.quantity_dispatched,
               sh.quantity_received,
               ROUND(sh.quantity_dispatched-COALESCE(sh.quantity_received,0),2) AS variance,
               sh.dispatch_reference, sh.receipt_reference,
               sh.dispatched_by, sh.received_by
        FROM shipments sh
        JOIN items i ON i.id=sh.item_id
        JOIN sites fs ON fs.id=sh.from_site_id
        JOIN sites ts ON ts.id=sh.to_site_id
        {site_filter}
        ORDER BY sh.id DESC
        """,
        conn,
        params=(site,) if site else (),
    )
    conn.close()
    return df


def shipment_voucher(shipment_id: int) -> dict | None:
    conn = connect_db()
    row = conn.execute(
        """SELECT sh.id,sh.stv_number,sh.dispatched_at,sh.received_at,sh.status,
        sh.quantity_dispatched,sh.dispatch_reference,sh.dispatch_notes,
        sh.quantity_received,sh.dispatched_by,sh.received_by,
        sh.receipt_reference,sh.receipt_notes,i.sku,i.name AS item,i.unit,
        fs.name AS from_site,ts.name AS to_site
        FROM shipments sh
        JOIN items i ON i.id=sh.item_id
        JOIN sites fs ON fs.id=sh.from_site_id
        JOIN sites ts ON ts.id=sh.to_site_id
        WHERE sh.id=?""",
        (shipment_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def receive_shipment(
    shipment_id: int,
    quantity_received: float,
    reference: str,
    notes: str,
    performed_by: str,
) -> None:
    if quantity_received < 0:
        raise ValueError("Received quantity cannot be negative.")
    if not float(quantity_received).is_integer():
        raise ValueError("Received quantity must be a whole number.")
    conn = connect_db()
    now = datetime.now().isoformat(timespec="seconds")
    shipment = conn.execute(
        """SELECT item_id,from_site_id,to_site_id,quantity_dispatched,status
        FROM shipments WHERE id=?""",
        (shipment_id,),
    ).fetchone()
    if not shipment or shipment["status"] != "In Transit":
        conn.close()
        raise ValueError("This dispatch is no longer open.")
    if quantity_received > shipment["quantity_dispatched"]:
        conn.close()
        raise ValueError("Received quantity cannot exceed the dispatched quantity.")
    try:
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE stock SET quantity=quantity+?, updated_at=? WHERE item_id=? AND site_id=?",
            (quantity_received, now, shipment["item_id"], shipment["to_site_id"]),
        )
        conn.execute(
            """UPDATE shipments SET quantity_received=?,status='Received',
            receipt_reference=?,receipt_notes=?,received_by=?,received_at=? WHERE id=?""",
            (
                quantity_received, reference.strip(), notes.strip(),
                performed_by.strip(), now, shipment_id,
            ),
        )
        receipt_notes = notes.strip()
        variance = shipment["quantity_dispatched"] - quantity_received
        if variance:
            receipt_notes = (
                f"{receipt_notes} | " if receipt_notes else ""
            ) + f"Short/damaged variance: {variance:.0f}"
        conn.execute(
            """INSERT INTO movements
            (movement_type,item_id,from_site_id,to_site_id,quantity,reference,notes,performed_by,created_at)
            VALUES ('Camp Receipt',?,?,?,?,?,?,?,?)""",
            (
                shipment["item_id"], shipment["from_site_id"], shipment["to_site_id"],
                quantity_received, reference.strip(), receipt_notes, performed_by.strip(), now,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def movements(limit: int = 500, site: str | None = None) -> pd.DataFrame:
    conn = connect_db()
    site_filter = " WHERE fs.name=? OR ts.name=?" if site else ""
    df = pd.read_sql_query(
        f"""
        SELECT m.id, m.created_at, m.movement_type, i.sku, i.name AS item,
               fs.name AS from_site, ts.name AS to_site, m.quantity,
               i.unit, m.reference, m.performed_by, m.notes
        FROM movements m
        JOIN items i ON i.id=m.item_id
        LEFT JOIN sites fs ON fs.id=m.from_site_id
        LEFT JOIN sites ts ON ts.id=m.to_site_id
        {site_filter}
        ORDER BY m.id DESC LIMIT ?
        """,
        conn,
        params=(site, site, limit) if site else (limit,),
    )
    conn.close()
    return df
