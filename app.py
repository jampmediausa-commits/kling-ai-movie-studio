
import os
import json
import base64
import hmac

import requests
import streamlit as st

API = "https://api-singapore.klingai.com"
st.set_page_config(page_title="Kling AI Movie Studio", page_icon="🎬", layout="wide")


def secret(name):
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return os.getenv(name, "").strip()


API_KEY = secret("KLING_API_KEY")
APP_PASSWORD = secret("APP_PASSWORD")


def api_call(method, path, payload=None, params=None):
    if not API_KEY:
        raise RuntimeError("KLING_API_KEY is not configured.")
    response = requests.request(
        method,
        API + path,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=payload,
        params=params,
        timeout=90,
    )
    try:
        result = response.json()
    except ValueError:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
    if not response.ok or result.get("code") not in (0, "0"):
        raise RuntimeError(f"HTTP {response.status_code}: {result}")
    return result.get("data")


def new_shot():
    return {
        "title": "New Shot", "prompt": "", "aspect": "16:9", "duration": 3,
        "resolution": "720p", "task_id": "", "video_url": "", "status": "", "message": ""
    }


def normalize_shot(data):
    shot = new_shot()
    if isinstance(data, dict):
        for key in shot:
            if key in data:
                shot[key] = data[key]
    for key in ("title", "prompt", "task_id", "video_url", "status", "message"):
        shot[key] = str(shot[key] or "")
    if shot["aspect"] not in ("16:9", "9:16", "1:1", "21:9", "adaptive"):
        shot["aspect"] = "16:9"
    try:
        shot["duration"] = int(shot["duration"])
    except (ValueError, TypeError):
        shot["duration"] = 3
    if shot["duration"] not in range(3, 31):
        shot["duration"] = 3
    if shot["resolution"] not in ("720p", "1080p"):
        shot["resolution"] = "720p"
    return shot


if "shots" not in st.session_state:
    st.session_state.shots = [new_shot()]
if "authorized" not in st.session_state:
    st.session_state.authorized = False

st.title("🎬 Kling AI Movie Studio")
st.caption("Storyboard and video generation with Kling 4.0")

if not APP_PASSWORD:
    st.error("APP_PASSWORD is missing. Configure it in Streamlit Secrets.")
    st.stop()

if not st.session_state.authorized:
    with st.form("login"):
        password = st.text_input("Studio password", type="password")
        login = st.form_submit_button("Unlock Studio")
    if login:
        if hmac.compare_digest(password, APP_PASSWORD):
            st.session_state.authorized = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

with st.sidebar:
    st.header("Studio Controls")
    if API_KEY:
        st.success("Kling API key configured")
    else:
        st.warning("Kling API key is missing")
    st.info("Generating video costs Kling API credits. Start with one short test.")
    if st.button("Lock Studio"):
        st.session_state.authorized = False
        st.rerun()
    st.divider()
    if st.button("➕ Add Shot", use_container_width=True):
        st.session_state.shots.append(new_shot())
        st.rerun()
    st.download_button(
        "⬇️ Export Storyboard",
        data=json.dumps(st.session_state.shots, indent=2),
        file_name="kling_storyboard.json", mime="application/json",
        use_container_width=True,
    )
    imported = st.file_uploader("Import Storyboard JSON", type=["json"], key="storyboard_import")
    if imported is not None and st.button("Load Imported Storyboard"):
        try:
            loaded = json.load(imported)
            if isinstance(loaded, dict):
                loaded = loaded.get("shots", [])
            if not isinstance(loaded, list) or not loaded:
                raise ValueError("Expected a nonempty list of shots.")
            st.session_state.shots = [normalize_shot(item) for item in loaded]
            st.rerun()
        except Exception as error:
            st.error(f"Import failed: {error}")

st.subheader("🎞️ Storyboard")
st.write("Leave the image empty for text-to-video, or upload a first-frame image.")

for i, shot in enumerate(st.session_state.shots):
    with st.expander(f"Shot {i + 1}: {shot['title']}", expanded=True):
        shot["title"] = st.text_input("Shot title", value=shot["title"], key=f"title_{i}")
        shot["prompt"] = st.text_area("Video prompt", value=shot["prompt"], height=150, key=f"prompt_{i}")
        c1, c2 = st.columns(2)
        aspect_options = ["16:9", "9:16", "1:1", "21:9", "adaptive"]
        shot["aspect"] = c1.selectbox(
            "Aspect ratio", aspect_options,
            index=aspect_options.index(shot["aspect"]), key=f"aspect_{i}"
        )
        durations = list(range(3, 31))
        shot["duration"] = c2.selectbox(
            "Duration (seconds)", durations,
            index=durations.index(shot["duration"]), key=f"duration_{i}"
        )
        resolutions = ["720p", "1080p"]
        shot["resolution"] = st.selectbox(
            "Resolution", resolutions,
            index=resolutions.index(shot["resolution"]), key=f"resolution_{i}"
        )
        photo = st.file_uploader(
            "Optional first-frame image (JPG or PNG, maximum 30 MB)",
            type=["png", "jpg", "jpeg"], key=f"photo_{i}"
        )
        if photo:
            st.image(photo, width=300)
        if shot["task_id"]:
            st.caption("Kling task ID: " + shot["task_id"])
        if shot["status"]:
            st.info("Task status: " + shot["status"])
        if shot["message"]:
            st.caption(shot["message"])
        if shot["video_url"]:
            st.video(shot["video_url"])
            st.link_button("Open / Save Video", shot["video_url"])

        if st.button(
            "🎥 Generate Shot — Paid API Request", key=f"generate_{i}",
            disabled=not (API_KEY and shot["prompt"].strip()),
        ):
            try:
                contents = [{"type": "prompt", "text": shot["prompt"].strip()}]
                if photo:
                    if photo.size > 30 * 1024 * 1024:
                        raise ValueError("Image exceeds Kling 4.0's 30 MB limit.")
                    encoded = base64.b64encode(photo.getvalue()).decode("ascii")
                    contents.append({"type": "first_frame", "url": encoded, "id": "image_1"})
                payload = {
                    "contents": contents,
                    "settings": {
                        "resolution": shot["resolution"],
                        "duration": int(shot["duration"]),
                        "aspect_ratio": shot["aspect"],
                    },
                }
                with st.spinner("Submitting video task..."):
                    data = api_call("POST", "/omni-video/kling-4.0", payload=payload)
                if not isinstance(data, dict) or not data.get("id"):
                    raise RuntimeError(f"No task ID returned: {data}")
                shot["task_id"] = str(data["id"])
                shot["status"] = data.get("status", "submitted")
                shot["video_url"] = ""
                shot["message"] = ""
                st.success("Task submitted. Use Check Status to retrieve the video.")
            except Exception as error:
                st.error(str(error))

        if st.button(
            "🔄 Check Status", key=f"status_{i}",
            disabled=not (API_KEY and shot["task_id"]),
        ):
            try:
                with st.spinner("Checking Kling task..."):
                    data = api_call("GET", "/tasks", params={"task_ids": shot["task_id"]})
                if isinstance(data, list):
                    tasks = data
                elif isinstance(data, dict):
                    tasks = data.get("result", [])
                    if not tasks and data.get("id"):
                        tasks = [data]
                else:
                    tasks = []
                if not tasks:
                    raise RuntimeError(f"No task found: {data}")
                task = next(
                    (item for item in tasks if str(item.get("id")) == shot["task_id"]),
                    tasks[0],
                )
                shot["status"] = task.get("status", "unknown")
                shot["message"] = task.get("message", "") or ""
                if shot["status"] == "succeeded":
                    outputs = task.get("outputs") or []
                    videos = [item for item in outputs if item.get("type") == "video" and item.get("url")]
                    if videos:
                        shot["video_url"] = videos[0]["url"]
                        st.success("Video is ready!")
                        st.rerun()
                    else:
                        st.warning("Task succeeded, but no video URL was returned.")
                elif shot["status"] == "failed":
                    st.error(shot["message"] or "Video generation failed.")
                else:
                    st.info("Current status: " + shot["status"])
            except Exception as error:
                st.error(str(error))

        if st.button("🗑️ Delete Shot", key=f"delete_{i}"):
            st.session_state.shots.pop(i)
            if not st.session_state.shots:
                st.session_state.shots = [new_shot()]
            st.rerun()

st.divider()
st.caption("Kling AI Movie Studio | Kling 4.0 | Save generated videos promptly.")
