import base64
import json
from urllib.request import Request, urlopen

import streamlit as st

REPOSITORY = "jampmediausa-commits/kling-ai-movie-studio"
ORIGINAL_BLOB = "5b140bd41d1ff7424a6241ce9956fd11a0fd2dba"


@st.cache_data(ttl=3600, show_spinner=False)
def load_original():
    url = (
        f"https://api.github.com/repos/"
        f"{REPOSITORY}/git/blobs/{ORIGINAL_BLOB}"
    )

    request = Request(
        url,
        headers={"User-Agent": "Kling-Movie-Studio"}
    )

    with urlopen(request, timeout=30) as response:
        payload = json.load(response)

    return base64.b64decode(
        payload["content"]
    ).decode("utf-8")


def indent_block(block):
    return "".join(
        "    " + line if line.strip() else line
        for line in block.splitlines(keepends=True)
    )


def restore_tabs(source):
    markers = [
        "with st.expander('✨ AI Director",
        "st.subheader('Reference image library')",
        "st.subheader('Storyboard')",
        "st.subheader('Generate & review')",
        "st.subheader('Completed video gallery')",
    ]

    positions = [
        source.find(marker)
        for marker in markers
    ]

    if (
        any(pos < 0 for pos in positions)
        or positions != sorted(positions)
    ):
        raise RuntimeError(
            "The saved app structure did not match "
            "the expected version."
        )

    prefix = source[:positions[0]]

    divider = "st.divider()\n"

    if prefix.endswith(divider):
        prefix = prefix[:-len(divider)]

    tabs = (
        "tab_ai, tab_story, tab_images, "
        "tab_generate, tab_history = st.tabs("
        "['✨ AI Director', "
        "'🎞️ Storyboard', "
        "'🖼️ Reference Images', "
        "'🎥 Generate & Review', "
        "'📁 History & Gallery'])\n"
    )

    sections = []

    names = [
        "ai",
        "images",
        "story",
        "generate",
        "history",
    ]

    for index, name in enumerate(names):
        end = (
            positions[index + 1]
            if index + 1 < len(names)
            else len(source)
        )

        body = source[positions[index]:end]

        trailing_divider = "\nst.divider()\n"

        if body.endswith(trailing_divider):
            body = (
                body[:-len(trailing_divider)]
                + "\n"
            )

        sections.append(
            f"\nwith tab_{name}:\n"
            + indent_block(body)
        )

    result = prefix + tabs + "".join(sections)

    old = (
        "    if p['images']:\n"
        "        st.caption('Available: ' "
        "+ ', '.join(p['images'].keys()))"
    )

    new = old + (
        "\n        gallery_cols = st.columns(3)"
        "\n        for image_index, "
        "(image_name, image_data) "
        "in enumerate(p['images'].items()):"
        "\n            with gallery_cols[image_index % 3]:"
        "\n                st.image("
        "image_data, "
        "caption=image_name, "
        "use_container_width=True)"
    )

    if old not in result:
        raise RuntimeError(
            "Reference image library could not be located."
        )

    result = result.replace(old, new, 1)

    history_gallery = (
        "with tab_history:\n"
        "    st.subheader("
        "'Reference images in current project')\n"
        "    if p['images']:\n"
        "        gallery_cols = st.columns(3)\n"
        "        for image_index, "
        "(image_name, image_data) "
        "in enumerate(p['images'].items()):\n"
        "            with gallery_cols[image_index % 3]:\n"
        "                st.image("
        "image_data, "
        "caption=image_name, "
        "use_container_width=True)\n"
        "    else:\n"
        "        st.info("
        "'No images in this session. "
        "Load your saved project JSON to restore them.')\n"
    )

    result = result.replace(
        "with tab_history:\n",
        history_gallery,
        1,
    )

    compile(
        result,
        "restored_movie_studio.py",
        "exec",
    )

    return result


try:
    restored_app = restore_tabs(
        load_original()
    )

except Exception as exc:
    st.error(
        f"Could not load the saved "
        f"Movie Studio code: {exc}"
    )
    st.stop()


exec(
    compile(
        restored_app,
        "restored_movie_studio.py",
        "exec",
    ),
    globals(),
)
