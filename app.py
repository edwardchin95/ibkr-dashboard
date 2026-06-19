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
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "changeme")

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
    """子页面调用 — 未登录时显示密码框（不再要求跳 app 页）"""
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if st.session_state["authenticated"]:
        return True

    # 未登录 → 显示全屏密码框
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
        password = st.text_input("🔒 请输入密码", type="password", key="auth_pw_input")

        if password == "":
            st.stop()

        if password == DASHBOARD_PASSWORD:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("密码错误")
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

    /* ============================================================
       默认（Light Mode）变量
       ============================================================ */
    :root {
        --text-primary: #111827;
        --text-secondary: #666666;
        --text-muted: #9CA3AF;
        --border-soft: #E5E7EB;
        --border-strong: #333333;
        --page-bg: #F5F7FB;
    }

    /* ============================================================
       Dark Mode 自动覆盖
       ============================================================ */
    @media (prefers-color-scheme: dark) {
        :root {
            --text-primary: #FAFAFA;
            --text-secondary: #C0C0C0;
            --text-muted: #9CA3AF;
            --border-soft: #333333;
            --border-strong: #555555;
            --page-bg: #0E1117;
        }
    }

    /* Streamlit 自己的 dark mode class（手动切换时也生效）*/
    [data-theme="dark"] {
        --text-primary: #FAFAFA;
        --text-secondary: #C0C0C0;
        --text-muted: #9CA3AF;
        --border-soft: #333333;
        --border-strong: #555555;
        --page-bg: #0E1117;
    }

    /* ============================================================
       全局
       ============================================================ */
    .main {
        background-color: var(--page-bg);
    }

    /* Card 一律深色（你 dashboard 视觉风格）*/
    .card {
        background-color: #111827;
        padding: 24px;
        border-radius: 16px;
        margin-bottom: 24px;
        color: #FAFAFA;
    }

    .big-number {
        font-size: 42px;
        font-weight: bold;
        color: #FAFAFA;
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

    /* Section title — 跟随主题 */
    .section-title {
        font-size: 28px;
        font-weight: bold;
        color: var(--text-primary);
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

    /* ============================================================
       响应式
       ============================================================ */
    @media (max-width: 640px) {
        .card { padding: 16px !important; border-radius: 12px !important; margin-bottom: 16px !important; }
        .big-number { font-size: 28px !important; }
        .section-title { font-size: 20px !important; margin-top: 16px !important; margin-bottom: 12px !important; }
        .green, .red { font-size: 18px !important; }
        div[style*="grid-template-columns"] { gap: 14px !important; }
        div[style*="display:flex"] > span { word-break: break-word; }
    }

    @media (min-width: 641px) and (max-width: 1024px) {
        .big-number { font-size: 34px !important; }
        .section-title { font-size: 24px !important; }
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