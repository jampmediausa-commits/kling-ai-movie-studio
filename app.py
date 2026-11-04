
import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime

import requests
import streamlit as st

try:
    from streamlit_cookies_controller import CookieController
except ImportError:
    CookieController = None


st.set_page_config(
    page_title="Kling AI Movie Studio",
    page_icon="🎬",
    layout="centered",
)

BASE = "https://api-singapore.klingai.com"
LOGIN_COOKIE = "kling_movie_remember_v1"
LOGIN_TTL = 30 * 24 * 60 * 60


# ============================================================
# SECRETS AND KLING API
# ============================================================

def secret(name):
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def api(method, path, payload=None, params=None):
    key = secret("KLING_API_KEY")
    if not key:
        raise RuntimeError("KLING_API_KEY is missing from Streamlit Secrets.")

    try:
        response = requests.request(
            method,
            BASE + path,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            params=params,
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"Kling connection error: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError("Kling returned unreadable JSON.") from exc

    if not isinstance(result, dict):
        raise RuntimeError("Unexpected Kling response.")

    if str(result.get("code", 0)) not in ("0", "200"):
        raise RuntimeError(
            str(result.get("message") or result.get("msg") or result)
        )

    return result.get("data")


# ============================================================
# SAFE COOKIE HANDLING - FIXES THE CRASH
# ============================================================

def get_cookie_controller():
    if CookieController is None:
        return None

    try:
        return CookieController(key="kling_login_cookies")
    except Exception:
        return None


cookies = get_cookie_controller()


def safe_cookie_get(name):
    if cookies is None:
        return None

    try:
        all_cookies = cookies.getAll()

        # CookieController can return None while initializing.
        if not isinstance(all_cookies, dict):
            return None

        value = all_cookies.get(name)
        return value if isinstance(value, str) else None

    except (TypeError, AttributeError, KeyError, ValueError):
        return None


def safe_cookie_set(name, value):
    if cookies is None:
        return False

    try:
        cookies.set(
            name,
            value,
            max_age=LOGIN_TTL,
            secure=True,
            same_site="strict",
            path="/",
        )
        return True
    except Exception:
        return False


def safe_cookie_remove(name):
    if cookies is None:
        return

    try:
        all_cookies = cookies.getAll()
        if isinstance(all_cookies, dict) and name in all_cookies:
            cookies.remove(name)
    except Exception:
        pass


# ============================================================
# 30-DAY LOGIN
# ============================================================

def signing_key():
    configured = secret("AUTH_SECRET")
    if configured:
        return configured.encode("utf-8")

    combined = secret("APP_PASSWORD") + ":" + secret("KLING_API_KEY")
    return hashlib.sha256(combined.encode("utf-8")).digest()


def create_login_token():
    expiry = str(int(time.time()) + LOGIN_TTL)
    nonce = secrets.token_urlsafe(18)
    message = f"{expiry}.{nonce}"

    signature = hmac.new(
        signing_key(),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return f"{message}.{signature}"


def valid_login_token(token):
    if not isinstance(token, str) or len(token) > 250:
        return False

    try:
        expiry, nonce, signature = token.split(".")

        if not expiry.isdigit() or len(nonce) < 12:
            return False

        now = int(time.time())
        if not now < int(expiry) <= now + LOGIN_TTL + 120:
            return False

        expected = hmac.new(
            signing_key(),
            f"{expiry}.{nonce}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(signature, expected)

    except (ValueError, TypeError):
        return False


password = secret("APP_PASSWORD")

if password and not st.session_state.get("unlocked", False):
    remembered = safe_cookie_get(LOGIN_COOKIE)

    if valid_login_token(remembered):
        st.session_state.unlocked = True

    else:
        st.title("🔒 Kling AI Movie Studio")

        with st.form("studio_login"):
            entered = st.text_input("Studio password", type="password")

            remember = st.checkbox(
                "Remember this device for 30 days",
                value=True,
            )

            unlock = st.form_submit_button(
                "Unlock",
                type="primary",
                use_container_width=True,
            )

        if unlock:
            if hmac.compare_digest(entered, password):
                st.session_state.unlocked = True

                if remember:
                    safe_cookie_set(LOGIN_COOKIE, create_login_token())
                else:
                    safe_cookie_remove(LOGIN_COOKIE)

                st.rerun()
            else:
                st.error("Incorrect password.")

        st.stop()


# ============================================================
# MP4 DOWNLOAD
# ============================================================

def video_download_control(url, key, filename):
    if st.button(
        "⬇️ Prepare MP4 Download",
        key=f"prepare_{key}",
        use_container_width=True,
    ):
        try:
            with st.spinner("Fetching video from Kling..."):
                response = requests.get(url, timeout=120)
                response.raise_for_status()

                if len(response.content) > 250 * 1024 * 1024:
                    st.warning("Video exceeds the 250 MB download limit.")
                else:
                    st.session_state[f"video_bytes_{key}"] = response.content

        except requests.RequestException as exc:
            st.error(f"Download failed: {exc}")

    data = st.session_state.get(f"video_bytes_{key}")

    if data:
        st.download_button(
            "💾 Download MP4 to iPhone",
            data=data,
            file_name=filename,
            mime="video/mp4",
            key=f"download_{key}",
            use_container_width=True,
        )


# ============================================================
# VIDEO STATE AND STATUS
# ============================================================

def new_video_state():
    return {
        "task_id": "",
        "status": "",
        "video_url": "",
        "error": "",
    }


if "simple_video" not in st.session_state:
    st.session_state.simple_video = new_video_state()

if "video_version" not in st.session_state:
    st.session_state.video_version = 0


def check_video_status():
    video = st.session_state.simple_video
    task_id = video.get("task_id")

    if not task_id:
        return

    data = api("GET", "/tasks", params={"task_ids": task_id})

    tasks = (
        data if isinstance(data, list)
        else [data] if isinstance(data, dict)
        else []
    )

    for task in tasks:
        if not isinstance(task, dict):
            continue

        if str(task.get("id")) != task_id:
            continue

        status = str(task.get("status", "processing")).lower()
        video["status"] = status

        if status in ("failed", "canceled"):
            video["error"] = str(
                task.get("message")
                or task.get("error")
                or "Video generation failed."
            )

        if status == "succeeded":
            for output in task.get("outputs") or []:
                if (
                    isinstance(output, dict)
                    and output.get("type") == "video"
                    and output.get("url")
                ):
                    video["video_url"] = str(output["url"])
                    break

        return


# ============================================================
# API BALANCE FUNCTIONS
# ============================================================

def account_request(method, path, payload=None, params=None):
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
            params=params,
            timeout=45,
        )

        response.raise_for_status()
        result = response.json()

    except requests.RequestException as exc:
        raise RuntimeError(
            f"Kling account request failed: {type(exc).__name__}"
        ) from exc

    except ValueError as exc:
        raise RuntimeError("Unreadable Kling account data.") from exc

    if not isinstance(result, dict):
        raise RuntimeError("Unexpected account response.")

    if str(result.get("code", 0)) not in ("0", "200"):
        raise RuntimeError(
            str(
                result.get("message")
                or result.get("msg")
                or "Kling account API error"
            )
        )

    data = result.get("data") or {}

    if not isinstance(data, dict):
        raise RuntimeError("Unexpected account data.")

    if str(data.get("code", 0)) not in ("0", "200"):
        raise RuntimeError(
            str(data.get("msg") or "Unexpected Kling account response.")
        )

    return data


def refresh_account_data(days):
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(days) * 86400 * 1000

    packages_data = account_request(
        "GET",
        "/account/costs",
        params={
            "start_time": start_ms,
            "end_time": end_ms,
        },
    )

    billing_data = account_request(
        "POST",
        "/account/billing/balance",
        payload={
            "start_time": start_ms,
            "end_time": end_ms,
            "limit": 100,
        },
    )

    result = billing_data.get("result") or {}
    if not isinstance(result, dict):
        result = {}

    details = result.get("detail") or []
    packages = packages_data.get("resource_pack_subscribe_infos") or []

    return {
        "packages": packages if isinstance(packages, list) else [],
        "details": details if isinstance(details, list) else [],
        "has_more": bool(billing_data.get("has_more")),
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ============================================================
# MAIN APP
# ============================================================

st.title("🎬 Kling AI Movie Studio")

if not secret("KLING_API_KEY"):
    st.error("Add KLING_API_KEY in Streamlit Secrets.")

with st.sidebar:
    if password and st.button("Log out of studio"):
        safe_cookie_remove(LOGIN_COOKIE)
        st.session_state.unlocked = False
        st.rerun()


generate_tab, recover_tab, balance_tab = st.tabs([
    "🎬 Generate",
    "📼 Recover",
    "💰 API Balance",
])


# ============================================================
# GENERATE TAB
# ============================================================

with generate_tab:
    st.subheader("Create a Video")

    version = st.session_state.video_version
    video = st.session_state.simple_video

    if st.button(
        "🔄 New Video",
        use_container_width=True,
        key="new_video",
    ):
        st.session_state.simple_video = new_video_state()
        st.session_state.video_version += 1

        for key in list(st.session_state.keys()):
            if key.startswith("video_bytes_generated_"):
                del st.session_state[key]

        st.rerun()

    image_file = st.file_uploader(
        "1. Upload Image",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=False,
        key=f"image_{version}",
    )

    if image_file is not None:
        st.image(
            image_file,
            caption="Your uploaded image",
            use_container_width=True,
        )

    prompt = st.text_area(
        "2. Video Prompt",
        placeholder="Describe what should happen in your video...",
        height=160,
        key=f"prompt_{version}",
    )

    can_generate = (
        image_file is not None
        and bool(prompt.strip())
        and not bool(video["task_id"])
    )

    if st.button(
        "🎬 Generate Video",
        type="primary",
        use_container_width=True,
        disabled=not can_generate,
        key=f"generate_{version}",
    ):
        try:
            raw = image_file.getvalue()

            if len(raw) > 30 * 1024 * 1024:
                raise RuntimeError("Image must be 30 MB or smaller.")

            mime = image_file.type or "image/png"

            image_data = (
                "data:"
                + mime
                + ";base64,"
                + base64.b64encode(raw).decode("ascii")
            )

            payload = {
                "contents": [
                    {
                        "type": "prompt",
                        "text": prompt.strip(),
                    },
                    {
                        "type": "first_frame",
                        "url": image_data,
                        "id": "image_1",
                    },
                ],
                "settings": {
                    "resolution": "720p",
                    "duration": 5,
                    "aspect_ratio": "9:16",
                },
            }

            with st.spinner("Submitting video to Kling..."):
                data = api("POST", "/omni-video/kling-4.0", payload)

            if not isinstance(data, dict) or not data.get("id"):
                raise RuntimeError(
                    f"Kling did not return a task ID: {data}"
                )

            st.session_state.simple_video = {
                "task_id": str(data["id"]),
                "status": str(
                    data.get("status", "submitted")
                ).lower(),
                "video_url": "",
                "error": "",
            }

            st.rerun()

        except Exception as exc:
            st.error(str(exc))

    video = st.session_state.simple_video

    if video["task_id"]:
        st.info("Video status: " + video.get("status", "submitted"))

        if video["status"] not in (
            "succeeded",
            "failed",
            "canceled",
        ):
            if st.button(
                "🔄 Check Video Status",
                use_container_width=True,
            ):
                try:
                    with st.spinner("Checking video..."):
                        check_video_status()
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

        if video["error"]:
            st.error(video["error"])

        if video["video_url"]:
            st.success("Your video is ready!")
            st.video(video["video_url"])

            st.link_button(
                "📱 Open Video to Save",
                video["video_url"],
                use_container_width=True,
            )

            video_download_control(
                video["video_url"],
                f"generated_{version}",
                "kling_video.mp4",
            )

            st.caption("Tap New Video to start another.")

        elif video["status"] == "succeeded":
            st.warning(
                "Kling reports success but no video URL was returned. "
                "Try the Recover tab."
            )


# ============================================================
# RECOVER TAB
# ============================================================

with recover_tab:
    st.divider()
    st.subheader("📼 Recover previous Kling videos")

    st.caption(
        "Read-only recovery from Kling API task history. "
        "This does not generate new videos or spend generation credits."
    )

    if "recovered_tasks" not in st.session_state:
        st.session_state.recovered_tasks = []

    if st.button(
        "🔎 Find previous video tasks (no generation charge)",
        use_container_width=True,
    ):
        try:
            collected = []
            cursor = ""
            seen = set()

            for page in range(10):
                query = {
                    "limit": 100,
                    "filters": [
                        {
                            "key": "product_type",
                            "values": ["video"],
                        }
                    ],
                    "cursor": cursor,
                }

                data = api("POST", "/tasks", query)

                if not isinstance(data, dict):
                    raise RuntimeError(
                        "Unexpected task-history response from Kling."
                    )

                results = data.get("result") or []

                for item in results:
                    if not isinstance(item, dict):
                        continue

                    task_id = str(item.get("id", ""))

                    if task_id not in seen:
                        collected.append(item)
                        seen.add(task_id)

                new_cursor = str(data.get("next_cursor") or "")

                if (
                    not data.get("has_more")
                    or not new_cursor
                    or new_cursor == cursor
                ):
                    break

                cursor = new_cursor

            st.session_state.recovered_tasks = collected

            st.success(
                f"Found {len(collected)} recent video task(s)."
            )

        except Exception as exc:
            st.error(f"Could not retrieve task history: {exc}")

    recovered = st.session_state.recovered_tasks

    if recovered:
        st.caption(
            "Download successful videos promptly. "
            "Kling may clear generated results after 30 days."
        )

        for item in recovered:
            task_id = str(item.get("id", "unknown"))
            status = str(item.get("status", "unknown")).lower()

            with st.expander(
                f"Task {task_id} — {status}",
                expanded=(status == "succeeded"),
            ):
                st.code(task_id)

                if item.get("message"):
                    st.caption(str(item["message"]))

                videos = [
                    output
                    for output in (item.get("outputs") or [])
                    if (
                        isinstance(output, dict)
                        and output.get("type") == "video"
                        and output.get("url")
                    )
                ]

                if videos:
                    for n, output in enumerate(videos, 1):
                        url = str(output["url"])

                        st.video(url)

                        st.link_button(
                            f"📱 Save to Photos — video {n}",
                            url,
                        )

                        st.caption(
                            "iPhone: open video → Share → Save Video. "
                            "Or download MP4 and save from Files."
                        )

                        video_download_control(
                            url,
                            f"recovered_{task_id}_{n}",
                            f"kling_{task_id}_{n}.mp4",
                        )

                elif status == "succeeded":
                    st.warning(
                        "Task succeeded but no downloadable "
                        "video URL was returned."
                    )


# ============================================================
# API BALANCE TAB
# ============================================================

with balance_tab:
    st.subheader("💰 Kling API Balance")

    st.caption(
        "Read-only account information. "
        "Refreshing does not generate a video."
    )

    days = st.selectbox(
        "Billing history period",
        [7, 30, 90, 365],
        index=1,
        format_func=lambda n: f"Last {n} days",
        key="balance_days",
    )

    if st.button(
        "🔄 Refresh API balance",
        use_container_width=True,
    ):
        try:
            with st.spinner("Checking Kling account..."):
                st.session_state.kling_account_data = (
                    refresh_account_data(days)
                )
                st.session_state.kling_account_days = days

        except Exception as exc:
            st.error(str(exc))

    account = st.session_state.get("kling_account_data")

    if account is None:
        st.info(
            "Tap Refresh API balance to load your account information."
        )

    else:
        st.caption(
            "Last refreshed: "
            + account["updated"]
            + " • History period: "
            + str(st.session_state.get("kling_account_days", days))
            + " days"
        )

        st.markdown("#### Resource packages")

        packs = account["packages"]

        if not packs:
            st.info(
                "No resource packages were returned for this period. "
                "This does not prove your cash balance is zero."
            )
        else:
            rows = []

            for pack in packs:
                if not isinstance(pack, dict):
                    continue

                rows.append({
                    "Package": pack.get("resource_pack_name", ""),
                    "Status": pack.get("status", ""),
                    "Remaining units": pack.get(
                        "remaining_quantity", ""
                    ),
                    "Total units": pack.get("total_quantity", ""),
                })

            if rows:
                st.dataframe(
                    rows,
                    use_container_width=True,
                    hide_index=True,
                )

        st.caption(
            "Resource-package quantities can be delayed. "
            "Units are not necessarily dollars."
        )

        st.markdown("#### Cash balance deductions")

        details = [
            item
            for item in account["details"]
            if isinstance(item, dict)
        ]

        if not details:
            st.info(
                "No cash-balance deductions were returned. "
                "A current cash balance cannot be inferred."
            )

        else:
            latest = {}

            for item in details:
                group = (
                    str(item.get("currency") or "Unknown currency"),
                    str(item.get("cash_type") or "balance"),
                )

                try:
                    timestamp = int(
                        item.get("deduction_time") or 0
                    )
                except (TypeError, ValueError):
                    timestamp = 0

                if (
                    group not in latest
                    or timestamp > latest[group][0]
                ):
                    latest[group] = (
                        timestamp,
                        item.get("balance_after_deduction"),
                    )

            for (currency, kind), (_, amount) in latest.items():
                st.metric(
                    "Latest reported balance after deduction "
                    f"({currency}, {kind})",
                    (
                        f"{amount} {currency}"
                        if amount is not None
                        else "Unavailable"
                    ),
                )

            st.caption(
                "This is the balance after the most recent "
                "returned charge, not a guaranteed live wallet balance."
            )

            rows = []

            for item in details:
                try:
                    when = datetime.fromtimestamp(
                        int(item.get("deduction_time", 0)) / 1000
                    ).strftime("%Y-%m-%d %H:%M")

                except (
                    TypeError,
                    ValueError,
                    OverflowError,
                    OSError,
                ):
                    when = ""

                rows.append({
                    "Date": when,
                    "Task ID": item.get("task_id", ""),
                    "Model": item.get("model_name", ""),
                    "Resolution": item.get("resolution", ""),
                    "Charge": item.get("deduction_amount", ""),
                    "Currency": item.get("currency", ""),
                    "Balance after": item.get(
                        "balance_after_deduction", ""
                    ),
                })

            st.dataframe(
                rows,
                use_container_width=True,
                hide_index=True,
            )

            if account["has_more"]:
                st.warning(
                    "More billing records exist. "
                    "Only the first 100 are shown."
                )

        st.caption(
            "For an authoritative live balance, "
            "also check your Kling developer console."
        )
