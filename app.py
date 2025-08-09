import os
import json
import sqlite3
from datetime import datetime, date, time as dtime
import time as t
import streamlit as st
import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from groq import Groq  # Groq API

try:
    groq_key = st.secrets["GROQ_API_KEY"]
except:
    groq_key = os.getenv("GROQ_API_KEY")

# Groq Client
client = Groq(api_key=groq_key)  

# --- DB Setup ---
DB_PATH = "posts.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT,
    scheduled_at TEXT,
    status TEXT,
    created_at TEXT
)
""")
conn.commit()

# --- Scheduler ---
scheduler = BackgroundScheduler()
scheduler.start()

# --- Helper Functions ---
def safe_json_load(s: str):
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        st.error("⚠️ Failed to parse JSON from Groq API response.")
        st.text("Raw response:")
        st.text(s)
        return None

def call_groq(messages, temperature=0.7):
    try:
        resp = client.chat.completions.create(
            model="llama3-70b-8192",  # free Groq model
            messages=messages,
            temperature=temperature
        )
        return {"choices": [{"message": {"content": resp.choices[0].message.content}}]}
    except Exception as e:
        st.error(f"Groq API error: {e}")
        return {"choices": [{"message": {"content": ""}}]}

def analyze_profile(profile_text):
    prompt = f"""
You are an assistant that extracts structured JSON from a LinkedIn profile.
Return ONLY valid JSON with:
- industry (string)
- top_skills (list of 5 strings)
- audience (string)
- content_pillars (list of 3 strings)
- tone (one word)
DO NOT add any explanation or extra text.

Profile text: {profile_text}
"""
    resp = call_groq([{"role": "user", "content": prompt}], temperature=0.2)
    return safe_json_load(resp['choices'][0]['message']['content'])

def generate_posts(pillars, tone, n=3):
    prompt = f"""
Given content pillars: {pillars}, and tone: {tone}, generate {n} LinkedIn post drafts.
Return ONLY a JSON array like:
[{{"post":"...","hashtags":["#...","#...","#..."]}}, ...]
NO extra text or explanation.
"""
    resp = call_groq([{"role": "user", "content": prompt}], temperature=0.7)
    return safe_json_load(resp['choices'][0]['message']['content'])

def save_post(content, run_at_iso):
    now = datetime.utcnow().isoformat()
    cur.execute("INSERT INTO posts (content, scheduled_at, status, created_at) VALUES (?,?,?,?)",
                (content, run_at_iso, "scheduled", now))
    conn.commit()
    pid = cur.lastrowid
    run_date = datetime.fromisoformat(run_at_iso)
    scheduler.add_job(execute_post, 'date', run_date=run_date, args=[pid])
    return pid

def execute_post(post_id):
    rec = cur.execute("SELECT content, status FROM posts WHERE id=?", (post_id,)).fetchone()
    if not rec:
        return
    _, status = rec
    if status == "posted":
        return
    t.sleep(1)  # simulate
    cur.execute("UPDATE posts SET status='posted' WHERE id=?", (post_id,))
    conn.commit()

# --- Streamlit UI ---
st.set_page_config(page_title="Influence OS MVP", layout="wide")
st.title("Influence OS — LinkedIn Personal Branding AI Agent (MVP)")

# Profile Analysis
st.header("1) Profile Analysis")
profile_text = st.text_area("Paste your LinkedIn profile or bio:", height=150)
if st.button("Analyze Profile"):
    with st.spinner("Analyzing..."):
        analysis = analyze_profile(profile_text)
        if analysis is None or not isinstance(analysis, dict):
            st.error("Invalid or no JSON response received for profile analysis.")
        else:
            st.json(analysis)
            if "content_pillars" in analysis:
                st.session_state['pillars'] = analysis['content_pillars']

# Content Generation
st.header("2) Generate Posts")
pillars = st.text_input("Content pillars (comma separated)",
                        value=", ".join(st.session_state.get('pillars', [])))
tone = st.selectbox("Tone", ["professional", "friendly", "thought-leader", "casual"])
num_posts = st.slider("Number of posts", 1, 5, 3)
if st.button("Generate Posts"):
    with st.spinner("Generating..."):
        posts = generate_posts(pillars, tone, num_posts)
        if posts is None or not isinstance(posts, list):
            st.error("Invalid or no JSON response received for post generation.")
        else:
            st.session_state['posts'] = posts

# Show Generated Posts
if 'posts' in st.session_state:
    st.subheader("Generated Posts")
    for idx, post in enumerate(st.session_state['posts']):
        st.markdown(f"**Post #{idx+1}**")
        text_val = post['post'] if isinstance(post, dict) else str(post)
        hashtags = ", ".join(post.get('hashtags', [])) if isinstance(post, dict) else ""
        new_text = st.text_area(f"Edit Post #{idx+1}", value=text_val, height=120, key=f"edit_post_{idx}")
        st.text(f"Hashtags: {hashtags}")
        col_a, col_b = st.columns(2)
        with col_a:
            sdate = st.date_input(f"Date #{idx+1}", value=date.today(), key=f"date_{idx}")
        with col_b:
            stime = st.time_input(f"Time #{idx+1}", value=dtime(hour=10, minute=0), key=f"time_{idx}")
        if st.button(f"Schedule Post #{idx+1}", key=f"schedule_{idx}"):
            run_at = datetime.combine(sdate, stime).isoformat()
            pid = save_post(new_text, run_at)
            st.success(f"Scheduled post #{pid} for {run_at}")

# Scheduled Posts & Analytics
st.header("3) Scheduled & Posted Posts")
df = pd.read_sql_query("SELECT * FROM posts ORDER BY scheduled_at DESC", conn)
st.dataframe(df)

if not df.empty:
    st.subheader("Post Status Counts")
    st.write(df['status'].value_counts().to_dict())
    df['scheduled_at'] = pd.to_datetime(df['scheduled_at'])
    posts_per_day = df.groupby(df['scheduled_at'].dt.date).size()
    st.line_chart(posts_per_day)
