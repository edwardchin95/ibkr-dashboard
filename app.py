import streamlit as st
import os

st.set_page_config(
    page_title="Portfolio Dashboard",
    page_icon="📊",
    layout="wide"
)

# ============================================================
# Constants
# ============================================================
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "Welcome#123")

if os.path.exists("/mnt/data"):
    DATA_DIR = "/mnt/data"
else:
    DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

SNAPSHOT_DIR = os.path.join(DATA_DIR, "snapshots")
HISTORY_FILE = os.path.join(DATA_DIR, "portfolio_history.csv")
INCOMING_DIR = os.path.join(DATA_DIR, "incoming")

os.makedirs(SNAPSHOT_DIR, exist_ok=True)
os.makedirs(INCOMING_DIR, exist_ok=True)

# ============================================================
# PASSWORD — 全屏覆盖
# ============================================================
def check_password():
    """只在 app.py 调用，全屏密码页面"""
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if st.session_state["authenticated"]:
        return True

    # 全屏覆盖样式
    st.markdown("""
    <style>
    [data-testid="stSidebar"] { display: none; }
    header { display: none; }
    </style>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.markdown("<div style='height:20vh'></div>", unsafe_allow_html=True)
        st.markdown("## 📊 Portfolio Dashboard")
        st.markdown("---")
        password = st.text_input("🔒 请输入密码", type="password")

        if password == "":
            st.stop()

        if password == DASHBOARD_PASSWORD:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("密码错误")
            st.stop()

def require_auth():
    """子页面调用这个 — 只检查状态，不显示密码框"""
    if not st.session_state.get("authenticated", False):
        st.warning("⚠️ 请先登录")
        st.stop()

# ============================================================
# CSS
# ============================================================
def load_css():
    st.markdown("""<style>

    /* 隐藏 sidebar 里的 app 入口 */
    [data-testid="stSidebarNav"] > ul > li:first-child {
        display: none;
    }

    .main {
        background-color: #F5F7FB;
    }

    .card {
        background-color: #111827;
        padding: 24px;
        border-radius: 16px;
        margin-bottom: 24px;
    }

    .big-number {
        font-size: 42px;
        font-weight: bold;
        color: white;
    }

    .green {
        color: #66FF99;
        font-size: 24px;
        font-weight: bold;
    }

    .red {
        color: #FF6666;
        font-size: 24px;
        font-weight: bold;
    }

    .section-title {
        font-size: 28px;
        font-weight: bold;
        color: black;
        margin-top: 24px;
        margin-bottom: 20px;
    }

    .progress-container {
        width: 100%;
        background-color: #1B2435;
        border-radius: 10px;
        height: 16px;
        margin-top: 6px;
        margin-bottom: 20px;
    }

    .progress-bar {
        height: 16px;
        border-radius: 10px;
    }

    .metric-title {
        color: black;
        font-size: 16px;
        font-weight: bold;
    }

    .metric-sub {
        color: #666666;
        font-size: 13px;
    }

    /* ============================================================
       响应式：手机模式
       ============================================================ */
    @media (max-width: 640px) {

        .card {
            padding: 16px !important;
            border-radius: 12px !important;
            margin-bottom: 16px !important;
        }

        .big-number {
            font-size: 28px !important;
        }

        .section-title {
            font-size: 20px !important;
            margin-top: 16px !important;
            margin-bottom: 12px !important;
        }

        .green, .red {
            font-size: 18px !important;
        }

        /* 让所有内联 grid 卡片在手机自动缩小 minmax */
        div[style*="grid-template-columns"] {
            gap: 14px !important;
        }

        /* flex 内的 span 在手机不会撑爆 */
        div[style*="display:flex"] > span {
            word-break: break-word;
        }
    }

    /* ============================================================
       响应式：平板模式
       ============================================================ */
    @media (min-width: 641px) and (max-width: 1024px) {

        .big-number {
            font-size: 34px !important;
        }

        .section-title {
            font-size: 24px !important;
        }
    }

    </style>""", unsafe_allow_html=True)

# ============================================================
# Detect Platform
# ============================================================
def detect_platform(file_bytes):
    if isinstance(file_bytes, str):
        file_bytes = file_bytes.encode("utf-8")
    if b'"AssetClass"' in file_bytes or b"ClientAccountID" in file_bytes:
        return "IBKR"
    return None

# ============================================================
# 主入口 — 登录后跳转 Overview
# ============================================================
# 只有直接跑 app.py 时才执行，被 import 时不执行
if __name__ == "__main__":
    check_password()
    st.switch_page("pages/1_Overview.py")