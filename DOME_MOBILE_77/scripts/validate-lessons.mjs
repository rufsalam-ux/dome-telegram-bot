import {existsSync,readdirSync,readFileSync} from 'node:fs';
import {dirname,extname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const here=dirname(fileURLToPath(import.meta.url));
const lessonsRoot=resolve(here,'../../DOME_77/content/lessons');
const selfTest=process.argv.includes('--self-test');
const supported=new Set([
  'slide','video','ai_dialogue','voice_answer','choice','drag_drop','animal_description','break','activity',
  'passive','repeat','speak','dialogue','roleplay','guided_speaking','presentation','card_selector','choice_card','guided_scene','transition','drag_and_drop','animal_compare','animal_riddle','personal_travel_story','mood_choice',
  'tap_select','multi_select','multiple_choice','true_false','listen_choose','odd_one_out','fill_gap','matching','match_visible','memory','sorting','sequence','ordering','puzzle','interactive_scene','tap_sound','trace','draw','drawing','coloring','maze','dictation','read_aloud','read_roles','echo_reading','shared_reading','comprehension','retell','continue_story','physical_action','photo_task','real_world_find','video_pause_question','word_builder','syllable_builder','sentence_builder','find_in_text','connect_lines','handwriting_screen','letter_path','sound_position','syllable_split','visual_pack','mini_game',
]);
const imageExtensions=new Set(['.png','.jpg','.jpeg','.webp','.gif']);
const videoExtensions=new Set(['.mp4','.m4v','.webm']);
const audioExtensions=new Set(['.mp3','.m4a','.ogg','.wav']);

function lessonFiles(){
  if(!existsSync(lessonsRoot))throw new Error(`Папка уроков не найдена: ${lessonsRoot}`);
  return readdirSync(lessonsRoot,{withFileTypes:true}).filter(item=>item.isDirectory()).map(item=>resolve(lessonsRoot,item.name,'lesson.json')).filter(existsSync);
}

function stepLabel(lessonId,step,index){return `УРОК ${lessonId} / ШАГ ${index+1} (${String(step.slide_id||step.id||'?')})`}
function localReference(value){const source=String(value||'').trim();return source&&!/^https?:\/\//i.test(source)?source:''}
function checkMedia(errors,lessonDir,label,source,kind,optional=false){
  const value=String(source||'').trim();if(!value){if(!optional)errors.push(`${label}: не указан файл ${kind}`);return}
  if(/^http:\/\//i.test(value)){errors.push(`${label}: для внешнего ${kind} нужен безопасный адрес https://`);return}
  if(/^https:\/\//i.test(value))return;
  const extension=extname(value.split(/[?#]/)[0]).toLowerCase();const allowed=kind==='картинки'?imageExtensions:kind==='видео'?videoExtensions:audioExtensions;
  if(!allowed.has(extension)){errors.push(`${label}: формат ${extension||'<без расширения>'} не поддерживается для ${kind}`);return}
  const path=resolve(lessonDir,value);if(!existsSync(path)&&!optional)errors.push(`${label}: файл ${kind} ${value} не найден`);
}

function validateLesson(file,injectBroken=false){
  const lessonDir=dirname(file);let data;
  try{data=JSON.parse(readFileSync(file,'utf8'))}catch(error){return {id:lessonDir.split(/[\\/]/).pop(),count:0,errors:[`УРОК ${lessonDir}: JSON не читается — ${error.message}`]}}
  const lessonId=String(data.lesson_id||data.lessonId||lessonDir.split(/[\\/]/).pop());const configured=Array.isArray(data.steps)?data.steps:Array.isArray(data.slides)?data.slides:null;const errors=[];
  if(!configured)return {id:lessonId,count:0,errors:[`УРОК ${lessonId}: нужен список steps или slides`]};
  const steps=configured.map(item=>({...item}));if(injectBroken)steps.splice(Math.min(1,steps.length),0,{id:'validation_broken_video',type:'video',src:'videos/__missing_validation_test__.mp4'});
  const ids=new Set();const orders=new Set();
  for(let index=0;index<steps.length;index++){
    const step=steps[index];const label=stepLabel(lessonId,step,index);const id=String(step.slide_id||step.id||'').trim();const type=String(step.authoring_type||step.type||'').trim().toLowerCase();
    if(!id)errors.push(`${label}: не указан id`);else if(ids.has(id))errors.push(`${label}: id ${id} повторяется`);ids.add(id);
    if(step.order!==undefined){const order=Number(step.order);if(!Number.isInteger(order)||order<1)errors.push(`${label}: order должен быть целым числом больше 0`);else if(orders.has(order))errors.push(`${label}: order ${order} повторяется`);orders.add(order)}
    if(!supported.has(type))errors.push(`${label}: тип задания ${type||'<пусто>'} не поддерживается`);
    const target=String(step.ai_instruction||step.tutor_instruction||step.target_phrase||step.task_goal||step.bot_says_target||step.question||step.prompt||'').trim();
    if(['ai_dialogue','voice_answer','animal_description'].includes(type)&&!target)errors.push(`${label}: добавьте ai_instruction или target_phrase`);
    if(step.image)checkMedia(errors,lessonDir,label,step.image,'картинки');if(step.image_file)checkMedia(errors,lessonDir,label,step.image_file,'картинки');
    const directVideo=step.src&&type==='video'?step.src:(step.video_file||step.video_url);if(type==='video'||directVideo)checkMedia(errors,lessonDir,label,directVideo,'видео');
    if(step.poster)checkMedia(errors,lessonDir,label,step.poster,'картинки',true);
    for(const media of Array.isArray(step.media_sequence)?step.media_sequence:[]){const kind=String(media?.type||'').toLowerCase();const source=media?.src||media?.url;if(kind==='image')checkMedia(errors,lessonDir,label,source,'картинки');else if(kind==='video')checkMedia(errors,lessonDir,label,source,'видео');else if(kind==='audio')checkMedia(errors,lessonDir,label,source,'аудио');else if(!['animation','youtube'].includes(kind))errors.push(`${label}: media type ${kind||'<пусто>'} не поддерживается`)}
    const presentation=step.preSlideVideo||step.pre_slide_video;if(presentation&&presentation.enabled!==false)checkMedia(errors,lessonDir,label,presentation.uri||presentation.src||presentation.url,'видео');
    if(['drag_drop','drag_and_drop'].includes(type)){
      const items=Array.isArray(step.items)?step.items:Array.isArray(step.drag_items)?step.drag_items:[];const targets=Array.isArray(step.targets)?step.targets:[];
      if(!items.length)errors.push(`${label}: для drag/drop нужен хотя бы один предмет`);
      if(type==='drag_drop'&&!targets.length&&!step.drop_zone&&!step.drag_target_asset)errors.push(`${label}: для drag/drop нужна цель targets/drop_zone`);
      if(targets.length){const targetIds=new Set(targets.map(item=>String(item?.id||item)));for(const item of items){const targetId=String(item?.target_id||item?.target||'');if(targetId&&!targetIds.has(targetId))errors.push(`${label}: у предмета ${item?.id||'?'} указана несуществующая цель ${targetId}`)}}
    }
  }
  for(let index=0;index<steps.length;index++){const next=String(steps[index].next_step_id||steps[index].next||steps[index].next_slide||'').trim();if(next&&!ids.has(next))errors.push(`${stepLabel(lessonId,steps[index],index)}: следующий шаг ${next} не найден`)}
  if(data.languages!==undefined){if(!data.languages||typeof data.languages!=='object')errors.push(`УРОК ${lessonId}: languages должен содержать target и native`);else for(const key of ['target','native'])if(!/^[a-z]{2,3}$/i.test(String(data.languages[key]||'')))errors.push(`УРОК ${lessonId}: languages.${key} — код языка из 2–3 букв`)}
  return {id:lessonId,count:steps.length,errors};
}

let failures=0;for(const file of lessonFiles()){const result=validateLesson(file,selfTest&&file.includes(`${resolve(lessonsRoot,'demo_001')}`));if(result.errors.length){failures+=result.errors.length;for(const error of result.errors)console.error(`ОШИБКА: ${error}`)}else console.log(`OK: урок ${result.id} — ${result.count} шагов`)}
if(selfTest){if(!failures){console.error('ОШИБКА SELF-TEST: сломанная ссылка не была обнаружена');process.exit(2)}console.log('SELF-TEST PASS: отсутствующий тестовый MP4 обнаружен; рабочие файлы не изменялись.');process.exit(0)}
if(failures){console.error(`\nНайдено ошибок: ${failures}. Исправьте строки выше и снова запустите проверку.`);process.exit(1)}
console.log('\nВСЕ УРОКИ ПРОШЛИ ПРОВЕРКУ.');
