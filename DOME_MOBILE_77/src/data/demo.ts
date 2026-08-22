import { LessonManifest } from '../types/lesson';
const q=(id:string,title:string,prompt:string,options:string[],correct:number)=>({id,order:+id.replace(/\D/g,'')||1,title,components:[{id:id+'q',type:'single_choice' as const,prompt,voicePrompt:prompt,required:true,canSkip:false,maxAttempts:3,payload:{options,correct}}]});
const v=(id:string,title:string,prompt:string,goal:string)=>({id,order:+id.replace(/\D/g,'')||1,title,components:[{id:id+'v',type:'voice_answer' as const,prompt,voicePrompt:prompt,required:true,canSkip:false,maxAttempts:3,payload:{maxSeconds:5,semanticGoal:goal}}]});
export const demoLesson:LessonManifest={id:'travel_001',courseId:'conversation_ru',version:3,title:'Путешествие',learningLanguage:'ru',moviePolicy:'each_completion',homeworkPolicy:'first_completion_only',maxCompletions:2,accessMonths:10,scenes:[
 {id:'s1',order:1,title:'✈️ Начинаем путешествие',components:[{id:'c1',type:'content',prompt:'Сегодня мы отправимся в путешествие! Нас ждут Исландия и Мадагаскар. Готов(а)?',required:true,payload:{}}]},
 v('s2','🎙 Поздоровайся','Скажи: «Привет! Я готов(а) путешествовать!»','ready_to_travel'),
 q('s3','🧳 Собираем чемодан','Что пригодится там, где холодно?',['Шапка','Ласты','Пляжный мяч'],0),
 q('s4','🌍 Куда летим?','Где можно увидеть лёд и вулканы?',['Исландия','Мадагаскар','Сахара'],0),
 {id:'s5',order:5,title:'❄️ Исландия',components:[{id:'c5',type:'content',prompt:'Мы в Исландии! Здесь прохладно, есть ледники, водопады и вулканы.',required:true,payload:{}}]},
 v('s6','🎙 Скажи герою','Лёша, почему ты так тепло одет?','why_warm_clothes'),
 q('s7','🌡 Погода','В Исландии сейчас…',['Холодно','Жарко'],0),
 q('s8','🧥 Выбираем одежду','Что наденем на прогулку?',['Куртку','Купальник','Пижаму'],0),
 v('s9','🎙 Ответь','Скажи коротко: «Мне холодно» или «Мне тепло».','temperature_phrase'),
 {id:'s10',order:10,title:'🌋 Вулкан',components:[{id:'c10',type:'content',prompt:'Посмотри: вулкан может быть горячим, даже когда вокруг холодно.',required:true,payload:{}}]},
 q('s11','🧠 Проверим','Что горячее?',['Лава','Лёд','Снег'],0),
 {id:'s12',order:12,title:'✈️ Летим дальше',components:[{id:'c12',type:'content',prompt:'Теперь летим на Мадагаскар. Там совсем другая погода!',required:true,payload:{}}]},
 q('s13','☀️ Мадагаскар','Какая одежда больше подходит для жары?',['Футболка','Шуба','Тёплая шапка'],0),
 q('s14','🐒 Животные','Кого можно встретить на Мадагаскаре?',['Лемура','Белого медведя','Пингвина'],0),
 v('s15','🎙 Расскажи','Скажи: «На Мадагаскаре жарко».','madagascar_hot'),
 q('s16','🧳 Чемодан','Что возьмём на солнечный остров?',['Панаму','Валенки','Лыжи'],0),
 v('s17','🎙 Сравни','Скажи: «В Исландии холодно, а на Мадагаскаре жарко».','compare_weather'),
 q('s18','🗺 Что запомнили?','Где было холодно?',['В Исландии','На Мадагаскаре'],0),
 q('s19','🗺 Что запомнили?','Где было жарко?',['На Мадагаскаре','В Исландии'],0),
 v('s20','🎬 Финальная реплика','Скажи: «Приходи со мной в путешествие!»','invite_friend'),
 {id:'s21',order:21,title:'⭐ Отличная работа!',components:[{id:'c21',type:'content',prompt:'Ты закончил(а) путешествие! Твои ответы войдут в персональный мультфильм.',required:true,payload:{}}]}
],homework:[{id:'hw1',order:1,title:'Домашнее задание',components:[{id:'h1',type:'creative_task',prompt:'Нарисуй своё путешествие. Выбери страну и назови 3 вещи, которые возьмёшь с собой.',required:false,payload:{}}]}]};
