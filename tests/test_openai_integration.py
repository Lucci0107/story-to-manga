"""OpenAI境界の決定論的なモック統合テスト。"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from PIL import Image

from app.services.ai_pipeline import AIProviderError, OpenAIProvider
from app.services.artwork import ArtworkGenerationError, save_openai_image
from app.services.openai_client import OpenAIRequestError, request_json
from app.services.storage import LocalFileStorage
from app.services.story_profile import build_story_source_profile


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
        openai_storyboard_timeout_seconds=240.0,
        openai_character_timeout_seconds=180.0,
        openai_max_retries=0,
        openai_storyboard_max_retries=1,
        openai_max_output_tokens=2_000,
        openai_character_max_output_tokens=25_000,
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


def unchanged_cast_review(context: dict) -> dict:
    return {"groups": [{"name": item["name"], "members": [item["name"]]} for item in context["candidates"]],
            "excluded": []}


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


def test_storyboard_schema_is_strict_for_nested_panels() -> None:
    """Storyboardの全オブジェクトがStructured Outputsのstrict条件を満たす。"""

    from app.services.ai_pipeline import PANEL_SCHEMA, STORYBOARD_SCHEMA

    assert set(STORYBOARD_SCHEMA["properties"]) == set(STORYBOARD_SCHEMA["required"])
    page_schema = STORYBOARD_SCHEMA["properties"]["pages"]["items"]
    assert set(page_schema["properties"]) == set(page_schema["required"])
    assert set(PANEL_SCHEMA["properties"]) == set(PANEL_SCHEMA["required"])
    assert PANEL_SCHEMA["additionalProperties"] is False


def test_character_request_uses_task_specific_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Character Bibleは一般AI処理より長いが、上限付きの専用timeoutを使う。"""

    timeouts: list[float] = []

    def fake_urlopen(request, timeout):
        timeouts.append(timeout)
        return FakeHTTPResponse(response_with_json({"characters": [valid_character()]}))

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)

    result = OpenAIProvider().characters("蒼は灯台へ向かった。", valid_analysis())

    assert result[0]["name"] == "蒼"
    assert timeouts == [180.0]


def test_proposal_stops_before_profiles_and_confirmed_profiles_do_not_repeat_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = [f"提案人物{i:02d}" for i in range(1, 15)]
    source = "\n\n".join(f"## 第{i}章\n{name}は主役を支えた。\n" + "物語の経過を振り返った。" * 90
                         for i, name in enumerate(names, 1))
    requests: list[dict] = []

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        requests.append(payload)
        context = json.JSONDecoder().raw_decode(payload["input"])[0]
        stage = payload["text"]["format"]["name"]
        if stage == "character_cast":
            value = {"cast": [{"name": name, "participation": "story_actor", "aliases": [], "role": "協力者",
                               "source_quotes": [f"{name}は主役を支えた。"]} for name in names if name in context["story_content"]]}
        elif stage == "character_cast_review":
            value = {"groups": [{"name": item["name"], "members": [item["name"]], "importance": 5 if index == 0 else 3,
                                 "recommendation_reason": "初期の設定対象として役割を確認してください。"}
                                for index, item in enumerate(context["candidates"])], "excluded": []}
        else:
            value = {"characters": [{**valid_character(), "name": item["name"]} for item in context["target_cast"]]}
        return FakeHTTPResponse(response_with_json(value))

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    provider = OpenAIProvider({"character_model": "gpt-6.1-sol", "reasoning_effort": "xhigh"})
    roster = provider.propose_characters(source, valid_analysis(), {"prompt_text": "未知の実在人物の属性を創作しない。"})
    assert len(roster) == 14
    assert not any(item["text"]["format"]["name"] == "character_bible" for item in requests)
    assert provider.last_generation_metadata["profiles_generated"] == 0
    extraction_count = len(requests)
    saved_batches: list[list[dict]] = []
    provider.character_profiles_callback = saved_batches.append
    result = provider.generate_character_profiles(roster[:3], valid_analysis(), {"prompt_text": "同一性を保持する。"})
    assert [item["name"] for item in result] == names[:3]
    assert len(requests) == extraction_count + 1
    assert len(saved_batches) == 1 and len(saved_batches[0]) == 3
    assert all(item["text"]["format"]["name"] == "character_bible" for item in requests[extraction_count:])
    assert all(item["model"] == "gpt-6.1-sol" and item["reasoning"] == {"effort": "xhigh"}
               and "<knowledge_reference>" in item["input"] for item in requests)


def test_long_character_source_covers_fourteen_people_and_repairs_missing_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """中間の人物も全文抽出へ届き、4人中2人だけの設定は再要求する。"""

    names = [f"登場人物{i:02d}" for i in range(1, 15)]
    source = "\n\n".join(f"## 第{i}章\n" + "これまでの出来事を振り返った。" * 140
                         + f"\n{name}は主人公の決断を支えた。\n"
                         + "次の場所へ進む準備をした。" * 140
                         for i, name in enumerate(names, 1))
    requests: list[dict] = []
    profile_requests: list[list[str]] = []

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        requests.append(payload)
        context, _ = json.JSONDecoder().raw_decode(payload["input"])
        if payload["text"]["format"]["name"] == "character_cast_review":
            return FakeHTTPResponse(response_with_json(unchanged_cast_review(context)))
        if payload["text"]["format"]["name"] == "character_cast":
            cast = [{"name": name, "participation": "story_actor", "aliases": [], "role": "決断を支える人物",
                     "source_quotes": [f"{name}は主人公の決断を支えた。"]}
                    for name in names if name in context["story_content"]]
            return FakeHTTPResponse(response_with_json({"cast": cast}))
        targets = [item["name"] for item in context["target_cast"]]
        profile_requests.append(targets)
        selected = targets[:2] if len(profile_requests) == 1 else targets
        characters = [{**valid_character(), "name": name} for name in selected]
        return FakeHTTPResponse(response_with_json({"characters": characters}))

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    provider = OpenAIProvider({"preset": "balanced", "character_model": "gpt-6.1-sol",
                              "reasoning_effort": "xhigh"})
    heartbeats: list[bool] = []
    provider.character_progress_callback = lambda: heartbeats.append(True)
    result = provider.characters(source, valid_analysis(), {"prompt_text": "重要な支援者も対象にする。"})

    assert [item["name"] for item in result] == names
    assert len({item["id"] for item in result}) == 14
    assert all(item["source_quotes"] for item in result)
    cast_requests = [item for item in requests if item["text"]["format"]["name"] == "character_cast"]
    assert len(cast_requests) >= 2
    assert all(any(name in request["input"] for request in cast_requests) for name in names)
    assert sum(len(json.JSONDecoder().raw_decode(item["input"])[0]["story_content"])
               for item in cast_requests) >= len(source)
    assert profile_requests == [names[:4], names[:4], names[4:8], names[8:12], names[12:]]
    assert all(item["model"] == "gpt-6.1-sol" and item["reasoning"] == {"effort": "xhigh"}
               for item in requests)
    assert all("<knowledge_reference>" in item["input"] for item in requests)
    assert len(heartbeats) == len(requests)
    assert provider.last_generation_metadata["character_count"] == 14
    for payload in requests:
        if payload["text"]["format"]["name"] != "character_bible":
            continue
        targets = json.JSONDecoder().raw_decode(payload["input"])[0]["target_cast"]
        array_schema = payload["text"]["format"]["schema"]["properties"]["characters"]
        assert array_schema["minItems"] == array_schema["maxItems"] == len(targets)
        assert array_schema["items"]["properties"]["name"]["enum"] == [item["name"] for item in targets]
        assert "今回の作成対象ではありません" in payload["instructions"]


@pytest.mark.parametrize("excluded_reason,participation,expected_candidate_count", [
    ("reference_only", "reference_only", 14), ("incidental_person", "story_actor", 84),
])
def test_long_source_with_seventy_nonessential_candidates_preserves_all_fourteen_story_actors(
    monkeypatch: pytest.MonkeyPatch, excluded_reason: str, participation: str, expected_candidate_count: int,
) -> None:
    """引用著者や一時的な対応者が64人を超えても、全編を照合して重要人物を固定する。"""

    names = [f"登場人物{i:02d}" for i in range(1, 15)]
    actors = [{"name": name, "participation": "story_actor", "aliases": [], "role": "相談相手",
               "source_quotes": [f"{name}は主人公の相談に応じた。"]} for name in names]
    prefix = "引用著者" if excluded_reason == "reference_only" else "一時対応者"
    references = [{"name": f"{prefix}{i:02d}", "participation": participation, "aliases": [],
                   "role": "説明中の著者または短い挨拶だけの窓口担当", "source_quotes": [f"{prefix}{i:02d}へ短く言及した。"]}
                  for i in range(70)]
    other = [
        {"name": "一般的な医師", "participation": "generic_or_hypothetical", "aliases": [],
         "role": "一般論の職業", "source_quotes": ["一般的な医師の責務を考えた。"]},
        {"name": "学会の参加者", "participation": "background_group", "aliases": [],
         "role": "背景の集団", "source_quotes": ["学会の参加者が会場を埋めた。"]},
    ]
    source = "\n\n".join(
        f"## 第{i + 1}章\n" + "語り手は出来事を振り返った。" * 160 + "\n"
        + "\n".join(item["source_quotes"][0] for item in [actor] + references[i * 5:(i + 1) * 5] + other)
        for i, actor in enumerate(actors)
    )
    requests: list[dict] = []
    observed_references: set[str] = set()
    profile_names: list[str] = []

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        requests.append(payload)
        context = json.JSONDecoder().raw_decode(payload["input"])[0]
        if payload["text"]["format"]["name"] == "character_cast_review":
            assert len(context["candidates"]) == expected_candidate_count
            return FakeHTTPResponse(response_with_json({
                "groups": [{"name": name, "members": [name]} for name in names],
                "excluded": [{"name": item["name"], "reason": excluded_reason}
                             for item in context["candidates"] if item["name"] not in names],
            }))
        if payload["text"]["format"]["name"] == "character_cast":
            cast = [item for item in actors + references + other
                    if item["source_quotes"][0] in context["story_content"]]
            observed_references.update(item["name"] for item in cast if item["name"].startswith(prefix))
            return FakeHTTPResponse(response_with_json({"cast": cast}))
        profile_names.extend(item["name"] for item in context["target_cast"])
        return FakeHTTPResponse(response_with_json({"characters": [
            {**valid_character(), "name": item["name"]} for item in context["target_cast"]
        ]}))

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    provider = OpenAIProvider({"character_model": "gpt-6.1-sol", "reasoning_effort": "xhigh"})
    result = provider.characters(source, valid_analysis(), {"prompt_text": "家族・支援者を省略しない。"})

    assert len(observed_references) == 70
    assert [item["name"] for item in result] == profile_names == names
    assert provider.last_generation_metadata["character_candidate_count"] == expected_candidate_count
    assert provider.last_generation_metadata["character_cast_reviewed"] is True
    assert all(quote in source for item in result for quote in item["source_quotes"])
    assert all(item["model"] == "gpt-6.1-sol" and item["reasoning"] == {"effort": "xhigh"}
               and "<knowledge_reference>" in item["input"] for item in requests)
    cast_requests = [item for item in requests if item["text"]["format"]["name"] == "character_cast"]
    assert len(cast_requests) > 1
    for request in cast_requests:
        item_schema = request["text"]["format"]["schema"]["properties"]["cast"]["items"]
        assert "participation" in item_schema["required"]
        assert "reference_only" in item_schema["properties"]["participation"]["enum"]
        assert "人名への言及だけではstory_actorにしない" in request["instructions"]


@pytest.mark.parametrize("limited_stage,limited_category", [
    ("character_cast", "output_limit"), ("character_bible", "output_limit"),
    ("character_cast", "timeout"), ("character_bible", "timeout"),
])
def test_character_output_limit_splits_only_the_failed_part_and_keeps_every_person(
    monkeypatch: pytest.MonkeyPatch, limited_stage: str, limited_category: str,
) -> None:
    names = [f"途中の人物{i:02d}" for i in range(1, 15)]
    source = "\n\n".join(f"## 第{i}章\n" + "状況を振り返った。" * 120
                         + f"\n{name}は主人公の決断を支えた。\n"
                         + "次の場所へ進む準備をした。" * 140
                         for i, name in enumerate(names, 1))
    requests: list[dict] = []
    limited_input = ""
    profile_requests: list[list[str]] = []

    def fake_urlopen(request, timeout):
        nonlocal limited_input
        payload = json.loads(request.data.decode("utf-8"))
        requests.append(payload)
        context, _ = json.JSONDecoder().raw_decode(payload["input"])
        stage = payload["text"]["format"]["name"]
        if stage == "character_cast_review":
            return FakeHTTPResponse(response_with_json(unchanged_cast_review(context)))
        if stage == "character_bible":
            profile_requests.append([item["name"] for item in context["target_cast"]])
        if stage == limited_stage and not limited_input:
            limited_input = payload["input"]
            if limited_category == "timeout":
                raise TimeoutError("simulated timeout")
            return FakeHTTPResponse({
                "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                "output_text": '{"characters":[',
            })
        if stage == "character_cast":
            cast = [{"name": name, "participation": "story_actor", "aliases": [], "role": "決断を支える人物",
                     "source_quotes": [f"{name}は主人公の決断を支えた。"]}
                    for name in names if name in context["story_content"]]
            return FakeHTTPResponse(response_with_json({"cast": cast}))
        return FakeHTTPResponse(response_with_json({"characters": [
            {**valid_character(), "name": item["name"]} for item in context["target_cast"]
        ]}))

    runtime = runtime_settings()
    runtime.openai_max_retries = 1
    monkeypatch.setattr("app.services.ai_pipeline.get_settings", lambda: runtime)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    provider = OpenAIProvider({"preset": "balanced", "character_model": "gpt-6.1-sol",
                              "reasoning_effort": "xhigh"})
    result = provider.characters(source, valid_analysis(), {"prompt_text": "支援者の設定も保持する。"})

    assert [item["name"] for item in result] == names
    assert sum(payload["input"] == limited_input for payload in requests) == 1
    assert all(item["max_output_tokens"] == 25_000 for item in requests)
    assert all(item["model"] == "gpt-6.1-sol" and item["reasoning"] == {"effort": "xhigh"}
               and "<knowledge_reference>" in item["input"] for item in requests)
    if limited_stage == "character_bible":
        assert profile_requests == [names[:4], names[:2], names[2:4], names[4:8], names[8:12], names[12:]]
    else:
        assert profile_requests == [names[:4], names[4:8], names[8:12], names[12:]]
        failed_context = json.JSONDecoder().raw_decode(limited_input)[0]["story_content"]
        sections = [json.JSONDecoder().raw_decode(item["input"])[0]["story_content"] for item in requests[1:3]]
        assert len(sections[0]) < len(failed_context) and len(sections[1]) < len(failed_context)
        assert sections[0] + sections[1][600:] == failed_context


def test_character_quote_repair_uses_the_specific_validation_reason_without_logging_source(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[dict] = []
    source = "葵は、\n主人公の決断を支えた。"

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data.decode("utf-8")))
        quote = "葵が主人公を救った。" if len(requests) == 1 else "葵は、主人公の決断を支えた。"
        return FakeHTTPResponse(response_with_json({"cast": [
            {"name": "葵", "participation": "story_actor", "aliases": [], "role": "支援者", "source_quotes": [quote]},
        ]}))

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    roster: list[dict] = []
    OpenAIProvider()._character_cast_part(source, roster, valid_analysis(), None, None, "人物を抽出する。",
                                        source_part=1, source_parts=1)
    assert roster[0]["source_quotes"] == [source]
    assert len(requests) == 2
    assert "連続した短い一文としてそのままコピー" in requests[1]["input"]
    assert "葵が主人公を救った。" not in requests[1]["input"]
    assert "character_evidence" in caplog.text
    assert "葵" not in caplog.text and "主人公の決断" not in caplog.text


@pytest.mark.parametrize("source_size", [100, 30_000])
def test_unknown_real_person_appearance_succeeds_without_repeating_profile_generation(
    monkeypatch: pytest.MonkeyPatch, source_size: int,
) -> None:
    quote = "協力者は相談に応じた。"
    source = quote + "\n\n" + "日々の出来事を振り返る。" * (source_size // 12)
    requests: list[dict] = []

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        requests.append(payload)
        if payload["text"]["format"]["name"] == "character_cast_review":
            context = json.JSONDecoder().raw_decode(payload["input"])[0]
            return FakeHTTPResponse(response_with_json(unchanged_cast_review(context)))
        if payload["text"]["format"]["name"] == "character_cast":
            context = json.JSONDecoder().raw_decode(payload["input"])[0]
            return FakeHTTPResponse(response_with_json({"cast": [
                {"name": "協力者", "participation": "story_actor", "role": "相談相手", "aliases": [], "source_quotes": [quote]},
            ] if quote in context["story_content"] else []}))
        return FakeHTTPResponse(response_with_json({"characters": [{
            **valid_character(), "name": "協力者", "appearance": "", "age_range": "", "clothing": "",
        }]}))

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    provider = OpenAIProvider({"character_model": "gpt-6.1-sol", "reasoning_effort": "xhigh"})
    result = provider.characters(source, valid_analysis(), {"prompt_text": "不明な外見は未設定として保持する。"})
    assert result[0]["appearance"].startswith("未設定")
    assert result[0]["age_range"] == result[0]["clothing"] == ""
    assert len([item for item in requests if item["text"]["format"]["name"] == "character_bible"]) == 1
    assert all(item["model"] == "gpt-6.1-sol" and item["reasoning"] == {"effort": "xhigh"}
               and "<knowledge_reference>" in item["input"] for item in requests)


@pytest.mark.parametrize("field,bad_value,category", [
    ("role", "", "character_role"), ("aliases", [""], "character_aliases"),
    ("participation", None, "character_participation"),
])
def test_cast_invalid_fields_are_repaired_or_reported_without_private_values(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    field: str, bad_value: object, category: str,
) -> None:
    quote = "非公開の人物は相談に応じた。"
    person = {"name": "非公開の人物", "participation": "story_actor", "role": "相談相手", "aliases": [], "source_quotes": [quote]}
    requests: list[dict] = []
    recover = True

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        requests.append(payload)
        item = person if recover and len(requests) == 2 else {**person, field: bad_value}
        return FakeHTTPResponse(response_with_json({"cast": [item]}))

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    roster: list[dict] = []
    provider = OpenAIProvider()
    provider._character_cast_part(quote, roster, {}, None, None, "人物を抽出する。",
                                 source_part=1, source_parts=1)
    assert len(roster) == 1 and len(requests) == 2
    assert field in requests[1]["input"].split("前回の出力を利用せず", 1)[1]
    recover = False
    requests.clear()
    with pytest.raises(AIProviderError) as raised:
        provider._character_cast_part(quote, [], {}, None, None, "人物を抽出する。",
                                     source_part=1, source_parts=1)
    assert len(requests) == 2
    assert raised.value.error_category == category and not raised.value.retryable
    assert "人物一覧の抽出" in str(raised.value)
    assert category in caplog.text
    assert "非公開の人物" not in str(raised.value) + caplog.text


@pytest.mark.parametrize("body,category", [
    ({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
      "output_text": '{"characters":[]}'}, "output_limit"),
    ({"status": "incomplete", "incomplete_details": {"reason": "content_filter"}}, "content_filter"),
    ({"status": "completed", "output": [{"type": "message", "content": [
        {"type": "refusal", "refusal": "private-source-fragment"}]}]}, "content_filter"),
])
def test_incomplete_or_refused_character_response_is_not_parsed_or_blindly_retried(
    monkeypatch: pytest.MonkeyPatch, body: dict, category: str, caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[dict] = []

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data.decode("utf-8")))
        return FakeHTTPResponse(body)

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(AIProviderError) as raised:
        OpenAIProvider().characters("private-source-fragment", valid_analysis())
    assert raised.value.error_category == category
    assert raised.value.retryable is False
    assert len(requests) == 1
    assert "private-source-fragment" not in str(raised.value) + caplog.text


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


def test_recommendation_reads_all_source_chapters_and_repairs_a_digest_estimate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "\n\n".join(f"## 第{i}章\n\n" + "場所を移り、登場人物は次の行動を決めた。" * 75 for i in range(1, 32))
    profile = build_story_source_profile(source)
    requests: list[dict] = []

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data.decode("utf-8")))
        value = valid_settings_recommendation()
        value["recommended_page_count"] = 8 if len(requests) == 1 else 96
        return FakeHTTPResponse(response_with_json(value))

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    result = OpenAIProvider().recommend_settings(
        valid_analysis(), {"language": "ja", "target_page_count": 8}, source_profile=profile,
    )
    assert result["recommended_page_count"] == 96
    assert len(requests) == 2
    prompt = json.loads(requests[0]["input"].split("\nrecommended_page_count", 1)[0])
    assert "target_page_count" not in prompt["current_settings"]
    assert "reference_page_count" not in prompt["source_profile"]
    assert prompt["source_profile"]["chapter_count"] == 31
    assert prompt["source_profile"]["sections"][-1]["title"] == "第31章"
    assert len(requests[0]["input"]) < len(source)
    assert "ダイジェストに圧縮しない" in requests[0]["instructions"]


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


def test_transport_timeout_is_classified_and_retried_within_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通信タイムアウトは分類し、設定回数を超えて再試行しない。"""

    calls: list[float] = []
    request_ids: list[str | None] = []

    def fake_urlopen(request, timeout):
        calls.append(timeout)
        request_ids.append(request.get_header("X-client-request-id"))
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.services.openai_client.time.sleep", lambda _seconds: None)

    with pytest.raises(OpenAIRequestError) as raised:
        request_json(
            "https://api.openai.com/v1/responses",
            api_key="test-key",
            payload={"model": "gpt-5.6-sol"},
            timeout=240.0,
            max_retries=1,
        )

    assert raised.value.category == "timeout"
    assert raised.value.retryable is True
    assert calls == [240.0, 240.0]
    assert request_ids[0] and request_ids[0] == request_ids[1]


def test_quota_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """残高・利用上限エラーで課金APIを無駄に繰り返さない。"""

    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        raise HTTPError(
            request.full_url,
            429,
            "quota",
            {},
            io.BytesIO(
                json.dumps(
                    {"error": {"code": "insufficient_quota", "type": "quota"}}
                ).encode()
            ),
        )

    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)

    with pytest.raises(OpenAIRequestError) as raised:
        request_json(
            "https://api.openai.com/v1/responses",
            api_key="test-key",
            payload={"model": "gpt-5.6-sol"},
            timeout=240.0,
            max_retries=2,
        )

    assert calls == 1
    assert raised.value.category == "quota"
    assert raised.value.retryable is False
    assert "利用上限" in str(raised.value)


def test_invalid_structured_output_request_is_classified_without_body_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAIのSchema拒否を分類し、本文や秘密を例外へ持ち込まない。"""

    def fake_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            400,
            "invalid request",
            {},
            io.BytesIO(
                json.dumps(
                    {
                        "error": {
                            "message": "Invalid schema with internal-secret-value",
                            "type": "invalid_request_error",
                            "param": "text.format.schema",
                            "code": "invalid_json_schema",
                        }
                    }
                ).encode()
            ),
        )

    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)

    with pytest.raises(OpenAIRequestError) as raised:
        request_json(
            "https://api.openai.com/v1/responses",
            api_key="test-key",
            payload={"model": "gpt-5.6-sol"},
            timeout=5.0,
            max_retries=0,
        )

    assert raised.value.category == "schema"
    assert raised.value.status_code == 400
    assert raised.value.error_code == "invalid_json_schema"
    assert raised.value.error_param == "text.format.schema"
    assert "internal-secret-value" not in str(raised.value)
    assert "スキーマ設定" in str(raised.value)


def test_storyboard_uses_task_specific_timeout_without_repeating_a_single_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Storyboardの長いtimeoutを維持し、1ページの失敗要求を再送しない。"""

    calls: list[float] = []

    def fake_urlopen(_request, timeout):
        calls.append(timeout)
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.services.openai_client.time.sleep", lambda _seconds: None)

    with pytest.raises(AIProviderError) as error:
        OpenAIProvider().storyboard(
            "蒼は灯台へ向かった。", valid_analysis(),
            {"target_page_count": 1, "language": "ja"}, [valid_character()],
        )
    assert error.value.error_category == "timeout"
    assert calls == [240.0]


def test_storyboard_batches_do_not_repeat_long_story_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """9ページ以上の各batchへ原文全文を繰り返し送らない。"""

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
            response_with_json({"pages": [raw_page(number) for number in range(9, 11)]}),
        ]
    )

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data.decode("utf-8")))
        return FakeHTTPResponse(next(responses))

    long_story = (
        "冒頭の固有場面。"
        + ("前半の出来事。" * 1_000)
        + "本文の中間固有語。"
        + ("後半の出来事。" * 1_000)
        + "結末の固有場面。"
    )
    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)

    result = OpenAIProvider().storyboard(
        long_story,
        valid_analysis(),
        {"target_page_count": 10, "language": "ja"},
        [valid_character()],
    )

    assert len(result) == 10
    assert len(requests) == 2
    assert sum("本文の中間固有語。" in request["input"] for request in requests) == 1
    contexts = [json.JSONDecoder().raw_decode(request["input"])[0] for request in requests]
    references = ["".join(unit["text"] for unit in context["story_reference"]) for context in contexts]
    assert "冒頭の固有場面。" in references[0]
    assert "結末の固有場面。" in references[-1]
    assert all(len(reference) < len(long_story) for reference in references)


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


@pytest.mark.parametrize(
    ("code", "category", "message"),
    [
        ("moderation_blocked", "content_filter", "安全性チェック"),
        ("content_policy_violation", "content_filter", "安全性チェック"),
        ("string_above_max_length", "input_limit", "長さの制限"),
    ],
)
def test_image_rejection_explains_reason_without_retrying_or_leaking_source(
    tmp_path, monkeypatch, caplog, code, category, message,
):
    """HTTP 400を原因別に表示し、原稿とキーはログへ出さず1回で止める。"""
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        raise HTTPError(request.full_url, 400, "bad request", {}, io.BytesIO(json.dumps({
            "error": {
                "code": code, "type": "invalid_request_error", "param": "prompt",
                "message": "private-story-fragment test-key",
            },
        }).encode()))

    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    with caplog.at_level("INFO", logger="story_to_manga.artwork"):
        with pytest.raises(ArtworkGenerationError) as raised:
            save_openai_image(
                {"generation_prompt": "private-story-fragment"}, runtime_settings(),
                LocalFileStorage(tmp_path), "failed.png",
            )
    assert len(requests) == 1
    assert raised.value.__cause__.category == category
    assert message in str(raised.value)
    assert "prompt_chars=22" in caplog.text
    assert f"error_code={code}" in caplog.text
    assert "client_request_id=image_generation-" in caplog.text
    assert "private-story-fragment" not in caplog.text + str(raised.value)
    assert "test-key" not in caplog.text + str(raised.value)
    assert not (tmp_path / "failed.png").exists()


def test_openai_error_metadata_discards_arbitrary_text(monkeypatch):
    """APIのエラー項目に混ざった本文をログ用メタデータへ残さない。"""
    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 400, "bad request", {}, io.BytesIO(json.dumps({
            "error": {"code": "private source fragment", "type": "秘密の本文", "param": "sk-secret/value"},
        }).encode()))

    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(OpenAIRequestError) as raised:
        request_json("https://api.openai.com/v1/images/generations", api_key="test-only", payload={"model": "gpt-image-2"})
    assert raised.value.error_code is raised.value.error_type is raised.value.error_param is None


def test_image_prompt_limit_stops_before_chargeable_request(tmp_path, monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("画像APIへ送信してはいけない")

    monkeypatch.setattr("app.services.artwork.request_json", unexpected)
    with pytest.raises(ArtworkGenerationError, match="長さ制限"):
        save_openai_image(
            {"generation_prompt": "あ" * 32_001}, runtime_settings(),
            LocalFileStorage(tmp_path), "too-long.png",
        )
    assert not (tmp_path / "too-long.png").exists()


@pytest.mark.parametrize(("param", "label"), [("size", "画像サイズ"), ("quality", "画質"), ("prompt", "描画指示")])
def test_image_parameter_rejection_names_the_rejected_setting(monkeypatch, param, label):
    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 400, "bad request", {}, io.BytesIO(json.dumps({
            "error": {"code": "invalid_value", "param": param, "type": "invalid_request_error"},
        }).encode()))

    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(OpenAIRequestError, match=label):
        request_json("https://api.openai.com/v1/images/generations", api_key="test-only", payload={"model": "gpt-image-2"})
