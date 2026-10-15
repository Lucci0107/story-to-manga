"""吹き出しの意味、身長の出所、実画像への採用と失効を検証する。"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from xml.etree import ElementTree

import pytest
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.schemas import normalize_dialogue_details, PanelPatch
from app.services.character_sheet import height_contract, render_sheet_svg
from app.services.manga_documents import character_sheet_design, render_name_markdown, name_snapshot
from app.services.dialogue_balloon import BALLOONS, balloon_spec
from app.services.direction_render import draw_directed_text
from app.services.layout import reflow_page, ensure_page_layout
from app.services.panel_direction import direction_fingerprint
from app.services.visual_style import text_direction, resolve_visual_style
from app.services.storage import get_storage


@pytest.mark.parametrize('family,shape,tail', [('normal','round','spoken'), ('thought','thought_cloud','thought'), ('shout','burst','spoken'), ('inner_scream','uni_flash','none'), ('trembling','wavy','spoken')])
def test_balloon_meaning_survives_design_render_and_name(family, shape, tail):
    detail = {**balloon_spec({'speaker':'あかり'}, family, 'p', 0), 'speaker':'あかり', 'source':'source_quote'}
    detail = normalize_dialogue_details([detail], 1)[0]
    assert detail['balloon']['type'] == BALLOONS[family][2]
    for style in ['minimal','cinematic','comedy']:
        token = text_direction({'type':'bubble','text':'同じ言葉'}, resolve_visual_style({'visual_style':style}), '', detail)
        assert (token['shape'],token['tail']) == (shape,tail)
        image = Image.new('RGB',(280,160),'#eeeeee')
        assert draw_directed_text(ImageDraw.Draw(image), {'type':'bubble','text':'同じ言葉','direction':token}, 20,20,240,120)
        # 形の尖端も、予約した領域外へ出ない。
        assert image.crop((0,0,20,160)).getextrema() == ((238,238),)*3
    settings={'composition_version':5,'language':'ja','color_mode':'bw','visual_style':'minimal'}
    page=reflow_page({'id':'p','page_number':1,'panels':[{'id':'a','dialogue':['同じ言葉'],'dialogue_details':[detail],'dialogue_types':[family]}]},settings)
    item=page['panels'][0]['text_layout']['items'][0]
    assert item['balloon']['type'] == detail['balloon']['type']
    assert item['balloon']['placement']['coordinate_system']=='panel_normalized_0_1'
    project={'id':'p','title':'声の検証','settings':settings,'storyboard':[page]}
    assert BALLOONS[family][4] in render_name_markdown(name_snapshot(project),1)


def test_strong_inner_voice_priority_and_no_punctuation_inference():
    detail=balloon_spec({'voice_scope':'inner','delivery':'strong_scream'},'thought')
    assert detail['balloon']['tail_type']=='none'
    assert text_direction({'text':'助けて！！！'},resolve_visual_style({}))['family']=='normal'
    assert PanelPatch(dialogue_details=[{'source_excerpt':'原文','voice_scope':'inner','delivery':'strong_scream'}]).model_dump(exclude_unset=True)['dialogue_details'][0]['source_excerpt']=='原文'


@pytest.mark.parametrize('family', ['whisper', 'weak', 'comedic_reaction', 'announcement'])
def test_legacy_balloon_families_keep_their_meaning(family):
    page = reflow_page({'id': 'p', 'panels': [{'id': 'a', 'dialogue': ['声'], 'dialogue_types': [family]}]}, {'composition_version': 5})
    item = page['panels'][0]['text_layout']['items'][0]
    assert item['direction']['family'] == family
    assert 'balloon' not in item  # 通常会話のメタデータを誤って付けない。


@pytest.mark.parametrize('value,expected', [('', '身長：未設定'), ('未設定','身長：未設定'), ('170','身長：未設定'), ('0 cm','身長：未設定'), ('-3 cm','身長：未設定'), ('１７０ｃｍ','身長：170 cm'), ('1.7 m','身長：1.7 m'), ('体高 32 cm','体高：32 cm'), ('全長：42 cm','全長：42 cm')])
def test_height_only_uses_explicit_positive_measurement(value,expected):
    character={'id':'cat','name':'ねこ','height':value,'age_range':'成体'}
    result=height_contract(character)
    assert result['display_text']==expected
    assert result['scope']['character_id']=='cat' and result['placement']=='A_profile'
    if '未設定' in expected: assert result['value'] is None
    design=character_sheet_design({'characters':[character]},character)
    svg=ElementTree.fromstring(render_sheet_svg(design))
    group=next(e for e in svg.iter() if e.attrib.get('clip-path')=='url(#A01)')
    assert expected in ''.join(group.itertext())
    assert sum(s['count'] for s in design['sections'])==50


def test_saved_legacy_tokens_and_fingerprint_stay_intact():
    settings={'composition_version':4,'language':'ja','visual_style':'minimal'}
    page=reflow_page({'id':'p','panels':[{'id':'a','dialogue':['声'],'dialogue_types':['shout']}]},settings)
    panel=page['panels'][0]
    panel['text_layout']['items'][0]['direction']['shape']='round'
    saved=deepcopy(page)
    fingerprint=direction_fingerprint(panel,settings)
    assert ensure_page_layout(page,settings)==saved
    panel['dialogue_details']=[{'speaker':'あかり','source':'unspecified'}]
    assert direction_fingerprint(panel,settings)==fingerprint
    panel['dialogue_details'][0].update(balloon_spec({},'inner_scream'))
    assert direction_fingerprint(panel,settings)!=fingerprint


@pytest.fixture
def review_project(tmp_path,monkeypatch):
    monkeypatch.setenv('STORY_MANGA_DATA_DIR',str(tmp_path))
    db.init_db()
    client=TestClient(app)
    client.post('/register',data={'email':'quality-test@example.com','password':'quality-test-password'})
    project=client.post('/api/projects',data={'title':'採用の検証','story_text':'二人が公園で話す。'}).json()['project']
    image=BytesIO(); Image.new('RGB',(300,400),'#bbeedd').save(image,format='PNG')
    storage=get_storage(); key=storage.asset_key(project['id'],'a.png'); storage.put_bytes(key,image.getvalue())
    pages=[reflow_page({'id':f'page-{i}','page_number':i,'panels':[{'id':f'panel-{i}','generation_status':'completed','image_url':'/media/a.png','dialogue':['こんにちは。']}]},project['settings']) for i in (1,2)]
    project=db.update_project(project['id'],project['user_id'],storyboard=pages)
    audit={'status':'attention','issues':[{'key':'test','label':'品質所見','detail':'見た目を確認してください'}],'warnings':[]}
    project=db.save_quality_check(project['id'],project['user_id'],audit)
    return client,project,storage,key,audit


def test_explicit_acceptance_preserves_audit_history_and_survives_resume(review_project):
    client,project,storage,key,audit=review_project
    base=f"/api/projects/{project['id']}/quality-review/page-1"
    review=client.get(base).json()
    assert review['audit_status']=='UNVERIFIED' and not review['quality_ok']
    png=client.get(review['image_url'])
    assert png.status_code==200 and sha256(png.content).hexdigest()==review['image_sha256']
    payload={k:review[k] for k in ['design_hash','image_sha256']}; payload['decision']='USER_ACCEPTED'
    assert client.post(base+'/accept',json={**payload,'decision':'AUTO'}).status_code==422
    accepted=client.post(base+'/accept',json=payload)
    assert accepted.status_code==200,accepted.text
    record=accepted.json()['acceptance']
    assert record['status']=='USER_ACCEPTED' and record['audit_status']=='UNVERIFIED'
    assert record['automated_check']==audit and record['remaining_findings']==audit['issues']
    assert accepted.json()['project']['quality_check']==audit
    assert client.post(base+'/accept',json=payload).json()['acceptance']['id']==record['id']
    db.init_db()
    assert client.get(base).json()['quality_ok']
    # 他ページの変更は対象の採用を失効させない。
    saved=db.get_project(project['id'],project['user_id']); saved['storyboard'][1]['panels'][0]['dialogue']=['別のページ']
    db.update_project(project['id'],project['user_id'],storyboard=saved['storyboard'])
    assert client.get(base).json()['quality_ok']
    # 同一URLでも実画像の変更を検出し、古い画面からの採用を拒否する。
    data=BytesIO(); Image.new('RGB',(300,400),'red').save(data,format='PNG'); storage.put_bytes(key,data.getvalue())
    assert not client.get(base).json()['quality_ok']
    assert client.post(base+'/accept',json=payload).status_code==409
    assert client.get(review['image_url']).status_code==409
    assert len(db.list_quality_acceptances(project['id'],project['user_id']))==1


def test_acceptance_ownership_missing_image_design_and_race(review_project):
    client,project,storage,key,_audit=review_project
    base=f"/api/projects/{project['id']}/quality-review/page-1"
    review=client.get(base).json(); payload={k:review[k] for k in ['design_hash','image_sha256']}; payload['decision']='USER_ACCEPTED'
    other=TestClient(app); other.post('/register',data={'email':'quality-other@example.com','password':'quality-other-password'})
    for url in [base,review['image_url']]: assert other.get(url).status_code==404
    assert other.post(base+'/accept',json=payload).status_code==404
    saved=db.get_project(project['id'],project['user_id']); saved['storyboard'][0]['panels'][0]['dialogue']=['変更']
    db.update_project(project['id'],project['user_id'],storyboard=saved['storyboard'])
    assert client.post(base+'/accept',json=payload).status_code==409
    with pytest.raises(ValueError,match='作品が更新'):
        db.save_quality_acceptance(project,project['user_id'],review)
    storage.put_bytes(key,b'invalid image')
    assert client.get(base).status_code==409
    assert client.post(base+'/accept',json=payload).status_code==409
    assert not db.list_quality_acceptances(project['id'],project['user_id'])


def test_export_only_uses_acceptance_for_the_current_image(review_project):
    client, project, storage, key, _audit = review_project
    pages = project['storyboard']
    pages[0]['panels'][0]['dialogue'] = ['長文です。' * 100]
    pages[0] = reflow_page(pages[0], project['settings'])
    db.update_project(project['id'], project['user_id'], storyboard=pages)
    export_url = f"/api/projects/{project['id']}/export"
    assert client.post(export_url, json={'format': 'zip'}).status_code == 422
    base = f"/api/projects/{project['id']}/quality-review/page-1"
    review = client.get(base).json()
    payload = {k: review[k] for k in ['design_hash', 'image_sha256']}
    assert client.post(base + '/accept', json={**payload, 'decision': 'USER_ACCEPTED'}).status_code == 200
    result = client.post(export_url, json={'format': 'zip'})
    assert result.status_code == 200, result.text
    assert client.get(result.json()['download_url']).status_code == 200
    data = BytesIO()
    Image.new('RGB', (300, 400), 'blue').save(data, format='PNG')
    storage.put_bytes(key, data.getvalue())
    assert client.post(export_url, json={'format': 'zip'}).status_code == 422
