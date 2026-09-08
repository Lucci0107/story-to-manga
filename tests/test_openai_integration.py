"""OpenAI境界の決定論的なモック統合テスト。"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.services.ai_pipeline import OpenAIProvider
from app.services.artwork import ArtworkGenerationError, save_openai_image
from app.services.storage import LocalFileStorage


class FakeHTTPResponse:
    """urllibレスポンスの最小モック。"""

    def __init__(self, body: dict) -> None:
        self.body = json.dumps(body, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def runtime_settings() -> SimpleNamespace:
    return SimpleNamespace(
        openai_api_key="test-key",
        openai_responses_url="https://api.openai.com/v1/responses",
        openai_image_url="https://api.openai.com/v1/images/generations",
        openai_text_model="gpt-5.6-luna",
        openai_image_model="gpt-image-2",
        openai_timeout_seconds=5.0,
        openai_max_retries=0,
        openai_max_output_tokens=2_000,
        storyboard_batch_pages=8,
    )


def response_with_json(value: dict) -> dict:
    return {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": json.dumps(value, ensure_ascii=False)}
                ],
            }
        ]
    }


def valid_analysis() -> dict:
    return {
        "title": "灯台の帰り道",
        "synopsis": "蒼が過去と向き合い、凛へ決断を伝える。",
        "genre": "ヒューマンドラマ",
        "tone": "静かで余韻がある",
        "world_setting": "霧の町",
        "main_characters": ["蒼"],
        "supporting_characters": ["凛"],
        "locations": ["灯台"],
        "major_events": ["再会", "決断"],
        "story_beats": ["導入", "対話", "結末"],
        "conflicts": ["過去への恐れ"],
        "climax": "蒼が決断を伝える",
        "ending": "ふたりが歩き出す",
        "important_objects": ["キーホルダー"],
    }


def valid_character() -> dict:
    return {
        "name": "蒼",
        "role": "主人公",
        "age_range": "20代",
        "personality": "慎重だが決断できる",
        "appearance": "短めの黒髪と静かな目",
        "hairstyle": "耳にかかる短髪",
        "hair_color": "黒",
        "eye_characteristics": "強い視線",
        "body_type": "細身",
        "clothing": "濃色のジャケット",
        "accessories": "古いキーホルダー",
        "distinguishing_features": "左手で握る癖",
        "expressions": "迷いと決意",
        "relationship_notes": "凛と過去を共有する",
        "visual_prompt": "短髪とジャケットを全コマで固定",
        "negative_constraints": "眼鏡を追加しない",
    }


def valid_settings_recommendation() -> dict:
    return {
        "recommended_page_count": 18,
        "recommended_visual_style": "cinematic",
        "recommended_color_mode": "bw",
        "recommended_pacing": "balanced",
        "recommended_dialogue_density": "medium",
        "recommended_target_audience": "一般読者",
        "recommendation_reason": "主要シーンの余白を確保します。",
        "page_count_reason": "導入と結末を急がず描きます。",
        "scene_page_budget": [
            {
                "scene": "導入",
                "scene_type": "establishing",
                "importance": "medium",
                "estimated_pages": 4,
                "reason": "舞台を示す",
            },
            {
                "scene": "決断",
                "scene_type": "climax",
                "importance": "high",
                "estimated_pages": 14,
                "reason": "感情の頂点",
            },
        ],
    }


def test_responses_structured_output_and_knowledge_are_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Responses API形式とKnowledge参照境界が実リクエストへ反映される。"""

    requests: list[dict] = []

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data.decode("utf-8")))
        return FakeHTTPResponse(response_with_json(valid_analysis()))

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)

    result = OpenAIProvider().analyze(
        "蒼は霧の町の灯台へ向かった。",
        "灯台",
        {"prompt_text": "霧の町では余白を広くする。"},
    )

    assert result["title"] == "灯台の帰り道"
    assert len(requests) == 1
    assert requests[0]["model"] == "gpt-5.6-luna"
    assert requests[0]["text"]["format"]["type"] == "json_schema"
    assert requests[0]["text"]["format"]["strict"] is True
    assert "<knowledge_reference>" in requests[0]["input"]
    assert "霧の町では余白を広くする" in requests[0]["input"]


def test_settings_recommendation_uses_structured_output_and_knowledge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict] = []

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data.decode("utf-8")))
        return FakeHTTPResponse(response_with_json(valid_settings_recommendation()))

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    result = OpenAIProvider().recommend_settings(
        valid_analysis(),
        {"language": "ja", "reading_direction": "right_to_left"},
        {"prompt_text": "重要な感情シーンは余白を広くする。"},
    )
    assert result["recommended_page_count"] == 18
    assert requests[0]["text"]["format"]["name"] == "manga_settings_recommendation"
    assert requests[0]["model"] == "gpt-5.6-luna"
    assert "<knowledge_reference>" in requests[0]["input"]
    assert "余白を広くする" in requests[0]["input"]


def test_invalid_structured_output_is_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """不正な構造化出力を無限再試行せず、1回だけ修復要求する。"""

    responses = iter(
        [
            {"output_text": "これはJSONではありません"},
            response_with_json(valid_analysis()),
        ]
    )
    calls: list[dict] = []

    def fake_urlopen(request, timeout):
        calls.append(json.loads(request.data.decode("utf-8")))
        return FakeHTTPResponse(next(responses))

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)

    result = OpenAIProvider().analyze("本文", "作品")

    assert result["synopsis"]
    assert len(calls) == 2
    assert "JSON Schema" in calls[1]["input"]


def test_panel_prompt_contains_continuity_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    """パネルPromptがCharacter Bible由来の固定情報を保持する。"""

    requests: list[dict] = []

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data.decode("utf-8")))
        return FakeHTTPResponse(response_with_json({"prompt": "cinematic foggy lighthouse scene"}))

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    panel = {
        "description": "蒼が灯台の扉へ手を伸ばす",
        "shot_type": "手元の寄り",
        "characters": ["蒼"],
        "action": "キーホルダーを握る",
        "expression": "静かな決意",
        "background": "霧の灯台",
    }
    character = {
        "name": "蒼",
        "appearance": "短めの黒髪",
        "hairstyle": "耳にかかる短髪",
        "hair_color": "黒",
        "eye_characteristics": "強い視線",
        "body_type": "細身",
        "clothing": "濃色のジャケット",
        "accessories": "古いキーホルダー",
        "distinguishing_features": "左手で握る癖",
        "negative_constraints": "眼鏡を追加しない",
    }

    prompt = OpenAIProvider().panel_prompt(
        panel,
        [character],
        {"color_mode": "bw", "visual_style": "cinematic"},
        {"prompt_text": "灯台は霧に包まれる。"},
    )

    assert "Continuity anchor" in prompt
    assert "短めの黒髪" in prompt
    assert "<knowledge_reference>" in requests[0]["input"]


def test_characters_storyboard_and_quality_use_structured_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主要な実AI工程がそれぞれ専用Schemaで検証される。"""

    requests: list[dict] = []
    values = iter(
        [
            response_with_json({"characters": [valid_character()]}),
            response_with_json(
                {
                    "pages": [
                        {
                            "page_number": 1,
                            "title": "霧の入口",
                            "layout": "classic",
                            "panels": [
                                {
                                    "description": "蒼が灯台を見る",
                                    "shot_type": "遠景",
                                    "characters": ["蒼"],
                                    "action": "立ち止まる",
                                    "expression": "迷い",
                                    "background": "霧の町",
                                    "dialogue": [],
                                    "narration": ["霧が町を包む。"],
                                    "sfx": [],
                                }
                            ],
                        }
                    ]
                }
            ),
            response_with_json(
                {
                    "status": "pass",
                    "summary": "人物とネームの流れを確認しました。",
                    "issues": [],
                    "warnings": [],
                    "suggestions": ["ページめくりを確認してください。"],
                }
            ),
        ]
    )

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data.decode("utf-8")))
        return FakeHTTPResponse(next(values))

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    provider = OpenAIProvider()
    analysis = valid_analysis()
    characters = provider.characters("蒼は灯台へ向かった。", analysis)
    storyboard = provider.storyboard(
        "蒼は灯台へ向かった。",
        analysis,
        {"target_page_count": 1, "color_mode": "bw", "visual_style": "cinematic"},
        characters,
    )
    review = provider.quality_check(
        {
            "title": "灯台",
            "original_text": "蒼は灯台へ向かった。",
            "analysis": analysis,
            "characters": characters,
            "storyboard": storyboard,
        },
        {"prompt_text": "霧の町では余白を広くする。"},
    )

    assert characters[0]["name"] == "蒼"
    assert storyboard[0]["panels"][0]["generation_prompt"]
    assert review["suggestions"]
    assert [request["text"]["format"]["name"] for request in requests] == [
        "character_bible",
        "manga_storyboard",
        "quality_review",
    ]


def test_large_storyboard_is_split_into_bounded_page_ranges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """大きな可変ページ数を1レスポンスへ集中させない。"""

    requests: list[dict] = []

    def raw_page(number: int) -> dict:
        return {
            "page_number": number,
            "title": f"ページ {number}",
            "layout": "classic",
            "panels": [
                {
                    "description": f"場面 {number}",
                    "shot_type": "遠景",
                    "characters": ["蒼"],
                    "action": "進む",
                    "expression": "決意",
                    "background": "灯台",
                    "dialogue": [],
                    "narration": [],
                    "sfx": [],
                }
            ],
        }

    responses = iter(
        [
            response_with_json({"pages": [raw_page(number) for number in range(1, 9)]}),
            response_with_json({"pages": [raw_page(number) for number in range(9, 17)]}),
            response_with_json({"pages": [raw_page(number) for number in range(17, 19)]}),
        ]
    )

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data.decode("utf-8")))
        return FakeHTTPResponse(next(responses))

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    provider = OpenAIProvider()

    storyboard = provider.storyboard(
        "蒼は灯台へ向かった。",
        valid_analysis(),
        {"target_page_count": 18, "language": "ja"},
        [valid_character()],
    )

    assert len(storyboard) == 18
    assert [page["page_number"] for page in storyboard] == list(range(1, 19))
    assert [request["text"]["format"]["name"] for request in requests] == [
        "manga_storyboard_1_8",
        "manga_storyboard_9_16",
        "manga_storyboard_17_18",
    ]
    assert [
        request["text"]["format"]["schema"]["properties"]["pages"]["maxItems"]
        for request in requests
    ] == [8, 8, 2]


def test_openai_image_base64_is_validated_and_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Images APIのbase64画像をサーバー側で検証して保存する。"""

    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(stream, format="PNG")
    encoded = base64.b64encode(stream.getvalue()).decode("ascii")

    def fake_urlopen(request, timeout):
        return FakeHTTPResponse({"data": [{"b64_json": encoded}]})

    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    storage = LocalFileStorage(tmp_path)
    storage_key = storage.asset_key("project-1", "panel.png")
    save_openai_image(
        {"generation_prompt": "白黒の灯台のコマ"},
        runtime_settings(),
        storage,
        storage_key,
    )

    with Image.open(io.BytesIO(storage.get_bytes(storage_key))) as image:
        assert image.size == (8, 8)


def test_invalid_openai_image_is_user_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """壊れた画像応答を成功扱いにしない。"""

    def fake_urlopen(request, timeout):
        return FakeHTTPResponse({"data": [{"b64_json": base64.b64encode(b"bad").decode("ascii")}]})

    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    storage = LocalFileStorage(tmp_path)
    with pytest.raises(ArtworkGenerationError):
        save_openai_image(
            {"generation_prompt": "画像"},
            runtime_settings(),
            storage,
            storage.asset_key("project-1", "bad.png"),
        )
