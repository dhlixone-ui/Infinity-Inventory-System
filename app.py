from __future__ import annotations

from html import escape
from io import BytesIO
from pathlib import Path
import secrets

import pandas as pd
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from core.database import (
    SITES,
    add_item,
    authenticate,
    bulk_add_items,
    change_password,
    create_user,
    dispatch_stock,
    guest_consumption,
    init_db,
    inventory,
    item_options,
    license_status,
    list_users,
    movements,
    open_shipments,
    purchase_receipt,
    receive_shipment,
    shipments,
    shipment_voucher,
    set_user_active,
    set_user_display_name,
    staff_consumption,
    update_profile,
)
from utils.branding import hero, inject_css, login_header, sidebar_brand

st.set_page_config(
    page_title="Infinity Cloud Systems Inventory",
    page_icon="🏕️",
    layout="wide",
    initial_sidebar_state="expanded",
)
init_db()
inject_css()

# Temporary proof-of-concept setting. Set this to False before the system is
# used operationally so individual user accounts and password protection return.
DEMO_MODE = True

if DEMO_MODE:
    st.session_state.auth_user = {
        "id": 0,
        "username": "demo-super-user",
        "display_name": "Super User",
        "role": "Super User",
        "site": "HQ Steppes Road",
        "profile_image": None,
        "profile_image_type": None,
        "must_change_password": False,
    }
elif "auth_user" not in st.session_state:
    login_header()
    left, centre, right = st.columns([1.1, 1.4, 1.1])
    with centre:
        with st.form("login_form"):
            st.markdown("### Sign in")
            st.caption("Use your assigned account details.")
            username = st.text_input(
                "Email address or Super User username",
                placeholder="name@example.com",
            )
            password = st.text_input("Password", type="password")
            login = st.form_submit_button("Sign in", type="primary", use_container_width=True)
    if login:
        user = authenticate(username, password)
        if user:
            st.session_state.auth_user = user
            st.rerun()
        else:
            st.error("Incorrect username or password.")
    st.stop()

user = st.session_state.auth_user
if user["must_change_password"]:
    hero("Set your password", "Your temporary password must be changed before continuing.")
    with st.form("password_change_form"):
        current_password = st.text_input("Temporary password", type="password")
        new_password = st.text_input("New password", type="password")
        confirm_password = st.text_input("Confirm new password", type="password")
        update_password = st.form_submit_button("Change password", type="primary")
    if update_password:
        if new_password != confirm_password:
            st.error("The new passwords do not match.")
        else:
            try:
                change_password(user["id"], current_password, new_password)
                st.session_state.auth_user["must_change_password"] = False
                st.success("Password changed successfully.")
                st.rerun()
            except ValueError as error:
                st.error(str(error))
    st.stop()

sidebar_brand()
if user.get("profile_image") and not DEMO_MODE:
    st.sidebar.image(user["profile_image"], width=82)
if DEMO_MODE:
    st.sidebar.info("Proof-of-concept mode\n\nNo login is required. Everyone has Super User access.")
else:
    st.sidebar.caption(f"Signed in as **{user['display_name']}**")
    st.sidebar.caption(f"{user['role']} · {user['site']}")
if not DEMO_MODE and st.sidebar.button("Sign out"):
    del st.session_state.auth_user
    st.rerun()

is_hq = user["role"] in {"Super User", "HQ User"}
is_super = user["role"] == "Super User"
licence = license_status()
licence_read_only = licence["expired"] and not is_super
can_operate = not licence_read_only
if licence["days_remaining"] <= 14:
    if licence["expired"]:
        if is_super:
            st.sidebar.warning("Licence expired · Super User access active")
        else:
            st.sidebar.error("Licence expired · Read-only access")
    else:
        day_word = "day" if licence["days_remaining"] == 1 else "days"
        st.sidebar.warning(
            f"Licence: {licence['days_remaining']} {day_word} remaining"
        )

pages = ["Overview", "Inventory"] if DEMO_MODE else ["My Profile", "Overview", "Inventory"]
if can_operate:
    pages.append("Stock movements")
pages.append("Reports")
if is_super:
    pages.append("Administration")
page = st.sidebar.radio("NAVIGATION", pages)

if DEMO_MODE:
    st.warning(
        "Proof-of-concept mode is active. Password protection and individual user "
        "accounts are temporarily disabled. Anyone with this link has full access."
    )

if licence_read_only:
    st.error(
        "The software licence has expired. The system is now in read-only mode: "
        "you can view and download existing records, but stock changes are disabled. "
        "Please contact the HQ Super User."
    )
elif licence["expired"] and is_super:
    st.warning(
        "The software licence has expired. Your Super User rights remain fully active "
        "for administration and licence management."
    )


def excel_bytes(frames: dict[str, pd.DataFrame]) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet[:31], index=False)
    return output.getvalue()


ITEM_CATEGORIES = [
    "Food & Beverage", "Fuel", "Housekeeping", "Maintenance",
    "Operations", "Safety", "Other",
]
BULK_ITEM_COLUMNS = [
    "SKU", "Item name", "Category", "Unit", "Unit cost (USD)",
    "Minimum level per site", "Opening HQ quantity",
    "Opening The Hide quantity", "Opening Changa quantity",
]


def bulk_item_template() -> bytes:
    instructions = pd.DataFrame(
        {
            "Instructions": [
                "Enter one new inventory item per row on the Items sheet.",
                "Do not change the column headings.",
                "SKUs and item names must be unique.",
                "Quantities and minimum levels must be whole numbers of zero or more.",
                "Opening quantities are optional; leave them blank to use zero.",
                "Allowed categories: " + ", ".join(ITEM_CATEGORIES),
            ]
        }
    )
    return excel_bytes(
        {"Items": pd.DataFrame(columns=BULK_ITEM_COLUMNS), "Instructions": instructions}
    )


def validate_bulk_items(frame: pd.DataFrame) -> tuple[list[dict], pd.DataFrame]:
    missing = [column for column in BULK_ITEM_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError("Missing columns: " + ", ".join(missing))

    frame = frame[BULK_ITEM_COLUMNS].dropna(how="all").copy()
    current = inventory()
    existing_skus = {str(value).strip().upper() for value in current["sku"].dropna()}
    existing_names = {str(value).strip().casefold() for value in current["name"].dropna()}
    file_skus = frame["SKU"].fillna("").astype(str).str.strip().str.upper()
    file_names = frame["Item name"].fillna("").astype(str).str.strip().str.casefold()
    duplicate_skus = set(file_skus[file_skus.duplicated(keep=False) & file_skus.ne("")])
    duplicate_names = set(file_names[file_names.duplicated(keep=False) & file_names.ne("")])

    records: list[dict] = []
    preview_rows: list[dict] = []
    numeric_columns = [
        "Unit cost (USD)", "Minimum level per site", "Opening HQ quantity",
        "Opening The Hide quantity", "Opening Changa quantity",
    ]
    for position, (_, row) in enumerate(frame.iterrows(), start=2):
        errors: list[str] = []
        sku = "" if pd.isna(row["SKU"]) else str(row["SKU"]).strip().upper()
        name = "" if pd.isna(row["Item name"]) else str(row["Item name"]).strip()
        category = "" if pd.isna(row["Category"]) else str(row["Category"]).strip()
        unit = "" if pd.isna(row["Unit"]) else str(row["Unit"]).strip()
        if not sku:
            errors.append("SKU is required")
        elif sku in existing_skus:
            errors.append("SKU already exists")
        elif sku in duplicate_skus:
            errors.append("Duplicate SKU in file")
        if not name:
            errors.append("Item name is required")
        elif name.casefold() in existing_names:
            errors.append("Item name already exists")
        elif name.casefold() in duplicate_names:
            errors.append("Duplicate item name in file")
        if category not in ITEM_CATEGORIES:
            errors.append("Invalid category")
        if not unit:
            errors.append("Unit is required")

        values: dict[str, float] = {}
        for column in numeric_columns:
            raw = 0 if pd.isna(row[column]) or str(row[column]).strip() == "" else row[column]
            try:
                number = float(raw)
                if number < 0:
                    errors.append(f"{column} cannot be negative")
                elif column != "Unit cost (USD)" and not number.is_integer():
                    errors.append(f"{column} must be a whole number")
                values[column] = number
            except (TypeError, ValueError):
                errors.append(f"{column} must be numeric")
                values[column] = 0

        status = "Ready" if not errors else "Needs correction"
        preview_rows.append(
            {
                "Excel row": position, "SKU": sku, "Item name": name,
                "Category": category, "Unit": unit, "Status": status,
                "Validation": "; ".join(errors) if errors else "Valid",
            }
        )
        if not errors:
            records.append(
                {
                    "sku": sku, "name": name, "category": category, "unit": unit,
                    "unit_cost": values["Unit cost (USD)"],
                    "minimum_level": int(values["Minimum level per site"]),
                    "opening_stock": {
                        "HQ Steppes Road": int(values["Opening HQ quantity"]),
                        "The Hide Safaris": int(values["Opening The Hide quantity"]),
                        "Changa Safari Camp": int(values["Opening Changa quantity"]),
                    },
                }
            )
    return records, pd.DataFrame(preview_rows)


def dynamics_table(frame: pd.DataFrame) -> pd.io.formats.style.Styler:
    table = frame.reset_index(drop=True)

    def row_shade(row: pd.Series) -> list[str]:
        background = "#e3e0dd" if row.name % 2 == 0 else "#faf9f8"
        return [f"background-color:{background};color:#292826" for _ in row]

    whole_number_columns = {
        column: "{:.0f}"
        for column in table.columns
        if column in {
            "quantity", "minimum_level", "quantity_dispatched",
            "quantity_received", "variance", "shortfall",
            "Healthy", "Low stock", "Out of stock",
        }
    }
    return table.style.apply(row_shade, axis=1).format(whole_number_columns)


def stv_pdf(voucher: dict) -> bytes:
    def value(item: object) -> str:
        return escape(str(item or "-"))

    dark = colors.HexColor("#474542")
    orange = colors.HexColor("#AA6B39")
    light = colors.HexColor("#E3E0DD")
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=value(voucher["stv_number"]),
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "STVTitle", parent=styles["Title"], textColor=dark, fontSize=20,
        leading=24, alignment=TA_CENTER, spaceAfter=5,
    )
    centre_style = ParagraphStyle(
        "Centre", parent=styles["BodyText"], alignment=TA_CENTER,
        textColor=colors.HexColor("#666666"),
    )
    small_style = ParagraphStyle(
        "Small", parent=styles["BodyText"], fontSize=8.5, leading=11,
    )
    section_style = ParagraphStyle(
        "Section", parent=styles["Heading3"], textColor=dark, fontSize=10,
        leading=13, leftIndent=4,
    )
    logo_filename = (
        "changa_safari_camp.png"
        if voucher["to_site"] == "Changa Safari Camp"
        else "the_hide.png"
    )
    logo_path = Path(__file__).resolve().parent / "assets" / logo_filename
    logo = Image(str(logo_path), width=42 * mm, height=42 * mm, kind="proportional")
    heading = [
        Paragraph("STOCK TRANSFER VOUCHER", title_style),
        Paragraph(f"Goods transferred to {value(voucher['to_site'])}", centre_style),
    ]
    header = Table([[logo, heading]], colWidths=[48 * mm, 126 * mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 3, orange),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    dispatched_at = pd.to_datetime(voucher["dispatched_at"]).strftime("%d %B %Y, %H:%M")
    strip = Table(
        [
            ["STV NUMBER", "DISPATCH DATE", "STATUS"],
            [value(voucher["stv_number"]), dispatched_at, value(voucher["status"])],
        ],
        colWidths=[58 * mm, 67 * mm, 49 * mm],
    )
    strip.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), dark),
        ("TEXTCOLOR", (0, 0), (-1, 0), orange),
        ("TEXTCOLOR", (0, 1), (-1, 1), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("FONTSIZE", (0, 1), (-1, 1), 9),
        ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#696663")),
        ("PADDING", (0, 0), (-1, -1), 7),
    ]))
    details = Table(
        [
            ["FROM LOCATION", "DESTINATION", "DISPATCH REFERENCE"],
            [
                value(voucher["from_site"]), value(voucher["to_site"]),
                value(voucher["dispatch_reference"]),
            ],
        ],
        colWidths=[58 * mm, 58 * mm, 58 * mm],
    )
    items = Table(
        [
            ["SKU", "ITEM", "QUANTITY", "UNIT"],
            [
                value(voucher["sku"]), value(voucher["item"]),
                f"{voucher['quantity_dispatched']:.0f}", value(voucher["unit"]),
            ],
        ],
        colWidths=[34 * mm, 78 * mm, 32 * mm, 30 * mm],
    )
    standard_table_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), light),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#292826")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), .5, colors.HexColor("#999999")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 7),
    ])
    details.setStyle(standard_table_style)
    items.setStyle(standard_table_style)

    def section_heading(text: str) -> Table:
        table = Table([[Paragraph(text, section_style)]], colWidths=[174 * mm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), light),
            ("LINEBEFORE", (0, 0), (0, 0), 4, orange),
            ("PADDING", (0, 0), (-1, -1), 7),
        ]))
        return table

    line = "________________________________________"
    issued = Table(
        [
            ["ISSUED BY", "ISSUER SIGNATURE"],
            [value(voucher["dispatched_by"]), line],
            ["DISPATCH DATE", "VEHICLE / DRIVER"],
            [dispatched_at, line],
        ],
        colWidths=[87 * mm, 87 * mm],
        rowHeights=[7 * mm, 12 * mm, 7 * mm, 12 * mm],
    )
    received = Table(
        [
            ["RECEIVED BY - PRINT FULL NAME", "RECEIVER SIGNATURE"],
            [line, line],
            ["ACTUAL QUANTITY RECEIVED", "DATE AND TIME RECEIVED"],
            [line, line],
            ["SHORTAGE / DAMAGE / VARIANCE", "CAMP RECEIPT REFERENCE"],
            [line, line],
        ],
        colWidths=[87 * mm, 87 * mm],
        rowHeights=[7 * mm, 12 * mm, 7 * mm, 12 * mm, 7 * mm, 12 * mm],
    )
    signature_style = TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("FONTNAME", (0, 4), (-1, 4), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#292826")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ])
    issued.setStyle(signature_style)
    received.setStyle(signature_style)
    notes = Table(
        [[Paragraph("<b>Dispatch notes</b>", small_style)],
         [Paragraph(value(voucher["dispatch_notes"]), small_style)]],
        colWidths=[174 * mm], rowHeights=[8 * mm, 16 * mm],
    )
    notes.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#999999")),
        ("BACKGROUND", (0, 0), (-1, 0), light),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    notice = Table(
        [[Paragraph(
            "This physical STV must travel with the transferred goods and be signed "
            "at the destination when the goods are received. Retain the signed copy "
            "for stock-control records.",
            small_style,
        )]],
        colWidths=[174 * mm],
    )
    notice.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), .8, orange),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF8F2")),
        ("PADDING", (0, 0), (-1, -1), 8),
    ]))
    story = [
        header, Spacer(1, 7 * mm), strip, Spacer(1, 5 * mm), details,
        Spacer(1, 5 * mm), items, Spacer(1, 5 * mm), notes,
        Spacer(1, 5 * mm), section_heading("Issue and dispatch confirmation"),
        issued, Spacer(1, 4 * mm),
        section_heading("Receipt confirmation - complete at destination"),
        received, Spacer(1, 5 * mm), notice,
    ]

    def white_page(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFillColor(colors.white)
        canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
        canvas.restoreState()

    document.build(story, onFirstPage=white_page, onLaterPages=white_page)
    return buffer.getvalue()


if page == "Overview":
    scope_label = "all locations" if is_hq else user["site"]
    hero(
        f"Hello, {user['display_name']}",
        f"Operational stock position and actions for {scope_label}.",
    )
    data = inventory() if is_hq else inventory(user["site"])
    transit = open_shipments() if is_hq else open_shipments(user["site"])
    shipment_history = shipments() if is_hq else shipments(user["site"])
    activity = movements(limit=500) if is_hq else movements(limit=500, site=user["site"])
    activity["created_at"] = pd.to_datetime(activity["created_at"])
    if not transit.empty:
        transit["dispatched_at"] = pd.to_datetime(transit["dispatched_at"])

    total_items = data["sku"].nunique()
    low = data[data["status"] == "Low stock"]
    out_of_stock = data[data["quantity"] <= 0]
    overdue = (
        transit[transit["dispatched_at"] < pd.Timestamp.now() - pd.Timedelta(days=3)]
        if not transit.empty else transit
    )
    month_start = pd.Timestamp.now().normalize().replace(day=1)
    month_consumption = activity[
        activity["movement_type"].isin(["Guest Consumption", "Staff Consumption"])
        & (activity["created_at"] >= month_start)
    ]
    received_variances = shipment_history[
        (shipment_history["status"] == "Received")
        & (shipment_history["variance"].fillna(0) != 0)
    ]

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Active SKUs", f"{total_items:,}")
    m2.metric("Low-stock lines", f"{len(low):,}", "Action required")
    m3.metric("Out of stock", f"{len(out_of_stock):,}")
    m4.metric("In transit", f"{len(transit):,}", "Awaiting receipt")
    m5.metric("Overdue receipts", f"{len(overdue):,}", "More than 3 days")
    m6.metric(
        "Consumption this month",
        f"{month_consumption['quantity'].sum():.0f}",
        "Guest + staff",
    )

    st.subheader("Action required")
    action_left, action_right = st.columns(2)
    with action_left:
        st.markdown("#### Replenishment required")
        if low.empty:
            st.success("All stock is above minimum level.")
        else:
            low_display = low[
                ["sku", "name", "site", "quantity", "minimum_level", "unit"]
            ].copy()
            low_display["shortfall"] = (
                low_display["minimum_level"] - low_display["quantity"]
            ).clip(lower=0)
            low_display = low_display.sort_values(
                ["shortfall", "site"], ascending=[False, True]
            )
            st.dataframe(
                dynamics_table(low_display),
                width="stretch",
                hide_index=True,
                column_config={
                    "minimum_level": "Minimum",
                    "shortfall": "Suggested replenishment",
                },
            )
    with action_right:
        st.markdown("#### Transfers awaiting receipt")
        if transit.empty:
            st.success("No transfers are awaiting receipt.")
        else:
            transfer_display = transit[
                [
                    "stv_number", "dispatched_at", "item", "to_site",
                    "quantity_dispatched", "unit", "dispatched_by",
                ]
            ].copy()
            transfer_display["dispatched_at"] = transfer_display[
                "dispatched_at"
            ].dt.strftime("%d %b %Y")
            st.dataframe(
                dynamics_table(transfer_display),
                width="stretch",
                hide_index=True,
                column_config={
                    "stv_number": "STV",
                    "dispatched_at": "Dispatched",
                    "to_site": "Destination",
                    "quantity_dispatched": "Quantity",
                    "dispatched_by": "Issued by",
                },
            )

    st.subheader("Location health and consumption")
    analysis_left, analysis_right = st.columns(2)
    with analysis_left:
        location_health = (
            data.groupby(["site", "status"], as_index=False)
            .size()
            .pivot(index="site", columns="status", values="size")
            .fillna(0)
        )
        st.markdown("#### Stock health by site")
        if data.empty:
            st.info("No actual stock has been entered yet.")
        else:
            st.bar_chart(
                location_health,
                color=[
                    "#aa6b39" if column == "Low stock" else "#474542"
                    for column in location_health.columns
                ],
                horizontal=True,
                height=250,
            )
            st.caption("Number of healthy and low-stock item lines at each location.")
    with analysis_right:
        last_30_days = activity[
            activity["movement_type"].isin(
                ["Guest Consumption", "Staff Consumption"]
            )
            & (
                activity["created_at"]
                >= pd.Timestamp.now().normalize() - pd.Timedelta(days=29)
            )
        ].copy()
        st.markdown("#### Consumption - last 30 days")
        if last_30_days.empty:
            st.info("No guest or staff consumption has been recorded.")
        else:
            guest_total = last_30_days.loc[
                last_30_days["movement_type"] == "Guest Consumption", "quantity"
            ].sum()
            staff_total = last_30_days.loc[
                last_30_days["movement_type"] == "Staff Consumption", "quantity"
            ].sum()
            guest_metric, staff_metric = st.columns(2)
            guest_metric.metric("Guest consumption", f"{guest_total:.0f}")
            staff_metric.metric("Staff consumption", f"{staff_total:.0f}")
            consumption_items = (
                last_30_days.groupby(["item", "movement_type"], as_index=False)[
                    "quantity"
                ]
                .sum()
                .sort_values("quantity", ascending=False)
                .head(6)
            )
            st.caption("Most consumed items")
            st.dataframe(
                dynamics_table(consumption_items),
                width="stretch",
                hide_index=True,
                column_config={
                    "item": "Item",
                    "movement_type": "Consumption type",
                    "quantity": "Quantity",
                },
            )

    st.subheader("Recent stock activity")
    recent = activity.head(12)[
        [
            "created_at", "movement_type", "item", "quantity", "unit",
            "from_site", "to_site", "performed_by", "reference",
        ]
    ].copy()
    recent["created_at"] = recent["created_at"].dt.strftime("%d %b %Y %H:%M")
    st.dataframe(
        dynamics_table(recent),
        width="stretch",
        hide_index=True,
        column_config={
            "created_at": "Date and time",
            "movement_type": "Activity",
            "performed_by": "Recorded by",
        },
    )
    if not received_variances.empty:
        st.warning(
            f"{len(received_variances)} received transfer(s) have a quantity variance."
        )

elif page == "Inventory":
    hero("Inventory", "A live view of stock levels across your locations.")
    all_inventory = inventory()
    scoped_inventory = inventory() if is_hq else inventory(user["site"])
    low_total = int((scoped_inventory["status"] == "Low stock").sum())
    item_total = int(scoped_inventory["id"].nunique())
    stock_value_total = float(scoped_inventory["stock_value"].sum())
    location_total = int(scoped_inventory["site"].nunique())
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Items in catalogue", f"{item_total:,}")
    m2.metric("Locations shown", location_total)
    m3.metric("Stock value", f"${stock_value_total:,.2f}")
    m4.metric("Needs attention", low_total)

    st.markdown('<div class="inventory-toolbar"><div class="inventory-toolbar-title">FIND STOCK</div>', unsafe_allow_html=True)
    f1, f2, f3, f4 = st.columns([1.1, 1.15, 1.8, 1])
    if is_hq:
        selected_site = f1.selectbox("Site", ["All sites", *SITES])
    else:
        selected_site = user["site"]
        f1.text_input("Site", value=selected_site, disabled=True)
    selected_category = f2.selectbox("Category", ["All categories"] + sorted(all_inventory["category"].unique().tolist()))
    search = f3.text_input("Search", placeholder="Search by item name or SKU")
    stock_filter = f4.selectbox("Stock status", ["All stock", "Needs attention", "Healthy only"])
    st.markdown('</div>', unsafe_allow_html=True)
    data = inventory(selected_site)
    if selected_category != "All categories":
        data = data[data["category"] == selected_category]
    if search:
        mask = data["name"].str.contains(search, case=False, na=False) | data["sku"].str.contains(search, case=False, na=False)
        data = data[mask]
    if stock_filter == "Needs attention":
        data = data[data["status"] == "Low stock"]
    elif stock_filter == "Healthy only":
        data = data[data["status"] == "Healthy"]
    display = data[["sku", "name", "category", "site", "quantity", "unit", "minimum_level", "status", "stock_value"]].copy()
    display = display.sort_values(["status", "name", "site"], ascending=[True, True, True])
    st.markdown('<div class="section-kicker">Stock register</div>', unsafe_allow_html=True)
    table_left, table_right = st.columns([3, 1])
    table_left.markdown(
        f'<p class="table-caption">Showing <strong>{len(display):,}</strong> stock record(s). '
        'Use the filters above to find an item quickly.</p>',
        unsafe_allow_html=True,
    )
    with table_right:
        st.download_button(
            "Download CSV", display.to_csv(index=False), "camp_inventory.csv", "text/csv",
            use_container_width=True,
        )
    if low_total and stock_filter != "Healthy only":
        st.markdown(
            f'<div class="notice-strip"><strong>{low_total} item(s)</strong> are below their minimum stock level. '
            'Choose “Needs attention” to focus on them.</div>',
            unsafe_allow_html=True,
        )
    st.dataframe(
        dynamics_table(display),
        width="stretch",
        hide_index=True,
        column_config={
            "sku": "SKU",
            "name": "Item",
            "category": "Category",
            "site": "Location",
            "quantity": st.column_config.NumberColumn("On hand", format="%d"),
            "unit": "Unit",
            "stock_value": st.column_config.NumberColumn("Value", format="$%.2f"),
            "minimum_level": "Minimum",
            "status": "Stock status",
        },
    )

elif page == "Stock movements":
    hero("Stock movements", "Track every item from HQ purchasing through camp receipt and guest consumption.")
    options = item_options()
    workflow_steps = (
        [
            "Purchase at HQ", "Dispatch to Camp", "Receive at Camp",
            "Guest Consumption", "Staff Consumption",
        ]
        if is_hq else ["Receive at Camp", "Guest Consumption", "Staff Consumption"]
    )
    movement_type = st.segmented_control(
        "Workflow step",
        workflow_steps,
        default=workflow_steps[0],
        key="movement_type",
    )

    if movement_type == "Purchase at HQ":
        st.info("Use this when supplier stock physically arrives at HQ Steppes Road.")
        with st.container(border=True):
            c1, c2 = st.columns(2)
            item_label = c1.selectbox("Item", list(options))
            quantity = c2.number_input("Quantity received", min_value=1, step=1)
            c3, c4 = st.columns(2)
            reference = c3.text_input("Purchase order / supplier invoice")
            c4.text_input("Received by", value=user["display_name"], disabled=True)
            notes = st.text_area("Supplier or delivery notes")
            submitted = st.button(
                "Receive into HQ stock", type="primary", key="submit_purchase"
            )
        if submitted:
            try:
                purchase_receipt(
                    options[item_label], quantity, reference, notes, user["display_name"]
                )
                st.success("Purchase receipt added to HQ Steppes Road.")
                st.rerun()
            except ValueError as error:
                st.error(str(error))

    elif movement_type == "Dispatch to Camp":
        st.info("HQ stock is deducted now and remains In Transit until the camp confirms receipt.")
        with st.container(border=True):
            c1, c2 = st.columns(2)
            item_label = c1.selectbox("Item", list(options))
            quantity = c2.number_input("Quantity to dispatch", min_value=1, step=1)
            c3, c4 = st.columns(2)
            destination = c3.selectbox("Destination camp", SITES[1:])
            reference = c4.text_input("Dispatch / transfer reference")
            st.text_input("Dispatched by", value=user["display_name"], disabled=True)
            notes = st.text_area("Vehicle, driver or dispatch notes")
            submitted = st.button(
                "Dispatch from HQ", type="primary", key="submit_dispatch"
            )
        if submitted:
            try:
                stv_number = dispatch_stock(
                    options[item_label], quantity, destination, reference, notes,
                    user["display_name"],
                )
                st.success(
                    f"Stock dispatched to {destination}. Voucher {stv_number} was created."
                )
                st.rerun()
            except ValueError as error:
                st.error(str(error))

    elif movement_type == "Receive at Camp":
        open_data = open_shipments() if is_hq else open_shipments(user["site"])
        if open_data.empty:
            st.success("There are no dispatches awaiting camp receipt.")
        else:
            shipment_labels = {
                f"#{int(row.id)} · {row.item} · {row.quantity_dispatched:.0f} {row.unit} → {row.to_site}": int(row.id)
                for row in open_data.itertuples()
            }
            selected_label = st.selectbox("Open dispatch", list(shipment_labels))
            selected_id = shipment_labels[selected_label]
            selected = open_data[open_data["id"] == selected_id].iloc[0]
            st.caption(
                f"Dispatched {selected['quantity_dispatched']:.0f} {selected['unit']} "
                f"from {selected['from_site']} to {selected['to_site']} · "
                f"Reference: {selected['dispatch_reference'] or '—'}"
            )
            with st.container(border=True):
                c1, c2 = st.columns(2)
                received_qty = c1.number_input(
                    "Actual quantity received",
                    min_value=0,
                    max_value=int(round(selected["quantity_dispatched"])),
                    value=int(round(selected["quantity_dispatched"])),
                    step=1,
                )
                receipt_reference = c2.text_input("Camp receipt reference")
                st.text_input("Received by", value=user["display_name"], disabled=True)
                receipt_notes = st.text_area("Shortage, damage or receipt notes")
                submitted = st.button(
                    "Confirm camp receipt", type="primary", key="submit_receipt"
                )
            if submitted:
                try:
                    receive_shipment(
                        selected_id, received_qty, receipt_reference, receipt_notes,
                        user["display_name"],
                    )
                    variance = float(selected["quantity_dispatched"]) - received_qty
                    if variance:
                        st.warning(f"Receipt saved with a variance of {variance:.0f} {selected['unit']}.")
                    else:
                        st.success("Camp receipt confirmed in full.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))

    else:
        is_staff_consumption = movement_type == "Staff Consumption"
        audience = "staff" if is_staff_consumption else "guests"
        st.info(f"Record drinks or other items provided to {audience}.")
        with st.container(border=True):
            c1, c2 = st.columns(2)
            item_label = c1.selectbox("Item", list(options))
            quantity = c2.number_input("Quantity consumed", min_value=1, step=1)
            c3, c4 = st.columns(2)
            if is_hq:
                camp = c3.selectbox("Camp", SITES[1:])
            else:
                camp = user["site"]
                c3.text_input("Camp", value=camp, disabled=True)
            reference_label = (
                "Staff member / department reference"
                if is_staff_consumption else
                "Tour / booking / guest reference"
            )
            reference = c4.text_input(reference_label)
            st.text_input("Recorded by", value=user["display_name"], disabled=True)
            notes = st.text_area("Guide, department or consumption notes")
            action_label = (
                "Record staff consumption"
                if is_staff_consumption else
                "Record guest consumption"
            )
            submitted = st.button(action_label, type="primary", key="submit_consumption")
        if submitted:
            try:
                consumption_function = (
                    staff_consumption if is_staff_consumption else guest_consumption
                )
                consumption_function(
                    options[item_label], quantity, camp, reference, notes,
                    user["display_name"],
                )
                st.success(f"{movement_type} deducted from {camp}.")
                st.rerun()
            except ValueError as error:
                st.error(str(error))

    transit_tab, history_tab = st.tabs(["In-transit dispatches", "Movement history"])
    with transit_tab:
        transit_display = open_shipments() if is_hq else open_shipments(user["site"])
        if transit_display.empty:
            st.caption("No stock is currently in transit.")
        else:
            st.dataframe(
                dynamics_table(transit_display), width="stretch", hide_index=True
            )
    with history_tab:
        history = movements() if is_hq else movements(site=user["site"])
        st.dataframe(dynamics_table(history), width="stretch", hide_index=True)

    st.subheader("Stock Transfer Vouchers")
    voucher_data = shipments() if is_hq else shipments(user["site"])
    if voucher_data.empty:
        st.caption("No stock transfer vouchers have been created yet.")
    else:
        voucher_labels = {
            f"{row.stv_number} · {row.item} · {row.from_site} → {row.to_site}": int(row.id)
            for row in voucher_data.itertuples()
        }
        selected_voucher_label = st.selectbox(
            "Select voucher", list(voucher_labels), key="selected_stv"
        )
        selected_voucher = shipment_voucher(
            voucher_labels[selected_voucher_label]
        )
        if selected_voucher:
            st.download_button(
                "Download STV",
                stv_pdf(selected_voucher),
                f"{selected_voucher['stv_number']}.pdf",
                "application/pdf",
                type="primary",
            )
            st.caption("The PDF is ready to print and send with the transferred goods.")

elif page == "Reports":
    hero("Reports", "Analyze stock, movements, consumption and transfer performance.")
    report_filter_1, report_filter_2 = st.columns(2)
    if is_hq:
        report_site = report_filter_1.selectbox(
            "Location", ["All locations", *SITES], key="report_site"
        )
    else:
        report_site = user["site"]
        report_filter_1.text_input("Location", value=report_site, disabled=True)
    all_report_data = inventory() if is_hq else inventory(user["site"])
    report_categories = sorted(all_report_data["category"].unique().tolist())
    report_category = report_filter_2.selectbox(
        "Category", ["All categories", *report_categories], key="report_category"
    )
    data = (
        inventory()
        if report_site == "All locations"
        else inventory(report_site)
    )
    if report_category != "All categories":
        data = data[data["category"] == report_category]
    history = (
        movements()
        if report_site == "All locations"
        else movements(site=report_site)
    )
    shipment_history = (
        shipments()
        if report_site == "All locations"
        else shipments(report_site)
    )

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Active SKUs", f"{data['sku'].nunique():,}")
    r2.metric("Inventory lines", f"{len(data):,}")
    r3.metric("Low-stock lines", f"{(data['status'] == 'Low stock').sum():,}")
    r4.metric("Stock value", f"${data['stock_value'].sum():,.2f}")

    if data.empty:
        st.info(
            "No actual inventory has been entered yet. Use Administration → Item "
            "catalogue → Bulk item import to upload the customer's opening stock."
        )
        st.stop()

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Stock position", "Movement ledger", "Transfers", "Exports"]
    )
    with tab1:
        stock_left, stock_right = st.columns([1.1, 1])
        with stock_left:
            st.markdown("#### Stock value by category")
            by_category = (
                data.groupby("category", as_index=False)["stock_value"]
                .sum()
                .sort_values("stock_value", ascending=True)
            )
            st.bar_chart(
                by_category,
                x="stock_value",
                y="category",
                color="#474542",
                horizontal=True,
                height=300,
            )
        with stock_right:
            st.markdown("#### Stock health by location")
            stock_health = (
                data.assign(
                    health=data.apply(
                        lambda row: (
                            "Out of stock"
                            if row["quantity"] <= 0
                            else row["status"]
                        ),
                        axis=1,
                    )
                )
                .groupby(["site", "health"], as_index=False)
                .size()
                .pivot(index="site", columns="health", values="size")
                .fillna(0)
                .reset_index()
            )
            for health_column in ["Healthy", "Low stock", "Out of stock"]:
                if health_column not in stock_health:
                    stock_health[health_column] = 0
            st.dataframe(
                dynamics_table(
                    stock_health[["site", "Healthy", "Low stock", "Out of stock"]]
                ),
                width="stretch",
                hide_index=True,
                column_config={"site": "Location"},
            )
            st.markdown("#### Highest-value stock lines")
            value_lines = data.nlargest(6, "stock_value")[
                ["sku", "name", "site", "quantity", "stock_value"]
            ]
            st.dataframe(
                dynamics_table(value_lines),
                width="stretch",
                hide_index=True,
                column_config={
                    "site": "Location",
                    "stock_value": st.column_config.NumberColumn(
                        "Value", format="$%.2f"
                    ),
                },
            )
    with tab2:
        movement_options = ["All activity", *sorted(history["movement_type"].unique())]
        movement_filter = st.selectbox(
            "Activity type", movement_options, key="report_movement_type"
        )
        ledger = history.copy()
        if movement_filter != "All activity":
            ledger = ledger[ledger["movement_type"] == movement_filter]
        st.dataframe(
            dynamics_table(ledger),
            width="stretch",
            hide_index=True,
            column_config={
                "created_at": "Date and time",
                "movement_type": "Activity",
                "performed_by": "Recorded by",
            },
        )
    with tab3:
        received_count = int((shipment_history["status"] == "Received").sum())
        transit_count = int((shipment_history["status"] == "In Transit").sum())
        variance_count = int(
            (
                (shipment_history["status"] == "Received")
                & (shipment_history["variance"].fillna(0) != 0)
            ).sum()
        )
        t1, t2, t3 = st.columns(3)
        t1.metric("Received transfers", f"{received_count:,}")
        t2.metric("In transit", f"{transit_count:,}")
        t3.metric("Receipt variances", f"{variance_count:,}")
        st.dataframe(
            dynamics_table(shipment_history),
            width="stretch",
            hide_index=True,
            column_config={
                "stv_number": "STV",
                "from_site": "From",
                "to_site": "Destination",
                "quantity_dispatched": "Dispatched",
                "quantity_received": "Received",
            },
        )
    with tab4:
        st.download_button(
            "Download complete Excel report",
            excel_bytes({"Inventory": data, "Movements": history, "Dispatches": shipment_history}),
            "infinity_cloud_systems_inventory_report.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
        st.caption("The workbook contains inventory, movement history and the complete dispatch/receipt trail.")

elif page == "My Profile":
    hero("My Profile", "Update the name and photograph shown on your account.")
    profile_left, profile_right = st.columns([1, 2])
    with profile_left:
        if user.get("profile_image"):
            st.image(user["profile_image"], width=220)
        else:
            st.info("No profile photograph has been added yet.")
    with profile_right:
        with st.container(border=True):
            identity_left, identity_right = st.columns(2)
            identity_left.text_input(
                "Login ID", value=user["username"], disabled=True,
                help="Email address used to sign in, or the protected Super User username.",
            )
            profile_name = identity_right.text_input(
                "Full name (shown on stock activity)",
                value=user["display_name"],
                disabled=not is_super,
                help=(
                    "Only the HQ Super User can change account names."
                    if not is_super else
                    "This name is recorded against stock activity."
                ),
            )
            profile_photo = st.file_uploader(
                "Profile image",
                type=["png", "jpg", "jpeg", "webp"],
                help="PNG, JPG or WebP. Maximum file size: 5 MB.",
                disabled=licence_read_only,
            )
            remove_photo = st.checkbox(
                "Remove current profile image",
                disabled=licence_read_only or not bool(user.get("profile_image")),
            )
            save_profile = st.button(
                "Save profile",
                type="primary",
                use_container_width=True,
                key="save_profile",
                disabled=licence_read_only,
            )
        if save_profile:
            if profile_photo and profile_photo.size > 5 * 1024 * 1024:
                st.error("The selected image is larger than 5 MB.")
            else:
                try:
                    image_bytes = profile_photo.getvalue() if profile_photo else None
                    image_type = profile_photo.type if profile_photo else None
                    update_profile(
                        user["id"],
                        profile_name if is_super else user["display_name"],
                        image_bytes,
                        image_type,
                        remove_photo,
                    )
                    st.session_state.auth_user["display_name"] = profile_name.strip()
                    if remove_photo:
                        st.session_state.auth_user["profile_image"] = None
                        st.session_state.auth_user["profile_image_type"] = None
                    elif image_bytes is not None:
                        st.session_state.auth_user["profile_image"] = image_bytes
                        st.session_state.auth_user["profile_image_type"] = image_type
                    st.success("Your profile has been updated.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))
    st.caption(f"Access: {user['role']} · {user['site']}")

else:
    hero("Administration", "Create users, control access and manage the item catalogue.")
    st.subheader("Software licence")
    licence_label = (
        "Expired"
        if licence["expired"]
        else f"Valid · {licence['days_remaining']} days remaining"
    )
    l1, l2 = st.columns(2)
    l1.metric("Licence status", licence_label)
    l2.metric("Valid until", licence["expires_on"].strftime("%d %B %Y"))
    st.caption(
        "The sidebar countdown becomes visible automatically when 14 days or fewer remain."
    )
    st.divider()
    st.subheader("Create a user")
    with st.container(border=True):
        c1, c2 = st.columns(2)
        new_username = c1.text_input(
            "Email address (used for login)", placeholder="e.g. stores@changa.com"
        )
        display_name = c2.text_input(
            "Full name",
            help="Shown on stock activity, STVs and the audit history.",
        )
        c3, c4 = st.columns(2)
        new_role = c3.selectbox("Role", ["HQ User", "Camp User"])
        if new_role == "HQ User":
            assigned_site = "HQ Steppes Road"
            c4.text_input("Assigned location", value=assigned_site, disabled=True)
            access_summary = (
                "HQ User · Access to all three locations and stock operations · "
                "No user-administration access"
            )
        else:
            assigned_site = c4.selectbox("Assigned camp", SITES[1:])
            access_summary = (
                f"Camp User · {assigned_site} only · Can record and view stock "
                "activity for this camp"
            )
        st.info(access_summary)
        generate_column, show_column = st.columns([1, 3])
        if generate_column.button(
            "Generate password", key="generate_temporary_password"
        ):
            st.session_state.new_user_temporary_password = secrets.token_urlsafe(12)
            st.rerun()
        show_password = show_column.checkbox(
            "Show temporary password", key="show_temporary_password"
        )
        temporary_password = st.text_input(
            "Temporary password",
            type="default" if show_password else "password",
            key="new_user_temporary_password",
            help="At least 10 characters. The user must change it at first sign-in.",
        )
        if st.button("Create user", type="primary", key="create_user"):
            if not display_name.strip():
                st.error("The user's full name is required.")
            else:
                try:
                    create_user(
                        new_username, display_name, temporary_password, new_role,
                        assigned_site, user["username"], True,
                    )
                    st.success(
                        f"User {new_username.lower()} was created successfully. "
                        "Give the temporary sign-in details to the user securely."
                    )
                except ValueError as error:
                    st.error(str(error))

    st.subheader("User accounts")
    user_data = list_users()
    st.dataframe(dynamics_table(user_data), width="stretch", hide_index=True)
    manageable = user_data[user_data["id"] != user["id"]]
    if not manageable.empty:
        account_labels = {
            f"{row.display_name} · {row.username} · {row.status}": int(row.id)
            for row in manageable.itertuples()
        }
        selected_account = st.selectbox("Manage account", list(account_labels))
        selected_account_id = account_labels[selected_account]
        selected_user_row = manageable[manageable["id"] == selected_account_id].iloc[0]
        with st.container(border=True):
            managed_full_name = st.text_input(
                "Full name shown on stock activity",
                value=selected_user_row["display_name"],
            )
            if st.button("Update full name", key="update_full_name"):
                try:
                    set_user_display_name(selected_account_id, managed_full_name)
                    st.success("The user's full name has been updated.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))
        b1, b2 = st.columns([1, 5])
        if b1.button("Activate"):
            set_user_active(selected_account_id, True, user["id"])
            st.success("Account activated.")
            st.rerun()
        if b2.button("Deactivate"):
            try:
                set_user_active(selected_account_id, False, user["id"])
                st.success("Account deactivated.")
                st.rerun()
            except ValueError as error:
                st.error(str(error))

    st.divider()
    st.subheader("Item catalogue")
    single_item_tab, bulk_item_tab = st.tabs(["Add one item", "Bulk item import"])
    with single_item_tab:
        with st.container(border=True):
            c1, c2 = st.columns(2)
            sku = c1.text_input("SKU")
            name = c2.text_input("Item name")
            c3, c4 = st.columns(2)
            category = c3.selectbox("Category", ITEM_CATEGORIES)
            unit = c4.text_input("Unit", placeholder="litres, cases, units")
            c5, c6 = st.columns(2)
            unit_cost = c5.number_input("Unit cost (USD)", min_value=0.0, step=0.5)
            minimum = c6.number_input("Minimum level per site", min_value=0, step=1)
            if st.button("Add item", type="primary", key="add_item"):
                if not sku.strip() or not name.strip() or not unit.strip():
                    st.error("SKU, item name and unit are required.")
                else:
                    try:
                        add_item(sku, name, category, unit, unit_cost, minimum)
                        st.success(f"{name} added to all three sites.")
                        st.rerun()
                    except Exception as error:
                        st.error(f"Could not add item: {error}")

    with bulk_item_tab:
        st.caption(
            "Download the template, enter one new item per row, then upload it for "
            "validation. Nothing is saved until you confirm the import."
        )
        st.download_button(
            "Download bulk item template",
            bulk_item_template(),
            "inventory_bulk_item_template.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            key="download_bulk_item_template",
        )
        bulk_file = st.file_uploader(
            "Upload completed template",
            type=["xlsx", "csv"],
            key="bulk_item_file",
            help="Use the supplied Excel template or a CSV containing the same headings.",
        )
        if bulk_file is not None:
            try:
                if bulk_file.name.lower().endswith(".csv"):
                    bulk_frame = pd.read_csv(bulk_file)
                else:
                    bulk_frame = pd.read_excel(bulk_file, sheet_name="Items")
                valid_records, validation_preview = validate_bulk_items(bulk_frame)
                if validation_preview.empty:
                    st.warning("The uploaded file does not contain any item rows.")
                else:
                    error_count = int(
                        (validation_preview["Status"] == "Needs correction").sum()
                    )
                    p1, p2, p3 = st.columns(3)
                    p1.metric("Rows uploaded", len(validation_preview))
                    p2.metric("Ready to import", len(valid_records))
                    p3.metric("Rows needing correction", error_count)
                    st.dataframe(
                        dynamics_table(validation_preview),
                        width="stretch",
                        hide_index=True,
                    )
                    if error_count:
                        st.error(
                            "Correct every highlighted row and upload the file again. "
                            "No items have been saved."
                        )
                    else:
                        confirm_bulk = st.checkbox(
                            f"I confirm that these {len(valid_records)} items and their "
                            "opening quantities are correct.",
                            key="confirm_bulk_items",
                        )
                        if st.button(
                            "Import all items",
                            type="primary",
                            disabled=not confirm_bulk,
                            key="import_bulk_items",
                        ):
                            imported = bulk_add_items(valid_records, user["display_name"])
                            results = validation_preview.copy()
                            results["Status"] = "Imported"
                            results["Validation"] = "Successfully imported"
                            st.success(
                                f"{imported} items were imported successfully. Opening "
                                "quantities were added to the stock activity history."
                            )
                            st.download_button(
                                "Download import results",
                                excel_bytes({"Import results": results}),
                                "bulk_item_import_results.xlsx",
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key="download_bulk_import_results",
                            )
            except ValueError as error:
                st.error(str(error))
            except Exception as error:
                st.error(f"The file could not be imported: {error}")
    st.subheader("System details")
    st.info("Database: SQLite · Interface: Streamlit · Data tools: Pandas and OpenPyXL")
