
import base64
import json
import re
import time
from urllib.parse import quote

import requests
import streamlit as st


st.set_page_config(
    page_title="Kling AI Movie Studio",
    page_icon="🎬",
    layout="centered",
)

# KEEP THE ORIGINAL WORKING KLING CONNECTION.
# No new keys or credentials are needed.
BASE = "https://api-singapore.klingai.com"
MODEL_PATH = "/omni-video/kling-4.0"

FINAL_STATUSES = {
    "succeeded", "success", "completed",
    "failed", "canceled", "cancelled",
}


def secret(name):
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def kling_request(method, path, payload=None):
    key = secret("KLING_API_KEY")

    if not key:
        raise RuntimeError(
            "KLING_API_KEY is missing from Streamlit Secrets."
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
            exc.response.text[:500]
            if exc.response is not None
            else str(exc)
        )
        raise RuntimeError(
            f"Kling request failed: {detail}"
        ) from exc

    except ValueError as exc:
        raise RuntimeError(
            "Kling returned an invalid JSON response."
        ) from exc

    if not isinstance(body, dict):
        raise RuntimeError(
            "Unexpected Kling API response."
        )

    if str(body.get("code", 0)) not in ("0", "200"):
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
            "records",
        ):
            value = data.get(key)

            if isinstance(value, list):
                return value

        for key in ("data", "result"):
            value = data.get(key)

            if isinstance(value, (list, dict)):
                return parse_tasks(value)

        if data.get("id") or data.get("task_id"):
            return [data]

    return []


def task_id_of(task):
    return str(
        task.get("id")
        or task.get("task_id")
        or ""
    ).strip()


def video_from_task(task):
    outputs = task.get("outputs") or []

    if isinstance(outputs, dict):
        outputs = (
            outputs.get("videos")
            or outputs.get("items")
            or [outputs]
        )

    if isinstance(outputs, list):
        for output in outputs:
            if not isinstance(output, dict):
                continue

            url = (
                output.get("url")
                or output.get("video_url")
            )

            if url and (
                output.get("type") in (None, "video")
            ):
                return str(url)

    for key in ("video_url", "videoUrl"):
        if task.get(key):
            return str(task[key])

    return ""


def normalize_status(status):
    status = str(status or "submitted").lower()

    if status in ("success", "completed"):
        return "succeeded"

    return status


def make_video(task_id, task=None):
    task = task or {}

    return {
        "task_id": str(task_id),
        "status": normalize_status(
            task.get("status")
        ),
        "video_url": video_from_task(task),
        "error": "",
        "prompt": str(
            task.get("prompt") or ""
        ),
        "created": time.strftime(
            "%Y-%m-%d %H:%M"
        ),
    }


def add_or_update_tasks(tasks):
    added = 0
    updated = 0

    existing = {
        item["task_id"]: item
        for item in st.session_state.videos
    }

    for task in tasks:
        if not isinstance(task, dict):
            continue

        task_id = task_id_of(task)

        if not task_id:
            continue

        if task_id in existing:
            video = existing[task_id]

            if task.get("status"):
                video["status"] = normalize_status(
                    task["status"]
                )

            url = video_from_task(task)

            if url:
                video["video_url"] = url

            if task.get("prompt"):
                video["prompt"] = str(
                    task["prompt"]
                )

            updated += 1

        else:
            video = make_video(task_id, task)
            st.session_state.videos.append(video)
            existing[task_id] = video
            added += 1

    return added, updated


def lookup_tasks(task_ids):
    results = []

    for start in range(0, len(task_ids), 20):
        group = task_ids[start:start + 20]

        path = (
            "/tasks?task_ids="
            + quote(",".join(group), safe=",")
        )

        data = kling_request("GET", path)
        results.extend(parse_tasks(data))

    return results


def refresh_videos():
    pending = [
        item["task_id"]
        for item in st.session_state.videos
        if item["status"] not in FINAL_STATUSES
        or (
            item["status"] == "succeeded"
            and not item["video_url"]
        )
    ]

    if not pending:
        return 0

    tasks = lookup_tasks(pending)
    add_or_update_tasks(tasks)

    return len(tasks)


def extract_task_ids(value):
    found = set()

    def walk(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key in ("task_id", "taskId"):
                    if isinstance(child, (str, int)):
                        found.add(str(child).strip())

                elif key == "id":
                    if (
                        isinstance(child, str)
                        and len(child) >= 8
                    ):
                        found.add(child.strip())

                elif isinstance(child, (dict, list)):
                    walk(child)

        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)

    return sorted(x for x in found if x)


def extract_manual_ids(text):
    return list(dict.fromkeys(
        value.strip()
        for value in re.split(
            r"[\s,;]+",
            text or "",
        )
        if value.strip()
    ))


def fetch_video_bytes(url):
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
                "Video is larger than 250 MB. "
                "Use Save to Photos / Open Video instead."
            )

        chunks.append(chunk)

    return b"".join(chunks)


def render_video(video, index, prefix):
    task_id = video["task_id"]
    url = video.get("video_url", "")
    status = video.get("status", "unknown")

    with st.container(border=True):
        st.markdown(f"### Video {index}")

        st.caption(
            f"Task ID: {task_id}"
        )

        if video.get("prompt"):
            st.write(video["prompt"][:250])

        st.write(f"Status: **{status}**")

        if video.get("error"):
            st.error(video["error"])

        if url:
            st.video(url)

            st.link_button(
                "📱 Save to Photos / Open Video",
                url,
                use_container_width=True,
            )

            st.caption(
                "On iPhone, open the video, "
                "tap Share, then Save Video. "
                "If it saves to Files, open "
                "the MP4 there and choose "
                "Share → Save Video."
            )

            key = f"{prefix}_{task_id}"

            if st.button(
                "⬇️ Prepare MP4 Download",
                key=f"prepare_{key}",
                use_container_width=True,
            ):
                try:
                    with st.spinner(
                        "Preparing your video..."
                    ):
                        st.session_state[
                            f"download_{key}"
                        ] = fetch_video_bytes(url)

                except Exception as exc:
                    st.error(str(exc))

            data = st.session_state.get(
                f"download_{key}"
            )

            if data:
                st.download_button(
                    "⬇️ Download MP4",
                    data=data,
                    file_name=(
                        f"kling_{task_id}.mp4"
                    ),
                    mime="video/mp4",
                    key=f"download_button_{key}",
                    use_container_width=True,
                )

        elif status == "succeeded":
            st.warning(
                "Kling reports this video as "
                "completed, but no video URL "
                "was returned."
            )

        elif status in (
            "failed",
            "canceled",
            "cancelled",
        ):
            st.warning(
                "This task did not complete."
            )

        else:
            st.info(
                "Video is processing. "
                "Use Refresh Status to check."
            )


# PASSWORD LOGIN
# Uses the same APP_PASSWORD secret.
password = secret("APP_PASSWORD")

if password and not st.session_state.get(
    "unlocked", False
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


if "videos" not in st.session_state:
    st.session_state.videos = []

if "recovered_ids" not in st.session_state:
    st.session_state.recovered_ids = []

if "recovery_message" not in st.session_state:
    st.session_state.recovery_message = ""


st.title("🎬 Kling AI Movie Studio")

st.caption(
    "Generate, recover, watch, and "
    "download your Kling videos."
)

if not secret("KLING_API_KEY"):
    st.error(
        "Your existing KLING_API_KEY "
        "was not found in Streamlit Secrets."
    )
    st.stop()


generate_tab, videos_tab, recover_tab = st.tabs([
    "🎬 Generate",
    "📹 Videos",
    "♻️ Recover",
])


# -----------------------------------------
# TAB 1: GENERATE
# -----------------------------------------

with generate_tab:
    st.subheader("Generate Video")

    with st.form(
        "generate_form",
        clear_on_submit=False,
    ):
        uploaded = st.file_uploader(
            "Upload image",
            type=[
                "png",
                "jpg",
                "jpeg",
                "webp",
            ],
        )

        prompt = st.text_area(
            "Video prompt",
            height=150,
            placeholder=(
                "Describe what happens "
                "in your video..."
            ),
        )

        duration = st.selectbox(
            "Duration",
            [5, 10],
            index=0,
        )

        aspect = st.selectbox(
            "Aspect ratio",
            ["9:16", "16:9", "1:1"],
            index=0,
        )

        st.caption(
            "Generate creates a paid Kling task. "
            "Each submission may use credits."
        )

        submitted = st.form_submit_button(
            "✨ Generate Video",
            type="primary",
            use_container_width=True,
        )

    if uploaded:
        st.image(
            uploaded.getvalue(),
            caption="Uploaded image",
            use_container_width=True,
        )

    if submitted:
        if uploaded is None:
            st.error("Upload an image first.")

        elif not prompt.strip():
            st.error("Enter a prompt first.")

        elif uploaded.size > 30 * 1024 * 1024:
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
                f"data:{mime};base64,{encoded}"
            )

            # ORIGINAL KLING 4.0 PAYLOAD
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
                    "Submitting video to Kling..."
                ):
                    data = kling_request(
                        "POST",
                        MODEL_PATH,
                        payload,
                    )

                if not isinstance(data, dict):
                    raise RuntimeError(
                        "Kling did not return "
                        "a valid task."
                    )

                task_id = task_id_of(data)

                if not task_id:
                    raise RuntimeError(
                        "Kling did not return "
                        "a task ID."
                    )

                add_or_update_tasks([{
                    **data,
                    "id": task_id,
                    "prompt": prompt.strip(),
                }])

                st.success(
                    "Video submitted! "
                    "Open the Videos tab."
                )

                st.code(task_id)

            except Exception as exc:
                st.error(str(exc))


# -----------------------------------------
# TAB 2: CURRENT VIDEOS
# -----------------------------------------

with videos_tab:
    st.subheader("Your Videos")

    if st.button(
        "🔄 Refresh Video Status",
        key="refresh_current",
        use_container_width=True,
    ):
        try:
            with st.spinner(
                "Checking Kling..."
            ):
                count = refresh_videos()

            st.success(
                f"Checked {count} task(s)."
            )

        except Exception as exc:
            st.error(str(exc))

    if not st.session_state.videos:
        st.info(
            "Videos generated or recovered "
            "during this session will "
            "appear here."
        )

    for index, video in enumerate(
        st.session_state.videos,
        1,
    ):
        render_video(
            video,
            index,
            "current",
        )


# -----------------------------------------
# TAB 3: RECOVER KLING VIDEOS
# -----------------------------------------

with recover_tab:
    st.subheader(
        "Recover Videos From Kling"
    )

    st.write(
        "Retrieve older videos using "
        "your existing Kling API key. "
        "Recovery does not generate "
        "new videos."
    )

    st.markdown(
        "#### 1. Find Available Kling Tasks"
    )

    st.caption(
        "This attempts to list existing "
        "tasks from the original Kling "
        "API server. Some accounts or "
        "API versions may not support "
        "listing complete history."
    )

    if st.button(
        "🔍 Find My Kling Videos",
        type="primary",
        key="find_history",
        use_container_width=True,
    ):
        try:
            with st.spinner(
                "Checking Kling task history..."
            ):
                data = kling_request(
                    "GET",
                    "/tasks",
                )

                tasks = parse_tasks(data)

                added, updated = (
                    add_or_update_tasks(tasks)
                )

                ids = [
                    task_id_of(task)
                    for task in tasks
                    if isinstance(task, dict)
                ]

                st.session_state.recovered_ids = (
                    list(dict.fromkeys(
                        st.session_state.recovered_ids
                        + [x for x in ids if x]
                    ))
                )

            if tasks:
                st.success(
                    f"Found {len(tasks)} tasks. "
                    f"Added {added} new videos."
                )
            else:
                st.warning(
                    "Kling did not return "
                    "a task list. Try recovering "
                    "by task ID or project JSON "
                    "below."
                )

        except Exception as exc:
            st.warning(
                "Kling's task-history listing "
                "was unavailable."
            )
            st.caption(str(exc))

    st.divider()

    st.markdown(
        "#### 2. Recover by Task ID"
    )

    manual_ids = st.text_area(
        "Paste Kling task IDs",
        placeholder=(
            "Paste one or more task IDs. "
            "Separate with commas or "
            "new lines."
        ),
        height=110,
    )

    if st.button(
        "🔎 Recover These Videos",
        key="recover_manual",
        use_container_width=True,
    ):
        ids = extract_manual_ids(
            manual_ids
        )

        if not ids:
            st.error(
                "Enter at least one task ID."
            )

        else:
            try:
                with st.spinner(
                    "Retrieving videos..."
                ):
                    tasks = lookup_tasks(ids)

                    added, updated = (
                        add_or_update_tasks(tasks)
                    )

                    st.session_state.recovered_ids = (
                        list(dict.fromkeys(
                            st.session_state.recovered_ids
                            + ids
                        ))
                    )

                st.success(
                    f"Found {len(tasks)} tasks. "
                    f"Added {added} new videos."
                )

                if len(tasks) < len(ids):
                    st.warning(
                        "Some task IDs were not "
                        "returned by Kling."
                    )

            except Exception as exc:
                st.error(str(exc))

    st.divider()

    st.markdown(
        "#### 3. Import Old Project JSON"
    )

    project_file = st.file_uploader(
        "Upload an older project JSON file",
        type=["json"],
        key="project_import",
    )

    if project_file is not None:
        if st.button(
            "📂 Recover From Project",
            key="recover_json",
            use_container_width=True,
        ):
            try:
                project = json.loads(
                    project_file.getvalue()
                    .decode("utf-8")
                )

                ids = extract_task_ids(
                    project
                )

                if not ids:
                    st.warning(
                        "No task IDs were found "
                        "in this project file."
                    )

                else:
                    with st.spinner(
                        "Checking saved tasks..."
                    ):
                        tasks = lookup_tasks(ids)

                        added, updated = (
                            add_or_update_tasks(tasks)
                        )

                        st.session_state.recovered_ids = (
                            list(dict.fromkeys(
                                st.session_state.recovered_ids
                                + ids
                            ))
                        )

                    st.success(
                        f"Found {len(tasks)} tasks "
                        f"from {len(ids)} IDs. "
                        f"Added {added} new videos."
                    )

            except Exception as exc:
                st.error(
                    f"Could not import project: {exc}"
                )

    st.divider()

    st.markdown(
        "#### 4. Recovered Videos"
    )

    recovered = [
        video
        for video in st.session_state.videos
        if video["task_id"]
        in st.session_state.recovered_ids
    ]

    if st.button(
        "🔄 Refresh Recovered Videos",
        key="refresh_recovered",
        use_container_width=True,
    ):
        try:
            with st.spinner(
                "Refreshing..."
            ):
                refresh_videos()

            st.success(
                "Video status refreshed."
            )

        except Exception as exc:
            st.error(str(exc))

    if not recovered:
        st.info(
            "Recovered videos will "
            "appear here."
        )

    for index, video in enumerate(
        recovered,
        1,
    ):
        render_video(
            video,
            index,
            "recovered",
        )

    st.divider()

    st.markdown(
        "#### Save Your Task IDs"
    )

    all_ids = list(dict.fromkeys(
        video["task_id"]
        for video in st.session_state.videos
    ))

    if all_ids:
        backup = {
            "task_ids": all_ids,
            "videos": st.session_state.videos,
        }

        st.download_button(
            "💾 Download Recovery Backup",
            data=json.dumps(
                backup,
                indent=2,
            ),
            file_name=(
                "kling_video_recovery.json"
            ),
            mime="application/json",
            use_container_width=True,
        )

        st.caption(
            "Keep this backup. It contains "
            "your task IDs so you can "
            "recover them after the "
            "Streamlit session resets."
        )


st.divider()

st.caption(
    "Kling AI Movie Studio | "
    "Original one-key Kling API connection"
)

st.caption(
    "Important: Kling video links may expire. "
    "Download completed videos promptly. "
    "This app keeps its working list in "
    "the current Streamlit session; "
    "use the recovery backup to preserve "
    "task IDs."
)
