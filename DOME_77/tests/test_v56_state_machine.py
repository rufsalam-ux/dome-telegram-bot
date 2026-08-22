from pathlib import Path
import json
from app.services.animal_compare import SAFE_TASKS


def lesson():
    return json.loads(Path('content/lessons/demo_001/lesson.json').read_text(encoding='utf-8'))


def test_each_compare_pair_covers_both_animals_once():
    assert SAFE_TASKS['penguin_parrot'] == [
        ('Кто из них умеет летать?', 'parrot'),
        ('Кто из них живёт там, где очень холодно?', 'penguin'),
    ]
    assert SAFE_TASKS['lion_turtle'] == [
        ('Кто из них быстрее бегает?', 'lion'),
        ('У кого есть панцирь?', 'turtle'),
    ]


def test_duplicate_guided_animal_steps_are_disabled():
    j=lesson(); by={s['slide_id']:s for s in j['slides']}
    assert by['slide_43']['skip_in_runtime'] is True
    assert by['slide_44']['skip_in_runtime'] is True
    assert by['slide_46']['next_slide']=='slide_51'
    assert by['slide_51']['next_slide']=='slide_45'


def test_all_compare_images_exist_and_have_pair_previews():
    for animal in ('penguin','parrot','lion','turtle'):
        p=Path(f'app/webapp/static/assets/animals/{animal}.png')
        assert p.exists() and p.stat().st_size > 1000
    for p in (
        Path('content/lessons/demo_001/lesson-images/animal-pair-penguin-parrot.png'),
        Path('content/lessons/demo_001/lesson-images/animal-pair-lion-turtle.png'),
    ):
        assert p.exists() and p.stat().st_size > 1000


def test_travel_instruction_is_one_action_only():
    s=next(x for x in lesson()['slides'] if x['slide_id']=='slide_48')
    assert s['open_answer'] is True
    assert s['personal_travel_followups']==[]
    assert s['max_ai_followups']==0
    assert 'одно место' in s['bot_says_target'].lower()
