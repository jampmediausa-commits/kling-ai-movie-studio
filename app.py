import os, json, time, base64
import requests
import jwt
import streamlit as st

st.set_page_config(page_title='Kling AI Movie Studio', page_icon='🎬', layout='wide')
st.title('🎬 Kling AI Movie Studio')
st.caption('Storyboard → approved image → Kling video → download')

API = 'https://api.klingai.com'

def credentials():
    # Set as hosting secrets/environment variables; never commit credentials.
    try:
        ak = st.secrets.get('KLING_ACCESS_KEY', '')
        sk = st.secrets.get('KLING_SECRET_KEY', '')
    except Exception:
        ak = sk = ''
    return os.getenv('KLING_ACCESS_KEY', ak), os.getenv('KLING_SECRET_KEY', sk)

def headers(ak, sk):
    now = int(time.time())
    token = jwt.encode({'iss': ak, 'exp': now + 1800, 'nbf': now - 5}, sk, algorithm='HS256', headers={'alg':'HS256','typ':'JWT'})
    return {'Authorization': f'Bearer {token}', 'Content-Type':'application/json'}

def api_call(method, path, ak, sk, payload=None):
    response = requests.request(method, API + path, headers=headers(ak, sk), json=payload, timeout=45)
    response.raise_for_status()
    result = response.json()
    if result.get('code') not in (0, '0', None):
        raise RuntimeError(f"Kling API: {result.get('message', result)}")
    return result.get('data') or {}

if 'shots' not in st.session_state:
    st.session_state.shots = [{'title':'Shot 001 — Opening','prompt':'A moonlit forest. The camera slowly moves forward between trees. Wind and forest sound effects only, no music.','aspect':'9:16','duration':'5','task_id':'','video_url':''}]

ak, sk = credentials()
with st.sidebar:
    st.header('Kling API')
    st.write('Credentials are read from server secrets, never entered into this page.')
    st.success('Credentials configured' if ak and sk else 'Credentials not configured yet')
    model = st.text_input('Kling model name', value='kling-v1', help='Change only to a model enabled for your API account. Model capabilities vary.')
    st.warning('Generating clips may incur API charges. Confirm your Kling balance and pricing before clicking Generate.')
    st.caption('Use a private deployment with authentication; a private GitHub repository does NOT make a hosted website private.')

st.subheader('Storyboard')
up = st.file_uploader('Import storyboard JSON', type='json')
if up and st.button('Import storyboard'):
    try:
        obj = json.load(up)
        items = obj['shots'] if isinstance(obj,dict) else obj
        st.session_state.shots = [{'title':str(s.get('title','Untitled')), 'prompt':str(s.get('prompt','')), 'aspect':s.get('aspect','9:16'), 'duration':str(s.get('duration','5')), 'task_id':'', 'video_url':''} for s in items]
        st.rerun()
    except Exception as e:
        st.error(f'Invalid JSON: {e}')
if st.button('➕ Add shot'):
    st.session_state.shots.append({'title':f'Shot {len(st.session_state.shots)+1:03d}','prompt':'','aspect':'9:16','duration':'5','task_id':'','video_url':''})
    st.rerun()
export = {'shots':[{k:s[k] for k in ('title','prompt','aspect','duration','task_id','video_url')} for s in st.session_state.shots]}
st.download_button('⬇️ Export storyboard and task IDs', json.dumps(export,indent=2), 'kling_storyboard.json', 'application/json')
st.caption('Save the JSON frequently. Reference images are not included in the export and must be uploaded again after restarting.')

for i, shot in enumerate(st.session_state.shots):
    with st.expander(f"{i+1:03d} · {shot['title']}", expanded=i==0):
        shot['title'] = st.text_input('Title', shot['title'], key=f't_{i}')
        shot['prompt'] = st.text_area('Video prompt', shot['prompt'], key=f'p_{i}', height=130)
        c1,c2 = st.columns(2)
        shot['aspect'] = c1.selectbox('Aspect ratio', ['9:16','16:9','1:1'], index=['9:16','16:9','1:1'].index(shot['aspect']) if shot['aspect'] in ('9:16','16:9','1:1') else 0, key=f'a_{i}')
        shot['duration'] = c2.selectbox('Duration (seconds)', ['5','10'], index=['5','10'].index(shot['duration']) if shot['duration'] in ('5','10') else 0, key=f'd_{i}')
        photo = st.file_uploader('Approved starting image (optional)', type=['jpg','jpeg','png'], key=f'img_{i}')
        if photo:
            st.image(photo, width=260)
        if shot['task_id']:
            st.caption('Kling task ID: '+shot['task_id'])
        if shot['video_url']:
            st.video(shot['video_url'])
            st.link_button('Open / save video', shot['video_url'])
        if st.button('🎥 Generate shot — paid API request', key=f'g_{i}', disabled=not(ak and sk and shot['prompt'].strip())):
            try:
                if photo:
                    # Kling image-to-video expects base64 image bytes, not a data URI.
                    img = base64.b64encode(photo.getvalue()).decode('ascii')
                    payload = {'model_name':model,'image':img,'prompt':shot['prompt'],'duration':shot['duration']}
                    path = '/v1/videos/image2video'
                else:
                    payload = {'model_name':model,'prompt':shot['prompt'],'duration':shot['duration'],'aspect_ratio':shot['aspect']}
                    path = '/v1/videos/text2video'
                data = api_call('POST',path,ak,sk,payload)
                shot['task_id'] = data.get('task_id','')
                shot['video_url'] = ''
                if not shot['task_id']:
                    st.error(f'No task ID returned: {data}')
                else:
                    st.success('Submitted! Use Check status after processing.')
            except Exception as e:
                st.error(f'Kling request failed: {e}')
        if st.button('🔄 Check status',key=f's_{i}',disabled=not(ak and sk and shot['task_id'])):
            try:
                kind = 'image2video' if photo else 'text2video'
                # A task can be checked after a reload: if photo is missing, try both task types.
                paths = [f'/v1/videos/{kind}/{shot["task_id"]}']
                paths += [f'/v1/videos/{"text2video" if kind == "image2video" else "image2video"}/{shot["task_id"]}']
                data = None
                for path in paths:
                    try:
                        data = api_call('GET',path,ak,sk)
                        break
                    except Exception:
                        continue
                if data is None:
                    raise RuntimeError('Unable to retrieve task. Check task ID and API credentials.')
                status = data.get('task_status','unknown')
                st.info('Task status: '+status)
                if status == 'succeed':
                    videos = (data.get('task_result') or {}).get('videos') or []
                    if videos:
                        shot['video_url'] = videos[0].get('url','')
                        st.rerun()
                elif status == 'failed':
                    st.error(str(data.get('task_status_msg','Kling reported a failed generation.')))
            except Exception as e:
                st.error(f'Status check failed: {e}')
        if st.button('Remove shot',key=f'r_{i}'):
            st.session_state.shots.pop(i)
            st.rerun()

st.caption('Prototype only. No persistent database, access control, or cost limits. Do not deploy publicly without authentication and quotas. Kling API endpoints/models may vary by account and should be verified before paid testing.')
