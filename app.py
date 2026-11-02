import base64
import json
import time
from datetime import datetime

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
        response = requests.request(method, BASE + path, headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        result = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f'Kling connection error: {exc}') from exc
    except ValueError as exc:
        raise RuntimeError('Kling returned an unreadable response') from exc
    if str(result.get('code', 0)) not in ('0', '200'):
        raise RuntimeError(str(result.get('message') or result.get('msg') or result))
    return result.get('data')


def account_request(method, path, payload=None, params=None):
    """Read-only account request using the existing Streamlit secret."""
    key = secret('KLING_API_KEY')
    if not key:
        raise RuntimeError('KLING_API_KEY is missing from Streamlit Secrets')
    try:
        response = requests.request(method, BASE + path,
                                    headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                                    json=payload, params=params, timeout=45)
        response.raise_for_status()
        result = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f'Kling account request failed: {type(exc).__name__}') from exc
    except ValueError as exc:
        raise RuntimeError('Kling returned unreadable account data') from exc
    if not isinstance(result, dict) or str(result.get('code', 0)) not in ('0', '200'):
        raise RuntimeError(str(result.get('message') or result.get('msg') or 'Kling account API error'))
    data = result.get('data') or {}
    if not isinstance(data, dict) or str(data.get('code', 0)) not in ('0', '200'):
        raise RuntimeError(str(data.get('msg') or 'Unexpected Kling account response'))
    return data


def refresh_account_data(days):
    """Reads account package usage and balance deductions; never generates videos."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(days) * 86400 * 1000
    packages_data = account_request('GET', '/account/costs', params={
        'start_time': start_ms, 'end_time': end_ms})
    billing_data = account_request('POST', '/account/billing/balance', payload={
        'start_time': start_ms, 'end_time': end_ms, 'limit': 100})
    result = billing_data.get('result') or {}
    if not isinstance(result, dict):
        result = {}
    details = result.get('detail') or []
    packages = packages_data.get('resource_pack_subscribe_infos') or []
    return {'packages': packages if isinstance(packages, list) else [],
            'details': details if isinstance(details, list) else [],
            'has_more': bool(billing_data.get('has_more')),
            'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}


def video_download_control(url, key, filename):
    """Fetch only on explicit click; avoid repeated large downloads on reruns."""
    if st.button('⬇️ Prepare MP4 download', key=f'prepare_{key}'):
        try:
            with st.spinner('Fetching video from Kling...'):
                response = requests.get(url, timeout=120)
                response.raise_for_status()
                if len(response.content) > 250 * 1024 * 1024:
                    st.warning('Video exceeds the 250 MB in-app download limit. Use Open / save video instead.')
                else:
                    st.session_state[f'video_bytes_{key}'] = response.content
        except requests.RequestException as exc:
            st.error(f'Could not fetch video: {exc}. Try Open / save video.')
    data = st.session_state.get(f'video_bytes_{key}')
    if data:
        st.download_button('💾 Download MP4 to iPhone', data=data,
                           file_name=filename, mime='video/mp4', key=f'download_{key}')


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
st.caption('Kling 4.0 • Generate new shots or recover previous videos')
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

generate_tab, recover_tab, balance_tab = st.tabs(['🎬 Generate', '📼 Recover', '💰 API Balance'])

with generate_tab:
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
    shot['title'] = st.text_input('Shot title', value=shot['title'], key=f'title_{selection}')
    shot['prompt'] = st.text_area('Video prompt', value=shot['prompt'], height=140, key=f'prompt_{selection}')
    a, b, c = st.columns(3)
    shot['duration'] = a.selectbox('Duration (seconds)', list(range(3, 31)),
                                   index=max(0, min(27, int(shot['duration']) - 3)), key=f'dur_{selection}')
    shot['resolution'] = b.selectbox('Resolution', ['720p', '1080p'],
                                     index=0 if shot['resolution'] == '720p' else 1, key=f'res_{selection}')
    ratios = ['16:9', '9:16', '1:1', '21:9', 'adaptive']
    shot['aspect_ratio'] = c.selectbox('Aspect ratio', ratios,
                                       index=ratios.index(shot['aspect_ratio']) if shot['aspect_ratio'] in ratios else 0,
                                       key=f'ratio_{selection}')
    image_choices = [''] + list(p['images'].keys())
    shot['image_name'] = st.selectbox('First-frame image (optional)', image_choices,
                                      index=image_choices.index(shot['image_name']) if shot['image_name'] in image_choices else 0,
                                      format_func=lambda x: x or 'No image — text to video', key=f'img_{selection}')
    if shot['image_name']:
        st.image(p['images'][shot['image_name']], width=280)

    st.write(f"**Status:** {shot['status']}" + (' • Approved ✓' if shot['approved'] else ''))
    if shot['task_id']:
        st.caption('Task ID: ' + shot['task_id'])
    if shot['error']:
        st.error(shot['error'])
    if shot['video_url']:
        st.video(shot['video_url'])
        st.link_button('📱 Save to Photos (open video)', shot['video_url'])
        st.caption('On iPhone: open the video, tap Share, then Save Video. If Save Video is missing, use the MP4 download and save it to Photos from Files.')
        video_download_control(shot['video_url'], f'shot_preview_{shot["id"]}', f'shot_{shot["id"]:03d}.mp4')

    st.divider()
    st.subheader('Generate & review')
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
            st.link_button('📱 Save to Photos (open video)', s['video_url'])
            st.caption('iPhone: tap Share → Save Video after opening. If unavailable, download the MP4 and share it from Files to Photos.')
            video_download_control(s['video_url'], f'gallery_{s["id"]}', f'shot_{s["id"]:03d}.mp4')

    st.caption('Kling video URLs may expire (often within 30 days). Download completed clips promptly. This app cannot guarantee persistent storage or automatic video downloads.')

with recover_tab:
    st.divider()
    st.subheader('📼 Recover previous Kling videos')
    st.caption('Read-only recovery from Kling API task history (last 30 days). This does NOT generate new videos or spend generation credits.')
    if 'recovered_tasks' not in st.session_state:
        st.session_state.recovered_tasks = []
    if st.button('🔎 Find previous video tasks (no generation charge)'):
        try:
            collected = []
            cursor = ''
            seen = set()
            for page in range(10):
                query = {'limit': 100, 'filters': [{'key': 'product_type', 'values': ['video']}], 'cursor': cursor}
                data = api('POST', '/tasks', query)
                if not isinstance(data, dict):
                    raise RuntimeError('Unexpected task-history response from Kling')
                results = data.get('result') or []
                for item in results:
                    if isinstance(item, dict) and str(item.get('id', '')) not in seen:
                        collected.append(item)
                        seen.add(str(item.get('id', '')))
                new_cursor = str(data.get('next_cursor') or '')
                if not data.get('has_more') or not new_cursor or new_cursor == cursor:
                    break
                cursor = new_cursor
            st.session_state.recovered_tasks = collected
            st.success(f'Found {len(collected)} recent video task(s). This includes unfinished or failed tasks.')
        except Exception as exc:
            st.error(f'Could not retrieve task history: {exc}')
    recovered = st.session_state.recovered_tasks
    if recovered:
        st.caption('Download successful videos promptly; Kling says generated results are cleared after 30 days. These are separate from your current storyboard.')
        for item in recovered:
            task_id = str(item.get('id', 'unknown'))
            status = str(item.get('status', 'unknown')).lower()
            with st.expander(f'Task {task_id} — {status}', expanded=(status == 'succeeded')):
                st.code(task_id)
                if item.get('message'):
                    st.caption(str(item['message']))
                videos = [o for o in (item.get('outputs') or []) if isinstance(o, dict) and o.get('type') == 'video' and o.get('url')]
                if videos:
                    for n, output in enumerate(videos, 1):
                        url = str(output['url'])
                        st.video(url)
                        st.link_button(f'📱 Save to Photos — video {n}', url)
                        st.caption('iPhone: open video → Share → Save Video. Or download MP4 and save from Files.')
                        video_download_control(url, f'recovered_{task_id}_{n}', f'kling_{task_id}_{n}.mp4')
                elif status == 'succeeded':
                    st.warning('Task succeeded but no downloadable video URL was returned.')

with balance_tab:
    st.subheader('💰 Kling API Balance')
    st.caption('Read-only account information. Refreshing does not generate a video.')
    days = st.selectbox('Billing history period', [7, 30, 90, 365], index=1,
                        format_func=lambda n: f'Last {n} days', key='balance_days')
    if st.button('🔄 Refresh API balance', use_container_width=True):
        try:
            with st.spinner('Checking Kling account...'):
                st.session_state.kling_account_data = refresh_account_data(days)
                st.session_state.kling_account_days = days
        except Exception as exc:
            st.error(str(exc))
    account = st.session_state.get('kling_account_data')
    if account is None:
        st.info('Tap Refresh API balance to load your account information.')
    else:
        st.caption('Last refreshed: ' + account['updated'] +
                   f' • History period: {st.session_state.get("kling_account_days", days)} days')
        st.markdown('#### Resource packages')
        packs = account['packages']
        if not packs:
            st.info('No resource packages were returned for this period. This does not prove that your cash balance is zero.')
        else:
            rows = []
            for pack in packs:
                if not isinstance(pack, dict):
                    continue
                rows.append({'Package': pack.get('resource_pack_name', ''),
                             'Status': pack.get('status', ''),
                             'Remaining units': pack.get('remaining_quantity', ''),
                             'Total units': pack.get('total_quantity', '')})
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True)
        st.caption('Kling says resource-package remaining quantities can be delayed by up to 12 hours. Units are not necessarily dollars.')
        st.markdown('#### Cash balance deductions')
        details = [d for d in account['details'] if isinstance(d, dict)]
        if not details:
            st.info('No cash-balance deductions were returned for this period. A current cash balance cannot be inferred.')
        else:
            latest = {}
            for d in details:
                group = (str(d.get('currency') or 'Unknown currency'),
                         str(d.get('cash_type') or 'balance'))
                try:
                    timestamp = int(d.get('deduction_time') or 0)
                except (TypeError, ValueError):
                    timestamp = 0
                if group not in latest or timestamp > latest[group][0]:
                    latest[group] = (timestamp, d.get('balance_after_deduction'))
            for (currency, kind), (_, amount) in latest.items():
                st.metric(f'Latest reported balance after deduction ({currency}, {kind})',
                          f'{amount} {currency}' if amount is not None else 'Unavailable')
            st.caption('This is the balance after the most recent returned charge, not a guaranteed live wallet balance.')
            rows = []
            for d in details:
                try:
                    when = datetime.fromtimestamp(int(d.get('deduction_time', 0)) / 1000).strftime('%Y-%m-%d %H:%M')
                except (TypeError, ValueError, OverflowError, OSError):
                    when = ''
                rows.append({'Date': when, 'Task ID': d.get('task_id', ''),
                             'Model': d.get('model_name', ''), 'Resolution': d.get('resolution', ''),
                             'Charge': d.get('deduction_amount', ''), 'Currency': d.get('currency', ''),
                             'Balance after': d.get('balance_after_deduction', '')})
            st.dataframe(rows, use_container_width=True, hide_index=True)
            if account['has_more']:
                st.warning('More billing records exist. Only the first 100 are shown; this is not a complete spending total.')
        st.caption('For an authoritative live balance, also check your Kling developer console.')
