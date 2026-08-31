"use strict";

const TASK_TYPES=[
  ["passive","Информационный слайд"],["voice_answer","Голосовой ответ"],["dialogue","Диалог с AI"],
  ["choice","Выбор ответа"],["tap_select","Выбор картинки"],["multi_select","Несколько вариантов"],
  ["drag_drop","Перетаскивание"],["matching","Соответствия"],["memory","Игра на память"],
  ["puzzle","Пазл"],["video","Видео"],["repeat","Повтори фразу"],["listen_choose","Послушай и выбери"],
  ["drawing","Рисование"],["physical_action","Движение"],["card_selector","Карточки"],
  ["guided_speaking","Разговор с подсказками"],["animal_compare","Сравнение"],["animal_riddle","Загадка"]
];
const TYPE_LABEL=Object.fromEntries(TASK_TYPES);
const OPTION_TYPES=new Set(["choice","tap_select","multi_select","listen_choose","animal_riddle"]);
const DRAG_TYPES=new Set(["drag_drop","drag_and_drop"]);
const $=selector=>document.querySelector(selector);
const deepCopy=value=>JSON.parse(JSON.stringify(value));
const escapeHtml=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));

const state={token:sessionStorage.getItem("dome_studio_token")||"",lessons:[],lesson:null,lessonId:"",versions:[],media:[],dirty:false,dialogMode:"slide",dragIndex:null,blobUrls:new Map()};

function steps(){
  if(!state.lesson)return [];
  if(Array.isArray(state.lesson.steps))return state.lesson.steps;
  if(!Array.isArray(state.lesson.slides))state.lesson.slides=[];
  return state.lesson.slides;
}

function markDirty(){state.dirty=true;$("#saveButton").textContent="Сохранить •";clearNotice()}
function clearNotice(){$("#notice").classList.add("hidden")}
function notice(message){const box=$("#notice");box.textContent=message;box.classList.remove("hidden");$("#errorPanel").classList.add("hidden")}
function showErrors(errors){const list=Array.isArray(errors)?errors:[errors||"Неизвестная ошибка"];
  $("#errorPanel").innerHTML=`<strong>Исправьте перед сохранением:</strong><ul>${list.map(item=>`<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
  $("#errorPanel").classList.remove("hidden");$("#errorPanel").scrollIntoView({behavior:"smooth",block:"center"});
}

async function api(path,options={}){
  const headers={...(options.headers||{}),Authorization:`Bearer ${state.token}`};
  if(options.body&&!(options.body instanceof FormData))headers["Content-Type"]="application/json";
  const response=await fetch(path,{...options,headers});
  let data={};try{data=await response.json()}catch{data={error:`HTTP ${response.status}`}}
  if(response.status===401){sessionStorage.removeItem("dome_studio_token");state.token="";showLogin("Токен не принят. Проверьте его и войдите снова.");throw new Error("Требуется повторный вход")}
  if(!response.ok){const error=new Error(data.error||`HTTP ${response.status}`);error.details=data.errors||data.technical_errors||[];throw error}
  return data;
}

async function fetchBlob(path){
  if(state.blobUrls.has(path))return state.blobUrls.get(path);
  if(/^https?:\/\//i.test(path))return path;
  const encoded=encodeURIComponent(path);
  const url=path.startsWith("media/")?`/api/studio/lessons/${state.lessonId}/media/${encodeURIComponent(path.slice(6))}`:`/api/studio/lessons/${state.lessonId}/asset?path=${encoded}`;
  const response=await fetch(url,{headers:{Authorization:`Bearer ${state.token}`}});
  if(!response.ok)throw new Error(`Файл недоступен: ${path}`);
  const objectUrl=URL.createObjectURL(await response.blob());state.blobUrls.set(path,objectUrl);return objectUrl;
}

function releaseBlobs(){for(const url of state.blobUrls.values())if(url.startsWith("blob:"))URL.revokeObjectURL(url);state.blobUrls.clear()}
function showLogin(message=""){$("#loginView").classList.remove("hidden");$("#studioView").classList.add("hidden");$("#loginError").textContent=message;$("#connectionBadge").className="badge muted";$("#connectionBadge").textContent="Не подключено"}

async function login(){
  state.token=$("#tokenInput").value.trim();if(!state.token){showLogin("Введите токен владельца.");return}
  try{await api("/api/studio/status");sessionStorage.setItem("dome_studio_token",state.token);$("#loginView").classList.add("hidden");$("#studioView").classList.remove("hidden");$("#connectionBadge").className="badge connected";$("#connectionBadge").textContent="Content Studio подключена";await loadLessons()}
  catch(error){showLogin(error.message)}
}

async function loadLessons(){
  const data=await api("/api/studio/lessons");state.lessons=data.lessons||[];renderLessonList();
  if(state.lessonId&&state.lessons.some(item=>item.lesson_id===state.lessonId))highlightLesson();
}

function renderLessonList(){
  const query=$("#lessonSearch").value.trim().toLowerCase();const list=$("#lessonList");list.innerHTML="";
  state.lessons.filter(item=>`${item.lesson_id} ${item.title}`.toLowerCase().includes(query)).forEach(item=>{
    const button=document.createElement("button");button.className="lesson-item";button.dataset.id=item.lesson_id;
    button.innerHTML=`<strong>${escapeHtml(item.title||item.lesson_id)}</strong><span>${escapeHtml(item.lesson_id)} · ${escapeHtml(item.status||"draft")}</span>`;
    button.addEventListener("click",()=>openLesson(item.lesson_id));list.append(button);
  });
}
function highlightLesson(){document.querySelectorAll(".lesson-item").forEach(item=>item.classList.toggle("active",item.dataset.id===state.lessonId))}

async function openLesson(id){
  if(state.dirty&&!confirm("Есть несохранённые изменения. Открыть другой урок и потерять их?"))return;
  try{releaseBlobs();const data=await api(`/api/studio/lessons/${id}`);state.lesson=deepCopy(data.lesson);state.lessonId=id;state.versions=data.versions||[];state.media=data.media||[];state.dirty=false;
    $("#emptyState").classList.add("hidden");$("#editorBody").classList.remove("hidden");$("#lessonTitle").value=state.lesson.title||id;$("#lessonIdLabel").textContent=id;
    $("#lessonMeta").textContent=`Курс: ${state.lesson.course_id||"conversation"} · Статус: ${data.summary?.status||state.lesson.status||"draft"} · Ревизия: ${state.lesson.revision||1}`;
    $("#saveButton").textContent="Сохранить";$("#errorPanel").classList.add("hidden");clearNotice();renumber();renderSteps();highlightLesson();
  }catch(error){showErrors(error.details?.length?error.details:error.message)}
}

function renumber(){steps().forEach((step,index)=>{step.order=index+1;if(!step.slide_id)step.slide_id=uniqueId(step.type==="video"?"video":"step")})}
function uniqueId(prefix){const used=new Set(steps().map(item=>String(item.slide_id||item.id)));let index=steps().length+1;let value;do{value=`${prefix}_${String(index++).padStart(2,"0")}`}while(used.has(value));return value}
function sourceOf(step){
  const media=Array.isArray(step.media_sequence)?step.media_sequence.find(item=>item&&["image","video","animation"].includes(String(item.type||"").toLowerCase())):null;
  return String(media?.src||step.src||step.video_file||step.video_url||step.image||step.image_file||"");
}
function isVideo(step){return String(step.type||"").toLowerCase()==="video"||/\.(mp4|mov|m4v|webm)(\?|$)/i.test(sourceOf(step))}
function setMedia(step,path,video){
  if(video){step.type="video";step.src=path;step.video_file=path;delete step.video_url;step.media_sequence=[{id:"video",type:"video",src:path,autoplay:step.autoplay!==false,auto_continue:step.auto_continue!==false,skippable:step.skippable!==false,replay:step.replay!==false}]}
  else{step.image=path;step.image_file=path;step.src=path;const existing=Array.isArray(step.media_sequence)?step.media_sequence.filter(item=>!item||!["image","animation"].includes(String(item.type||"").toLowerCase())):[];step.media_sequence=[{id:"visual",type:"image",src:path},...existing]}
}

function taskTypeOptions(selected){return TASK_TYPES.map(([value,label])=>`<option value="${value}" ${value===selected?"selected":""}>${escapeHtml(label)}</option>`).join("")+(TYPE_LABEL[selected]?"":`<option value="${escapeHtml(selected)}" selected>${escapeHtml(selected||"Другой")}</option>`)}
function valueFrom(step,friendly,legacy=[]){const direct=String(step[friendly]||"").trim();if(direct)return direct;for(const key of legacy){const value=String(step[key]||"").trim();if(value)return value}return ""}
function listText(items){return (Array.isArray(items)?items:[]).map(item=>typeof item==="string"?item:(item?.label_ru||item?.label_en||item?.label||item?.text||item?.id||"")).filter(Boolean).join("\n")}
function answerValue(step){const mode=String(step.answer_mode||"");if(mode==="none")return "none";if(mode.includes("required")||step.requiredForMovie)return "required";if(mode.includes("optional"))return "optional";return "none"}

function renderSteps(){
  const host=$("#steps");host.innerHTML="";renumber();
  steps().forEach((step,index)=>host.append(renderStep(step,index)));
  if(!steps().length)host.innerHTML='<div class="empty card"><h3>В уроке пока нет шагов</h3><p>Добавьте первый слайд или видео.</p></div>';
}

function renderStep(step,index){
  const node=$("#stepTemplate").content.firstElementChild.cloneNode(true);node.dataset.index=String(index);const video=isVideo(step);const type=String(step.type||"passive");
  node.querySelector(".step-number").textContent=String(index+1);node.querySelector(".step-kind").textContent=video?"Видео":(TYPE_LABEL[type]||type);node.querySelector(".step-name").textContent=valueFrom(step,"target_phrase",["bot_says_target","task_goal","prompt"])||step.slide_id||`Шаг ${index+1}`;
  node.querySelector("[data-field=slide_id]").value=step.slide_id||"";node.querySelector("[data-field=type]").innerHTML=taskTypeOptions(type);
  node.querySelector("[data-field=ai_instruction]").value=valueFrom(step,"ai_instruction",["tutor_instruction"]);node.querySelector("[data-field=target_phrase]").value=valueFrom(step,"target_phrase",["bot_says_target","task_goal"]);node.querySelector("[data-field=native_explanation]").value=valueFrom(step,"native_explanation",["bot_says_native","native_hint"]);
  node.querySelector("[data-control=answer]").value=answerValue(step);node.querySelector("[data-control=continue]").value=step.continue_policy||"always";node.querySelector("[data-control=hint]").checked=step.hint_enabled!==false;node.querySelector("[data-control=follow]").checked=step.allow_ai_followup===true||step.follow_up_policy==="optional";
  node.querySelector("[data-list=options]").value=listText(step.selection_options||step.options);node.querySelector("[data-list=drag_items]").value=listText(step.drag_items||step.items);node.querySelector("[data-list=drag_targets]").value=listText(step.drag_targets||step.targets);
  node.querySelector(".task-options").classList.toggle("hidden",!OPTION_TYPES.has(type));node.querySelectorAll(".drag-items").forEach(item=>item.classList.toggle("hidden",!DRAG_TYPES.has(type)));node.querySelector(".video-options").classList.toggle("hidden",!video);
  node.querySelector("[data-video=autoplay]").checked=step.autoplay!==false;node.querySelector("[data-video=skippable]").checked=step.skippable!==false;node.querySelector("[data-video=replay]").checked=step.replay!==false;
  const source=sourceOf(step);node.querySelector(".media-path").textContent=source||"Медиафайл не выбран";node.querySelector(".replace-media").accept=video?"video/mp4,video/quicktime,video/webm":"image/*";renderMedia(node.querySelector(".media-preview"),source,video);

  node.querySelectorAll("[data-field]").forEach(field=>field.addEventListener("input",event=>updateField(index,event.target.dataset.field,event.target.value)));
  node.querySelectorAll("[data-control]").forEach(field=>field.addEventListener("change",event=>updateControl(index,event.target.dataset.control,event.target.type==="checkbox"?event.target.checked:event.target.value)));
  node.querySelectorAll("[data-list]").forEach(field=>field.addEventListener("change",event=>updateList(index,event.target.dataset.list,event.target.value)));
  node.querySelectorAll("[data-video]").forEach(field=>field.addEventListener("change",event=>updateVideo(index,event.target.dataset.video,event.target.checked)));
  node.querySelector(".replace-media").addEventListener("change",event=>replaceStepMedia(index,event.target.files?.[0]));
  node.querySelector(".move-up").addEventListener("click",()=>moveStep(index,index-1));node.querySelector(".move-down").addEventListener("click",()=>moveStep(index,index+1));
  node.querySelector(".duplicate-step").addEventListener("click",()=>duplicateStep(index));node.querySelector(".delete-step").addEventListener("click",()=>deleteStep(index));
  node.addEventListener("dragstart",event=>{state.dragIndex=index;node.classList.add("dragging");event.dataTransfer.effectAllowed="move";event.dataTransfer.setData("text/dome-lesson",String(index))});
  node.addEventListener("dragend",()=>{state.dragIndex=null;document.querySelectorAll(".step-card").forEach(item=>item.classList.remove("dragging","drop-before"))});
  node.addEventListener("dragover",event=>{event.preventDefault();node.classList.add("drop-before")});node.addEventListener("dragleave",()=>node.classList.remove("drop-before"));
  node.addEventListener("drop",event=>{event.preventDefault();node.classList.remove("drop-before");const from=Number(event.dataTransfer.getData("text/dome-lesson"));if(Number.isInteger(from))moveStep(from,index)});
  return node;
}

async function renderMedia(host,source,video){
  host.textContent=source?"Загружаем…":(video?"Выберите видео":"Изображение не выбрано");if(!source)return;
  try{const url=await fetchBlob(source);host.innerHTML="";const media=document.createElement(video?"video":"img");media.src=url;if(video)media.controls=true;media.alt="Предпросмотр";host.append(media)}catch(error){host.textContent=error.message}
}

function updateField(index,key,value){const step=steps()[index];step[key]=value;if(key==="target_phrase"){step.bot_says_target=value;step.task_goal=value;if(!step.prompt)step.prompt=value}if(key==="native_explanation"){step.bot_says_native=value;step.native_hint=value}if(key==="ai_instruction")step.tutor_instruction=value;if(key==="type")renderSteps();else{const card=document.querySelector(`.step-card[data-index="${index}"]`);if(card)card.querySelector(".step-name").textContent=valueFrom(step,"target_phrase",["bot_says_target","task_goal","prompt"])||step.slide_id}markDirty()}
function updateControl(index,key,value){const step=steps()[index];step.controls=step.controls&&typeof step.controls==="object"?step.controls:{};
  if(key==="answer"){const enabled=value!=="none";step.controls.answer={enabled,required:value==="required"};step.answer_mode=!enabled?"none":value==="required"?"required_voice":"optional_voice"}
  if(key==="continue"){step.controls.continue={enabled:true,when:value};step.continue_policy=value}
  if(key==="hint"){step.controls.hint={enabled:Boolean(value)};step.hint_enabled=Boolean(value)}
  if(key==="follow"){step.controls.follow_up={enabled:Boolean(value)};step.follow_up_policy=value?"optional":"none";step.allow_ai_followup=Boolean(value)}markDirty()}
function lines(value){return value.split(/\r?\n/).map(item=>item.trim()).filter(Boolean)}
function slug(value,index){return `${value.toLowerCase().replace(/[^a-zа-яё0-9]+/gi,"_").replace(/^_|_$/g,"")||"item"}_${index+1}`}
function updateList(index,key,value){const step=steps()[index];const values=lines(value);
  if(key==="options"){step.options=values;step.selection_options=values.map((label,i)=>({id:slug(label,i),label,label_ru:label}))}
  if(key==="drag_items"){step.drag_items=values.map((label,i)=>({id:slug(label,i),label,label_ru:label}));step.items=deepCopy(step.drag_items)}
  if(key==="drag_targets"){step.drag_targets=values.map((label,i)=>({id:slug(label,i),label,label_ru:label}));step.targets=deepCopy(step.drag_targets)}markDirty()}
function updateVideo(index,key,value){const step=steps()[index];step[key]=Boolean(value);if(key==="autoplay")step.autoplay=Boolean(value);const descriptor=Array.isArray(step.media_sequence)?step.media_sequence.find(item=>item?.type==="video"):null;if(descriptor)descriptor[key]=Boolean(value);markDirty()}
function moveStep(from,to){const list=steps();if(from<0||to<0||from>=list.length||to>=list.length||from===to)return;const [item]=list.splice(from,1);list.splice(to,0,item);renumber();markDirty();renderSteps()}
function duplicateStep(index){const copy=deepCopy(steps()[index]);copy.slide_id=uniqueId(`${copy.type||"step"}_copy`);copy.id=copy.slide_id;steps().splice(index+1,0,copy);renumber();markDirty();renderSteps();notice("Копия шага добавлена. Нажмите «Сохранить».")}
function deleteStep(index){const step=steps()[index];if(!confirm(`Удалить шаг «${step.slide_id||index+1}»? Это действие попадёт в черновик только после сохранения.`))return;steps().splice(index,1);renumber();markDirty();renderSteps()}

async function uploadFile(file){if(!file)throw new Error("Сначала выберите файл.");const body=new FormData();body.append("file",file);return api(`/api/studio/lessons/${state.lessonId}/media`,{method:"POST",body})}
async function replaceStepMedia(index,file){if(!file)return;const button=document.querySelector(`.step-card[data-index="${index}"] .file-button`);button.firstChild.textContent="Загружаем…";
  try{const asset=await uploadFile(file);setMedia(steps()[index],asset.path,isVideo(steps()[index]));releaseBlobs();markDirty();renderSteps();notice("Файл загружен и подставлен в шаг. Нажмите «Сохранить».")}
  catch(error){showErrors(error.details?.length?error.details:error.message);renderSteps()}}

function fillPosition(select){select.innerHTML=Array.from({length:steps().length+1},(_,index)=>`<option value="${index}">${index===steps().length?"В конец":`Перед шагом ${index+1}`}</option>`).join("");select.value=String(steps().length)}
function openAddDialog(mode){state.dialogMode=mode;$("#stepDialogTitle").textContent=mode==="video"?"Добавить видео":"Добавить слайд";$("#confirmAddStep").textContent=mode==="video"?"Добавить и сохранить":"Добавить";$("#slideFields").classList.toggle("hidden",mode==="video");$("#videoFields").classList.toggle("hidden",mode!=="video");fillPosition($("#slidePosition"));fillPosition($("#videoPosition"));$("#newSlideType").innerHTML=taskTypeOptions("passive");$("#newSlideFile").value="";$("#newVideoFile").value="";$("#newTargetPhrase").value="";$("#newNativeExplanation").value="";$("#newAiInstruction").value="";$("#stepDialog").showModal()}
async function confirmAdd(event){event.preventDefault();const button=$("#confirmAddStep");button.disabled=true;button.textContent="Добавляем…";
  try{
    let step,position;
    if(state.dialogMode==="video"){
      const file=$("#newVideoFile").files?.[0];if(!file)throw new Error("Выберите видеофайл MP4, MOV или WebM.");const asset=await uploadFile(file);position=Number($("#videoPosition").value);step={slide_id:uniqueId("video"),type:"video",src:asset.path,video_file:asset.path,autoplay:$("#videoAutoplay").checked,auto_continue:true,autoContinue:true,skippable:$("#videoSkippable").checked,replay:$("#videoReplay").checked,aspect_ratio:"16:9",requiredForMovie:false,media_sequence:[{id:"video",type:"video",src:asset.path,autoplay:$("#videoAutoplay").checked,auto_continue:true,skippable:$("#videoSkippable").checked,replay:$("#videoReplay").checked,aspect_ratio:"16:9"}]};
    }else{
      position=Number($("#slidePosition").value);const target=$("#newTargetPhrase").value.trim(),nativeText=$("#newNativeExplanation").value.trim(),ai=$("#newAiInstruction").value.trim(),type=$("#newSlideType").value;step={slide_id:uniqueId("step"),type,prompt:target||nativeText||ai||"Новый слайд",target_phrase:target,bot_says_target:target,task_goal:target,native_explanation:nativeText,bot_says_native:nativeText,native_hint:nativeText,ai_instruction:ai,tutor_instruction:ai,requiredForMovie:false,max_attempts:3,controls:{answer:{enabled:type==="voice_answer",required:type==="voice_answer"},continue:{enabled:true,when:type==="voice_answer"?"after_answer":"always"},hint:{enabled:true},follow_up:{enabled:type==="dialogue"}},answer_mode:type==="voice_answer"?"required_voice":"none",continue_policy:type==="voice_answer"?"after_answer":"always",hint_enabled:true,follow_up_policy:type==="dialogue"?"optional":"none",allow_ai_followup:type==="dialogue"};
      const file=$("#newSlideFile").files?.[0];if(file){const asset=await uploadFile(file);setMedia(step,asset.path,false)}
    }
    steps().splice(Math.max(0,Math.min(position,steps().length)),0,step);renumber();releaseBlobs();markDirty();renderSteps();$("#stepDialog").close();
    if(state.dialogMode==="video"){const saved=await save();if(saved)notice("Видео загружено, вставлено в выбранную позицию и сохранено.")}else notice("Слайд добавлен. Нажмите «Сохранить».")
  }catch(error){showErrors(error.details?.length?error.details:error.message)}finally{button.disabled=false;button.textContent=state.dialogMode==="video"?"Добавить и сохранить":"Добавить"}
}

function candidate(){const lesson=deepCopy(state.lesson);lesson.title=$("#lessonTitle").value.trim();const key=Array.isArray(lesson.steps)?"steps":"slides";lesson[key]=steps().map((step,index)=>({...deepCopy(step),order:index+1}));return lesson}
async function validateCandidate(lesson){const data=await api(`/api/studio/lessons/${state.lessonId}/validate`,{method:"POST",body:JSON.stringify({lesson})});if(!data.ok){showErrors(data.errors);return false}return true}
async function save(){
  const button=$("#saveButton");button.disabled=true;button.textContent="Проверяем…";$("#errorPanel").classList.add("hidden");
  try{const lesson=candidate();if(!await validateCandidate(lesson))return;button.textContent="Сохраняем…";const data=await api(`/api/studio/lessons/${state.lessonId}`,{method:"PUT",body:JSON.stringify({lesson})});state.lesson=deepCopy(data.lesson);state.dirty=false;state.versions=data.backup_version?[data.backup_version,...state.versions]:state.versions;button.textContent="Сохранить";notice(data.backup_version?`Сохранено. Резервная версия: ${data.backup_version}`:"Сохранено. Это первый черновик урока.");await loadLessons();return true}
  catch(error){showErrors(error.details?.length?error.details:error.message);return false}finally{button.disabled=false;button.textContent=state.dirty?"Сохранить •":"Сохранить"}}
async function publish(){if(state.dirty&&!await save())return;if(!confirm("Опубликовать проверенный черновик? Дети увидят новую версию урока."))return;try{const data=await api(`/api/studio/lessons/${state.lessonId}/publish`,{method:"POST"});state.lesson=deepCopy(data.lesson);state.dirty=false;notice("Урок опубликован. Предыдущая версия сохранена для отката.");await openLesson(state.lessonId);await loadLessons()}catch(error){showErrors(error.details?.length?error.details:error.message)}}

async function preview(){
  const lesson=candidate();if(!await validateCandidate(lesson))return;const host=$("#previewContent");host.innerHTML="";$("#previewTechnical").textContent=JSON.stringify(lesson,null,2);
  for(const [index,step] of (Array.isArray(lesson.steps)?lesson.steps:lesson.slides||[]).entries()){
    const video=isVideo(step),source=sourceOf(step),card=document.createElement("article");card.className="preview-step";card.innerHTML=`<div class="preview-media">${source?"Загружаем…":"Без изображения"}</div><div class="preview-copy"><div class="eyebrow">${index+1} · ${escapeHtml(video?"Видео":(TYPE_LABEL[step.type]||step.type||"Слайд"))}</div><h3>${escapeHtml(valueFrom(step,"target_phrase",["bot_says_target","task_goal","prompt"])||step.slide_id)}</h3><p>${escapeHtml(valueFrom(step,"native_explanation",["bot_says_native","native_hint"]))}</p><p class="muted">${escapeHtml(valueFrom(step,"ai_instruction",["tutor_instruction"]))}</p></div>`;host.append(card);if(source)renderMedia(card.querySelector(".preview-media"),source,video);
  }
  $("#previewDialog").showModal();
}

function showVersions(){const host=$("#versionList");host.innerHTML=state.versions.length?"":"<p>Резервных версий пока нет.</p>";state.versions.forEach(version=>{const row=document.createElement("div");row.className="version-row";row.innerHTML=`<span>${escapeHtml(version)}</span><button type="button">Восстановить в черновик</button>`;row.querySelector("button").addEventListener("click",()=>restoreVersion(version));host.append(row)});$("#versionDialog").showModal()}
async function restoreVersion(version){if(!confirm("Создать черновик из этой резервной версии? Текущий опубликованный урок останется активным."))return;try{await api(`/api/studio/lessons/${state.lessonId}/rollback`,{method:"POST",body:JSON.stringify({version,as_draft:true})});$("#versionDialog").close();await openLesson(state.lessonId);notice("Резервная версия восстановлена в черновик. Проверьте и сохраните/опубликуйте её.")}catch(error){showErrors(error.details?.length?error.details:error.message)}}

$("#loginButton").addEventListener("click",login);$("#tokenInput").addEventListener("keydown",event=>{if(event.key==="Enter")login()});$("#refreshLessons").addEventListener("click",loadLessons);$("#lessonSearch").addEventListener("input",renderLessonList);
$("#lessonTitle").addEventListener("input",markDirty);$("#addSlideButton").addEventListener("click",()=>openAddDialog("slide"));$("#addVideoButton").addEventListener("click",()=>openAddDialog("video"));$("#confirmAddStep").addEventListener("click",confirmAdd);
$("#saveButton").addEventListener("click",save);$("#publishButton").addEventListener("click",publish);$("#previewButton").addEventListener("click",preview);$("#restoreButton").addEventListener("click",showVersions);
window.addEventListener("beforeunload",event=>{if(state.dirty){event.preventDefault();event.returnValue=""}});

if(state.token){$("#tokenInput").value=state.token;login()}else showLogin();
