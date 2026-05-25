import streamlit as st
import pandas as pd
import requests
import ast
import re
import sqlite3
import os
import html as html_lib
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

# -------------------------------------------------------------
# 🌟 Page Configuration & Theme Initialization
# -------------------------------------------------------------
st.set_page_config(
    page_title="B-Bot Financial Intel",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Injected extreme visual styling (Obsidian Glassmorphism Dark-Mode SaaS)
st.markdown("""
    <style>
        /* Google Fonts integration */
        @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=Orbitron:wght@400;500;700;900&family=JetBrains+Mono:wght@400;700&display=swap');

        /* General dark obsidian body setup */
        .stApp {
            background: radial-gradient(circle at 50% 50%, #0e091a 0%, #040108 100%) !important;
            color: #f1f1f1 !important;
            font-family: 'Outfit', sans-serif !important;
        }

        /* Sidebar Glassmorphic Overrides */
        section[data-testid="stSidebar"] {
            background-color: rgba(6, 3, 13, 0.95) !important;
            border-right: 1px solid rgba(124, 58, 237, 0.15) !important;
            backdrop-filter: blur(25px) !important;
            box-shadow: 10px 0 30px rgba(0,0,0,0.8) !important;
        }

        /* Custom headers in Sidebar */
        .sidebar-brand {
            text-align: center; 
            padding: 20px 0;
            background: linear-gradient(135deg, rgba(124,58,237,0.05) 0%, rgba(239,71,111,0.05) 100%);
            border-radius: 16px;
            border: 1px solid rgba(255,255,255,0.04);
            margin-bottom: 25px;
        }

        /* Glowing Custom Header */
        .header-title-container {
            display: flex;
            align-items: center;
            gap: 15px;
            margin-bottom: 30px;
            padding: 10px 0;
        }
        .pulse-light {
            width: 14px;
            height: 14px;
            background: #06b6d4;
            border-radius: 50%;
            box-shadow: 0 0 15px #06b6d4, 0 0 30px #06b6d4;
            animation: pulse-glow 1.8s infinite;
        }
        @keyframes pulse-glow {
            0% { transform: scale(1); opacity: 1; box-shadow: 0 0 15px #06b6d4; }
            50% { transform: scale(1.25); opacity: 0.5; box-shadow: 0 0 25px #06b6d4, 0 0 40px #06b6d4; }
            100% { transform: scale(1); opacity: 1; box-shadow: 0 0 15px #06b6d4; }
        }

        /* Custom Sidebar Suggested queries */
        .sug-button button {
            background: rgba(255, 255, 255, 0.02) !important;
            border: 1px solid rgba(255, 255, 255, 0.05) !important;
            color: #d1d1d1 !important;
            border-radius: 12px !important;
            width: 100%;
            text-align: left !important;
            transition: all 0.3s ease !important;
            padding: 10px 14px !important;
            font-size: 0.86rem !important;
            margin-bottom: 8px;
            font-family: 'Outfit', sans-serif !important;
        }
        .sug-button button:hover {
            background: linear-gradient(135deg, rgba(124, 58, 237, 0.15) 0%, rgba(239, 71, 111, 0.05) 100%) !important;
            border-color: rgba(124, 58, 237, 0.35) !important;
            color: #ffffff !important;
            box-shadow: 0 4px 15px rgba(124, 58, 237, 0.15) !important;
            transform: translateX(3px);
        }

        /* Active Apply settings button */
        .apply-btn button {
            background: linear-gradient(135deg, #7c3aed 0%, #ef476f 100%) !important;
            color: white !important;
            border: none !important;
            font-weight: 700 !important;
            border-radius: 12px !important;
            box-shadow: 0 8px 25px rgba(124, 58, 237, 0.35) !important;
            width: 100%;
            padding: 10px 0 !important;
            text-transform: uppercase;
            font-family: 'Orbitron', sans-serif !important;
            font-size: 0.82rem !important;
            letter-spacing: 1px;
            transition: all 0.3s ease !important;
        }
        .apply-btn button:hover {
            box-shadow: 0 12px 35px rgba(239, 71, 111, 0.5) !important;
            transform: scale(1.02);
        }

        /* Tabs styling */
        div[data-testid="stTabBar"] {
            background: rgba(255,255,255,0.01) !important;
            border: 1px solid rgba(255,255,255,0.04) !important;
            border-radius: 14px;
            padding: 4px !important;
            margin-bottom: 25px;
        }
        button[data-baseweb="tab"] {
            color: rgba(255,255,255,0.4) !important;
            font-family: 'Orbitron', sans-serif !important;
            font-weight: 600 !important;
            font-size: 0.9rem !important;
            padding: 10px 20px !important;
            transition: all 0.3s ease !important;
            border-radius: 10px !important;
            background-color: transparent !important;
            border: none !important;
        }
        button[data-baseweb="tab"][aria-selected="true"] {
            background: linear-gradient(135deg, rgba(124,58,237,0.15) 0%, rgba(239,71,111,0.05) 100%) !important;
            color: #ffffff !important;
            border: 1px solid rgba(124, 58, 237, 0.3) !important;
            box-shadow: 0 4px 15px rgba(124, 58, 237, 0.1) !important;
        }

        /* Clean paginated DataFrame wrappers */
        .stDataFrame {
            background-color: rgba(6,3,13,0.4) !important;
            border-radius: 20px;
            border: 1px solid rgba(255, 255, 255, 0.05);
            overflow: hidden;
            box-shadow: 0 15px 40px rgba(0,0,0,0.5);
            padding: 10px;
        }

        /* Custom scrollbar */
        ::-webkit-scrollbar {
            width: 6px;
            height: 6px;
        }
        ::-webkit-scrollbar-track {
            background: transparent;
        }
        ::-webkit-scrollbar-thumb {
            background: rgba(124, 58, 237, 0.15);
            border-radius: 10px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: rgba(124, 58, 237, 0.3);
        }

        /* Transparent Plotly Containers */
        div[data-testid="stPlotlyChart"] {
            background-color: rgba(255,255,255,0.01) !important;
            border: 1px solid rgba(255, 255, 255, 0.05) !important;
            border-radius: 20px !important;
            padding: 15px !important;
            backdrop-filter: blur(15px) !important;
            box-shadow: 0 10px 30px rgba(0,0,0,0.4) !important;
        }

        /* Styled streamlit default expanders */
        div[data-testid="stExpander"] {
            background-color: rgba(255, 255, 255, 0.01) !important;
            border: 1px solid rgba(255, 255, 255, 0.05) !important;
            border-radius: 16px !important;
            box-shadow: 0 10px 25px rgba(0,0,0,0.3) !important;
        }
        
        /* Chat Input Glow outline */
        .stChatInputContainer textarea {
            border: 1px solid rgba(124, 58, 237, 0.2) !important;
            background-color: rgba(6, 3, 13, 0.6) !important;
            border-radius: 14px !important;
            color: #ffffff !important;
            font-family: 'Outfit', sans-serif !important;
            box-shadow: inset 0 2px 8px rgba(0,0,0,0.8) !important;
            transition: all 0.3s ease !important;
        }
        .stChatInputContainer textarea:focus {
            border-color: rgba(124, 58, 237, 0.6) !important;
            box-shadow: 0 0 15px rgba(124, 58, 237, 0.25) !important;
        }

        /* Slide up animation */
        @keyframes slide-up {
            from { opacity: 0; transform: translateY(15px); }
            to { opacity: 1; transform: translateY(0); }
        }
    </style>
""", unsafe_allow_html=True)

# -------------------------------------------------------------
# 🌐 API Configuration Constants
# -------------------------------------------------------------
API_URL_PROCESS = "http://127.0.0.1:8000/process"
API_URL_PROCESS_DEBUG = "http://127.0.0.1:8000/process-debug"
API_URL_SETTINGS = "http://127.0.0.1:8000/update-settings"

# Initialize Session State
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "latest_debug_data" not in st.session_state:
    st.session_state.latest_debug_data = None
if "input_query" not in st.session_state:
    st.session_state.input_query = ""
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "logged_in_user_id" not in st.session_state:
    st.session_state.logged_in_user_id = None

# -------------------------------------------------------------
# 🔒 User Authentication Gateway Gate
# -------------------------------------------------------------
if not st.session_state.logged_in:
    _, col, _ = st.columns([1, 1.8, 1])
    with col:
        st.markdown("""
            <div style="text-align: center; margin-top: 50px; margin-bottom: 30px;">
                <h2 style="color:#ffffff; font-family:'Orbitron',sans-serif; font-weight:900; margin:0; font-size:2rem; letter-spacing:1px; background:linear-gradient(to right, #7c3aed, #ef476f); -webkit-background-clip:text; -webkit-text-fill-color:transparent;">B-BOT ENTERPRISE LOGIN</h2>
                <span style="color:rgba(255,255,255,0.4); font-family:'Outfit',sans-serif; font-size:0.85rem; text-transform:uppercase; font-weight:600; letter-spacing:0.5px;">SaaS Core Operations Gateway</span>
            </div>
        """, unsafe_allow_html=True)
        
        with st.container():
            st.markdown("""
                <div style="
                    background: linear-gradient(135deg, rgba(255,255,255,0.02) 0%, rgba(255,255,255,0.005) 100%);
                    backdrop-filter: blur(25px);
                    border: 1px solid rgba(124, 58, 237, 0.2);
                    border-radius: 20px;
                    padding: 30px;
                    box-shadow: 0 15px 40px rgba(0,0,0,0.6);
                    margin-bottom: 25px;
                ">
            """, unsafe_allow_html=True)
            
            user_id = st.text_input("User ID", placeholder="Enter your User ID (e.g. Cust_0410)")
            password = st.text_input("Password", type="password", placeholder="••••••••")
            
            st.markdown("<div class='apply-btn' style='margin-top: 20px;'>", unsafe_allow_html=True)
            login_clicked = st.button("Authenticate Client", key="login_btn")
            st.markdown("</div>", unsafe_allow_html=True)
            
            if login_clicked:
                if not user_id.strip() or not password.strip():
                    st.error("Please enter both User ID and Password.")
                else:
                    with st.spinner("Authorizing credentials..."):
                        try:
                            res = requests.post("http://localhost:8000/login", json={
                                "user_id": user_id.strip(),
                                "password": password.strip()
                            })
                            if res.status_code == 200:
                                res_data = res.json()
                                if res_data.get("status") == "success":
                                    st.session_state.logged_in = True
                                    st.session_state.logged_in_user_id = res_data.get("user_id")
                                    st.success("Access Granted! Synchronizing gateway...")
                                    st.rerun()
                                else:
                                    st.error(res_data.get("message", "Invalid credentials."))
                            else:
                                st.error(f"Authentication Gateway Offline (Status Code {res.status_code})")
                        except Exception as e:
                            st.error(f"Cannot connect to core API gateway: {e}")
                            
            st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

# -------------------------------------------------------------
# 🎛️ Sidebar Configuration
# -------------------------------------------------------------
st.sidebar.markdown(
    """
    <div class="sidebar-brand">
        <h2 style="color:#ffffff; font-family:'Orbitron',sans-serif; font-weight:900; margin:0; font-size:1.35rem; letter-spacing:1px; background:linear-gradient(to right, #7c3aed, #ef476f); -webkit-background-clip:text; -webkit-text-fill-color:transparent;">B-BOT INTEL</h2>
        <span style="color:rgba(255,255,255,0.4); font-family:'Outfit',sans-serif; font-size:0.75rem; text-transform:uppercase; font-weight:600; letter-spacing:0.5px;">SaaS Core Operations</span>
    </div>
    """, 
    unsafe_allow_html=True
)

st.sidebar.markdown("### 🎯 Context Target")
allowed_users = ["Cust_0410", "Cust_0411", "Cust_0100"]
default_idx = 0
logged_in_user = st.session_state.get("logged_in_user_id")
if logged_in_user in allowed_users:
    default_idx = allowed_users.index(logged_in_user)
else:
    if logged_in_user:
        allowed_users.append(logged_in_user)
        default_idx = len(allowed_users) - 1

customer_id = st.sidebar.selectbox("Customer ID", allowed_users, index=default_idx)
session_id = st.sidebar.text_input("Session Token ID", value="sess_streamlit_01")

st.sidebar.markdown("---")

st.sidebar.markdown("### ⚙️ Gateway Hyperparameters")
provider = st.sidebar.selectbox("LLM Provider", ["openai", "gemini", "groq", "anthropic"], index=0)
temperature = st.sidebar.slider("Temperature Range", min_value=0.0, max_value=1.0, value=0.2, step=0.1)
max_tokens = st.sidebar.slider("Max Output Tokens", min_value=250, max_value=2500, value=1500, step=250)

# Apply settings dynamically using our custom backend endpoint
st.sidebar.markdown("<div class='apply-btn'>", unsafe_allow_html=True)
if st.sidebar.button("Sync Configuration", key="apply_settings_btn"):
    with st.spinner("Broadcasting settings..."):
        try:
            res = requests.post(API_URL_SETTINGS, json={
                "llm_provider": provider,
                "temperature": temperature,
                "max_tokens": max_tokens
            })
            if res.status_code == 200 and res.json().get("status") == "success":
                st.sidebar.success(f"Configured {provider.upper()} successfully!")
            else:
                st.sidebar.error("Failed to update: " + res.text)
        except Exception as e:
            st.sidebar.error(f"Cannot connect to backend: {e}")
st.sidebar.markdown("</div>", unsafe_allow_html=True)

st.sidebar.markdown("---")

# 💡 Suggested Queries List
st.sidebar.markdown("### 💡 Dynamic Query Prompts")
suggested_queries = [
    "Show my spending in groceries",
    "What was my highest transaction this year?",
    "List my transactions in Australia",
    "How much did I spend on Eating Out?",
    "Show my premium debit card transactions"
]

for idx, q in enumerate(suggested_queries):
    st.sidebar.markdown(f"<div class='sug-button'>", unsafe_allow_html=True)
    if st.sidebar.button(q, key=f"sug_btn_{idx}"):
        st.session_state.input_query = q
    st.sidebar.markdown("</div>", unsafe_allow_html=True)

# 🔓 Log Out Option
st.sidebar.markdown("---")
st.sidebar.markdown("<div class='sug-button'>", unsafe_allow_html=True)
if st.sidebar.button("🔓 Terminate Session (Log Out)", key="logout_btn"):
    st.session_state.logged_in = False
    st.session_state.logged_in_user_id = None
    st.session_state.chat_history = []
    st.session_state.latest_debug_data = None
    st.rerun()
st.sidebar.markdown("</div>", unsafe_allow_html=True)

# -------------------------------------------------------------
# 🏢 Main Header
# -------------------------------------------------------------
st.markdown("""
    <div class="header-title-container">
        <div class="pulse-light"></div>
        <h1 style="color:#ffffff; font-family:'Orbitron',sans-serif; font-weight:900; margin:0; font-size:2.2rem; letter-spacing:-0.5px; background:linear-gradient(to right, #ffffff 30%, rgba(255,255,255,0.6) 100%); -webkit-background-clip:text; -webkit-text-fill-color:transparent;">B-BOT TRANSACTION INTELLIGENCE</h1>
    </div>
""", unsafe_allow_html=True)

# -------------------------------------------------------------
# 🛠️ Helper Custom HTML Render Components
# -------------------------------------------------------------
def render_custom_metric(title, value, icon, color):
    """Renders highly premium SaaS metrics cards."""
    card_html = f"""
    <div style="
        background: linear-gradient(135deg, rgba(255,255,255,0.02) 0%, rgba(255,255,255,0.005) 100%);
        backdrop-filter: blur(20px);
        border: 1px solid rgba(255, 255, 255, 0.05);
        border-bottom: 3px solid {color};
        border-radius: 20px;
        padding: 22px 24px;
        box-shadow: 0 15px 35px rgba(0,0,0,0.5);
        transition: all 0.3s ease;
        margin-bottom: 20px;
    ">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
            <span style="color: rgba(255,255,255,0.4); font-size: 0.8rem; font-weight: 700; text-transform: uppercase; font-family: 'Outfit', sans-serif; letter-spacing:0.8px;">{title}</span>
            <span style="color: {color}; font-size: 1.6rem; text-shadow: 0 0 10px {color}33;">{icon}</span>
        </div>
        <div style="color: #ffffff; font-size: 2.1rem; font-weight: 700; font-family: 'Orbitron', sans-serif; letter-spacing: -0.5px;">{value}</div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)

def render_glowing_chat_bubble(role, content):
    """Renders elite chat cards with left neon glowing indicator strips."""
    if role == "user":
        bubble_html = f"""
        <div style="display: flex; justify-content: flex-end; margin-bottom: 25px; animation: slide-up 0.35s ease forwards;">
            <div style="
                max-width: 80%;
                background: linear-gradient(135deg, rgba(239, 71, 111, 0.08) 0%, rgba(239, 71, 111, 0.02) 100%);
                border: 1px solid rgba(239, 71, 111, 0.2);
                border-left: 4px solid #ef476f;
                border-radius: 20px 20px 4px 20px;
                padding: 16px 22px;
                box-shadow: 0 12px 30px rgba(239, 71, 111, 0.05);
                color: #ffffff;
                font-family: 'Outfit', sans-serif;
            ">
                <span style="color: #ef476f; font-weight: 700; font-size: 0.72rem; text-transform: uppercase; display: block; margin-bottom: 6px; font-family: 'Orbitron', sans-serif; letter-spacing:1px; text-shadow:0 0 10px rgba(239,71,111,0.2);">👤 CUSTOMER CLIENT</span>
                <span style="font-size:1.02rem; line-height:1.6; font-weight:400; color:rgba(255,255,255,0.95);">{content}</span>
            </div>
        </div>
        """
    else:
        bubble_html = f"""
        <div style="display: flex; justify-content: flex-start; margin-bottom: 25px; animation: slide-up 0.35s ease forwards;">
            <div style="
                max-width: 85%;
                background: linear-gradient(135deg, rgba(6, 182, 212, 0.08) 0%, rgba(124, 58, 237, 0.02) 100%);
                border: 1px solid rgba(6, 182, 212, 0.2);
                border-left: 4px solid #06b6d4;
                border-radius: 20px 20px 20px 4px;
                padding: 16px 22px;
                box-shadow: 0 12px 30px rgba(6, 182, 212, 0.05);
                color: #ffffff;
                font-family: 'Outfit', sans-serif;
            ">
                <span style="color: #06b6d4; font-weight: 700; font-size: 0.72rem; text-transform: uppercase; display: block; margin-bottom: 6px; font-family: 'Orbitron', sans-serif; letter-spacing:1px; text-shadow:0 0 10px rgba(6,182,212,0.2);">🤖 B-BOT CORE ASSISTANT</span>
                <span style="font-size:1.02rem; line-height:1.6; font-weight:400; color:rgba(255,255,255,0.95);">{content}</span>
            </div>
        </div>
        """
    st.markdown(bubble_html, unsafe_allow_html=True)

def render_visual_dag_timeline(latest_debug):
    """Constructs a visually striking vertical DAG flow representing agent steps."""
    context_res = latest_debug.get("context_builder_response") or {}
    context_val = html_lib.escape(str(context_res.get("user_query", "N/A")))
    
    supervisor_res = latest_debug.get("suprivisor_response") or {}
    sub_queries = supervisor_res.get("classified_sub_queries") or []
    
    route_text = "N/A"
    route_label = "BankSQL"
    if sub_queries:
        routes = [sq.get("class_label") for sq in sub_queries]
        route_text = ", ".join(routes)
        if len(sub_queries) > 0:
            route_label = html_lib.escape(str(sub_queries[0].get("class_label", "BankSQL")))
            
    sql_res = latest_debug.get("text2sql_generator_response") or {}
    sql_query = sql_res.get("sql_query")
    
    sql_corrected = latest_debug.get("sql_query_correction")
    
    fallback_res = latest_debug.get("fallback_response") or {}
    has_fallback = fallback_res.get("fallback_flag", False)
    fallback_msg = html_lib.escape(str(fallback_res.get("fallback_message") or ""))

    dag_html = f"""
    <div style="
        background: linear-gradient(135deg, rgba(6,3,13,0.5) 0%, rgba(6,3,13,0.2) 100%);
        border: 1px solid rgba(255, 255, 255, 0.04);
        border-radius: 20px;
        padding: 25px 30px;
        margin-top: 15px;
        margin-bottom: 25px;
        box-shadow: inset 0 2px 10px rgba(0,0,0,0.8);
        font-family: 'Outfit', sans-serif;
    ">
        <h5 style="color: #ffffff; margin-top:0; margin-bottom: 25px; font-family:'Orbitron',sans-serif; font-size:1rem; font-weight:700; letter-spacing:0.8px; text-transform:uppercase; background:linear-gradient(to right, #06b6d4, #7c3aed); -webkit-background-clip:text; -webkit-text-fill-color:transparent;">🔍 LangGraph Execution Timeline DAG</h5>
        
        <div style="display: flex; flex-direction: column; gap: 20px; position: relative; padding-left: 28px; border-left: 2px solid rgba(124, 58, 237, 0.15);">
            
            <!-- Step 1: Context Builder -->
            <div style="position: relative; animation: slide-up 0.3s ease forwards;">
                <div style="position: absolute; left: -34px; top: 4px; width: 10px; height: 10px; border-radius: 50%; background: #10b981; box-shadow: 0 0 10px #10b981, 0 0 20px #10b981;"></div>
                <span style="color: rgba(255,255,255,0.4); font-size: 0.72rem; text-transform: uppercase; font-family: 'Orbitron', sans-serif; font-weight:600; letter-spacing:0.8px;">01 | Context Builder Agent</span>
                <div style="color: #ffffff; font-weight: 500; font-size: 0.95rem; margin-top:4px;">Resolved conversational history to query: <code style="background: rgba(6,182,212,0.1); border: 1px solid rgba(6,182,212,0.2); padding: 3px 8px; border-radius: 6px; color: #06b6d4; font-family:'JetBrains Mono',monospace; font-size:0.85rem;">"{context_val}"</code></div>
            </div>
            
            <!-- Step 2: Supervisor Router -->
            <div style="position: relative; animation: slide-up 0.3s ease forwards; animation-delay: 0.1s;">
                <div style="position: absolute; left: -34px; top: 4px; width: 10px; height: 10px; border-radius: 50%; background: #10b981; box-shadow: 0 0 10px #10b981, 0 0 20px #10b981;"></div>
                <span style="color: rgba(255,255,255,0.4); font-size: 0.72rem; text-transform: uppercase; font-family: 'Orbitron', sans-serif; font-weight:600; letter-spacing:0.8px;">02 | Supervisor Routing Classifier</span>
                <div style="color: #ffffff; font-weight: 500; font-size: 0.95rem; margin-top:4px; display:flex; align-items:center; gap:8px;">
                    Dispatched workflow routing to sub-graph: 
                    <span style="background: linear-gradient(135deg, #7c3aed 0%, #4f46e5 100%); border:1px solid rgba(124,58,237,0.3); padding: 3px 10px; border-radius: 20px; font-size: 0.76rem; font-weight:700; font-family:'Orbitron',sans-serif; letter-spacing:0.5px; box-shadow:0 3px 10px rgba(124,58,237,0.2);">{route_label}</span>
                </div>
            </div>
    """
    
    if sql_query:
        dag_html += f"""
            <!-- Step 3: Text2SQL Agent -->
            <div style="position: relative; animation: slide-up 0.3s ease forwards; animation-delay: 0.2s;">
                <div style="position: absolute; left: -34px; top: 4px; width: 10px; height: 10px; border-radius: 50%; background: #10b981; box-shadow: 0 0 10px #10b981, 0 0 20px #10b981;"></div>
                <span style="color: rgba(255,255,255,0.4); font-size: 0.72rem; text-transform: uppercase; font-family: 'Orbitron', sans-serif; font-weight:600; letter-spacing:0.8px;">03 | Text2SQL Generator Agent</span>
                <div style="color: #ffffff; font-weight: 500; font-size: 0.95rem; margin-top:4px; margin-bottom: 8px;">Synthesized relational database SQLite instructions:</div>
            </div>
        """
        
    if sql_corrected:
        dag_html += f"""
            <!-- Step 4: SQL Correction Node -->
            <div style="position: relative; animation: slide-up 0.3s ease forwards; animation-delay: 0.25s;">
                <div style="position: absolute; left: -34px; top: 4px; width: 10px; height: 10px; border-radius: 50%; background: #ef476f; box-shadow: 0 0 10px #ef476f, 0 0 20px #ef476f;"></div>
                <span style="color: rgba(255,255,255,0.4); font-size: 0.72rem; text-transform: uppercase; font-family: 'Orbitron', sans-serif; font-weight:600; letter-spacing:0.8px;">03b | Database Verification Correction Node</span>
                <div style="color: #ef476f; font-weight: 600; font-size: 0.95rem; margin-top:4px;">SQLite parser error detected! Dynamically corrected query structure.</div>
            </div>
        """
        
    if has_fallback:
        dag_html += f"""
            <!-- Step 5: Fallback System -->
            <div style="position: relative; animation: slide-up 0.3s ease forwards; animation-delay: 0.3s;">
                <div style="position: absolute; left: -34px; top: 4px; width: 10px; height: 10px; border-radius: 50%; background: #ef476f; box-shadow: 0 0 10px #ef476f, 0 0 20px #ef476f;"></div>
                <span style="color: rgba(255,255,255,0.4); font-size: 0.72rem; text-transform: uppercase; font-family: 'Orbitron', sans-serif; font-weight:600; letter-spacing:0.8px;">04 | Core Fallback Node</span>
                <div style="color: #ffffff; font-weight: 500; font-size: 0.95rem; margin-top:4px;">Rerouted request to fallback handler. Message output: <span style="color:rgba(255,255,255,0.6); font-style:italic;">"{fallback_msg}"</span></div>
            </div>
        """
    else:
        dag_html += f"""
            <!-- Step 5: SQL Execution & Response Node -->
            <div style="position: relative; animation: slide-up 0.3s ease forwards; animation-delay: 0.3s;">
                <div style="position: absolute; left: -34px; top: 4px; width: 10px; height: 10px; border-radius: 50%; background: #10b981; box-shadow: 0 0 10px #10b981, 0 0 20px #10b981;"></div>
                <span style="color: rgba(255,255,255,0.4); font-size: 0.72rem; text-transform: uppercase; font-family: 'Orbitron', sans-serif; font-weight:600; letter-spacing:0.8px;">04 | Relational DB Query Execution Node</span>
                <div style="color: #ffffff; font-weight: 500; font-size: 0.95rem; margin-top:4px;">Successfully queried local engine; returned records passed to Conversational Synthesizer.</div>
            </div>
        """
        
    dag_html += """
        </div>
    </div>
    """
    
    st.html(dag_html)
    if sql_query and not has_fallback:
        st.code(sql_query, language="sql")

# -------------------------------------------------------------
# 🏢 Main Interface Tabs (Modern SaaS view tabs)
# -------------------------------------------------------------
tab_chat, tab_analytics = st.tabs(["💬 Dynamic Chat Assistant", "📈 Financial Intelligence Center"])

# Helper function to parse chat history for API debug input
def build_api_chat_history():
    messages = []
    for msg in st.session_state.chat_history:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})
    return messages

def _extract_sql_filter(sql_query, column_name):
    """Pull a simple equality or LIKE value from generated SQL for dashboard drilldowns."""
    if not sql_query:
        return None
    patterns = [
        rf"{column_name}\s*=\s*'([^']+)'",
        rf"{column_name}\s+LIKE\s+'%?([^'%]+)%?'",
        rf"LOWER\({column_name}\)\s+LIKE\s+LOWER\('%([^']+)%'\)"
    ]
    for pattern in patterns:
        match = re.search(pattern, sql_query, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None

def load_visual_transaction_rows(latest_debug, selected_customer_id):
    """
    Agent answers often return one aggregate row, such as total_spent.
    For charts, fetch matching transaction rows locally using safe parameterized filters.
    """
    if not latest_debug:
        return pd.DataFrame()

    sql_query = ((latest_debug.get("text2sql_generator_response") or {}).get("sql_query") or "")
    query = """
        SELECT transaction_date, amount_usd, merchant_brand_name,
               merchant_system_category1_name, merchant_system_category2_name,
               country, channel, card_type
        FROM transactions
        WHERE customer_id = ?
    """
    params = [selected_customer_id]

    for column in [
        "merchant_system_category1_name",
        "merchant_system_category2_name",
        "merchant_brand_name",
        "country",
        "channel",
        "card_type"
    ]:
        value = _extract_sql_filter(sql_query, column)
        if value:
            query += f" AND LOWER({column}) LIKE LOWER(?)"
            params.append(f"%{value}%")

    year_num_match = re.search(r"\byear\s*=\s*'?(\d{4})'?", sql_query, flags=re.IGNORECASE)
    if year_num_match:
        year_value = int(year_num_match.group(1))
        query += " AND year = ?"
        params.append(year_value)
    else:
        strftime_match = re.search(r"\byear\s*=\s*strftime\('%Y',\s*'now'\)(?:\s*([-+])\s*(\d+))?", sql_query, flags=re.IGNORECASE)
        if strftime_match:
            current_year = datetime.now().year
            offset_sign = strftime_match.group(1)
            offset_val = strftime_match.group(2)
            if offset_sign and offset_val:
                if offset_sign == "-":
                    year_value = current_year - int(offset_val)
                else:
                    year_value = current_year + int(offset_val)
            else:
                year_value = current_year
            query += " AND year = ?"
            params.append(year_value)

    query += " ORDER BY transaction_date DESC LIMIT 250"
    try:
        db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "transactions.db")
        with sqlite3.connect(db_path) as conn:
            return pd.read_sql_query(query, conn, params=params)
    except Exception:
        return pd.DataFrame()

# Helper to execute user request
def submit_query(query_text):
    if not query_text.strip():
        return

    # Append user message
    st.session_state.chat_history.append({"role": "user", "content": query_text})
    
    # 1. Setup API Requests to /process
    payload_process = {
        "customer_id": customer_id,
        "session_id": session_id,
        "user_query": query_text
    }
    
    with st.spinner("B-Bot processing query through LangGraph agents..."):
        try:
            # Perform a real HTTP POST request to the FastAPI backend /process endpoint
            res_process = requests.post(API_URL_PROCESS, json=payload_process)
            
            if res_process.status_code == 200:
                # Render the text response received from /process inside the Streamlit chat layout
                try:
                    assistant_answer = res_process.json()
                except Exception:
                    assistant_answer = res_process.text
                
                # Check for backend error dict
                if isinstance(assistant_answer, dict) and "error" in assistant_answer:
                    st.error(f"Error from banking core server: {assistant_answer.get('details')}")
                    return

                # 2. Get the actual database results to update visual state (metrics, charts, dataframes)
                # Parse the output of /process-debug to get the actual database results
                payload_debug = {
                    "user_query": query_text,
                    "customer_id": customer_id,
                    "session_id": session_id,
                    "chat_history": build_api_chat_history()[:-1]
                }
                
                res_debug = requests.post(API_URL_PROCESS_DEBUG, json=payload_debug)
                
                raw_state = None
                if res_debug.status_code == 200:
                    raw_state = res_debug.json()
                    st.session_state.latest_debug_data = raw_state
                
                # Append assistant reply containing the chat text response and the debug database results
                st.session_state.chat_history.append({
                    "role": "assistant", 
                    "content": assistant_answer,
                    "debug": raw_state
                })
            else:
                st.error(f"Error from banking core server (Code {res_process.status_code}): {res_process.text}")
        except Exception as e:
            st.error(f"Failed to submit query to API backend. Please ensure the FastAPI server is running on port 8000. Error: {e}")

# -------------------------------------------------------------
# 💬 TAB 1: Chat Assistant & DAG Traces
# -------------------------------------------------------------
with tab_chat:
    st.markdown("<div style='margin-bottom:20px;'></div>", unsafe_allow_html=True)
    
    # Empty Welcome layout
    if not st.session_state.chat_history:
        st.markdown(
            """
            <div style="
                background: linear-gradient(135deg, rgba(124, 58, 237, 0.05) 0%, rgba(239, 71, 111, 0.01) 100%);
                border: 1px solid rgba(255,255,255,0.03);
                border-radius: 20px;
                padding: 40px;
                text-align: center;
                box-shadow: 0 15px 40px rgba(0,0,0,0.4);
                margin-bottom: 30px;
                animation: slide-up 0.4s ease forwards;
            ">
                <span style="font-size:3rem; display:block; margin-bottom:15px; text-shadow:0 0 15px rgba(6,182,212,0.3);">💎</span>
                <h3 style="color:#ffffff; font-family:'Orbitron',sans-serif; font-weight:700; margin:0 0 10px 0; font-size:1.45rem; letter-spacing:0.5px;">Welcome to B-Bot Core Conversational Hub</h3>
                <p style="color:rgba(255,255,255,0.5); font-family:'Outfit',sans-serif; max-width:550px; margin:0 auto; font-size:0.95rem; line-height:1.6;">
                    Ask natural language questions about your transactions, spending habits, merchant lists, or specific timeframes. Select from suggestions in the sidebar or type a query below.
                </p>
            </div>
            """, 
            unsafe_allow_html=True
        )

    # Render Glowing chat thread
    for msg in st.session_state.chat_history:
        render_glowing_chat_bubble(msg["role"], msg["content"])
        
        # Check for system truncation notifications
        if msg["role"] == "assistant" and "debug" in msg:
            debug_state = msg["debug"]
            db_results = debug_state.get("database_query_results") or []
            for row in db_results:
                if isinstance(row, str) and row.startswith("[System Notification:"):
                    st.markdown(
                        f"""
                        <div style="
                            background: rgba(239, 71, 111, 0.05); 
                            border: 1px solid rgba(239, 71, 111, 0.2); 
                            border-radius: 12px; 
                            padding: 10px 16px; 
                            color: #ef476f; 
                            font-size: 0.82rem; 
                            margin: 10px 0 20px 0; 
                            font-family: 'Outfit', sans-serif;
                        ">
                            ⚠️ Note: {row.replace('[','').replace(']','').replace('System Notification:','').strip()}
                        </div>
                        """, 
                        unsafe_allow_html=True
                    )

    # Render DAG timeline under the chat interface for the LATEST chatbot run
    if st.session_state.chat_history:
        last_msg = st.session_state.chat_history[-1]
        if last_msg["role"] == "assistant" and "debug" in last_msg:
            st.markdown("<div style='margin-top: 30px;'></div>", unsafe_allow_html=True)
            with st.expander("🔍 View LangGraph Execution Trace Steps & Pricing Telemetries"):
                render_visual_dag_timeline(last_msg["debug"])
                
                # Render Pricing telemetries table
                metrics_dict = last_msg["debug"].get("agent_metrics") or {}
                if metrics_dict:
                    st.markdown("<h6 style='color:#ffffff; font-family:\"Orbitron\",sans-serif; text-transform:uppercase; margin-bottom:12px; font-size:0.85rem; letter-spacing:0.5px;'>Model Billing Metadata Grid</h6>", unsafe_allow_html=True)
                    metrics_rows = []
                    total_tokens = 0
                    total_cost = 0.0
                    total_latency = 0.0
                    
                    for node, details in metrics_dict.items():
                        metrics_rows.append({
                            "Agent Node": node,
                            "LLM Model": details.get("model_used"),
                            "Input Tokens": f"{details.get('input_tokens'):,}",
                            "Output Tokens": f"{details.get('output_tokens'):,}",
                            "Cached Tokens": f"{details.get('cached_tokens'):,}",
                            "Latency": f"{details.get('latency', 0.0):.3f} s",
                            "Price (USD)": f"${details.get('model_cost_with_cache', 0.0):.6f}"
                        })
                        total_tokens += details.get("total_tokens", 0)
                        total_cost += details.get("model_cost_with_cache", 0.0)
                        total_latency += details.get("latency", 0.0)
                    
                    st.table(pd.DataFrame(metrics_rows))
                    
                    # Accumulator metrics columns
                    ac1, ac2, ac3 = st.columns(3)
                    ac1.markdown(f"<span style='color:rgba(255,255,255,0.4); font-size:0.8rem; text-transform:uppercase;'>Accumulated Tokens:</span> <code style='font-family:\"JetBrains Mono\",monospace; font-size:0.95rem; color:#06b6d4; font-weight:700;'>{total_tokens:,}</code>", unsafe_allow_html=True)
                    ac2.markdown(f"<span style='color:rgba(255,255,255,0.4); font-size:0.8rem; text-transform:uppercase;'>Accumulated Price:</span> <code style='font-family:\"JetBrains Mono\",monospace; font-size:0.95rem; color:#ef476f; font-weight:700;'>${total_cost:.6f}</code>", unsafe_allow_html=True)
                    ac3.markdown(f"<span style='color:rgba(255,255,255,0.4); font-size:0.8rem; text-transform:uppercase;'>Total Network Latency:</span> <code style='font-family:\"JetBrains Mono\",monospace; font-size:0.95rem; color:#10b981; font-weight:700;'>{total_latency:.3f} s</code>", unsafe_allow_html=True)

# -------------------------------------------------------------
# 📈 TAB 2: Financial Intelligence Center (Analytics View)
# -------------------------------------------------------------
# Extract latest transaction data for calculations
latest_debug = st.session_state.latest_debug_data
db_rows = []

if latest_debug and latest_debug.get("database_query_results"):
    raw_results = latest_debug.get("database_query_results")
    for r in raw_results:
        if isinstance(r, str) and r.startswith("[System Notification:"):
            continue
        try:
            db_rows.append(ast.literal_eval(r))
        except Exception:
            pass

with tab_analytics:
    st.markdown("<div style='margin-bottom:20px;'></div>", unsafe_allow_html=True)
    
    if db_rows:
        query_summary_df = pd.DataFrame(db_rows)
        df = query_summary_df.copy()

        if "amount_usd" not in df.columns:
            visual_df = load_visual_transaction_rows(latest_debug, customer_id)
            if not visual_df.empty:
                df = visual_df
        
        # Standardize Types
        if "amount_usd" in df.columns:
            df["amount_usd"] = pd.to_numeric(df["amount_usd"], errors="coerce").fillna(0.0)
        if "transaction_date" in df.columns:
            df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce")
            df = df.sort_values(by="transaction_date")

        # 1. Glowing Metrics Cards Layout
        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        
        total_spending = df["amount_usd"].sum() if "amount_usd" in df.columns else 0.0
        if "total_spent" in query_summary_df.columns:
            total_spending = pd.to_numeric(query_summary_df["total_spent"], errors="coerce").fillna(0.0).sum()
        elif "highest_transaction" in query_summary_df.columns:
            total_spending = pd.to_numeric(query_summary_df["highest_transaction"], errors="coerce").fillna(0.0).max()
        tx_count = len(df)
        avg_value = df["amount_usd"].mean() if "amount_usd" in df.columns and tx_count > 0 else 0.0
        
        top_category = "N/A"
        if "merchant_system_category1_name" in df.columns and not df.empty:
            top_cat_series = df.groupby("merchant_system_category1_name")["amount_usd"].sum()
            if not top_cat_series.empty:
                top_category = top_cat_series.idxmax()

        with m_col1:
            render_custom_metric("Total Gross Spending", f"${total_spending:,.2f}", "💵", "#ef476f")
        with m_col2:
            render_custom_metric("Transaction Volumes", f"{tx_count}", "📊", "#06b6d4")
        with m_col3:
            render_custom_metric("Highest Sector Category", top_category, "🍕", "#7c3aed")
        with m_col4:
            render_custom_metric("Average Ticket Value", f"${avg_value:,.2f}", "📈", "#10b981")

        # 2. Plots & Charts Layout
        st.markdown("<h4 style='color:#ffffff; font-family:\"Orbitron\",sans-serif; text-transform:uppercase; font-size:1.1rem; margin-top:20px; margin-bottom:20px; letter-spacing:0.8px;'>Visual Data Dimensions</h4>", unsafe_allow_html=True)
        v_col1, v_col2 = st.columns(2)

        with v_col1:
            # Donut Chart Category Distribution
            if "merchant_system_category1_name" in df.columns:
                cat_df = df.groupby("merchant_system_category1_name")["amount_usd"].sum().reset_index()
                
                # Premium Purple/Pink/Cyan scale
                custom_colors = ["#7c3aed", "#ef476f", "#06b6d4", "#10b981", "#ffc857", "#4b3f72"]
                
                fig_pie = px.pie(
                    cat_df, 
                    values="amount_usd", 
                    names="merchant_system_category1_name", 
                    hole=0.55,
                    color_discrete_sequence=custom_colors,
                    title="Spending Ratios by Sector"
                )
                fig_pie.update_traces(textposition='inside', textinfo='percent+label', marker=dict(line=dict(color='rgba(6,3,13,0.5)', width=2)))
                fig_pie.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#ffffff",
                    font_family="Outfit",
                    title_font=dict(family="Orbitron", size=15, color="#ffffff"),
                    legend=dict(orientation="h", yanchor="bottom", y=-0.35, xanchor="center", x=0.5)
                )
                st.plotly_chart(fig_pie, width='stretch')
            else:
                st.info("Sector Category details unavailable in transaction results.")

        with v_col2:
            # Timeline Area Spending Curve
            if "transaction_date" in df.columns and "amount_usd" in df.columns:
                time_df = df.groupby(df["transaction_date"].dt.date)["amount_usd"].sum().reset_index()
                fig_line = px.area(
                    time_df, 
                    x="transaction_date", 
                    y="amount_usd",
                    title="Spend Curve Timeline Trend",
                )
                fig_line.update_traces(line_color="#ef476f", fillcolor="rgba(239, 71, 111, 0.15)", line_width=3)
                fig_line.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#ffffff",
                    font_family="Outfit",
                    title_font=dict(family="Orbitron", size=15, color="#ffffff"),
                    xaxis=dict(showgrid=False, color="rgba(255,255,255,0.2)"),
                    yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.04)", color="rgba(255,255,255,0.2)")
                )
                st.plotly_chart(fig_line, width='stretch')
            else:
                st.info("Date range information unavailable for trend charting.")

        # Full-Width Horizontal Merchant Bar Chart
        if "merchant_brand_name" in df.columns:
            st.markdown("<div style='margin-bottom: 25px;'></div>", unsafe_allow_html=True)
            merch_df = df.groupby("merchant_brand_name")["amount_usd"].sum().reset_index().sort_values(by="amount_usd", ascending=True)
            fig_bar = px.bar(
                merch_df, 
                x="amount_usd", 
                y="merchant_brand_name", 
                orientation="h",
                title="Spending Magnitude by Merchant Brand",
                color="amount_usd",
                color_continuous_scale=["#0e091a", "#7c3aed", "#ef476f"]
            )
            fig_bar.update_traces(marker_line_color='rgba(255,255,255,0.06)', marker_line_width=1)
            fig_bar.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color="#ffffff",
                font_family="Outfit",
                title_font=dict(family="Orbitron", size=15, color="#ffffff"),
                xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.04)", color="rgba(255,255,255,0.2)"),
                yaxis=dict(showgrid=False, color="rgba(255,255,255,0.2)"),
                coloraxis_showscale=False
            )
            st.plotly_chart(fig_bar, width='stretch')

        # 3. Raw Structured Tabular records
        st.markdown("<h4 style='color:#ffffff; font-family:\"Orbitron\",sans-serif; text-transform:uppercase; font-size:1.1rem; margin-top:30px; margin-bottom:20px; letter-spacing:0.8px;'>Structured Ledger records</h4>", unsafe_allow_html=True)
        if not query_summary_df.equals(df):
            st.markdown("<h6 style='color:rgba(255,255,255,0.55); font-family:\"Orbitron\",sans-serif; text-transform:uppercase; font-size:0.78rem; letter-spacing:0.7px;'>Query Summary</h6>", unsafe_allow_html=True)
            st.dataframe(query_summary_df, width='stretch')
            st.markdown("<h6 style='color:rgba(255,255,255,0.55); font-family:\"Orbitron\",sans-serif; text-transform:uppercase; font-size:0.78rem; letter-spacing:0.7px;'>Visualized Transaction Rows</h6>", unsafe_allow_html=True)
        st.dataframe(df, width='stretch')

        # CSV Download utility
        csv_bytes = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Structured Ledger as CSV",
            data=csv_bytes,
            file_name=f"ledger_{customer_id}_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv"
        )
        
    else:
        st.markdown(
            """
            <div style="
                background: rgba(255,255,255,0.02);
                border: 1px solid rgba(255,255,255,0.05);
                border-radius: 16px;
                padding: 30px;
                text-align: center;
                color: rgba(255,255,255,0.4);
                font-family: 'Outfit', sans-serif;
                font-size: 0.95rem;
                margin-top: 20px;
            ">
                No active transaction datasets queried. Submit a spending question in the Chat view to populate this analytics center!
            </div>
            """, 
            unsafe_allow_html=True
        )

# -------------------------------------------------------------
# ⌨️ Handle Chat Input Submit & Suggestions Cache
# -------------------------------------------------------------
# Use a double path approach (handles suggested buttons vs typed box inputs)
typed_input = st.chat_input("Ask B-Bot about your transaction ledger...")
active_query = ""

if typed_input:
    active_query = typed_input
elif st.session_state.input_query:
    active_query = st.session_state.input_query
    st.session_state.input_query = "" # Reset suggestions cache

if active_query:
    submit_query(active_query)
    st.rerun()
