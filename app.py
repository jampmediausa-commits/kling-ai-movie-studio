
import base64
import time
from urllib.parse import quote

import requests
import streamlit as st


st.set_page_config(
    page_title="Kling AI Movie Studio",
    page_icon="🎬",
    layout="centered",
)

# ORIGINAL KLING API CONFIGURATION — UNCHANGED
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
        raise RuntimeError("KLING_API_KEY is missing.")

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
        result = response.json()

    except requests.RequestException as exc:
        detail = (
            exc.response.text[:500]
            if exc.response is not None
            else str(exc)
        )
        raise RuntimeError(detail) from exc

    if not isinstance(result, dict):
        raise RuntimeError("Invalid Kling response.")

    if str(result.get("code", 0)) not in ("0", "200"):
        raise RuntimeError(
            str(
                result.get("message")
                or result.get("msg")
                or result
            )
        )

    return result.get("data")


def parse_tasks(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("tasks", "items", "list", "records"):
            if isinstance(data.get(key), list):
                return data[key]

        for key in ("data", "result"):
            if isinstance(data.get(key), (dict, list)):
                return parse_tasks(data[key])

        if data.get("id") or data.get("task_id"):
            return [data]

    return []


def get_video_url(task):
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

            if url and output.get("type") in (None, "video"):
                return str(url)

    return str(
        task.get("video_url")
        or task.get("videoUrl")
        or ""
    )


def get_task_id(task):
    return str(
        task.get("id")
        or task.get("task_id")
        or ""
    ).strip()


def save_tasks(tasks):
    added = 0

    existing = {
        v["task_id"]: v
        for v in st.session_state.videos
    }

    for task in tasks:
        if not isinstance(task, dict):
            continue

        task_id = get_task_id(task)

        if not task_id:
            continue

        status = str(
            task.get("status") or "submitted"
        ).lower()

        if status in ("completed", "success"):
            status = "succeeded"

        url = get_video_url(task)

        if task_id in existing:
            video = existing[task_id]
            video["status"] = status

            if url:
                video["video_url"] = url

        else:
            video = {
                "task_id": task_id,
                "status": status,
                "video_url": url,
                "prompt": str(task.get("prompt") or ""),
                "created": time.strftime("%Y-%m-%d %H:%M"),
            }

            st.session_state.videos.append(video)
            existing[task_id] = video
            added += 1

    return added


def refresh_videos():
    pending = [
        v["task_id"]
        for v in st.session_state.videos
        if v["status"] not in (
            "succeeded", "failed", "canceled", "cancelled"
        )
        or (
            v["status"] == "succeeded"
            and not v["video_url"]
        )
    ]

    for start in range(0, len(pending), 20):
        ids = pending[start:start + 20]

        path = (
            "/tasks?task_ids="
            + quote(",".join(ids), safe=",")
        )

        data = kling_request("GET", path)
        save_tasks(parse_tasks(data))


def find_all_kling_videos():
    """
    Read-only search using the existing Kling /tasks endpoint.

    Pagination is attempted only when the API response
    explicitly supplies a next-page cursor or next page.
    An API that does not expose history cannot be bypassed.
    """
    all_tasks = []
    seen_ids = set()
    seen_cursors = set()

    path = "/tasks"

    for _ in range(100):
        data = kling_request("GET", path)
        tasks = parse_tasks(data)

        for task in tasks:
            if not isinstance(task, dict):
                continue

            task_id = get_task_id(task)

            if task_id and task_id not in seen_ids:
                seen_ids.add(task_id)
                all_tasks.append(task)

        if not isinstance(data, dict):
            break

        next_cursor = (
            data.get("next_cursor")
            or data.get("nextCursor")
        )

        if not next_cursor:
            pagination = data.get("pagination")

            if isinstance(pagination, dict):
                next_cursor = (
                    pagination.get("next_cursor")
                    or pagination.get("nextCursor")
                )

        if next_cursor:
            next_cursor = str(next_cursor)

            if next_cursor in seen_cursors:
                break

            seen_cursors.add(next_cursor)

            path = (
                "/tasks?cursor="
                + quote(next_cursor, safe="")
            )
            continue

        next_page = data.get("next_page")

        if next_page is None:
            next_page = data.get("nextPage")

        if next_page is not None:
            try:
                page = int(next_page)
            except (TypeError, ValueError):
                break

            path = f"/tasks?page={page}"
            continue

        break

    return all_tasks


def download_video(url):
    response = requests.get(
        url,
        stream=True,
        timeout=120,
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
                "Video exceeds 250 MB. "
                "Use Open Video instead."
            )

        chunks.append(chunk)

    return b"".join(chunks)


def display_video(video, index, section):
    task_id = video["task_id"]
    status = video["status"]
    url = video["video_url"]

    with st.container(border=True):
        st.markdown(f"### Video {index}")

        st.caption(f"Status: {status}")

        if video.get("prompt"):
            st.write(video["prompt"][:200])

        if url:
            st.video(url)

            st.link_button(
                "📱 Save to Photos / Open Video",
                url,
                use_container_width=True,
            )

            st.caption(
                "On iPhone, open the video and "
                "use Share → Save Video. "
                "If it opens in Files, share "
                "the MP4 from Files to Photos."
            )

            key = f"{section}_{task_id}"

            if st.button(
                "⬇️ Prepare MP4",
                key=f"prepare_{key}",
                use_container_width=True,
            ):
                try:
                    with st.spinner("Preparing MP4..."):
                        st.session_state[
                            f"mp4_{key}"
                        ] = download_video(url)

                except Exception as exc:
                    st.error(str(exc))

            video_bytes = st.session_state.get(
                f"mp4_{key}"
            )

            if video_bytes:
                st.download_button(
                    "⬇️ Download MP4",
                    data=video_bytes,
                    file_name=f"kling_{task_id}.mp4",
                    mime="video/mp4",
                    key=f"download_{key}",
                    use_container_width=True,
                )

        elif status == "succeeded":
            st.warning(
                "Kling reports completion but "
                "did not return a video URL."
            )

        elif status in ("failed", "canceled", "cancelled"):
            st.warning("This video task did not complete.")

        else:
            st.info("Video is still processing.")


# LOGIN — ORIGINAL APP_PASSWORD
password = secret("APP_PASSWORD")

if password and not st.session_state.get("unlocked", False):
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

if "history_ids" not in st.session_state:
    st.session_state.history_ids = []

if "history_searched" not in st.session_state:
    st.session_state.history_searched = False


st.title("🎬 Kling AI Movie Studio")

if not secret("KLING_API_KEY"):
    st.error("KLING_API_KEY is missing.")
    st.stop()


generate_tab, videos_tab, history_tab = st.tabs([
    "🎬 Generate",
    "📹 Videos",
    "📚 Kling History",
])


# ====================================
# TAB 1 — GENERATE
# ====================================

with generate_tab:
    st.subheader("Generate Video")

    with st.form("generate_form"):
        uploaded = st.file_uploader(
            "Upload Image",
            type=["png", "jpg", "jpeg", "webp"],
        )

        prompt = st.text_area(
            "Video Prompt",
            height=150,
            placeholder="Describe the video...",
        )

        duration = st.selectbox(
            "Duration",
            [5, 10],
        )

        aspect = st.selectbox(
            "Aspect Ratio",
            ["9:16", "16:9", "1:1"],
        )

        st.caption(
            "Generate submits a paid Kling task."
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
            st.error("Upload an image first.")

        elif not prompt.strip():
            st.error("Enter a prompt first.")

        elif uploaded.size > 30 * 1024 * 1024:
            st.error("Image exceeds 30 MB.")

        else:
            mime = uploaded.type or "image/png"

            encoded = base64.b64encode(
                uploaded.getvalue()
            ).decode("ascii")

            image_url = (
                f"data:{mime};base64,{encoded}"
            )

            # ORIGINAL GENERATION PAYLOAD
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
                with st.spinner("Submitting to Kling..."):
                    data = kling_request(
                        "POST",
                        MODEL_PATH,
                        payload,
                    )

                if not isinstance(data, dict):
                    raise RuntimeError(
                        "Kling did not return a valid task."
                    )

                task_id = get_task_id(data)

                if not task_id:
                    raise RuntimeError(
                        "Kling did not return a task ID."
                    )

                save_tasks([{
                    **data,
                    "id": task_id,
                    "prompt": prompt.strip(),
                }])

                st.success(
                    "Video submitted! "
                    "Open the Videos tab."
                )

            except Exception as exc:
                st.error(str(exc))


# ====================================
# TAB 2 — VIDEOS
# ====================================

with videos_tab:
    st.subheader("My Videos")

    if st.button(
        "🔄 Refresh Videos",
        use_container_width=True,
    ):
        try:
            with st.spinner("Checking Kling..."):
                refresh_videos()

            st.success("Video statuses refreshed.")

        except Exception as exc:
            st.error(str(exc))

    if not st.session_state.videos:
        st.info("Your videos will appear here.")

    for index, video in enumerate(
        st.session_state.videos,
        1,
    ):
        display_video(video, index, "videos")


# ====================================
# TAB 3 — KLING HISTORY
# ONE SEARCH BUTTON
# ====================================

with history_tab:
    st.subheader("My Kling Video History")

    st.write(
        "Search the Kling API for your "
        "previously generated videos."
    )

    if st.button(
        "🔍 Find All My Kling Videos",
        type="primary",
        use_container_width=True,
    ):
        try:
            with st.spinner(
                "Searching Kling video history..."
            ):
                tasks = find_all_kling_videos()

                save_tasks(tasks)

                st.session_state.history_ids = (
                    list(dict.fromkeys(
                        get_task_id(task)
                        for task in tasks
                        if get_task_id(task)
                    ))
                )

                st.session_state.history_searched = True

            if tasks:
                st.success(
                    f"Found {len(tasks)} Kling tasks."
                )
            else:
                st.warning(
                    "Kling returned no historical tasks. "
                    "This API may not expose your "
                    "account's full video history."
                )

        except Exception as exc:
            st.error(
                "Kling could not provide video history "
                "through this API endpoint."
            )
            st.caption(str(exc))

    history_ids = set(
        st.session_state.history_ids
    )

    history_videos = [
        video
        for video in st.session_state.videos
        if video["task_id"] in history_ids
    ]

    completed = [
        video
        for video in history_videos
        if video.get("video_url")
    ]

    if completed:
        st.markdown(
            f"### Available Videos ({len(completed)})"
        )

        for index, video in enumerate(
            completed,
            1,
        ):
            display_video(
                video,
                index,
                "history",
            )

    elif st.session_state.history_searched:
        st.info(
            "No downloadable videos were returned "
            "by Kling's history endpoint."
        )

    else:
        st.info(
            "Press the button above to search."
        )


st.divider()

st.caption(
    "Kling AI Movie Studio — "
    "Original KLING_API_KEY connection."
)

st.caption(
    "Video history availability depends on "
    "what Kling's API returns. Video URLs "
    "may expire, so save completed videos."
)
