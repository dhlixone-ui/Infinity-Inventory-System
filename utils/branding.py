import streamlit as st
from pathlib import Path
import base64

ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"


def _image_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .stApp { background:#f4f5f6; }
        .block-container { padding-top:1.25rem; max-width:1540px; }
        [data-testid="stSidebar"] { background:#474542; border-right:1px solid #363532; }
        [data-testid="stSidebarCollapseButton"],
        [data-testid="collapsedControl"] { display:none!important; }
        [data-testid="stSidebar"] * { color:#f7fbf8; }
        [data-testid="stSidebar"] .stRadio label {
          padding:.5rem .65rem; border-left:3px solid transparent;
          border-radius:2px; margin:.1rem 0;
        }
        [data-testid="stSidebar"] .stRadio label:has(input:checked) {
          background:rgba(170,107,57,.28); border-left-color:#aa6b39;
        }
        #MainMenu, footer, header { visibility:hidden; }
        h1,h2,h3 { letter-spacing:-.015em; color:#292826; font-weight:600; }
        [data-testid="stMetric"] {
          background:#fff; border:1px solid #d8d9da; border-top:3px solid #aa6b39;
          border-radius:2px; padding:14px 16px; box-shadow:none;
        }
        [data-testid="stMetricLabel"] { color:#64615e; font-weight:600; }
        [data-testid="stMetricValue"] { color:#292826; font-weight:600; }
        .hero {
          position:relative; background:#fff; color:#292826;
          border:1px solid #d9dadb; border-left:5px solid #aa6b39;
          border-radius:2px; padding:20px 24px; margin-bottom:12px;
          box-shadow:none;
        }
        .hero::before {
          content:"OPERATIONS"; display:block; color:#aa6b39; font-size:.69rem;
          font-weight:800; letter-spacing:.12em; margin-bottom:6px;
        }
        .hero h1 { color:#292826; margin:0; font-size:1.75rem; font-weight:600; }
        .hero p { color:#696663; margin:.3rem 0 0; }
        .inventory-toolbar {
          background:#fff; border:1px solid #d9dadb; border-radius:2px;
          padding:14px 16px 4px; margin:0 0 14px;
        }
        .inventory-toolbar-title {
          color:#474542; font-size:.72rem; font-weight:800;
          letter-spacing:.11em; margin:0 0 8px;
        }
        .section-kicker {
          color:#aa6b39; font-size:.72rem; font-weight:800;
          letter-spacing:.11em; text-transform:uppercase; margin:18px 0 3px;
        }
        .table-caption { color:#6a6865; font-size:.9rem; margin:0 0 10px; }
        .notice-strip {
          background:#fff8f2; border-left:4px solid #aa6b39; color:#5d4634;
          padding:10px 12px; margin:12px 0; border-radius:1px;
        }
        .brand { padding:12px 8px 22px; border-bottom:1px solid #68645f; margin-bottom:15px; }
        .brand-badge {
          display:inline-grid; place-items:center; width:42px; height:42px;
          background:#aa6b39; color:#fff; border-radius:12px; font-weight:900;
          margin-bottom:10px;
        }
        .brand-title { font-weight:800; font-size:1.05rem; }
        .brand-sub { color:#a9bdb1!important; font-size:.76rem; margin-top:2px; }
        .camp-logos {
          display:grid; grid-template-columns:1fr 1fr; gap:12px;
          margin:4px 0 18px;
        }
        .camp-logo-item { text-align:center; min-width:0; }
        .camp-logo-card {
          height:92px; width:100%; background:#fff!important; border-radius:2px;
          display:flex; align-items:center; justify-content:center;
          overflow:hidden; padding:10px;
        }
        .camp-logo-card img {
          width:100%; height:100%; object-fit:contain; display:block;
        }
        .camp-logo-label {
          color:#b7c9be!important; font-size:.73rem; font-weight:700;
          line-height:1.2; margin-top:8px; min-height:1.8em;
        }
        .login-shell {
          max-width:1180px; margin:18px auto 30px;
        }
        .login-brand-strip {
          display:flex; gap:20px; justify-content:center; align-items:center;
          max-width:620px; margin:0 auto 28px;
        }
        .login-brand-card {
          flex:0 0 280px; width:280px; height:280px;
          background:#fff!important;
          border:1px solid #e0e7e2;
          border-radius:18px; padding:18px; display:flex; align-items:center;
          justify-content:center; box-shadow:0 10px 28px rgba(22,57,41,.07);
        }
        .login-brand-card img {
          display:block; width:100%; height:100%; object-fit:contain;
        }
        .login-brand-hide {
          background:#fff!important;
          border-color:#e0e7e2;
        }
        .login-heading { text-align:center; margin:0 auto 22px; }
        .login-heading h1 {
          color:#474542; font-size:2rem!important; margin:0 0 8px;
        }
        .login-heading p { color:#718078; margin:0; }
        @media(max-width:760px) {
          .login-brand-strip { flex-direction:column; }
          .login-brand-card {
            flex-basis:250px; width:250px; height:250px; margin:0 auto;
          }
        }
        .low-card { background:#fff8f2; border:1px solid #e6c8af; border-left:4px solid #aa6b39; border-radius:2px; padding:14px; margin:.5rem 0; }
        .low-card strong { color:#9b4c27; }
        .stButton>button {
          border-radius:1px!important; font-weight:600; border-color:#aa6b39;
          background:#aa6b39; color:#fff;
        }
        .stButton>button[kind="primary"] {
          background:#474542; border-color:#474542; color:#fff;
        }
        button { border-radius:1px!important; }
        [data-testid="stDownloadButton"] button {
          background:#aa6b39!important;
          border-color:#aa6b39!important;
          border-radius:1px!important;
          color:#fff!important;
        }
        [data-testid="stDownloadButton"] button * {
          color:#fff!important;
          -webkit-text-fill-color:#fff!important;
        }
        [data-testid="stSidebar"] [data-testid="stBaseButton-secondary"],
        [data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] *,
        [data-testid="stSidebar"] .stButton button,
        [data-testid="stSidebar"] .stButton button p {
          color:#fff!important;
          -webkit-text-fill-color:#fff!important;
          opacity:1!important;
        }
        [data-testid="stDataFrame"] {
          border:1px solid #cfd1d2; border-radius:0; overflow:hidden;
          background:#fff;
        }
        [data-testid="stDataFrame"] [role="columnheader"] {
          background:#474542!important; color:#fff!important;
          font-weight:700!important;
        }
        [data-testid="stVerticalBlockBorderWrapper"] {
          border-radius:2px!important; border-color:#d3d4d5!important;
          background:#fff;
        }
        [data-baseweb="input"] > div,
        [data-baseweb="select"] > div,
        [data-baseweb="textarea"] {
          border-radius:2px!important;
        }
        [data-baseweb="tab-list"] {
          background:#eceeef; border-bottom:1px solid #c9cbcc; padding:0 6px;
        }
        [data-baseweb="tab"] { border-radius:0!important; }
        hr { border-color:#d5d6d7!important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def sidebar_brand() -> None:
    hide_logo = _image_b64(ASSET_DIR / "the_hide.png")
    changa_logo = _image_b64(ASSET_DIR / "changa_safari_camp.png")
    st.sidebar.markdown(
        f"""
        <div class="brand">
          <div class="brand-title">Infinity Cloud Systems</div>
          <div class="brand-sub">Inventory control centre</div>
        </div>
        <div class="camp-logos">
          <div class="camp-logo-item">
            <div class="camp-logo-card">
              <img src="data:image/png;base64,{hide_logo}" alt="The Hide Safaris logo">
            </div>
            <div class="camp-logo-label">The Hide Safaris</div>
          </div>
          <div class="camp-logo-item">
            <div class="camp-logo-card">
              <img src="data:image/png;base64,{changa_logo}" alt="Changa Safari Camp logo">
            </div>
            <div class="camp-logo-label">Changa Safari Camp</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def login_header() -> None:
    hide_logo = _image_b64(ASSET_DIR / "the_hide.png")
    changa_logo = _image_b64(ASSET_DIR / "changa_safari_camp.png")
    st.markdown(
        f"""
        <div class="login-shell">
          <div class="login-brand-strip">
            <div class="login-brand-card login-brand-hide">
              <img src="data:image/png;base64,{hide_logo}" alt="The Hide Safaris">
            </div>
            <div class="login-brand-card">
              <img src="data:image/png;base64,{changa_logo}" alt="Changa Safari Camp">
            </div>
          </div>
          <div class="login-heading">
            <h1>Inventory Control Centre</h1>
            <p>Secure access for HQ Steppes Road, The Hide Safaris and Changa Safari Camp.</p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def hero(title: str, subtitle: str) -> None:
    st.markdown(f'<div class="hero"><h1>{title}</h1><p>{subtitle}</p></div>', unsafe_allow_html=True)
