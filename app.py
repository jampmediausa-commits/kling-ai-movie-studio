import base64
import io
import json
import time
import zipfile
from copy import deepcopy

import requests
import streamlit as st

st.set_page_config(page_title='Kling AI Movie Studio', page_icon='🎬', layout='wide')
BASE = 'https://api-singapore.klingai.com'
MODEL = 'kling-4.0'


def secret(name):
    try:
        return str(st.secrets.get(name, '')).strip()
    except Exception:
        return ''


def blank_shot(n):
    return {'id': n, 'title': f'Shot {n:03d}', 'prompt': '', 'duration': 3,
            'resolution': '720p', 'aspect_ratio': '16:9', 'image_name': '',
            'task_id': '', 'status': 'draft', 'video_url': '', 'approved': False,
            'error': ''}


def new_project():
    return {'name': 'Untitled Movie', 'shots': [blank_shot(1)], 'images': {},
            'mode': 'Review & Approve', 'batch_active': False, 'review_active': False}


def reset_runtime():
    st.session_state.project = new_project()
    st.session_state.selected = 0
    st.session_state.confirm_paid = False


def get_project():
    if 'project' not in st.session_state:
        reset_runtime()
    return st.session_state.project


def normalize_project(obj):
    if not isinstance(obj, dict):
        raise ValueError('Not a project file')
    p = new_project()
    p['name'] = str(obj.get('name', p['name']))
    p['images'] = obj.get('images', {}) if isinstance(obj.get('images'), dict) else {}
    p['mode'] = obj.get('mode', p['mode']) if obj.get('mode') in ['Review & Approve', 'Batch'] else 'Review & Approve'
    p['shots'] = []
    for i, raw in enumerate(obj.get('shots', []), 1):
        if not isinstance(raw, dict):
            continue
        shot = blank_shot(i)
        for key in shot:
            if key in raw:
                shot[key] = raw[key]
        shot['id'] = i
        shot['duration'] = max(3, min(30, int(shot['duration'])))
        p['shots'].append(shot)
    if not p['shots']:
        p['shots'] = [blank_shot(1)]
    return p


def api(method, path, payload=None):
    key = secret('KLING_API_KEY')
    if not key:
        raise RuntimeError('KLING_API_KEY is missing from Streamlit Secrets')
    headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
    try:
        response = requests.request(method, BASE + path, headers=headers,
                                    json=payload, timeout=45)
        response.raise_for_status()
        result = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f'Kling connection error: {exc}') from exc
    except ValueError as exc:
        raise RuntimeError('Kling returned an unreadable response') from exc
    if str(result.get('code', 0)) not in ('0', '200'):
        raise RuntimeError(str(result.get('message') or result.get('msg') or result))
    return result.get('data')


def submit(shot, p):
    if shot['task_id'] and shot['status'] in ('submitted', 'processing', 'succeeded'):
        raise RuntimeError('This shot already has a task. Use Retry only if you want a new paid generation.')
    if not shot['prompt'].strip():
        raise RuntimeError(f"{shot['title']}: enter a video prompt first")
    contents = [{'type': 'prompt', 'text': shot['prompt']}]
    image_name = shot.get('image_name', '')
    if image_name:
        if image_name not in p['images']:
            raise RuntimeError(f'{shot["title"]}: assigned reference image is missing')
        contents.append({'type': 'first_frame', 'url': p['images'][image_name], 'id': 'image_1'})
    payload = {'contents': contents, 'settings': {
        'resolution': shot['resolution'], 'duration': int(shot['duration']),
        'aspect_ratio': shot['aspect_ratio']}}
    data = api('POST', '/omni-video/kling-4.0', payload)
    if not isinstance(data, dict) or not data.get('id'):
        raise RuntimeError(f'Kling did not return a task ID: {data}')
    shot.update(task_id=str(data['id']), status=str(data.get('status', 'submitted')),
                video_url='', approved=False, error='')


def check_tasks(p):
    active = [s for s in p['shots'] if s.get('task_id') and s['status'] not in ('succeeded', 'failed', 'canceled')]
    if not active:
        return 0
    count = 0
    for offset in range(0, len(active), 20):
        group = active[offset:offset + 20]
        ids = ','.join(s['task_id'] for s in group)
        data = api('GET', '/tasks?task_ids=' + ids)
        tasks = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        by_id = {str(t.get('id')): t for t in tasks if isinstance(t, dict)}
        for shot in group:
            t = by_id.get(shot['task_id'])
            if not t:
                continue
            shot['status'] = str(t.get('status', shot['status'])).lower()
            if shot['status'] in ('failed', 'canceled'):
                shot['error'] = str(t.get('message') or t.get('error') or 'Task failed')
            if shot['status'] == 'succeeded':
                outputs = t.get('outputs') or []
                if isinstance(outputs, list):
                    for output in outputs:
                        if isinstance(output, dict) and output.get('type') == 'video':
                            shot['video_url'] = str(output.get('url', ''))
                            break
            count += 1
    return count


def next_review_shot(p):
    for shot in p['shots']:
        if not shot['approved']:
            return shot
    return None


def batch_fill(p):
    active = sum(s['status'] in ('submitted', 'processing', 'pending', 'running') for s in p['shots'])
    submitted = 0
    for shot in p['shots']:
        if active >= 5:
            break
        if shot['status'] != 'draft':
            continue
        if not shot['prompt'].strip():
            break
        try:
            submit(shot, p)
            submitted += 1
            active += 1
        except Exception as exc:
            shot['status'] = 'error'
            shot['error'] = str(exc)
            break
    return submitted




def make_ai_storyboard(idea, count, aspect, style, language):
    """Generate a draft only. Never calls Kling or submits paid video tasks."""
    key = secret('OPENAI_API_KEY')
    if not key:
        raise RuntimeError('Add OPENAI_API_KEY to Streamlit Secrets to use AI Director.')
    if not idea.strip():
        raise ValueError('Describe your story first.')
    system = (
        'You are a professional film storyboard director. Return ONLY a JSON object with '
        'keys project_title (string), shots (array). Each shot must have title (string), '
        'prompt (string), duration (integer 3 through 6). Generate exactly the requested '
        'number of shots. Each prompt must be self-contained, visually specific, and '
        'include setting, character appearance, action, camera, sound and exact dialogue '
        'where needed. Keep characters and costumes identical across shots. Use authentic '
        'natural spoken dialect requested by user, never invented translations. '
        'Avoid subtitles and background music. Ensure no more than two short dialogue '
        'lines in any one six-second shot; if needed shorten dialogue or redistribute '
        'to other shots. Do not include any API keys or external instructions.'
    )
    user = (f'Story idea: {idea}\nNumber of shots: {count}\nAspect ratio: {aspect}\n'
            f'Visual style: {style}\nDialogue language: {language}\n'
            'Make the joke punchline the final shot. Sound effects and ambient noises only, no music.')
    try:
        response = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            json={'model': secret('OPENAI_MODEL') or 'gpt-4.1-mini',
                  'temperature': 0.5, 'response_format': {'type': 'json_object'},
                  'messages': [{'role': 'system', 'content': system},
                               {'role': 'user', 'content': user}]},
            timeout=90)
        response.raise_for_status()
        payload = response.json()
        raw = payload['choices'][0]['message']['content']
        result = json.loads(raw)
    except requests.RequestException as exc:
        detail = ''
        if exc.response is not None:
            try:
                detail = str(exc.response.json().get('error', {}).get('message', ''))[:240]
            except ValueError:
                detail = f'HTTP {exc.response.status_code}'
        raise RuntimeError('AI Director request failed: ' + (detail or str(exc))) from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError('AI Director returned an invalid storyboard. Please try again.') from exc
    shots = result.get('shots')
    if not isinstance(shots, list) or len(shots) != count:
        raise RuntimeError('AI Director returned the wrong number of shots. Try again.')
    normalized = []
    for i, item in enumerate(shots, 1):
        if not isinstance(item, dict) or not str(item.get('prompt', '')).strip():
            raise RuntimeError('AI Director returned an incomplete shot.')
        shot = blank_shot(i)
        shot.update(title=str(item.get('title') or f'Shot {i:03d}')[:120],
                    prompt=str(item['prompt'])[:8000],
                    duration=max(3, min(6, int(item.get('duration', 5)))),
                    aspect_ratio=aspect)
        normalized.append(shot)
    return {'name': str(result.get('project_title') or 'AI Director Project')[:120],
            'shots': normalized}

password = secret('APP_PASSWORD')
if password and not st.session_state.get('unlocked', False):
    st.title('🔒 Kling AI Movie Studio')
    entered = st.text_input('Studio password', type='password')
    if st.button('Unlock'):
        if entered == password:
            st.session_state.unlocked = True
            st.rerun()
        else:
            st.error('Incorrect password')
    st.stop()

p = get_project()
st.title('🎬 Kling AI Movie Studio')
st.caption('Kling 4.0 • Storyboards • Review & Approve • Batch generation')
if not secret('KLING_API_KEY'):
    st.error('Kling API key not configured. Add KLING_API_KEY in Streamlit Secrets.')

with st.sidebar:
    st.header('Project')
    p['name'] = st.text_input('Movie title', value=p['name'])
    p['mode'] = st.radio('Generation mode', ['Review & Approve', 'Batch'],
                         index=0 if p['mode'] == 'Review & Approve' else 1)
    st.info('Project progress is held in this browser session. Download your project JSON regularly; uploads and progress are not stored permanently on the server.')
    project_bytes = json.dumps(p, ensure_ascii=False, indent=2).encode('utf-8')
    st.download_button('💾 Save project JSON', project_bytes,
                       file_name='kling_movie_project.json', mime='application/json', use_container_width=True)
    upload_project = st.file_uploader('Load saved project JSON', type=['json'])
    if upload_project is not None and st.button('Load this project', use_container_width=True):
        try:
            st.session_state.project = normalize_project(json.loads(upload_project.getvalue()))
            st.session_state.selected = 0
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
    if st.button('New empty project', use_container_width=True):
        reset_runtime()
        st.rerun()
    if password and st.button('Lock studio'):
        st.session_state.unlocked = False
        st.rerun()

st.divider()
with st.expander('✨ AI Director — Turn an idea into a storyboard', expanded=True):
    st.caption('Creates draft prompts using the OpenAI API. This may incur OpenAI API charges, but does NOT submit Kling video generations.')
    idea = st.text_area('Describe your movie or joke', value=(
        'A classic Syrian Homsi joke: Abu Abdo tries to pull his stubborn donkey along '
        'an old Homs village alley. His friend Abu Mahmoud says: "ولك اضربه شوي ليمشي!" '
        'Abu Abdo says: "ما فيني، هاد متل أخي!" His friend asks: "لهالدرجة بتحبه؟" '
        'Abu Abdo deadpans: "لأ، أخي كمان ما بيشتغل إلا بالضرب!" '
        'Make the punchline land with a donkey bray and a stunned reaction.'), height=135)
    director_cols = st.columns(4)
    director_count = director_cols[0].number_input('Shots', min_value=1, max_value=20, value=4)
    director_aspect = director_cols[1].selectbox('Format', ['9:16', '16:9', '1:1'])
    director_style = director_cols[2].selectbox('Visual style', ['Photorealistic Syrian comedy', '3D animation', '2D cartoon'])
    director_language = director_cols[3].selectbox('Dialogue', ['Natural Syrian Arabic (Homsi dialect)', 'English', 'Modern Standard Arabic'])
    if st.button('✨ Draft storyboard with AI', use_container_width=True):
        try:
            with st.spinner('Writing storyboard...'):
                st.session_state.ai_draft = make_ai_storyboard(
                    idea, int(director_count), director_aspect, director_style, director_language)
            st.success('Storyboard drafted. Review it below before applying.')
        except Exception as exc:
            st.error(str(exc))
    draft = st.session_state.get('ai_draft')
    if draft:
        st.markdown('**Draft project:** ' + draft['name'])
        for i, draft_shot in enumerate(draft['shots'], 1):
            with st.expander(f"Draft {i:03d} — {draft_shot['title']}"):
                st.caption(f"{draft_shot['duration']} seconds • {draft_shot['aspect_ratio']}")
                st.text(draft_shot['prompt'])
        st.warning('Applying replaces the current storyboard. Save your current project JSON first if you want to keep it. No Kling videos will be generated.')
        replace_ack = st.checkbox('I understand this replaces my current storyboard', key='ai_replace_ack')
        if st.button('Use this storyboard in Movie Studio', disabled=not replace_ack, use_container_width=True):
            st.session_state.project = new_project()
            st.session_state.project['name'] = draft['name']
            st.session_state.project['shots'] = deepcopy(draft['shots'])
            st.session_state.selected = 0
            st.session_state.confirm_paid = False
            st.session_state.ai_draft = None
            st.session_state.editor_revision = st.session_state.get('editor_revision', 0) + 1
            st.rerun()

st.subheader('Reference image library')
st.caption('Images uploaded here are FIRST FRAMES for shots, not a persistent character identity model. Create a full scene image for each shot for best consistency.')
images_upload = st.file_uploader('Add scene/reference images', type=['png', 'jpg', 'jpeg', 'webp'], accept_multiple_files=True)
if images_upload and st.button('Add uploaded images to library'):
    added = 0
    for file in images_upload:
        raw = file.getvalue()
        if len(raw) > 30 * 1024 * 1024:
            st.warning(f'{file.name}: exceeds 30 MB, skipped')
            continue
        mime = file.type or 'image/png'
        p['images'][file.name] = 'data:' + mime + ';base64,' + base64.b64encode(raw).decode('ascii')
        added += 1
    st.success(f'{added} image(s) added')
if p['images']:
    st.caption('Available: ' + ', '.join(p['images'].keys()))

st.divider()
st.subheader('Storyboard')
cols = st.columns([1, 1, 2])
if cols[0].button('➕ Add shot', use_container_width=True):
    p['shots'].append(blank_shot(len(p['shots']) + 1))
    st.session_state.selected = len(p['shots']) - 1
    st.rerun()
if cols[1].button('➕ Add 10 shots', use_container_width=True):
    for _ in range(10):
        p['shots'].append(blank_shot(len(p['shots']) + 1))
    st.rerun()
counts = {s: sum(x['status'] == s for x in p['shots']) for s in ['draft', 'submitted', 'processing', 'succeeded', 'failed']}
cols[2].caption(f"{len(p['shots'])} total • {sum(x['approved'] for x in p['shots'])} approved • {counts['succeeded']} succeeded")

options = [f"{i+1:03d} | {s['title']} | {s['status']}{' ✓' if s['approved'] else ''}" for i, s in enumerate(p['shots'])]
selected = min(st.session_state.get('selected', 0), len(p['shots']) - 1)
selection = st.selectbox('Choose shot to edit or review', range(len(options)),
                         index=selected, format_func=lambda i: options[i])
st.session_state.selected = selection
shot = p['shots'][selection]
shot['title'] = st.text_input('Shot title', value=shot['title'], key=f'title_{st.session_state.get("editor_revision", 0)}_{selection}')
shot['prompt'] = st.text_area('Video prompt', value=shot['prompt'], height=140, key=f'prompt_{st.session_state.get("editor_revision", 0)}_{selection}')
a, b, c = st.columns(3)
shot['duration'] = a.selectbox('Duration (seconds)', list(range(3, 31)),
                               index=max(0, min(27, int(shot['duration']) - 3)), key=f'dur_{st.session_state.get("editor_revision", 0)}_{selection}')
shot['resolution'] = b.selectbox('Resolution', ['720p', '1080p'],
                                 index=0 if shot['resolution'] == '720p' else 1, key=f'res_{st.session_state.get("editor_revision", 0)}_{selection}')
ratios = ['16:9', '9:16', '1:1', '21:9', 'adaptive']
shot['aspect_ratio'] = c.selectbox('Aspect ratio', ratios,
                                   index=ratios.index(shot['aspect_ratio']) if shot['aspect_ratio'] in ratios else 0,
                                   key=f'ratio_{st.session_state.get("editor_revision", 0)}_{selection}')
image_choices = [''] + list(p['images'].keys())
shot['image_name'] = st.selectbox('First-frame image (optional)', image_choices,
                                  index=image_choices.index(shot['image_name']) if shot['image_name'] in image_choices else 0,
                                  format_func=lambda x: x or 'No image — text to video', key=f'img_{st.session_state.get("editor_revision", 0)}_{selection}')
if shot['image_name']:
    st.image(p['images'][shot['image_name']], width=280)

st.write(f"**Status:** {shot['status']}" + (' • Approved ✓' if shot['approved'] else ''))
if shot['task_id']:
    st.caption('Task ID: ' + shot['task_id'])
if shot['error']:
    st.error(shot['error'])
if shot['video_url']:
    st.video(shot['video_url'])
    st.link_button('⬇️ Open/download video', shot['video_url'])

st.divider()
st.subheader('Generate & review')
st.warning('Video generation is a paid API request. Costs vary by model/settings. Estimates below are planning figures, not a guarantee of Kling billing. Check Kling pricing before large batches.')
ready = [s for s in p['shots'] if s['status'] == 'draft' and s['prompt'].strip()]
st.write(f'**Ready drafts:** {len(ready)} | **Total draft duration:** {sum(s["duration"] for s in ready)} seconds')
st.caption('At an illustrative rate of 1 unit/second, those drafts would use ' + str(sum(s['duration'] for s in ready)) + ' units. Higher resolutions or other settings may cost more.')

if st.button('🔄 Check all task statuses', use_container_width=True):
    try:
        updated = check_tasks(p)
        st.success(f'Checked {updated} task(s)')
        if p['mode'] == 'Batch' and p['batch_active']:
            submitted = batch_fill(p)
            if submitted:
                st.info(f'Submitted {submitted} more shot(s)')
        st.rerun()
    except Exception as exc:
        st.error(str(exc))

if p['mode'] == 'Review & Approve':
    current = next_review_shot(p)
    if current is None:
        st.success('All shots approved!')
    else:
        idx = p['shots'].index(current)
        st.info(f"Next approval required: Shot {idx + 1:03d} — {current['title']} ({current['status']})")
        if current['status'] == 'draft':
            if st.button('🎥 Generate next unapproved shot (paid)', use_container_width=True):
                st.session_state.confirm_paid = True
            if st.session_state.get('confirm_paid'):
                st.write(f"Confirm paid generation: **{current['title']}**, {current['duration']}s, {current['resolution']}")
                x, y = st.columns(2)
                if x.button('Confirm & submit', type='primary'):
                    st.session_state.confirm_paid = False
                    try:
                        submit(current, p)
                        st.success('Task submitted')
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
                if y.button('Cancel'):
                    st.session_state.confirm_paid = False
                    st.rerun()
        elif current['status'] == 'succeeded':
            if current['video_url']:
                st.video(current['video_url'])
            if st.button('✅ Approve & generate next shot', type='primary', use_container_width=True):
                current['approved'] = True
                nxt = next_review_shot(p)
                if nxt is not None and nxt['status'] == 'draft':
                    try:
                        submit(nxt, p)
                        st.success('Approved; next paid shot submitted')
                    except Exception as exc:
                        st.error('Approved, but next shot not submitted: ' + str(exc))
                st.rerun()
        elif current['status'] in ('submitted', 'processing', 'pending', 'running'):
            st.info('Wait for this shot to finish. Tap Check all task statuses to refresh.')
        else:
            st.info('This shot needs attention. Edit it and use Retry below if needed.')
else:
    if not p['batch_active']:
        if st.button('🎥 Start batch (paid requests, max 5 at a time)', use_container_width=True):
            st.session_state.confirm_paid = True
        if st.session_state.get('confirm_paid'):
            st.write(f"Ready to submit up to 5 shots now; {len(ready)} draft shots are ready in total. Each successful submission costs credits.")
            x, y = st.columns(2)
            if x.button('Confirm & start batch', type='primary'):
                st.session_state.confirm_paid = False
                p['batch_active'] = True
                n = batch_fill(p)
                st.success(f'Submitted {n} shot(s). Use Check all task statuses to advance the queue.')
                st.rerun()
            if y.button('Cancel batch start'):
                st.session_state.confirm_paid = False
                st.rerun()
    else:
        st.info('Batch queue active. Each time you press Check all task statuses, finished tasks free slots and the next drafts are submitted.')
        if st.button('⏸ Pause batch (does not cancel running tasks)'):
            p['batch_active'] = False
            st.rerun()

st.divider()
st.subheader('Shot controls')
if shot['status'] in ('failed', 'error', 'canceled', 'succeeded'):
    st.caption('Retry creates a NEW paid video request. The previous task/video link will be replaced in this project. Save your project or download the old clip first.')
    if st.checkbox('I understand retry uses more credits', key=f'retry_ack_{selection}'):
        if st.button('🔁 Reset this shot to draft for retry'):
            shot.update(task_id='', status='draft', video_url='', approved=False, error='')
            st.rerun()
if len(p['shots']) > 1 and st.checkbox('Enable delete selected shot', key='delete_ack'):
    if st.button('🗑️ Delete selected shot'):
        p['shots'].pop(selection)
        for i, s in enumerate(p['shots'], 1):
            s['id'] = i
        st.session_state.selected = 0
        st.rerun()

st.divider()
st.subheader('Completed video gallery')
completed = [s for s in p['shots'] if s['status'] == 'succeeded' and s['video_url']]
for s in completed:
    with st.expander(f"{s['title']} {'✓ Approved' if s['approved'] else '• Awaiting approval'}"):
        st.video(s['video_url'])
        st.link_button('Open video / download', s['video_url'])

st.caption('Kling video URLs may expire (often within 30 days). Download completed clips promptly. This app cannot guarantee persistent storage or automatic video downloads.')
