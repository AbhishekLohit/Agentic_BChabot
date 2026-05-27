import streamlit as st

st.set_page_config(page_title="B-Bot", layout="wide")

# Hide all Streamlit chrome so only the iframe is visible
st.markdown("""
<style>
    header[data-testid="stHeader"] { display: none !important; }
    footer { display: none !important; }
    #MainMenu { display: none !important; }
    .block-container { padding: 0 !important; }
    [data-testid="stToolbar"] { display: none !important; }
</style>
<iframe
    src="http://localhost:8000"
    style="position:fixed;top:0;left:0;width:100%;height:100%;border:none;z-index:9999;"
    allowfullscreen>
</iframe>
""", unsafe_allow_html=True)
