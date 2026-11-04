import base64
import json
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

import requests
import streamlit as st

st.set_page_config(
    page_title="Kling AI Movie Studio",
    page_icon="🎬",
    layout="centered",
)

# Same API configuration and Streamlit Secrets
# as your existing app.
BASE = "https://api-singapore.klingai.com"
MODEL_PATH = "/omni-video/kling-4.0"
DB_PATH = Path("/tmp/kling_movie_studio_videos.sqlite3")


def secret(name):
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def connection():
    db = sqlite3.connect(str(DB_PATH), timeout=20)
    db.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            task_id TEXT PRIMARY KEY,
            prompt TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'submitted',
            video_url TEXT NOT NULL DEFAULT '',
            created INTEGER NOT NULL
        )
    """)
    db.commit()
    return db


def save_video(
    task_id,
    prompt="",
    status="submitted",
    video_url="",
):
    with connection() as db:
        db.execute(
            """
            INSERT INTO videos (
                task_id,
                prompt,
                status,
                video_url,
                created
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(task_id)
            DO UPDATE SET
                prompt = CASE
                    WHEN excluded.prompt != ''
                    THEN excluded.prompt
                    ELSE videos.prompt
                END,
                status = excluded.status,
                video_url = CASE
                    WHEN excluded.video_url != ''
                    THEN excluded.video_url
                    ELSE videos.video_url
                END
            """,
            (
                task_id,
                prompt,
                status,
                video_url,
                int(time.time()),
            ),
        )
        db.commit()


def all_videos():
    with connection() as db:
        rows = db.execute(
            """
            SELECT
                task_id,
                prompt,
                status,
                video_url,
                created
            FROM videos
            ORDER BY created DESC
            """
        ).fetchall()

    fields = (
        "task_id",
        "prompt",
        "status",
        "video_url",
        "created",
    )

    return [
        dict(zip(fields, row))
        for row in rows
    ]


def kling_request(method, path, payload=None):
    key = secret("KLING_API_KEY")

    if not key:
        raise RuntimeError(
            "KLING_API_KEY is missing "
            "from Streamlit Secrets."
        )

    try:
        response = requests.request(
            method,
            BASE + path,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=45,
        )

        response.raise_for_status()
        body = response.json()

    except requests.RequestException as exc:
        detail = (
            exc.response.text[:400]
            if exc.response is not None
            else str(exc)
        )

        raise RuntimeError(
            "Kling request failed: " + detail
        ) from exc

    if not isinstance(body, dict):
        raise RuntimeError(
            "Unexpected Kling response."
        )

    if str(body.get("code", 0)) not in (
        "0",
        "200",
    ):
        raise RuntimeError(
            str(
                body.get("message")
                or body.get("msg")
                or body
            )
        )

    return body.get("data")


def parse_tasks(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in (
            "tasks",
            "items",
            "list",
        ):
            if isinstance(data.get(key), list):
                return data[key]

        if data.get("id"):
            return [data]

    return []


def video_from_task(task):
    outputs = task.get("outputs") or []

    for output in outputs:
        if not isinstance(output, dict):
            continue

        if (
            output.get("type") == "video"
            and output.get("url")
        ):
            return str(output["url"])

    return str(
        task.get("video_url") or ""
    )


def recover_ids(ids):
    found = set()

    for start in range(0, len(ids), 20):
        group = ids[start:start + 20]

        path = (
            "/tasks?task_ids="
            + quote(
                ",".join(group),
                safe=",",
            )
        )

        data = kling_request(
            "GET",
            path,
        )

        for task in parse_tasks(data):
            if not isinstance(task, dict):
                continue

            task_id = str(
                task.get("id") or ""
            )

            if not task_id:
                continue

            status = str(
                task.get("status")
                or "processing"
            ).lower()

            if status in (
                "completed",
                "success",
            ):
                status = "succeeded"

            save_video(
                task_id=task_id,
                status=status,
                video_url=video_from_task(task),
            )

            found.add(task_id)

    return found


def fetch_mp4(url):
    response = requests.get(
        url,
        timeout=120,
        stream=True,
    )

    response.raise_for_status()

    chunks = []
    total = 0

    maximum = 250 * 1024 * 1024

    for chunk in response.iter_content(
        chunk_size=1024 * 1024
    ):
        if not chunk:
            continue

        total += len(chunk)

        if total > maximum:
            raise RuntimeError(
                "MP4 exceeds 250 MB. "
                "Use Open Video instead."
            )

        chunks.append(chunk)

    return b"".join(chunks)


# ====================================
# PASSWORD LOGIN
# ====================================

password = secret("APP_PASSWORD")

if password and not st.session_state.get(
    "unlocked",
    False,
):
    st.title("🔒 Kling AI Movie Studio")

    with st.form("login"):
        entered = st.text_input(
            "Studio password",
            type="password",
        )

        submitted = st.form_submit_button(
            "Unlock",
            use_container_width=True,
        )

    if submitted:
        if entered == password:
            st.session_state.unlocked = True
            st.rerun()
        else:
            st.error("Incorrect password")

    st.stop()


# ====================================
# MAIN APP
# ====================================

st.title("🎬 Kling AI Movie Studio")

st.caption(
    "Generate, watch, recover, "
    "and download Kling videos."
)

if not secret("KLING_API_KEY"):
    st.error(
        "KLING_API_KEY is missing "
        "from Streamlit Secrets."
    )
    st.stop()


generate_tab, videos_tab, recover_tab = st.tabs([
    "🎬 Generate",
    "📹 My Videos",
    "♻️ Recover Videos",
])


# ====================================
# TAB 1 — GENERATE
# ====================================

with generate_tab:
    st.subheader("Generate Video")

    with st.form("generate_form"):
        uploaded = st.file_uploader(
            "Upload Image",
            type=[
                "png",
                "jpg",
                "jpeg",
                "webp",
            ],
        )

        prompt = st.text_area(
            "Video Prompt",
            height=150,
        )

        duration = st.selectbox(
            "Duration",
            [5, 10],
        )

        aspect = st.selectbox(
            "Aspect Ratio",
            [
                "9:16",
                "16:9",
                "1:1",
            ],
        )

        st.caption(
            "Generate submits a paid "
            "Kling video request."
        )

        submitted = st.form_submit_button(
            "✨ Generate Video",
            type="primary",
            use_container_width=True,
        )

    if uploaded:
        st.image(
            uploaded.getvalue(),
            use_container_width=True,
        )

    if submitted:
        if uploaded is None:
            st.error(
                "Upload an image first."
            )

        elif not prompt.strip():
            st.error(
                "Enter a video prompt."
            )

        elif (
            uploaded.size
            > 30 * 1024 * 1024
        ):
            st.error(
                "Image exceeds 30 MB."
            )

        else:
            mime = (
                uploaded.type
                or "image/png"
            )

            encoded = base64.b64encode(
                uploaded.getvalue()
            ).decode("ascii")

            image_url = (
                f"data:{mime};base64,"
                + encoded
            )

            payload = {
                "contents": [
                    {
                        "type": "prompt",
                        "text": prompt.strip(),
                    },
                    {
                        "type": "first_frame",
                        "url": image_url,
                        "id": "image_1",
                    },
                ],
                "settings": {
                    "resolution": "720p",
                    "duration": duration,
                    "aspect_ratio": aspect,
                },
            }

            try:
                with st.spinner(
                    "Submitting to Kling..."
                ):
                    data = kling_request(
                        "POST",
                        MODEL_PATH,
                        payload,
                    )

                if (
                    not isinstance(data, dict)
                    or not data.get("id")
                ):
                    raise RuntimeError(
                        "Kling did not return "
                        "a task ID."
                    )

                task_id = str(
                    data["id"]
                )

                save_video(
                    task_id=task_id,
                    prompt=prompt.strip(),
                    status=str(
                        data.get("status")
                        or "submitted"
                    ).lower(),
                )

                st.success(
                    "Video submitted. "
                    "Open My Videos to "
                    "refresh and download."
                )

            except Exception as exc:
                st.error(str(exc))


# ====================================
# TAB 2 — MY VIDEOS
# ====================================

with videos_tab:
    st.subheader("My Videos")

    if st.button(
        "🔄 Refresh Videos",
        use_container_width=True,
    ):
        ids = [
            video["task_id"]
            for video in all_videos()
            if video["status"] not in (
                "succeeded",
                "failed",
                "canceled",
            )
            or not video["video_url"]
        ]

        if ids:
            try:
                with st.spinner(
                    "Checking Kling..."
                ):
                    recover_ids(ids)

                st.rerun()

            except Exception as exc:
                st.error(str(exc))

        else:
            st.info(
                "No unfinished videos "
                "to refresh."
            )

    videos = all_videos()

    if not videos:
        st.info(
            "No saved videos found. "
            "Check Recover Videos "
            "for older task IDs."
        )

    for index, video in enumerate(
        videos,
        1,
    ):
        with st.container(border=True):
            st.markdown(
                f"### Video {index}"
            )

            if video["prompt"]:
                st.caption(
                    video["prompt"][:180]
                )

            st.write(
                "Status: **"
                + video["status"]
                + "**"
            )

            if video["video_url"]:
                st.video(
                    video["video_url"]
                )

                st.link_button(
                    "📱 Open Video / Save to Photos",
                    video["video_url"],
                    use_container_width=True,
                )

                key = (
                    "mp4_"
                    + video["task_id"]
                )

                if st.button(
                    "⬇️ Prepare Download",
                    key="prepare_" + key,
                    use_container_width=True,
                ):
                    try:
                        with st.spinner(
                            "Downloading MP4 "
                            "from Kling..."
                        ):
                            st.session_state[
                                key
                            ] = fetch_mp4(
                                video["video_url"]
                            )

                    except Exception as exc:
                        st.error(str(exc))

                if key in st.session_state:
                    st.download_button(
                        "⬇️ Download MP4",
                        data=st.session_state[key],
                        file_name=(
                            "kling_"
                            + video["task_id"]
                            + ".mp4"
                        ),
                        mime="video/mp4",
                        key="download_" + key,
                        use_container_width=True,
                    )

            elif video["status"] in (
                "failed",
                "canceled",
            ):
                st.warning(
                    "This video task "
                    "did not complete."
                )

            else:
                st.info(
                    "No MP4 link yet. "
                    "Tap Refresh Videos "
                    "to check."
                )


# ====================================
# TAB 3 — RECOVER VIDEOS
# ====================================

with recover_tab:
    st.subheader(
        "Recover Older Kling Videos"
    )

    st.info(
        "Kling's tested /tasks endpoint "
        "requires task IDs. "
        "It does NOT provide a complete "
        "account-wide video history. "
        "This tab cannot discover "
        "lost task IDs automatically."
    )

    st.write(
        "If you have task IDs from "
        "a previous project or Kling "
        "response, paste them here. "
        "No new videos will be generated."
    )

    pasted = st.text_area(
        "Task IDs — one per line "
        "or separated by commas",
        height=150,
    )

    if st.button(
        "♻️ Recover Videos",
        use_container_width=True,
    ):
        ids = list(dict.fromkeys(
            x
            for x in re.split(
                r"[\s,]+",
                pasted.strip(),
            )
            if x
        ))

        if not ids:
            st.warning(
                "Enter at least one task ID."
            )

        elif len(ids) > 100:
            st.error(
                "Enter up to 100 task IDs "
                "at a time."
            )

        else:
            try:
                with st.spinner(
                    "Looking up existing tasks..."
                ):
                    found = recover_ids(ids)

                st.success(
                    f"Found {len(found)} task(s). "
                    "Open My Videos to download."
                )

                missing = [
                    x for x in ids
                    if x not in found
                ]

                if missing:
                    st.warning(
                        f"{len(missing)} task ID(s) "
                        "were not returned by Kling."
                    )

            except Exception as exc:
                st.error(str(exc))

    st.divider()

    st.write(
        "Backup the task IDs "
        "the app currently knows about:"
    )

    records = all_videos()

    if records:
        st.download_button(
            "⬇️ Download Task Backup",
            data=json.dumps(
                records,
                indent=2,
            ),
            file_name="kling_task_backup.json",
            mime="application/json",
            use_container_width=True,
        )

    else:
        st.caption(
            "No saved task IDs "
            "to back up yet."
        )

    backup = st.file_uploader(
        "Optional: Restore an older "
        "task backup",
        type=["json"],
    )

    if (
        backup is not None
        and st.button("Restore Backup")
    ):
        try:
            data = json.loads(
                backup.getvalue().decode(
                    "utf-8"
                )
            )

            if isinstance(data, dict):
                data = data.get(
                    "videos",
                    [],
                )

            if not isinstance(data, list):
                raise ValueError(
                    "Backup must contain "
                    "a list of video records."
                )

            count = 0

            for item in data:
                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                task_id = str(
                    item.get("task_id")
                    or item.get("id")
                    or ""
                )

                if task_id:
                    save_video(
                        task_id,
                        prompt=str(
                            item.get("prompt")
                            or ""
                        ),
                        status=str(
                            item.get("status")
                            or "submitted"
                        ),
                        video_url=str(
                            item.get("video_url")
                            or ""
                        ),
                    )

                    count += 1

            st.success(
                f"Restored {count} "
                "task record(s). "
                "Open My Videos."
            )

        except Exception as exc:
            st.error(
                "Invalid backup: "
                + str(exc)
            )


# ====================================
# FOOTER
# ====================================

st.divider()

st.caption(
    "History is stored in temporary "
    "Streamlit Cloud SQLite storage. "
    "A server restart or redeployment "
    "can erase it. "
    "Download your MP4 files and "
    "task backup promptly."
)
