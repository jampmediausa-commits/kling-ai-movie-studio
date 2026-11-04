import base64
import json
import time
from urllib.parse import quote

import requests
import streamlit as st

st.set_page_config(page_title="Kling AI Movie Studio", page_icon="🎬", layout="centered")

# Original working one-key Kling configuration. Do not change your Streamlit secrets.
BASE = "https://api-singapore.klingai.com"
MODEL_PATH = "/omni-video/kling-4.0"


def secret(name):
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def kling_request(method, path, payload=None):
    key = secret("KLING_API_KEY")
    if not key:
        raise RuntimeError("KLING_API_KEY is missing from Streamlit Secrets.")
    try:
        response = requests.request(
            method,
            BASE + path,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=45,
        )
        response.raise_for_status()
        body = response.json()
    except requests.RequestException as exc:
        detail = exc.response.text[:400] if exc.response is not None else str(exc)
        raise RuntimeError(f"Kling request failed: {detail}") from exc
    except ValueError as exc:
        raise RuntimeError("Kling returned an invalid JSON response.") from exc
    if str(body.get("code", 0)) not in ("0", "200"):
        raise RuntimeError(str(body.get("message") or body.get("msg") or body))
    return body.get("data")


def parse_tasks(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("tasks", "items", "list"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return []


def video_from_task(task):
    for output in task.get("outputs") or []:
        if isinstance(output, dict) and output.get("type") == "video" and output.get("url"):
            return str(output["url"])
    return ""


def refresh_videos():
    pending = [v for v in st.session_state.videos if v["status"] not in ("succeeded", "failed", "canceled")]
    for start in range(0, len(pending), 20):
        group = pending[start:start + 20]
        ids = ",".join(v["task_id"] for v in group)
        data = kling_request("GET", "/tasks?task_ids=" + quote(ids, safe=","))
        tasks = {str(t.get("id")): t for t in parse_tasks(data) if isinstance(t, dict)}
        for v in group:
            task = tasks.get(v["task_id"])
            if task:
                v["status"] = str(task.get("status", v["status"])).lower()
                v["video_url"] = video_from_task(task) or v["video_url"]
                if v["status"] in ("failed", "canceled"):
                    v["error"] = str(task.get("message") or task.get("error") or "Generation failed")


# Keep the existing password secret and login behavior.
password = secret("APP_PASSWORD")
if password and not st.session_state.get("unlocked", False):
    st.title("🔒 Kling AI Movie Studio")
    with st.form("login"):
        entered = st.text_input("Studio password", type="password")
        submitted = st.form_submit_button("Unlock", use_container_width=True)
    if submitted:
        if entered == password:
            st.session_state.unlocked = True
            st.rerun()
        else:
            st.error("Incorrect password")
    st.stop()

if "videos" not in st.session_state:
    st.session_state.videos = []

st.title("🎬 Kling AI Movie Studio")
st.caption("Upload an image, enter a prompt, generate, and download.")

if not secret("KLING_API_KEY"):
    st.error("Your existing KLING_API_KEY was not found in Streamlit Secrets.")
    st.stop()

generate_tab, videos_tab = st.tabs(["🎬 Generate", "📹 Videos"])

with generate_tab:
    with st.form("generate_form", clear_on_submit=False):
        uploaded = st.file_uploader("Upload image", type=["png", "jpg", "jpeg", "webp"])
        prompt = st.text_area("Video prompt", height=150, placeholder="Describe what should happen in your video...")
        duration = st.selectbox("Duration", [5, 10], index=0)
        aspect = st.selectbox("Aspect ratio", ["9:16", "16:9", "1:1"], index=0)
        st.caption("Generate submits a paid Kling request. Each click creates a new task.")
        submitted = st.form_submit_button("✨ Generate Video", type="primary", use_container_width=True)

    if uploaded:
        st.image(uploaded.getvalue(), caption="Uploaded first frame", use_container_width=True)

    if submitted:
        if uploaded is None:
            st.error("Upload an image first.")
        elif not prompt.strip():
            st.error("Enter a prompt first.")
        elif uploaded.size > 30 * 1024 * 1024:
            st.error("Image exceeds 30 MB.")
        else:
            mime = uploaded.type or "image/png"
            image_url = f"data:{mime};base64,{base64.b64encode(uploaded.getvalue()).decode('ascii')}"
            payload = {
                "contents": [
                    {"type": "prompt", "text": prompt.strip()},
                    {"type": "first_frame", "url": image_url, "id": "image_1"},
                ],
                "settings": {"resolution": "720p", "duration": duration, "aspect_ratio": aspect},
            }
            try:
                with st.spinner("Submitting video to Kling..."):
                    data = kling_request("POST", MODEL_PATH, payload)
                if not isinstance(data, dict) or not data.get("id"):
                    raise RuntimeError(f"Kling did not return a task ID: {data}")
                task_id = str(data["id"])
                st.session_state.videos.insert(0, {
                    "task_id": task_id,
                    "status": str(data.get("status") or "submitted").lower(),
                    "video_url": "",
                    "error": "",
                    "prompt": prompt.strip(),
                    "created": time.strftime("%Y-%m-%d %H:%M"),
                })
                st.success("Video submitted! Open the Videos tab to check progress.")
            except Exception as exc:
                st.error(str(exc))

with videos_tab:
    if st.button("🔄 Refresh video status", use_container_width=True):
        try:
            with st.spinner("Checking Kling tasks..."):
                refresh_videos()
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    if not st.session_state.videos:
        st.info("Your generated videos will appear here after you submit a prompt.")
    for number, video in enumerate(st.session_state.videos, 1):
        with st.container(border=True):
            st.markdown(f"**Video {number}** · {video['created']}")
            st.caption(video["prompt"][:180])
            st.write(f"Status: **{video['status']}**")
            if video["error"]:
                st.error(video["error"])
            if video["video_url"]:
                st.video(video["video_url"])
                st.link_button("⬇️ Open / Download Video", video["video_url"], use_container_width=True)
            elif video["status"] == "succeeded":
                st.warning("Kling reported success but did not provide a video URL.")

st.caption("Video history is kept in the current Streamlit session. Download finished clips promptly; Kling URLs can expire.")
