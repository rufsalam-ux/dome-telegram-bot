import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.animation_engine.character_motion_library import CharacterMotionLibrary, stable_animation_id
from app.services.animation_engine.local_motion_cache import LOCAL_MOTION_VERSION, stable_local_animation_id
from app.services.language_realization import (
    contains_technical_gender_placeholder,
    enforce_female_tutor,
    realize_tutor_result,
    resolve_gender_placeholders,
    russian_accusative,
)
from app.services.lesson_voice_context import authoritative_voice_context
from app.services.mobile_lesson_movie import select_movie_voice_takes
from app.services.preset_characters import list_preset_characters
from app.webapp.mobile_api import _selected_context_turn


def test_01_female_child_gender_is_realized():
    assert resolve_gender_placeholders("Ты выбрал(а) куртку", "girl") == "Ты выбрала куртку"


def test_02_male_child_gender_is_realized():
    assert resolve_gender_placeholders("Ты сказал(а) фразу", "boy") == "Ты сказал фразу"


def test_03_female_ai_self_reference_is_enforced():
    assert enforce_female_tutor("Я услышал тебя и я рад") == "Я услышала тебя и я рада"


def test_04_technical_gender_placeholders_are_rejected():
    assert contains_technical_gender_placeholder("ты выбрал(а)") is True
    realized=realize_tutor_result({"reaction_native":"Ты выбрал(а)!"},"en","ru","girl")
    assert "(а)" not in realized["reaction_native"]


def test_05_russian_accusative_regular_feminine_noun():
    assert russian_accusative("куртка") == "куртку"


def test_06_authored_case_form_wins_over_fallback():
    slide={"drag_items":[{"id":"coat","label_ru":"куртка","label_ru_accusative":"зимнюю куртку","label_en":"coat"}]}
    context=authoritative_voice_context(slide,{"selected_items":["coat"]},"ru","en")
    assert context["selected_items"][0]["label_target_accusative"] == "зимнюю куртку"


def test_07_legacy_gray_cat_is_not_in_active_catalog():
    ids={item["id"] for item in list_preset_characters()}
    assert "dome_cat" in ids and "cat" not in ids


def test_08_animation_id_is_stable_across_movie_runs():
    first=stable_local_animation_id("child-7","wave")
    second=stable_local_animation_id("child-7","wave")
    assert first==second and first.startswith("anim_")


def test_09_animation_id_is_scoped_to_avatar_action_and_direction():
    wave=stable_animation_id("child-7","wave","front",LOCAL_MOTION_VERSION)
    walk=stable_animation_id("child-7","walk_left","left",LOCAL_MOTION_VERSION)
    other=stable_animation_id("child-8","wave","front",LOCAL_MOTION_VERSION)
    assert len({wave,walk,other})==3


def _take(take_id:int,path:Path,status="ACCEPTED_CORRECT"):
    return SimpleNamespace(id=take_id,phrase_id="p1",status=status,audio_path=str(path))


def test_10_movie_selects_latest_successful_retake(tmp_path:Path):
    old=tmp_path/"old.m4a";new=tmp_path/"new.m4a";old.write_bytes(b"old");new.write_bytes(b"new")
    lesson={"timeline":[{"phrase_id":"p1"}],"slides":[]}
    chosen,missing=select_movie_voice_takes([_take(22,new),_take(11,old)],lesson)
    assert chosen["p1"]==new and missing==[]


def test_11_failed_retake_does_not_replace_previous_success(tmp_path:Path):
    old=tmp_path/"old.m4a";failed=tmp_path/"failed.m4a";old.write_bytes(b"old");failed.write_bytes(b"bad")
    lesson={"timeline":[{"phrase_id":"p1"}],"slides":[]}
    chosen,_=select_movie_voice_takes([_take(11,old),_take(22,failed,"RETRY_REQUIRED")],lesson)
    assert chosen["p1"]==old


def test_12_voice_request_identity_survives_authoritative_context():
    client={"request_id":"r-1","session_id":9,"step_id":"slide_19","turn_id":5,"hero_id":"dome_cat"}
    context=authoritative_voice_context({},client,"en","ru")
    assert {key:context[key] for key in client}==client


def test_13_client_cannot_inject_unpublished_visible_items():
    slide={"selection_options":[{"id":"coat","label_en":"coat","label_ru":"куртка"}]}
    context=authoritative_voice_context(slide,{"visible_items":["coat","spaceship"],"selected_items":["spaceship"]},"en","ru")
    assert [item["id"] for item in context["visible_items"]]==["coat"]
    assert context["selected_items"]==[]


@pytest.mark.asyncio
async def test_14_selected_item_turn_uses_female_gender_and_accusative():
    context={"task_type":"suitcase","selected_items":[{"id":"coat","label_target_accusative":"куртку","label_native_accusative":"coat"}]}
    turn=await _selected_context_turn(context,"ru","en",True,child_gender="girl")
    assert turn.reaction_target=="Ты выбрала куртку!"
    assert "выбрала куртку" in turn.follow_up_target


def _mobile_source(name:str)->str:
    return (Path(__file__).resolve().parents[2]/"DOME_MOBILE_77"/name).read_text(encoding="utf-8")


def test_15_answer_again_is_the_primary_control_without_duplicate_button():
    source=_mobile_source("src/screens/LessonPlayer.tsx")
    assert "testID='speak-more'" not in source
    assert "Ответить ещё" in source


def test_16_recording_uses_immutable_request_snapshot_and_stale_guard():
    source=_mobile_source("src/screens/LessonPlayer.tsx")
    for marker in ("recordingSnapshotRef","VOICE_STALE_RESPONSE_IGNORED","expectedRequest","turn_id:nextTurn"):
        assert marker in source


def test_17_mood_requires_an_actual_user_tap():
    source=_mobile_source("src/screens/LessonPlayer.tsx")
    assert "stored.input_source==='user_tap'" in source
    assert "input_source:'user_tap'" in source


def test_18_pre_slide_video_audio_is_stopped_on_exit():
    source=_mobile_source("src/components/PreSlideVideoStage.tsx")
    assert "try{player.pause()}catch{}" in source
    assert "playToEnd',()=>stop('ended')" in source


def test_19_registered_motion_persists_stable_animation_id(tmp_path:Path):
    avatar=tmp_path/"avatar.png";clip=tmp_path/"idle.mov"
    avatar.write_bytes(b"same-avatar-source");clip.write_bytes(b"motion")
    library=CharacterMotionLibrary(tmp_path,avatar,avatar_id="dome_cat")
    library.register("sig",clip,description_ru="idle",speaking=False,view="front",duration=2.0,
                     provider="local_cutout",animation_key="idle",direction="front",generation_version="cutout-v1")
    motion=json.loads(library.manifest_path.read_text("utf-8"))["motions"]["sig"]
    assert motion["animation_id"]==stable_animation_id("dome_cat","idle","front","cutout-v1")


def test_20_voice_latency_trace_has_all_twelve_boundaries():
    mobile=_mobile_source("src/screens/LessonPlayer.tsx")
    backend=(Path(__file__).resolve().parents[1]/"app/webapp/mobile_api.py").read_text("utf-8")
    speech=(Path(__file__).resolve().parents[1]/"app/services/speech_pipeline.py").read_text("utf-8")
    combined="\n".join((mobile,backend,speech))
    for stage in range(13):
        assert f"T{stage}_" in combined
