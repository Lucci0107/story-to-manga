"""画像生成後にExportが構図を黙って再設計しないことを検証。"""
from copy import deepcopy
from app.services.layout import ensure_page_layout, page_layout_issues
from app.services.layout import reflow_page
from app.services.panel_direction import direction_is_ready
from scripts.manga_validation_fixture import validation_pages, SETTINGS


def test_saved_planned_artwork_geometry_survives_shot_edit():
    page = validation_pages()[1]
    panel = page['panels'][2]
    panel['shot_type'] = 'medium_close'
    panel['image_url'] = '/media/qa/preserved.png'
    before = deepcopy(page)
    after = ensure_page_layout(page, SETTINGS)
    assert after == before
    assert any(i['key'].startswith('layout-stale-planned-') for i in page_layout_issues(after, SETTINGS))


def test_finalize_shot_before_saving_and_generating():
    page = validation_pages()[1]
    page['panels'][2]['shot_type'] = 'medium_close'
    saved = ensure_page_layout(reflow_page(page, SETTINGS), SETTINGS)
    target = saved['panels'][2]
    assert direction_is_ready(target, SETTINGS)
    geometry = deepcopy(target['geometry'])
    target['image_url'] = '/media/qa/new-revision.png'
    completed = ensure_page_layout(saved, SETTINGS)
    assert completed['panels'][2]['geometry'] == geometry
    assert direction_is_ready(completed['panels'][2], SETTINGS)
