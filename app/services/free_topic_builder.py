from __future__ import annotations

import json
from pathlib import Path
import httpx

from app.core.config import settings

ALLOWED_TYPES = {"passive","voice_answer","choice","drag_drop","memory","drawing","video","roleplay","mini_game"}
INTERACTIVE = {"voice_answer","choice","drag_drop","memory","drawing","roleplay","mini_game"}
BAD_MARKERS = {"item 1","item 2","item 3","picture 1","picture 2","picture 3","learn and use useful","say this short phrase"}


def _topic_pack(topic: str) -> dict:
    t=topic.lower()
    packs={
      'майнкрафт': {'words':['block','pickaxe','tree','house','diamond','creeper'], 'meanings':['блок','кирка','дерево','дом','алмаз','крипер'], 'verbs':['build','mine','find'], 'friend':'Alex'},
      'minecraft': {'words':['block','pickaxe','tree','house','diamond','creeper'], 'meanings':['блок','кирка','дерево','дом','алмаз','крипер'], 'verbs':['build','mine','find'], 'friend':'Alex'},
      'диноз': {'words':['dinosaur','egg','forest','footprint','tail','teeth'], 'meanings':['динозавр','яйцо','лес','след','хвост','зубы'], 'verbs':['walk','run','find'], 'friend':'Mia'},
      'космос': {'words':['rocket','planet','star','moon','helmet','alien'], 'meanings':['ракета','планета','звезда','луна','шлем','инопланетянин'], 'verbs':['fly','see','explore'], 'friend':'Nova'},
      'кот': {'words':['cat','tail','paws','toy','milk','window'], 'meanings':['кот','хвост','лапы','игрушка','молоко','окно'], 'verbs':['jump','play','sleep'], 'friend':'Milo'},
      'roblox': {'words':['game','avatar','door','coin','tower','friend'], 'meanings':['игра','аватар','дверь','монета','башня','друг'], 'verbs':['play','jump','build'], 'friend':'Max'},
    }
    for k,v in packs.items():
        if k in t: return v
    return {'words':['place','friend','object','path','surprise','goal'], 'meanings':['место','друг','предмет','путь','сюрприз','цель'], 'verbs':['look','find','make'], 'friend':'Sam'}


def _fallback(topic: str, target_language: str, native_language: str, age: int | None, level: str, count: int = 21) -> dict:
    pack=_topic_pack(topic); w=pack['words']; m=pack['meanings']; v=pack['verbs']; friend=pack['friend']
    # Fallback remains a coherent adventure and never exposes technical placeholders.
    stages=[
      ('passive','Start the adventure',f'Welcome to the world of {topic}. Look around and find {w[0]} and {w[1]}.'),
      ('choice','First choice',f'Which word names this object: {w[0]}?', [w[0],w[2],w[4]],0),
      ('voice_answer','Tell me',f'What can you see? Say: I see a {w[0]}.',None,None),
      ('passive','Meet a friend',f'{friend} joins the adventure. You need to reach the goal together.'),
      ('drag_drop','Match words and meanings','Match each study-language word with its meaning.'),
      ('voice_answer','Story moment 1',f'I found a {w[1]}!'),
      ('memory','Memory','Find the matching word pairs.'),
      ('passive','A new clue',f'You see a {w[2]} near the path. Something has changed.'),
      ('choice','Choose the action',f'What should you do now?', [v[0],v[1],v[2]],0),
      ('voice_answer','Story moment 2',f'Let us {v[0]} together!'),
      ('drawing','Create',f'Draw a {w[3]} and say its name.'),
      ('video','Watch the scene',f'Watch a short scene about {topic} and listen for familiar words.'),
      ('drag_drop','Build the sentence',f'Build the sentence: I / can / {v[1]} / a / {w[4]}.'),
      ('voice_answer','Story moment 3',f'Look! I found a {w[4]}!'),
      ('mini_game','Quick challenge',f'Find the {w[5]} before it disappears.', [w[5],w[3],w[1]],0),
      ('choice','Pack for the next step',f'Choose what will help you continue.', [w[1],w[3],w[4]],1),
      ('memory','Match again','Match three new pairs from the adventure.'),
      ('voice_answer','Story moment 4',f'We are almost there, {friend}!'),
      ('roleplay','Talk to your friend',f'{friend} asks: What did you find? Answer in one short sentence.'),
      ('passive','Remember the adventure',f'Remember: {w[0]}, {w[1]}, {w[3]}, {w[4]}.'),
      ('voice_answer','Story moment 5',f'We did it! What an adventure!'),
    ]
    slides=[]
    required_positions={5,9,13,17,20}
    for i in range(count):
        st=stages[i % len(stages)]; kind,title,prompt=st[0],st[1],st[2]; required=i in required_positions
        support=f'Тема: {topic}. ' + ('Скажи фразу на изучаемом языке.' if kind in {'voice_answer','roleplay'} else 'Посмотри, послушай и выполни задание.')
        slide={'id':f'free_{i+1:02d}','order':i+1,'type':kind,'title':title,'prompt':prompt,'support_text':support,'teacher_instruction':prompt,'audio_text':prompt,'expects_answer':kind in INTERACTIVE,'can_skip':False if required else True,'required_cartoon_line':required,'target_phrase':prompt if required else '', 'accepted_meaning':[prompt] if kind in {'voice_answer','roleplay'} else [], 'image_prompt':f'Unique scene {i+1} in one coherent child-friendly adventure about {topic}. Show visible objects/actions for this exact stage: {prompt}. Different composition and camera angle from prior scenes. No written text, no logos.'}
        if len(st)>=5 and isinstance(st[3],list): slide['options']=st[3]; slide['correct_option_index']=st[4]
        if kind in {'drag_drop','memory'}:
            offset=(i*2)%3; pairs=[(w[(offset+j)%len(w)],m[(offset+j)%len(m)]) for j in range(3)]; slide['items']=[a for a,b in pairs]; slide['targets']=[b for a,b in pairs]
        if required and i in {5,13,17}: slide['companion_reply']=f'{friend}: Great! Keep going!'
        slides.append(slide)
    return {'title':f'Свободная тема: {topic}','topic':topic,'target_language':target_language,'native_language':native_language,'story_summary':f'Ребёнок попадает в мир {topic}, встречает {friend}, решает задачи и вместе с другом достигает цели.','slides':slides,'source':'fallback'}


def _extract_output_text(payload: dict) -> str:
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return str(content["text"]).strip()
    return ""


def _contains_placeholder(slide: dict) -> bool:
    blob=json.dumps(slide,ensure_ascii=False).lower()
    return any(x in blob for x in BAD_MARKERS)


def _normalize(data: dict, topic: str, target_language: str, native_language: str) -> dict:
    slides=data.get('slides') or []
    required=[]
    seen_prompts=set(); seen_images=set()
    for i,s in enumerate(slides,1):
        s['id']=str(s.get('id') or f'free_{i:02d}'); s['order']=i
        if s.get('type') not in ALLOWED_TYPES: s['type']='voice_answer'
        s['expects_answer']=bool(s.get('expects_answer', s['type'] in INTERACTIVE))
        s['prompt']=str(s.get('prompt') or s.get('teacher_instruction') or s.get('title') or '').strip()
        s['support_text']=str(s.get('support_text') or '').strip()
        s['audio_text']=str(s.get('audio_text') or s['prompt']).strip()
        s['image_prompt']=str(s.get('image_prompt') or f'Distinct scene {i} of an educational adventure about {topic}, no text').strip()
        if s['prompt'].lower() in seen_prompts:
            s['prompt'] += f" (stage {i})"
        seen_prompts.add(s['prompt'].lower())
        if s['image_prompt'].lower() in seen_images:
            s['image_prompt'] += f", unique scene number {i}, different camera angle and objects"
        seen_images.add(s['image_prompt'].lower())
        if s.get('required_cartoon_line'):
            s['type']='voice_answer'; s['expects_answer']=True; s['can_skip']=False
            s['target_phrase']=str(s.get('target_phrase') or s.get('phrase') or s['prompt']).strip()
            s['prompt']=str(s.get('prompt') or f'Say: {s["target_phrase"]}')
            s['accepted_meaning']=list(s.get('accepted_meaning') or [s['target_phrase']])
            required.append(i-1)
        else:
            s['can_skip']=bool(s.get('can_skip', True))
            if s['type'] in {'voice_answer','roleplay'}:
                s['accepted_meaning']=list(s.get('accepted_meaning') or [])
        if s['type']=='choice':
            opts=[str(x).strip() for x in (s.get('options') or []) if str(x).strip()]
            if len(opts)<2: opts=['Yes','No','Not sure']
            s['options']=opts[:6]
        if s['type'] in {'drag_drop','memory'}:
            items=[str(x).strip() for x in (s.get('items') or []) if str(x).strip()]
            targets=[str(x).strip() for x in (s.get('targets') or []) if str(x).strip()]
            if len(items)<2 or len(targets)<2 or any(x.lower().startswith(('item ','picture ')) for x in items+targets):
                raise ValueError('placeholder interactive content')
            n=min(len(items),len(targets),5); s['items']=items[:n]; s['targets']=targets[:n]
        if _contains_placeholder(s):
            raise ValueError('generic placeholder content')
    if len(required)!=5:
        raise ValueError('exactly five cartoon lines required')
    data.update({'topic':topic,'target_language':target_language,'native_language':native_language,'source':'ai'})
    return data


async def build_free_topic_lesson(topic: str, *, target_language: str, native_language: str, age: int | None, level: str, slide_count: int = 21) -> dict:
    slide_count=max(18,min(25,int(slide_count or 21)))
    if not settings.openai_api_key:
        return _fallback(topic,target_language,native_language,age,level,slide_count)
    instructions=(
        "Create a COMPLETE, concrete, story-driven DOME language lesson. Return ONLY valid JSON. "
        f"Exactly {slide_count} slides. Learner age={age or 'unknown'}, level={level}, study language={target_language}, support/native language={native_language}. Topic={topic}. "
        "The lesson must be ONE coherent STORY: opening, goal/problem, recurring companion character, rising action, practice embedded in events, climax and meaningful ending. Every slide must causally continue the previous one. "
        "NEVER use placeholders such as item 1, picture 1, A/B/C without content, 'learn useful words', or 'say a short phrase'. "
        "Each slide must contain a DISTINCT concrete prompt and a DISTINCT image_prompt for that exact scene. image_prompt must describe visible objects/actions needed for that task, no written text/logos. "
        "Add support_text in the learner's native language for EVERY slide because the child may not read the study language. prompt/audio_text are in the study language. Both must be suitable for TTS. "
        "Use at least 3 concrete choice tasks with real options, 2 drag_drop with real items/targets, 2 memory tasks with real pairs, 1 drawing, 1 video stage (video_query field describing what clip is needed), 1 roleplay, passive illustrated teaching. "
        "For every voice_answer/roleplay provide accepted_meaning. For EXACTLY FIVE spaced voice_answer slides set required_cartoon_line=true, can_skip=false, target_phrase to the EXACT study-language sentence the child must record (<=5 seconds), and accepted_meaning including that sentence. "
        "Those five target_phrase lines must form the child's side of a coherent 60-90 second cartoon. Add companion_reply to 2-4 of those scenes for another character. "
        "For choices provide correct_option_index. For drag_drop/memory items[i] must correspond to targets[i]. "
        "Passive/video expects_answer=false; interactive expects_answer=true. Other non-cartoon tasks may be skippable. "
        "Output shape: {title,story_summary,slides:[{id,order,type,title,prompt,support_text,audio_text,image_prompt,video_query,options,correct_option_index,items,targets,accepted_meaning,target_phrase,companion_reply,expects_answer,can_skip,required_cartoon_line}]}"
    )
    payload={"model":settings.openai_text_model,"instructions":instructions,"input":f"Build the lesson about: {topic}"}
    headers={"Authorization":f"Bearer {settings.openai_api_key}","Content-Type":"application/json"}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r=await client.post('https://api.openai.com/v1/responses',headers=headers,json=payload)
        r.raise_for_status(); raw=_extract_output_text(r.json()).strip()
        if raw.startswith('```'):
            raw=raw.strip('`'); raw=raw[4:].strip() if raw.lower().startswith('json') else raw
        data=json.loads(raw)
        if not (18 <= len(data.get('slides') or []) <= 25): raise ValueError('slide count')
        return _normalize(data,topic,target_language,native_language)
    except Exception:
        return _fallback(topic,target_language,native_language,age,level,slide_count)


def save_free_topic_lesson(child_id: int, lesson: dict) -> Path:
    root=settings.storage_root/'children'/str(child_id)/'free-topic-lessons'; root.mkdir(parents=True,exist_ok=True)
    import time
    path=root/f"free_topic_{int(time.time())}.json"
    path.write_text(json.dumps(lesson,ensure_ascii=False,indent=2),encoding='utf-8'); return path
