from __future__ import annotations
from app.services.ai_speech import translate_text

SAFE_TASKS = {
    "penguin_parrot": [
        ("Кто из них умеет летать?", "parrot"),
        ("Кто из них живёт там, где очень холодно?", "penguin"),
    ],
    "lion_turtle": [
        ("Кто из них быстрее бегает?", "lion"),
        ("У кого есть панцирь?", "turtle"),
    ],
}
NAMES_RU={"penguin":"пингвин","parrot":"попугай","lion":"лев","turtle":"черепаха"}

async def build_compare_task(pair_id:str,target_language:str,seed:str="",question_index:int|None=None) -> dict:
    pool=SAFE_TASKS[pair_id]
    idx=0 if question_index is None else max(0,min(int(question_index),len(pool)-1))
    question_ru,correct=pool[idx]
    animals=pair_id.split("_")
    question=await translate_text(question_ru,"ru",target_language)
    labels={a:await translate_text(NAMES_RU[a],"ru",target_language) for a in animals}
    return {"pair_id":pair_id,"animals":animals,"question":question,"question_ru":question_ru,"correct":correct,"labels":labels,"question_index":idx}
