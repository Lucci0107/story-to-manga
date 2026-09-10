"""失われた原作品のメタデータを仮定せず、新規4ページの制作順序を検証する。"""

from copy import deepcopy
import io
import zipfile

import pytest
from PIL import Image
from pypdf import PdfReader

from scripts.manga_validation_fixture import SETTINGS, preflight, validation_pages
from app.services.ai_pipeline import compose_panel_prompt
from app.services.export import render_page_png, export_pdf, export_zip
from app.services.layout import ensure_page_layout
from app.services.panel_direction import plan_panel_direction
from app.services.storage import LocalFileStorage
from app.services.visual_style import STYLES, resolve_visual_style, text_direction


def intersects(a, b):
    return (a['x'] < b['x'] + b['width'] and b['x'] < a['x'] + a['width']
            and a['y'] < b['y'] + b['height'] and b['y'] < a['y'] + a['height'])


def test_four_page_design_before_any_artwork():
    pages = validation_pages()
    assert len(pages) == 4
    assert all(4 <= len(page['panels']) <= 6 for page in pages)
    assert len(preflight(pages)) == 16
    for page in pages:
        for panel in page['panels']:
            assert not panel.get('image_url')
            direction = panel['panel_direction']
            assert direction['source'] == 'pre_generation_plan'
            assert not direction['breakout_policy']['enabled']
            assert 'reserved_text_zones' in compose_panel_prompt(panel, [], SETTINGS)
            assert 'artwork_viewport' not in panel['geometry']
            for zone in direction['reserved_text_zones']:
                assert not intersects(zone, direction['face_safe_zone'])
                assert not intersects(zone, direction['character_zone'])


@pytest.mark.parametrize('style', list(STYLES))
def test_visual_style_never_changes_manga_medium_to_photography(style):
    panel = validation_pages()[0]['panels'][0]
    prompt = compose_panel_prompt(panel, [], {**SETTINGS, 'visual_style': style})
    assert 'hand-drawn manga illustration' in prompt
    assert 'not a photograph or live-action film still' in prompt
    assert resolve_visual_style({**SETTINGS, 'visual_style': style})['artwork_tone'] in prompt


def test_subtle_sfx_remains_legible_on_dark_artwork():
    profile = resolve_visual_style(SETTINGS)
    foot = text_direction({'type': 'sfx', 'text': 'テク テク'}, profile, 'footstep')
    impact = text_direction({'type': 'sfx', 'text': 'パチン'}, profile, 'impact')
    assert foot['size_scale'] < impact['size_scale']
    assert foot['text_stroke_fill'] != foot['fill']
    assert foot['text_stroke'] == 1


def test_quiet_page_is_rectangular_and_dominant_pages_have_area_rhythm():
    pages = validation_pages()
    assert all(panel['geometry']['shape'] == 'rectangle' for panel in pages[3]['panels'])
    for page in (pages[0], pages[2]):
        areas = [p['geometry']['width'] * p['geometry']['height'] for p in page['panels']]
        dominant = [p for p in page['panels'] if p['geometry'].get('dominant')]
        assert len(dominant) == 1
        area = dominant[0]['geometry']['width'] * dominant[0]['geometry']['height']
        assert 0.30 <= area <= 0.50
        assert area == max(areas)
        assert max(areas) / min(areas) > 2


@pytest.mark.parametrize('kind', ['dialogue', 'narration'])
def test_conflicting_face_blocks_pre_generation(kind):
    panel = validation_pages()[0]['panels'][0]
    panel['dialogue'] = []
    panel['narration'] = []
    panel[kind] = ['安全な場所へ。']
    panel['protected_zones'] = [{'x': 0, 'y': 0, 'width': 1, 'height': 1}]
    assert plan_panel_direction(panel, SETTINGS)['status'] == 'needs_revision'


def test_saved_artwork_does_not_trigger_relayout_or_regeneration():
    page = validation_pages()[0]
    for panel in page['panels']:
        panel['image_url'] = '/media/qa/' + panel['id'] + '.png'
        panel['generation_status'] = 'completed'
    before = deepcopy(page)
    assert ensure_page_layout(page, SETTINGS) == before


def test_four_page_preview_pdf_zip_use_same_composition(tmp_path):
    pages = validation_pages()
    storage = LocalFileStorage(tmp_path)
    project = {'id': 'isolated-qa', 'settings': SETTINGS, 'storyboard': pages}
    pdf = PdfReader(io.BytesIO(export_pdf(project, storage)))
    with zipfile.ZipFile(io.BytesIO(export_zip(project, storage))) as archive:
        assert len(pdf.pages) == 4
        for number, page in enumerate(pages, 1):
            png = render_page_png(project, page, storage)
            assert archive.read(f'pages/page-{number:03d}.png') == png
            preview = Image.open(io.BytesIO(png)).convert('RGB')
            exported = pdf.pages[number - 1].images[0].image.convert('RGB')
            assert exported.size == preview.size
            assert exported.tobytes() == preview.tobytes()
