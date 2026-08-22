from __future__ import annotations

import argparse
import json
import re
import webbrowser
import zipfile
from pathlib import Path
from uuid import uuid4

from aiohttp import web
from app.core.config import settings
from app.services.lesson_importer import import_package

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
LESSONS = settings.storage_root / "authored-content" / "lessons"
COURSES = CONTENT / "courses"
REGISTRY_FILE = CONTENT / "templates" / "activity_types.json"
ANIMATION_FILE = settings.storage_root / "animation-library" / "manifest.json"
EXPORTS = ROOT / "exports"

for p in (LESSONS, COURSES, EXPORTS):
    p.mkdir(parents=True, exist_ok=True)

SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]{2,80}$")


def safe_id(value: str) -> str:
    value = value.strip().replace(" ", "_")
    if not SAFE_ID.fullmatch(value):
        raise web.HTTPBadRequest(text="ID: only letters, digits, _ and -")
    return value


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def lesson_manifest_path(lesson_id: str) -> Path:
    return LESSONS / lesson_id / "manifest.json"


def default_homework() -> dict:
    return {
        "enabled": False,
        "source": "manual",
        "optional": True,
        "duration_minutes": 5,
        "instructions": "",
        "activities": [],
        "send_to_bot": True,
        "send_to_parent_email": True,
        "allow_skip": True,
        "allow_defer": True,
        "keep_in_archive": True,
    }


def builder_index() -> str:
    return r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DOME — Курсы, уроки и домашние задания</title><style>
body{font-family:system-ui,-apple-system,sans-serif;max-width:1200px;margin:0 auto;padding:22px;background:#f6f7fb;color:#111827}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin:14px 0;box-shadow:0 2px 12px #0000000d}h1{margin:0 0 8px}h2{margin-top:0}input,select,textarea,button{font:inherit}input,select,textarea{width:100%;box-sizing:border-box;padding:9px;border:1px solid #cbd5e1;border-radius:9px}textarea{min-height:88px}button{border:0;border-radius:10px;padding:10px 14px;cursor:pointer;background:#111827;color:#fff}.secondary{background:#475569}.danger{background:#991b1b}.success{background:#166534}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}.row{display:grid;grid-template-columns:80px 1fr 230px 110px;gap:8px;align-items:center;border-top:1px solid #eee;padding:9px 0}.hwrow{display:grid;grid-template-columns:1fr 230px 80px;gap:8px;align-items:center;border-top:1px solid #eee;padding:9px 0}.pill{display:inline-block;padding:3px 8px;border-radius:20px;background:#e2e8f0;margin:2px;font-size:12px}.muted{color:#64748b;font-size:13px}.ok{color:#166534}.err{color:#991b1b}.preview{max-width:72px;max-height:52px;border-radius:6px;border:1px solid #ddd}.toolbar{display:flex;gap:8px;flex-wrap:wrap}.check{display:flex;gap:6px;align-items:center}.check input{width:auto}.notice{padding:10px 12px;border-radius:10px;background:#eef6ff;color:#1e3a5f;margin:10px 0}.warn{padding:10px 12px;border-radius:10px;background:#fff7e6;color:#7c4a03;margin:10px 0}</style></head><body>
<h1>DOME — Курсы, уроки и домашние задания</h1><p class="muted">Здесь вы наполняете DOME с компьютера. API-ключи остаются отдельно в .env / Railway Variables.</p>
<div class="notice">Урок и домашнее задание сохраняются как данные. Для уже поддерживаемых типов заданий код менять не нужно. Новая механика, которой движок ещё не умеет выполнять, всё ещё требует обновления движка.</div>
<div class="card"><h2>0. Быстро добавить готовый урок</h2><p class="muted">Загрузите урок PDF/PPTX, инструкцию преподавателя DOCX/PDF и ДЗ PDF/PPTX. DOME разрежет материалы на страницы, сопоставит указания со слайдами и создаст черновик. Перед публикацией проверьте карту заданий.</p><div class="grid"><label>ID урока<input id="impId" placeholder="reading_002"></label><label>Название<input id="impTitle" placeholder="Книжные истории · Встреча 2"></label><label>Курс<select id="impCourse"><option value="conversation">Разговорная практика</option><option value="learn_to_read">Читайка</option><option value="reading">Книжные истории</option></select></label><label>Урок PDF/PPTX<input id="impLesson" type="file" accept=".pdf,.pptx"></label><label>Инструкция DOCX/PDF<input id="impInstruction" type="file" accept=".docx,.pdf,.txt"></label><label>ДЗ PDF/PPTX<input id="impHomework" type="file" accept=".pdf,.pptx"></label></div><p><button class="success" onclick="importPackage()">Проанализировать и создать черновик</button></p><div id="impStatus"></div></div>
<div class="card"><h2>1. Курс</h2><div class="grid"><label>ID курса<input id="courseId" value="course_001"></label><label>Название<input id="courseTitle" value="Новый курс"></label><label>Цена<input id="coursePrice" type="number" step="0.01"></label><label>Валюта<select id="courseCurrency"><option>EUR</option><option>USD</option><option>GEL</option></select></label></div><p><button onclick="saveCourse()">Сохранить курс</button></p><div id="courses"></div></div>
<div class="card"><h2>1A. Порядок уроков и сезонные темы</h2><p class="muted">Введите ID уроков через запятую в общем порядке. Если у курса больше 1 нового урока в неделю, во время активного сезона DOME чередует: 1 тематический → 1 обычный по порядку → 1 тематический → 1 обычный. Если одна очередь закончилась, продолжает вторую.</p>
<div class="grid"><label>Новых уроков в неделю<input id="lessonsPerWeek" type="number" min="1" max="14" value="1"></label></div>
<label>Общий порядок уроков<textarea id="lessonOrder" placeholder="lesson_001, lesson_002, lesson_003"></textarea></label>
<div class="grid"><label>☀️ Летние уроки (1 июня — 31 августа)<textarea id="summerLessons" placeholder="lesson_010, lesson_011"></textarea></label><label>🎃 Halloween (17 октября — 3 ноября)<textarea id="halloweenLessons" placeholder="lesson_020, lesson_021"></textarea></label><label>❄️ Зима / Новый год (1 декабря — 28 февраля)<textarea id="winterLessons" placeholder="lesson_030, lesson_031"></textarea></label></div>
<p><button class="success" onclick="saveCourseSchedule()">Сохранить порядок и сезоны</button></p><div id="scheduleStatus"></div></div>
<div class="card"><h2>1B. Переходы между курсами</h2><p class="muted">Клиент может выбирать курс сам. Здесь задаётся рекомендуемый маршрут и параллельные курсы.</p><label>Рекомендуемые следующие курсы (ID через запятую)<textarea id="nextCourses" placeholder="conversation_2, reading_stories"></textarea></label><label>Курсы, которые можно проходить параллельно<textarea id="parallelCourses" placeholder="reading_basics"></textarea></label><label class="check"><input id="freeCourseChoice" type="checkbox" checked>Разрешить родителю самостоятельно выбирать доступный курс</label><p><button class="success" onclick="saveCourseFlow()">Сохранить переходы</button></p><div id="flowStatus"></div></div>
<div class="card"><h2>2. Урок</h2><div class="grid"><label>ID урока<input id="lessonId" value="lesson_001"></label><label>Название<input id="lessonTitle" value="Новый урок"></label><label>Курс<select id="lessonCourse"></select></label><label>Изучаемый язык<input id="targetLanguage" value="en"></label></div><p><button onclick="createLesson()">Создать / открыть урок</button></p><label>Общая инструкция к уроку<textarea id="lessonInstruction" placeholder="Например: урок живой, говорим в основном по-английски; обязательные реплики для мультфильма не пропускать; при ошибке 2 попытки + упрощение."></textarea></label><div id="lessons"></div><div id="lessonStatus"></div></div>
<div class="card"><h2>3. Слайды / материалы урока</h2><p>Выберите несколько PNG/JPG. Они сортируются по имени. Для каждого экрана задайте инструкцию AI и тип активности.</p><input id="slides" type="file" accept="image/*" multiple><p><button onclick="uploadSlides()">Загрузить слайды</button></p><div id="slideRows"></div></div>
<div class="card"><h2>4. Финальный мультфильм урока</h2><p class="muted">Загрузите готовый базовый MP4 без героя ребёнка. Ниже задайте, когда и где появляется герой. Координаты X/Y — пиксели от левого верхнего угла видео.</p>
<div class="grid"><label>Базовый мультфильм MP4<input id="cartoonBase" type="file" accept="video/mp4"></label><label>Длительность первой сцены героя, сек<input id="firstHeroSeconds" type="number" step="0.5" value="8"></label></div><p><button onclick="uploadCartoon()">Загрузить мультфильм</button></p><div id="cartoonStatus"></div>
<h3>Появления героя ребёнка</h3><div id="timelineRows"></div><p><button class="secondary" onclick="addTimeline()">+ Добавить появление героя</button></p></div>
<div class="card"><h2>4A. Animation Library</h2><p class="muted">Добавляйте с компьютера переиспользуемые варианты движения героя. В таймлайне используйте имя профиля. Для настоящего риггинга/AI-видео можно сохранить ссылку/путь на готовый asset после его создания.</p><div class="grid"><label>Название профиля<input id="animName" placeholder="walk_right_talk"></label><label>Направление<select id="animDirection"><option value="">нет</option><option value="left">влево</option><option value="right">вправо</option></select></label><label class="check"><input id="animTalk" type="checkbox" checked>Говорит / lip-sync</label><label class="check"><input id="animCamera" type="checkbox" checked>Смотрит в камеру</label></div><label>Описание движения для AI / аниматора<textarea id="animPrompt" placeholder="Идёт вправо, говорит, естественно двигает руками и ногами; затем останавливается и смотрит в камеру"></textarea></label><label>Готовый asset / cache key (необязательно)<input id="animAsset" placeholder="storage/animation-library/assets/walk_right_talk.mp4"></label><p><button onclick="saveAnimation()">+ Сохранить вариант анимации</button></p><div id="animationList"></div></div>
<div class="card"><h2>5. Правила урока</h2><div class="grid"><label class="check"><input id="adaptive" type="checkbox" checked>Адаптировать сложность</label><label class="check"><input id="gentle" type="checkbox" checked>Мягкая коррекция</label><label class="check"><input id="lowConf" type="checkbox" checked>Низкая уверенность AI ≠ ошибка ребёнка</label><label class="check"><input id="spaced" type="checkbox" checked>Возвращать сложные навыки позже</label></div></div>
<div class="card"><h2>6. Домашнее задание к этому уроку</h2>
<div class="grid"><label class="check"><input id="hwEnabled" type="checkbox">Отправлять ДЗ после урока</label><label>Источник<select id="hwSource"><option value="manual">Я создаю сама</option><option value="ai_auto">AI создаёт по результатам урока</option></select></label><label>Ориентировочное время, минут<input id="hwDuration" type="number" min="1" max="30" value="5"></label><label class="check"><input id="hwOptional" type="checkbox" checked>Необязательное</label></div>
<label>Текст / инструкция домашнего задания<textarea id="hwInstructions" placeholder="Например: Повтори 5 слов из урока и запиши 2 фразы голосом."></textarea></label>
<div class="grid"><label class="check"><input id="hwBot" type="checkbox" checked>Отправлять в бот</label><label class="check"><input id="hwEmail" type="checkbox" checked>Отправлять родителю на email</label><label class="check"><input id="hwSkip" type="checkbox" checked>Можно пропустить</label><label class="check"><input id="hwArchive" type="checkbox" checked>Оставлять в архиве</label></div>
<h3>Мини-задания внутри ДЗ</h3><p class="muted">Можно перечислить конкретные механики. Пока бот гарантированно отправляет их как план ДЗ; интерактивное выполнение доступно только для механик, которые уже реализованы движком.</p><div id="hwRows"></div><p><button class="secondary" onclick="addHomeworkActivity()">+ Добавить мини-задание</button></p></div>
<div class="card"><h2>7. Сохранение</h2><div class="toolbar"><button class="success" onclick="saveLesson()">Сохранить урок и ДЗ</button><button class="secondary" onclick="exportLesson()">Экспорт ZIP урока</button><a href="/" style="align-self:center">← Control Center</a></div><div id="saveStatus"></div></div>
<div class="card"><h2>Типы заданий движка</h2><div id="registry"></div></div>
<script>
let types=[], current=null, slides=[], hwActivities=[], timeline=[];
const $=x=>document.getElementById(x);
async function api(url,opt){const r=await fetch(url,opt);if(!r.ok)throw new Error(await r.text());return r.headers.get('content-type')?.includes('json')?r.json():r.text()}
async function load(){types=await api('/api/activity-types'); renderRegistry(); await refreshCourses(); await refreshLessons();}
function renderRegistry(){let g={};types.forEach(x=>(g[x.category]??=[]).push(x));$('registry').innerHTML=Object.entries(g).map(([k,v])=>`<b>${k}</b><div>${v.map(x=>`<span class="pill" title="${x.description_ru}">${x.title_ru}${x.implemented_now?' ✓':' (позже)'}</span>`).join('')}</div>`).join('<br>')}
async function refreshCourses(){let cs=await api('/api/courses');$('courses').innerHTML=cs.length?cs.map(c=>`<span class="pill">${c.title} (${c.course_id})</span>`).join(''):'Пока нет курсов';$('lessonCourse').innerHTML=cs.map(c=>`<option value="${c.course_id}">${c.title}</option>`).join('')}
async function refreshLessons(){let ls=await api('/api/lessons');$('lessons').innerHTML=ls.length?'<p class="muted">Существующие уроки: '+ls.map(x=>`<button class="secondary" style="padding:4px 8px;margin:2px" onclick="openExisting(\'${x.lesson_id}\')">${x.title||x.lesson_id}</button>`).join('')+'</p>':''}
async function openExisting(id){let d=await api('/api/lessons/'+id);current=d;slides=d.activities||[];$('lessonId').value=d.lesson_id;$('lessonTitle').value=d.title||d.lesson_id;$('targetLanguage').value=d.target_language||'en';$('lessonInstruction').value=(d.metadata&&d.metadata.lesson_instruction)||'';if([...$('lessonCourse').options].some(o=>o.value===d.course_id))$('lessonCourse').value=d.course_id;timeline=(d.cartoon&&d.cartoon.timeline)||[];$('firstHeroSeconds').value=(d.cartoon&&d.cartoon.first_child_scene_seconds)||8;fillHomework();renderSlides();renderTimeline();$('lessonStatus').innerHTML='<span class="ok">Урок открыт: '+d.lesson_id+'</span>'+(d.legacy_source?'<div class="warn">Это старый урок. Здесь безопасно редактируется его ДЗ; сам старый урок пока не переводится в новый универсальный формат автоматически.</div>':'')}
async function saveCourse(){let body={course_id:$('courseId').value,title:$('courseTitle').value,price:$('coursePrice').value?Number($('coursePrice').value):null,currency:$('courseCurrency').value};let d=await api('/api/courses',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});fillCourseSchedule(d);await refreshCourses()}
function csv(v){return v.split(',').map(x=>x.trim()).filter(Boolean)}
function fillCourseSchedule(c){$('lessonsPerWeek').value=c.lessons_per_week||1;$('lessonOrder').value=(c.lesson_ids||[]).join(', ');let ps=((c.seasonal||{}).periods||[]);let by={};ps.forEach(x=>by[x.id]=x);$('summerLessons').value=((by.summer||{}).lesson_ids||[]).join(', ');$('halloweenLessons').value=((by.halloween||{}).lesson_ids||[]).join(', ');$('winterLessons').value=((by.winter||{}).lesson_ids||[]).join(', ')}
async function saveCourseSchedule(){let cid=$('courseId').value;let body={lessons_per_week:+$('lessonsPerWeek').value||1,lesson_ids:csv($('lessonOrder').value),seasonal:{periods:[{id:'summer',title:'Лето',start:'06-01',end:'08-31',priority:20,enabled:true,lesson_ids:csv($('summerLessons').value)},{id:'halloween',title:'Halloween',start:'10-17',end:'11-03',priority:10,enabled:true,lesson_ids:csv($('halloweenLessons').value)},{id:'winter',title:'Зима / Новый год',start:'12-01',end:'02-28',priority:15,enabled:true,lesson_ids:csv($('winterLessons').value)}]}};let d=await api('/api/courses/'+cid+'/schedule',{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(body)});fillCourseSchedule(d);$('scheduleStatus').innerHTML='<span class="ok">Порядок и сезоны сохранены.</span>'}
async function saveCourseFlow(){let cid=$('courseId').value;let body={next_courses:csv($('nextCourses').value),parallel_courses:csv($('parallelCourses').value),free_choice:$('freeCourseChoice').checked};await api('/api/courses/'+cid+'/flow',{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(body)});$('flowStatus').innerHTML='<span class="ok">Маршрут сохранён.</span>'}
async function refreshAnimations(){let d=await api('/api/animations');$('animationList').innerHTML=Object.entries(d.animations||{}).map(([k,v])=>'<div class="pill"><b>'+esc(k)+'</b>: '+esc(v.description||v.direction||'готово')+'</div>').join('')}
async function saveAnimation(){let body={name:$('animName').value,direction:$('animDirection').value,talk:$('animTalk').checked,lip_sync:$('animTalk').checked,look_at:$('animCamera').checked?'camera':'scene',description:$('animPrompt').value,asset:$('animAsset').value,reusable:true};await api('/api/animations',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});await refreshAnimations()}
async function createLesson(){current={lesson_id:$('lessonId').value,title:$('lessonTitle').value,course_id:$('lessonCourse').value,target_language:$('targetLanguage').value,metadata:{lesson_instruction:$('lessonInstruction').value}};let d=await api('/api/lessons',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(current)});current=d;slides=d.activities||[];fillHomework();$('lessonStatus').innerHTML='<span class="ok">Урок открыт: '+d.lesson_id+'</span>';renderSlides();await refreshLessons()}
async function uploadSlides(){if(!current)return alert('Сначала создай урок');let fd=new FormData();for(let f of $('slides').files)fd.append('slides',f);let d=await api('/api/lessons/'+current.lesson_id+'/slides',{method:'POST',body:fd});current=d;slides=d.activities||[];renderSlides()}
function typeOptions(sel, onlyImplemented=false){return types.filter(t=>!onlyImplemented||t.implemented_now).map(t=>`<option value="${t.id}" ${t.id==sel?'selected':''}>${t.title_ru}${t.implemented_now?'':' (ещё не исполняется)'}</option>`).join('')}
function renderSlides(){$('slideRows').innerHTML=slides.map((s,i)=>`<div class="card" style="margin:8px 0;padding:12px"><div class="grid"><div>${s.image?`<img class="preview" src="/lesson-media/${current.lesson_id}/${s.image}">`:(i+1)}<br><b>Экран ${i+1}</b></div><label>Тип задания<select onchange="slides[${i}].type=this.value">${typeOptions(s.type||'speak')}</select></label><label class="check"><input type="checkbox" ${s.required?'checked':''} onchange="slides[${i}].required=this.checked;slides[${i}].allow_skip=!this.checked">Обязательно / нельзя пропустить</label></div><label>Инструкция боту<textarea oninput="slides[${i}].instruction=this.value" placeholder="Что бот должен объяснить, спросить и проверить?">${s.instruction||''}</textarea></label><div class="grid"><label>Фраза/вопрос на изучаемом языке<input value="${(s.prompt||'').replaceAll('\"','&quot;')}" oninput="slides[${i}].prompt=this.value"></label><label>Пояснение на языке ребёнка<input value="${(s.native_hint||'').replaceAll('\"','&quot;')}" oninput="slides[${i}].native_hint=this.value"></label><label>Обязательная фраза для мультфильма<input value="${(s.cartoon_phrase||'').replaceAll('\"','&quot;')}" oninput="slides[${i}].cartoon_phrase=this.value;slides[${i}].required=!!this.value;if(this.value)slides[${i}].allow_skip=false" placeholder="Напр.: Look! I found diamonds!"></label><label>Варианты / объекты (через |)<input value="${((s.options||[]).join(' | ')).replaceAll('\"','&quot;')}" oninput="slides[${i}].options=this.value.split('|').map(x=>x.trim()).filter(Boolean)"></label></div></div>`).join('')}
function renderTimeline(){$('timelineRows').innerHTML=timeline.map((t,i)=>`<div class="card" style="padding:10px"><div class="grid"><label>Начало, сек<input type="number" step="0.1" value="${t.visible_start??0}" oninput="timeline[${i}].visible_start=+this.value"></label><label>Начало речи, сек<input type="number" step="0.1" value="${t.talk_start??t.visible_start??0}" oninput="timeline[${i}].talk_start=+this.value"></label><label>Конец, сек<input type="number" step="0.1" value="${t.end??5}" oninput="timeline[${i}].end=+this.value"></label><label>X<input type="number" value="${t.x??t.x_end??100}" oninput="timeline[${i}].x=+this.value"></label><label>Y<input type="number" value="${t.y??200}" oninput="timeline[${i}].y=+this.value"></label><label>Высота героя, px<input type="number" value="${t.height??220}" oninput="timeline[${i}].height=+this.value"></label><label>Движение<select onchange="timeline[${i}].animation=this.value"><option value="stand_front_talk" ${t.animation==='stand_front_talk'?'selected':''}>Стоит и говорит</option><option value="walk_from_left" ${t.animation==='walk_from_left'?'selected':''}>Выходит слева</option><option value="walk_left_to_right_talk" ${t.animation==='walk_left_to_right_talk'?'selected':''}>Идёт вправо и говорит</option><option value="walk_right_to_left_talk" ${t.animation==='walk_right_to_left_talk'?'selected':''}>Идёт влево и говорит</option></select></label><label>ID обязательной фразы<input value="${t.phrase_id||''}" oninput="timeline[${i}].phrase_id=this.value" placeholder="phrase_01"></label></div><button class="danger" onclick="timeline.splice(${i},1);renderTimeline()">Удалить</button></div>`).join('')}
function addTimeline(){timeline.push({phrase_id:'phrase_'+String(timeline.length+1).padStart(2,'0'),visible_start:0,talk_start:0,end:8,x:100,y:200,height:220,animation:'stand_front_talk',layer:3,disappear:'fade_out'});renderTimeline()}
async function uploadCartoon(){if(!current)return alert('Сначала открой урок');let f=$('cartoonBase').files[0];if(!f)return alert('Выбери MP4');let fd=new FormData();fd.append('cartoon',f);let d=await api('/api/lessons/'+current.lesson_id+'/cartoon',{method:'POST',body:fd});current=d;$('cartoonStatus').innerHTML='<span class="ok">Мультфильм загружен.</span>'}
function fillHomework(){let h=current?.homework||{};$('hwEnabled').checked=!!h.enabled;$('hwSource').value=h.source||'manual';$('hwDuration').value=h.duration_minutes||5;$('hwOptional').checked=h.optional!==false;$('hwInstructions').value=h.instructions||'';$('hwBot').checked=h.send_to_bot!==false;$('hwEmail').checked=h.send_to_parent_email!==false;$('hwSkip').checked=h.allow_skip!==false;$('hwArchive').checked=h.keep_in_archive!==false;hwActivities=h.activities||[];renderHomeworkActivities()}
function renderHomeworkActivities(){$('hwRows').innerHTML=hwActivities.map((a,i)=>`<div class="hwrow"><input value="${(a.instruction||'').replaceAll('"','&quot;')}" oninput="hwActivities[${i}].instruction=this.value" placeholder="Что сделать дома?"><select onchange="hwActivities[${i}].type=this.value">${typeOptions(a.type||'speak')}</select><button class="danger" onclick="hwActivities.splice(${i},1);renderHomeworkActivities()">×</button></div>`).join('')}
function addHomeworkActivity(){hwActivities.push({id:'homework_'+String(hwActivities.length+1).padStart(2,'0'),type:'speak',instruction:'',required:false,allow_skip:true});renderHomeworkActivities()}
async function saveLesson(){if(!current)return;current.title=$('lessonTitle').value;current.course_id=$('lessonCourse').value;current.target_language=$('targetLanguage').value;current.activities=slides;current.cartoon={...(current.cartoon||{}),base_file:(current.cartoon&&current.cartoon.base_file)||null,first_child_scene_seconds:+$('firstHeroSeconds').value||8,timeline:timeline};current.metadata={...(current.metadata||{}),lesson_instruction:$('lessonInstruction').value,adaptive:$('adaptive').checked,spaced_repetition:$('spaced').checked,feedback_default:$('gentle').checked?'gentle':'normal',low_confidence_is_not_error:$('lowConf').checked};current.homework={enabled:$('hwEnabled').checked,source:$('hwSource').value,optional:$('hwOptional').checked,duration_minutes:+$('hwDuration').value||5,instructions:$('hwInstructions').value,activities:hwActivities,send_to_bot:$('hwBot').checked,send_to_parent_email:$('hwEmail').checked,allow_skip:$('hwSkip').checked,allow_defer:true,keep_in_archive:$('hwArchive').checked};let d=await api('/api/lessons/'+current.lesson_id,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(current)});current=d;slides=d.activities||[];fillHomework();$('saveStatus').innerHTML='<span class="ok">Сохранено. Урок и ДЗ лежат в content/lessons/'+current.lesson_id+'/manifest.json.</span>';await refreshLessons()}
function exportLesson(){if(!current)return;location.href='/api/lessons/'+current.lesson_id+'/export'}
async function importPackage(){let lf=$('impLesson').files[0];if(!lf)return alert('Выберите файл урока');let fd=new FormData();fd.append('lesson_id',$('impId').value);fd.append('title',$('impTitle').value||$('impId').value);fd.append('course_id',$('impCourse').value);fd.append('lesson',lf);let inf=$('impInstruction').files[0],hw=$('impHomework').files[0];if(inf)fd.append('instruction',inf);if(hw)fd.append('homework',hw);$('impStatus').textContent='Обрабатываю материалы…';let d=await api('/api/import-package',{method:'POST',body:fd});$('impStatus').innerHTML='<span class="ok">Черновик создан: '+d.lesson_id+'. Проверьте типы заданий перед публикацией.</span>';await refreshLessons()}
load().catch(e=>$('saveStatus').innerHTML='<span class="err">'+e+'</span>')
</script></body></html>'''


async def index(_: web.Request):
    return web.Response(text=builder_index(), content_type="text/html")


async def activity_types(_: web.Request):
    return web.json_response(read_json(REGISTRY_FILE, []))


async def get_courses(_: web.Request):
    return web.json_response([read_json(x, {}) for x in sorted(COURSES.glob("*.json"))])


async def get_lessons(_: web.Request):
    items = []
    seen = set()
    for p in sorted(LESSONS.glob("*/manifest.json")):
        d = read_json(p, {})
        if d:
            lid = d.get("lesson_id", p.parent.name); seen.add(lid)
            items.append({"lesson_id": lid, "title": d.get("title", p.parent.name), "course_id": d.get("course_id"), "legacy": False})
    for p in sorted(LESSONS.glob("*/lesson.json")):
        d = read_json(p, {})
        lid = d.get("lesson_id", p.parent.name)
        if d and lid not in seen:
            items.append({"lesson_id": lid, "title": d.get("title", p.parent.name), "course_id": d.get("course_id"), "legacy": True})
    return web.json_response(items)


async def get_lesson(request: web.Request):
    lid = safe_id(request.match_info["lesson_id"])
    d = read_json(lesson_manifest_path(lid), None)
    if d is not None:
        d.setdefault("homework", default_homework())
        d["legacy_source"] = False
        return web.json_response(d)
    legacy_path = LESSONS / lid / "lesson.json"
    legacy = read_json(legacy_path, None)
    if legacy is None:
        raise web.HTTPNotFound(text="Lesson not found")
    return web.json_response({
        "schema_version": "legacy-wrapper-1", "lesson_id": lid, "title": legacy.get("title", lid),
        "course_id": legacy.get("course_id") or "demo_english", "target_language": legacy.get("target_language") or "en",
        "activities": [], "metadata": {}, "homework": legacy.get("homework") or default_homework(),
        "legacy_source": True
    })


async def post_course(request: web.Request):
    d = await request.json(); cid = safe_id(d.get("course_id", ""))
    data = {"schema_version": "1.0", "course_id": cid, "title": str(d.get("title") or cid), "description": str(d.get("description") or ""), "price": d.get("price"), "currency": d.get("currency") or "EUR", "active": True, "lesson_ids": [], "lessons_per_week": 1, "metadata": {"access_model": "per_course"}}
    old = read_json(COURSES / f"{cid}.json", {}); data["lesson_ids"] = old.get("lesson_ids", []); data["lessons_per_week"] = old.get("lessons_per_week", 1)
    if old.get("seasonal") is not None:
        data["seasonal"] = old.get("seasonal")
    write_json(COURSES / f"{cid}.json", data); return web.json_response(data)


async def post_lesson(request: web.Request):
    d = await request.json(); lid = safe_id(d.get("lesson_id", "")); cid = safe_id(d.get("course_id", ""))
    path = lesson_manifest_path(lid)
    if path.exists():
        current = read_json(path, {})
        current.setdefault("homework", default_homework())
        return web.json_response(current)
    data = {"schema_version": "2.1", "lesson_id": lid, "course_id": cid, "title": str(d.get("title") or lid), "target_language": d.get("target_language") or "en", "native_language_mode": "child_profile", "activities": [], "homework": default_homework(), "metadata": {"adaptive": True, "feedback_default": "gentle", "low_confidence_is_not_error": True}}
    write_json(path, data)
    cp = COURSES / f"{cid}.json"; course = read_json(cp, {"schema_version": "1.0", "course_id": cid, "title": cid, "active": True, "lesson_ids": []}); course.setdefault("lesson_ids", [])
    if lid not in course["lesson_ids"]: course["lesson_ids"].append(lid)
    write_json(cp, course); return web.json_response(data)


async def upload_slides(request: web.Request):
    lid = safe_id(request.match_info['lesson_id']); path = lesson_manifest_path(lid)
    data = read_json(path, None)
    if data is None: raise web.HTTPNotFound(text="Lesson not found")
    folder = LESSONS / lid / 'lesson-images'; folder.mkdir(parents=True, exist_ok=True)
    reader = await request.multipart(); incoming = []
    while True:
        part = await reader.next()
        if part is None: break
        if part.name != 'slides': continue
        ext = Path(part.filename or '').suffix.lower()
        if ext not in {'.png', '.jpg', '.jpeg', '.webp'}: continue
        incoming.append((part.filename or str(uuid4()), await part.read(decode=False), ext))
    incoming.sort(key=lambda x: x[0].lower())
    activities = []
    for i, (name, blob, ext) in enumerate(incoming, 1):
        fn = f"slide-{i:03d}{ext}"; (folder / fn).write_bytes(blob)
        activities.append({"id": f"activity_{i:03d}", "type": "speak", "instruction": "", "prompt": "", "required": False, "allow_skip": True, "max_attempts": 3, "target_language_required": False, "waits_for_answer": True, "cartoon_phrase_id": None, "feedback": {"mode": "gentle", "max_corrections_per_block": 1, "ignore_minor_errors": True, "low_confidence_is_not_error": True, "encouragement_after_correction": True}, "config": {}, "image": f"lesson-images/{fn}"})
    data['activities'] = activities; data.setdefault('homework', default_homework()); write_json(path, data); return web.json_response(data)


async def put_lesson(request: web.Request):
    lid = safe_id(request.match_info['lesson_id']); d = await request.json()
    if d.get('lesson_id') != lid: d['lesson_id'] = lid
    if d.get("legacy_source"):
        legacy_path = LESSONS / lid / "lesson.json"
        legacy = read_json(legacy_path, None)
        if legacy is None:
            raise web.HTTPNotFound(text="Legacy lesson not found")
        legacy["homework"] = d.get("homework") or default_homework()
        write_json(legacy_path, legacy)
        return web.json_response(d)
    allowed = {x['id'] for x in read_json(REGISTRY_FILE, [])}
    for a in d.get('activities', []):
        if a.get('type') not in allowed: raise web.HTTPBadRequest(text=f"Unknown activity type: {a.get('type')}")
        if a.get('required'): a['allow_skip'] = False
    h = d.setdefault('homework', default_homework())
    for a in h.get('activities', []):
        if a.get('type') not in allowed: raise web.HTTPBadRequest(text=f"Unknown homework activity type: {a.get('type')}")
        a['required'] = False
        a['allow_skip'] = True
    h['optional'] = True if h.get('optional', True) else False
    h['duration_minutes'] = max(1, min(30, int(h.get('duration_minutes', 5) or 5)))
    write_json(lesson_manifest_path(lid), d)
    runtime = compile_runtime_lesson(d)
    write_json(LESSONS / lid / "lesson.json", runtime)
    return web.json_response(d)


def _csv_lesson_ids(value):
    return [str(x).strip() for x in (value or []) if str(x).strip()]


async def put_course_schedule(request: web.Request):
    cid = safe_id(request.match_info["course_id"])
    path = COURSES / f"{cid}.json"
    course = read_json(path, None)
    if course is None:
        raise web.HTTPNotFound(text="Course not found")
    body = await request.json()
    course["lesson_ids"] = _csv_lesson_ids(body.get("lesson_ids"))
    course["lessons_per_week"] = max(1, min(14, int(body.get("lessons_per_week", course.get("lessons_per_week", 1)) or 1)))
    seasonal = body.get("seasonal") or {}
    periods = []
    for item in seasonal.get("periods") or []:
        periods.append({
            "id": str(item.get("id") or "season"), "title": str(item.get("title") or ""),
            "start": str(item.get("start") or "01-01"), "end": str(item.get("end") or "12-31"),
            "priority": int(item.get("priority", 100)), "enabled": bool(item.get("enabled", True)),
            "lesson_ids": _csv_lesson_ids(item.get("lesson_ids")),
        })
    course["seasonal"] = {"periods": periods}
    write_json(path, course)
    return web.json_response(course)


async def upload_cartoon(request: web.Request):
    lid = safe_id(request.match_info["lesson_id"])
    path = lesson_manifest_path(lid)
    data = read_json(path, None)
    if data is None:
        raise web.HTTPNotFound(text="Lesson not found")
    reader = await request.multipart()
    part = await reader.next()
    if part is None or part.name != "cartoon":
        raise web.HTTPBadRequest(text="MP4 is required")
    ext = Path(part.filename or "").suffix.lower()
    if ext != ".mp4":
        raise web.HTTPBadRequest(text="Only MP4 is supported")
    folder = LESSONS / lid
    out = folder / "cartoon-base.mp4"
    out.write_bytes(await part.read(decode=False))
    cartoon = data.setdefault("cartoon", {})
    cartoon["base_file"] = "cartoon-base.mp4"
    cartoon.setdefault("first_child_scene_seconds", 8)
    cartoon.setdefault("timeline", [])
    write_json(path, data)
    return web.json_response(data)


def compile_runtime_lesson(d: dict) -> dict:
    required_phrases = []
    slides = []
    for i, a in enumerate(d.get("activities") or [], 1):
        slide_id = f"slide_{i:02d}"
        phrase_id = None
        cartoon_phrase = str(a.get("cartoon_phrase") or "").strip()
        if cartoon_phrase:
            phrase_id = a.get("cartoon_phrase_id") or f"phrase_{i:02d}"
            required_phrases.append({
                "phrase_id": phrase_id, "target_text": cartoon_phrase,
                "native_hint": a.get("native_hint") or a.get("instruction") or "",
                "accepted_meaning": a.get("accepted_meaning") or [],
                "simplified_text": a.get("simplified_text") or cartoon_phrase,
                "image": a.get("image"),
            })
        typ = a.get("type") or "speak"
        legacy_type = {"tap_select": "image_choice"}.get(typ, typ)
        answer_mode = "required_voice" if (phrase_id or typ in {"speak","voice_answer","dialogue","repeat","roleplay","read_aloud"}) else "none"
        slide = {
            "slide_id": slide_id, "order": i, "image": a.get("image"), "type": legacy_type,
            "bot_says_target": a.get("prompt") or a.get("instruction") or "",
            "bot_explains_native": a.get("native_hint") or "",
            "question": a.get("prompt") or "", "answer_mode": answer_mode,
            "expects_answer": answer_mode != "none" or typ in {"image_choice","object_click","card_selector","mood_choice"},
            "required_phrase_id": phrase_id, "allow_skip": False if (phrase_id or a.get("required")) else bool(a.get("allow_skip", True)),
            "adaptive": True, "max_voice_seconds": 5 if phrase_id else 60,
        }
        if typ in {"drag_drop", "memory"}:
            slide["interactive_task"] = typ
            slide["task_items"] = list(a.get("options") or [])
            slide["task_targets"] = list((a.get("config") or {}).get("targets") or a.get("options") or [])
            slide["expects_answer"] = True
        options = a.get("options") or []
        if typ in {"image_choice","object_click"}:
            slide["image_choices"] = [{"id": str(n), "label_ru": str(x)} for n,x in enumerate(options)]
        slides.append(slide)
    cartoon = d.get("cartoon") or {}
    timeline = cartoon.get("timeline") or []
    if required_phrases and timeline:
        valid = {x["phrase_id"] for x in required_phrases}
        for n,t in enumerate(timeline):
            if not t.get("phrase_id") or t.get("phrase_id") not in valid:
                t["phrase_id"] = required_phrases[min(n, len(required_phrases)-1)]["phrase_id"]
    return {
        "schema_version": "builder-runtime-1", "lesson_id": d.get("lesson_id"), "course_id": d.get("course_id"),
        "title": d.get("title"), "target_language": d.get("target_language") or "en",
        "slides": slides, "required_phrases": required_phrases,
        "cartoon_base": cartoon.get("base_file") or "cartoon-base.mp4", "timeline": timeline,
        "homework": d.get("homework") or default_homework(),
        "builder_managed": True,
    }


async def export_lesson(request: web.Request):
    lid = safe_id(request.match_info['lesson_id']); src = LESSONS / lid
    if not src.exists(): raise web.HTTPNotFound()
    out = EXPORTS / f"{lid}.zip"
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in src.rglob('*'):
            if p.is_file(): z.write(p, Path(lid) / p.relative_to(src))
    return web.FileResponse(out, headers={'Content-Disposition': f'attachment; filename="{lid}.zip"'})


async def lesson_media(request: web.Request):
    lid = safe_id(request.match_info['lesson_id']); tail = request.match_info['tail']
    p = (LESSONS / lid / tail).resolve(); root = (LESSONS / lid).resolve()
    if root not in p.parents or not p.is_file(): raise web.HTTPNotFound()
    return web.FileResponse(p)


async def get_animations(request: web.Request):
    from app.services.animation_library import ensure_animation_library
    ensure_animation_library(ANIMATION_FILE.parent)
    return web.json_response(read_json(ANIMATION_FILE,{"version":2,"animations":{}}))

async def post_animation(request: web.Request):
    from app.services.animation_library import ensure_animation_library
    ensure_animation_library(ANIMATION_FILE.parent)
    body=await request.json(); name=safe_id(str(body.get("name") or ""))
    data=read_json(ANIMATION_FILE,{"version":2,"animations":{},"generated_assets":{}})
    profile={
      "description":str(body.get("description") or ""),"direction":str(body.get("direction") or ""),
      "talk":bool(body.get("talk",False)),"lip_sync":bool(body.get("lip_sync",False)),
      "look_at":str(body.get("look_at") or "scene"),"asset":str(body.get("asset") or ""),
      "reusable":bool(body.get("reusable",True)),
    }
    data.setdefault("animations",{})[name]=profile
    if profile["asset"]: data.setdefault("generated_assets",{})[name]=profile["asset"]
    write_json(ANIMATION_FILE,data); return web.json_response(data)

async def put_course_flow(request: web.Request):
    cid=safe_id(request.match_info["course_id"]); path=COURSES/f"{cid}.json"; course=read_json(path,None)
    if course is None: raise web.HTTPNotFound(text="Course not found")
    body=await request.json(); course["flow"]={"next_courses":_csv_lesson_ids(body.get("next_courses")),"parallel_courses":_csv_lesson_ids(body.get("parallel_courses")),"free_choice":bool(body.get("free_choice",True))}
    write_json(path,course); return web.json_response(course)


async def import_package_endpoint(request: web.Request):
    reader=await request.multipart(); fields={}; files={}
    while True:
        part=await reader.next()
        if part is None: break
        if part.filename:
            tmp=settings.storage_root/'imports'/(part.filename or 'upload.bin'); tmp.parent.mkdir(parents=True,exist_ok=True); tmp.write_bytes(await part.read(decode=False)); files[part.name]=tmp
        else:
            fields[part.name]=(await part.text()).strip()
    if 'lesson' not in files: raise web.HTTPBadRequest(text='lesson file required')
    try:
        data=import_package(lesson_id=fields.get('lesson_id',''),course_id=fields.get('course_id','conversation'),title=fields.get('title') or fields.get('lesson_id',''),lesson_file=files['lesson'],instruction_file=files.get('instruction'),homework_file=files.get('homework'),target_language='ru',order=999)
    except Exception as exc:
        raise web.HTTPBadRequest(text=str(exc))
    return web.json_response(data)


def register_routes(app: web.Application, include_index: bool = True) -> None:
    if include_index:
        app.router.add_get('/', index)
    else:
        app.router.add_get('/builder', index)
    app.router.add_post('/api/import-package', import_package_endpoint); app.router.add_get('/api/activity-types', activity_types); app.router.add_get('/api/animations', get_animations); app.router.add_post('/api/animations', post_animation)
    app.router.add_get('/api/courses', get_courses); app.router.add_post('/api/courses', post_course); app.router.add_put('/api/courses/{course_id}/schedule', put_course_schedule); app.router.add_put('/api/courses/{course_id}/flow', put_course_flow)
    app.router.add_get('/api/lessons', get_lessons); app.router.add_post('/api/lessons', post_lesson)
    app.router.add_get('/api/lessons/{lesson_id}', get_lesson)
    app.router.add_post('/api/lessons/{lesson_id}/slides', upload_slides); app.router.add_post('/api/lessons/{lesson_id}/cartoon', upload_cartoon)
    app.router.add_put('/api/lessons/{lesson_id}', put_lesson); app.router.add_get('/api/lessons/{lesson_id}/export', export_lesson)
    app.router.add_get('/lesson-media/{lesson_id}/{tail:.*}', lesson_media)


def make_app():
    app = web.Application(client_max_size=500 * 1024 * 1024)
    register_routes(app, include_index=True)
    return app


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--host', default='127.0.0.1'); ap.add_argument('--port', type=int, default=8765); ap.add_argument('--no-browser', action='store_true'); args = ap.parse_args()
    if not args.no_browser:
        import threading, time
        threading.Thread(target=lambda: (time.sleep(.8), webbrowser.open(f'http://{args.host}:{args.port}')), daemon=True).start()
    print(f'DOME Lesson Builder: http://{args.host}:{args.port}')
    web.run_app(make_app(), host=args.host, port=args.port, print=None)


if __name__ == '__main__': main()
