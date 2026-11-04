
import os
import time
import json
import base64
import sqlite3
from pathlib import Path

import jwt
import requests
import streamlit as st

st.set_page_config(
    page_title="Kling AI Video Studio",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ---------- CONFIGURATION ----------

API = "https://api.klingai.com"
DATA = Path("studio_data")
DATA.mkdir(exist_ok=True)
DB = DATA / "videos.db"

def secret(name):
    try:
        return st.secrets.get(name, os.getenv(name, ""))
    except Exception:
        return os.getenv(name, "")

ACCESS_KEY = secret("KLING_ACCESS_KEY")
SECRET_KEY = secret("KLING_SECRET_KEY")

# ---------- DATABASE ----------

def database():
    db = sqlite3.connect(DB)
    db.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            task_id TEXT PRIMARY KEY,
            title TEXT,
            prompt TEXT,
            image TEXT,
            model TEXT,
            duration TEXT,
            aspect_ratio TEXT,
            status TEXT,
            video_url TEXT,
            created_at TEXT
        )
    """)
    db.commit()
    return db

def save_video(record):
    db = database()
    db.execute("""
        INSERT OR REPLACE INTO videos
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, record)
    db.commit()
    db.close()

def get_videos():
    db = database()
    rows = db.execute(
        "SELECT * FROM videos ORDER BY created_at DESC"
    ).fetchall()
    db.close()
    return rows

def update_video(task_id, status, url):
    db = database()
    db.execute("""
        UPDATE videos
        SET status=?, video_url=?
        WHERE task_id=?
    """, (status, url, task_id))
    db.commit()
    db.close()

# ---------- KLING API ----------

def token():
    if not ACCESS_KEY or not SECRET_KEY:
        raise ValueError(
            "Add KLING_ACCESS_KEY and KLING_SECRET_KEY "
            "to Streamlit Secrets."
        )

    now = int(time.time())

    return jwt.encode(
        {
            "iss": ACCESS_KEY,
            "exp": now + 1800,
            "nbf": now - 5
        },
        SECRET_KEY,
        algorithm="HS256",
        headers={"alg": "HS256", "typ": "JWT"}
    )

def kling_request(method, endpoint, payload=None):
    headers = {
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json"
    }

    response = requests.request(
        method,
        API + endpoint,
        headers=headers,
        json=payload,
        timeout=60
    )

    response.raise_for_status()
    result = response.json()

    if str(result.get("code", 0)) != "0":
        raise RuntimeError(
            result.get("message", "Kling API error")
        )

    return result.get("data", {})

def generate_video(image, prompt, model, duration, ratio):
    encoded = base64.b64encode(image).decode("utf-8")

    payload = {
        "model_name": model,
        "image": encoded,
        "prompt": prompt,
        "duration": str(duration),
        "aspect_ratio": ratio,
        "mode": "pro"
    }

    return kling_request(
        "POST",
        "/v1/videos/image2video",
        payload
    )

def check_video(task_id):
    return kling_request(
        "GET",
        f"/v1/videos/image2video/{task_id}"
    )

def refresh_library():
    for video in get_videos():
        task_id = video[0]

        if video[7] in ("succeed", "failed"):
            continue

        try:
            result = check_video(task_id)
            status = result.get("task_status", "processing")

            outputs = result.get("task_result", {}).get(
                "videos", []
            )

            url = outputs[0].get("url", "") if outputs else ""

            update_video(task_id, status, url)
        except Exception:
            continue

# ---------- DESIGN ----------

st.markdown("""
<style>
.stApp {
    background: linear-gradient(160deg, #101522, #080b12);
    color: #f5f5f5;
}

.block-container {
    max-width: 1050px;
    padding-top: 1.5rem;
    padding-bottom: 4rem;
}

h1, h2, h3 {
    letter-spacing: -0.035em;
}

.stButton > button[kind="primary"] {
    background: linear-gradient(110deg, #dba65c, #f6d08d);
    color: #17120b;
    border: none;
    border-radius: 12px;
    font-weight: 800;
    min-height: 3rem;
}

.stButton > button {
    border-radius: 12px;
    min-height: 2.8rem;
}

[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 18px;
    border-color: #343d50;
}

[data-testid="stFileUploader"] {
    border-radius: 14px;
}

.hero {
    padding: 28px;
    border-radius: 22px;
    background: linear-gradient(120deg, #242d40, #151a28);
    border: 1px solid #3c4353;
    margin-bottom: 22px;
}

.hero h1 {
    font-size: 2.2rem;
    margin-bottom: 5px;
}

.hero p {
    color: #b7bfce;
    margin: 0;
}

.gold {
    color: #efc47e;
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
    <h1>🎬 Kling AI <span class="gold">Video Studio</span></h1>
    <p>Create beautiful AI videos, one shot at a time.</p>
</div>
""", unsafe_allow_html=True)

# ---------- SESSION STATE ----------

if "generation" not in st.session_state:
    st.session_state.generation = 0

if "last_task" not in st.session_state:
    st.session_state.last_task = None

def new_video():
    st.session_state.generation += 1
    st.session_state.last_task = None

# ---------- MAIN TABS ----------

create_tab, library_tab, settings_tab = st.tabs([
    "✨ Create Video",
    "🎞️ My Videos",
    "⚙️ Settings"
])

# ---------- CREATE VIDEO ----------

with create_tab:

    left, right = st.columns([3, 1])

    with left:
        st.subheader("Create a new video")
        st.caption("Upload an image and bring it to life.")

    with right:
        st.button(
            "＋ New Video",
            on_click=new_video,
            use_container_width=True
        )

    n = st.session_state.generation

    with st.container(border=True):

        title = st.text_input(
            "Video name",
            placeholder="Example: Episode 1 - Shot 001",
            key=f"title_{n}"
        )

        image = st.file_uploader(
            "Upload reference image",
            type=["png", "jpg", "jpeg"],
            key=f"image_{n}"
        )

        if image:
            st.image(
                image,
                caption="Reference image",
                width=300
            )

        prompt = st.text_area(
            "Video prompt",
            placeholder=(
                "Describe the movement, camera angle, "
                "actions, lighting, and sound..."
            ),
            height=190,
            key=f"prompt_{n}"
        )

        st.markdown("#### Video Settings")

        col1, col2 = st.columns(2)

        with col1:
            model = st.selectbox(
                "Model",
                ["kling-v2-1"],
                key=f"model_{n}"
            )

            duration = st.selectbox(
                "Duration",
                ["5", "10"],
                key=f"duration_{n}"
            )

        with col2:
            ratio = st.selectbox(
                "Aspect ratio",
                ["9:16", "16:9", "1:1"],
                key=f"ratio_{n}"
            )

        st.caption(
            "Kling API-supported durations depend on the model. "
            "Choose 5 seconds for short individual shots."
        )

        confirmed = st.checkbox(
            "I understand this generation may use paid Kling credits.",
            key=f"confirm_{n}"
        )

        if st.button(
            "✦ Generate Video",
            type="primary",
            use_container_width=True,
            disabled=not (image and prompt.strip() and confirmed),
            key=f"generate_{n}"
        ):

            try:
                raw = image.getvalue()

                with st.spinner("Submitting video to Kling..."):
                    result = generate_video(
                        raw,
                        prompt.strip(),
                        model,
                        duration,
                        ratio
                    )

                task_id = result.get("task_id")

                if not task_id:
                    raise RuntimeError(
                        f"No task ID returned: {result}"
                    )

                image_path = DATA / (
                    f"{task_id}_{image.name.replace('/', '_')}"
                )
                image_path.write_bytes(raw)

                save_video((
                    task_id,
                    title or "Untitled Video",
                    prompt,
                    str(image_path),
                    model,
                    duration,
                    ratio,
                    "submitted",
                    "",
                    time.strftime("%Y-%m-%d %H:%M:%S")
                ))

                st.session_state.last_task = task_id

                st.success("Video submitted successfully!")
                st.info(
                    "Open My Videos to check progress, "
                    "or click + New Video to create another."
                )

            except Exception as error:
                st.error(f"Generation failed: {error}")

    if st.session_state.last_task:
        st.markdown("#### Latest generation")
        st.code(st.session_state.last_task)

        if st.button("Check latest video status"):
            try:
                result = check_video(st.session_state.last_task)
                status = result.get("task_status", "processing")

                videos = result.get(
                    "task_result", {}
                ).get("videos", [])

                url = videos[0].get("url", "") if videos else ""

                update_video(
                    st.session_state.last_task,
                    status,
                    url
                )

                st.info(f"Status: {status}")

                if url:
                    st.video(url)

            except Exception as error:
                st.error(str(error))

# ---------- VIDEO LIBRARY ----------

with library_tab:

    st.subheader("My Videos")
    st.caption("Your saved generations and their prompts.")

    if st.button("🔄 Refresh Video Status"):
        with st.spinner("Checking Kling..."):
            refresh_library()
        st.rerun()

    videos = get_videos()

    if not videos:
        st.info("No videos yet. Create your first video!")

    for video in videos:

        (
            task_id,
            title,
            prompt,
            image_path,
            model,
            duration,
            ratio,
            status,
            video_url,
            created_at
        ) = video

        with st.container(border=True):

            st.markdown(f"### 🎬 {title}")

            st.caption(
                f"{created_at} • {duration}s • "
                f"{ratio} • {model}"
            )

            st.write(f"**Status:** {status}")

            if video_url:
                st.video(video_url)
                st.link_button(
                    "Open / Download Video",
                    video_url
                )

            if image_path and Path(image_path).exists():
                with st.expander("Reference Image"):
                    st.image(image_path, width=260)

            with st.expander("View Original Prompt"):
                st.code(prompt, language="text")

            with st.expander("Task ID"):
                st.code(task_id)

            if status not in ("succeed", "failed"):
                if st.button(
                    "Refresh this video",
                    key=f"refresh_{task_id}"
                ):
                    try:
                        result = check_video(task_id)
                        new_status = result.get(
                            "task_status", "processing"
                        )
                        outputs = result.get(
                            "task_result", {}
                        ).get("videos", [])

                        url = (
                            outputs[0].get("url", "")
                            if outputs else ""
                        )

                        update_video(task_id, new_status, url)
                        st.rerun()

                    except Exception as error:
                        st.error(str(error))

# ---------- SETTINGS ----------

with settings_tab:

    st.subheader("Kling API Connection")

    if ACCESS_KEY and SECRET_KEY:
        st.success("Kling API credentials are configured.")
    else:
        st.warning("Kling API credentials are missing.")

    st.markdown(
        "Add your credentials to **Streamlit → App Settings → Secrets**."
    )

    st.code(
        'KLING_ACCESS_KEY = "YOUR_ACCESS_KEY"\n'
        'KLING_SECRET_KEY = "YOUR_SECRET_KEY"',
        language="toml"
    )

    st.info(
        "Keep your API credentials private. "
        "Never paste them directly into GitHub."
    )

    st.warning(
        "The video library currently uses local SQLite storage. "
        "On Streamlit Community Cloud, local history can be lost "
        "after an app restart or redeployment. "
        "Use a persistent database for permanent history."
    )

st.divider()
st.caption("Kling AI Video Studio • Your creative workspace")
