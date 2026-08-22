import * as FileSystem from 'expo-file-system/legacy';

const DEFAULT_BASE='https://dome-telegram-bot-production.up.railway.app';
export const API_BASE=(process.env.EXPO_PUBLIC_DOME_API_BASE_URL||process.env.EXPO_PUBLIC_API_URL||DEFAULT_BASE).replace(/\/$/,'');
let token='';
export function setApiToken(v:string){token=v||''}
function headers(extra:any={}){return {'Accept':'application/json',...(token?{'Authorization':`Bearer ${token}`}:{ }),...extra}}
async function parse(r:Response){
  const txt=await r.text();let data:any={};
  try{data=txt?JSON.parse(txt):{}}catch{data={error:txt}}
  if(!r.ok){const error:any=new Error(data.error||data.message||`HTTP ${r.status}`);error.status=r.status;error.code=data.code;error.data=data;throw error}
  return data
}
export async function registerAccount(name:string,email:string,password:string){return parse(await fetch(`${API_BASE}/api/mobile/register`,{method:'POST',headers:headers({'Content-Type':'application/json'}),body:JSON.stringify({name,email,password})}))}
export async function verifyEmail(email:string,code:string){return parse(await fetch(`${API_BASE}/api/mobile/verify-email`,{method:'POST',headers:headers({'Content-Type':'application/json'}),body:JSON.stringify({email,code})}))}
export async function resendVerification(email:string){return parse(await fetch(`${API_BASE}/api/mobile/resend-verification`,{method:'POST',headers:headers({'Content-Type':'application/json'}),body:JSON.stringify({email})}))}
export async function loginAccount(email:string,password:string){return parse(await fetch(`${API_BASE}/api/mobile/login`,{method:'POST',headers:headers({'Content-Type':'application/json'}),body:JSON.stringify({email,password})}))}
export async function requestPasswordReset(email:string){return parse(await fetch(`${API_BASE}/api/mobile/password-reset/request`,{method:'POST',headers:headers({'Content-Type':'application/json'}),body:JSON.stringify({email})}))}
export async function confirmPasswordReset(email:string,code:string,password:string){return parse(await fetch(`${API_BASE}/api/mobile/password-reset/confirm`,{method:'POST',headers:headers({'Content-Type':'application/json'}),body:JSON.stringify({email,code,password})}))}
export async function bootstrap(){return parse(await fetch(`${API_BASE}/api/mobile/bootstrap`,{headers:headers()}))}
export async function createChild(name:string,ageYears:number,targetLanguage:string,nativeLanguage:string){return parse(await fetch(`${API_BASE}/api/mobile/children`,{method:'POST',headers:headers({'Content-Type':'application/json'}),body:JSON.stringify({name,age_years:ageYears,target_language:targetLanguage,native_language:nativeLanguage})}))}
export async function getLesson(id='demo_001'){return parse(await fetch(`${API_BASE}/api/mobile/lesson/${id}`,{headers:headers()}))}
export async function startSession(childId:string|number,lessonId='demo_001'){return parse(await fetch(`${API_BASE}/api/mobile/session/start`,{method:'POST',headers:headers({'Content-Type':'application/json'}),body:JSON.stringify({child_id:Number(childId),lesson_id:lessonId})}))}

export async function sendVoice(sessionId:number,uri:string,slideId:string,phraseId:string|undefined,prompt:string){
  const audio_base64=await FileSystem.readAsStringAsync(uri,{encoding:FileSystem.EncodingType.Base64});
  return parse(await fetch(`${API_BASE}/api/mobile/session/${sessionId}/voice`,{
    method:'POST',
    headers:headers({'Content-Type':'application/json'}),
    body:JSON.stringify({audio_base64,slide_id:slideId,phrase_id:phraseId||null,prompt:prompt||''})
  }))
}

export async function sendInteractive(sessionId:number,slideId:string,taskType:string,result:any){return parse(await fetch(`${API_BASE}/api/mobile/session/${sessionId}/interactive`,{method:'POST',headers:headers({'Content-Type':'application/json'}),body:JSON.stringify({slide_id:slideId,task_type:taskType,result})}))}
export async function completeSession(sessionId:number){return parse(await fetch(`${API_BASE}/api/mobile/session/${sessionId}/complete`,{method:'POST',headers:headers({'Content-Type':'application/json'}),body:'{}'}))}

export function ttsUrl(text:string,targetLanguage='ru',nativeText='',nativeLanguage='ru',sourceLanguage='ru'){
  const q=new URLSearchParams({text,target_language:targetLanguage,native_text:nativeText,native_language:nativeLanguage,source_language:sourceLanguage,token});
  return `${API_BASE}/api/mobile/tts?${q.toString()}`
}

export async function translateText(text:string,targetLanguage:string,sourceLanguage='ru'){
  if(!text||!targetLanguage||targetLanguage===sourceLanguage)return text;
  return (await parse(await fetch(`${API_BASE}/api/mobile/translate`,{
    method:'POST',headers:headers({'Content-Type':'application/json'}),
    body:JSON.stringify({text,source_language:sourceLanguage,target_language:targetLanguage})
  }))).text||text;
}

export async function updateChildLanguages(childId:string|number,targetLanguage:string,nativeLanguage:string){
  return parse(await fetch(`${API_BASE}/api/mobile/child/${childId}/language`,{
    method:'PATCH',headers:headers({'Content-Type':'application/json'}),
    body:JSON.stringify({target_language:targetLanguage,native_language:nativeLanguage})
  }))
}

export async function choosePresetHero(childId:string|number,catalogId:string){return parse(await fetch(`${API_BASE}/api/mobile/child/${childId}/hero/preset`,{method:'POST',headers:headers({'Content-Type':'application/json'}),body:JSON.stringify({catalog_id:catalogId})}))}
export async function uploadHero(childId:string|number,uri:string){
  const image_base64=await FileSystem.readAsStringAsync(uri,{encoding:FileSystem.EncodingType.Base64});
  return parse(await fetch(`${API_BASE}/api/mobile/child/${childId}/hero/upload`,{
    method:'POST',headers:headers({'Content-Type':'application/json'}),
    body:JSON.stringify({image_base64,filename:'hero.jpg'})
  }))
}
export async function listMovies(childId:string|number){return parse(await fetch(`${API_BASE}/api/mobile/child/${childId}/movies`,{headers:headers()}))}
