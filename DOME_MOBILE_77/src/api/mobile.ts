import * as FileSystem from 'expo-file-system/legacy';
import * as SecureStore from 'expo-secure-store';

const DEFAULT_BASE='https://dome-telegram-bot-production.up.railway.app';
const TOKEN_KEY='dome_mobile_token';

export const API_BASE=(process.env.EXPO_PUBLIC_DOME_API_BASE_URL||process.env.EXPO_PUBLIC_API_URL||DEFAULT_BASE).replace(/\/$/,'');

type SessionInvalidatedListener=()=>void;

let cachedToken:string|undefined;
let invalidationPromise:Promise<void>|null=null;
const sessionInvalidatedListeners=new Set<SessionInvalidatedListener>();

export class MobileApiError extends Error{
  status:number;
  code?:string;
  data:any;

  constructor(status:number,message:string,code?:string,data:any={}){
    super(message);
    this.name='MobileApiError';
    this.status=status;
    this.code=code;
    this.data=data;
  }
}

export function isUnauthorizedError(error:unknown):boolean{
  return error instanceof MobileApiError&&error.status===401;
}

export function onApiSessionInvalidated(listener:SessionInvalidatedListener):()=>void{
  sessionInvalidatedListeners.add(listener);
  return ()=>sessionInvalidatedListeners.delete(listener);
}

export async function restoreApiToken():Promise<string>{
  if(cachedToken===undefined){
    cachedToken=(await SecureStore.getItemAsync(TOKEN_KEY))||'';
  }
  return cachedToken;
}

export async function persistApiToken(value:string):Promise<void>{
  const next=String(value||'').trim();
  cachedToken=next;
  if(next)await SecureStore.setItemAsync(TOKEN_KEY,next);
  else await SecureStore.deleteItemAsync(TOKEN_KEY);
}

export async function clearApiToken():Promise<void>{
  cachedToken='';
  await SecureStore.deleteItemAsync(TOKEN_KEY);
}

async function invalidateApiSession():Promise<void>{
  if(!invalidationPromise){
    invalidationPromise=(async()=>{
      await clearApiToken();
      for(const listener of sessionInvalidatedListeners)listener();
    })().finally(()=>{invalidationPromise=null});
  }
  await invalidationPromise;
}

async function requiredToken():Promise<string>{
  const current=await restoreApiToken();
  if(current)return current;
  await invalidateApiSession();
  throw new MobileApiError(401,'Сессия завершена. Войдите снова.','MOBILE_SESSION_REQUIRED');
}

async function decodeResponse(response:Response):Promise<any>{
  const text=await response.text();
  try{return text?JSON.parse(text):{}}
  catch{return text?{error:text}:{}}
}

async function request(path:string,init:RequestInit={},authenticated=true):Promise<any>{
  const requestHeaders:Record<string,string>={
    Accept:'application/json',
    ...((init.headers||{}) as Record<string,string>),
  };
  if(authenticated)requestHeaders.Authorization=`Bearer ${await requiredToken()}`;

  const response=await fetch(`${API_BASE}${path}`,{...init,headers:requestHeaders});
  const data=await decodeResponse(response);
  if(!response.ok){
    if(authenticated&&response.status===401)await invalidateApiSession();
    const message=data.error||data.message||`${response.status} ${response.statusText||'HTTP error'}`;
    throw new MobileApiError(response.status,message,data.code,data);
  }
  return data;
}

function jsonInit(method:string,body:Record<string,unknown>):RequestInit{
  return {method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
}

export function registerAccount(name:string,email:string,password:string){
  return request('/api/mobile/register',jsonInit('POST',{name,email,password}),false);
}

export function verifyEmail(email:string,code:string){
  return request('/api/mobile/verify-email',jsonInit('POST',{email,code}),false);
}

export function resendVerification(email:string){
  return request('/api/mobile/resend-verification',jsonInit('POST',{email}),false);
}

export function loginAccount(email:string,password:string){
  return request('/api/mobile/login',jsonInit('POST',{email,password}),false);
}

export function requestPasswordReset(email:string){
  return request('/api/mobile/password-reset/request',jsonInit('POST',{email}),false);
}

export function confirmPasswordReset(email:string,code:string,password:string){
  return request('/api/mobile/password-reset/confirm',jsonInit('POST',{email,code,password}),false);
}

export function bootstrap(){return request('/api/mobile/bootstrap')}

export function createChild(name:string,ageYears:number,targetLanguage:string,nativeLanguage:string){
  return request('/api/mobile/children',jsonInit('POST',{name,age_years:ageYears,target_language:targetLanguage,native_language:nativeLanguage}));
}

export function getLesson(id='demo_001'){return request(`/api/mobile/lesson/${encodeURIComponent(id)}`)}

export function startSession(childId:string|number,lessonId='demo_001'){
  return request('/api/mobile/session/start',jsonInit('POST',{child_id:Number(childId),lesson_id:lessonId}));
}

export function saveSessionProgress(sessionId:number,currentStep:number){
  return request(`/api/mobile/session/${sessionId}/progress`,jsonInit('POST',{current_step:currentStep}));
}

export async function sendVoice(sessionId:number,uri:string,slideId:string,phraseId:string|undefined,prompt:string){
  const audio_base64=await FileSystem.readAsStringAsync(uri,{encoding:FileSystem.EncodingType.Base64});
  return request(`/api/mobile/session/${sessionId}/voice`,jsonInit('POST',{audio_base64,slide_id:slideId,phrase_id:phraseId||null,prompt:prompt||''}));
}

export function sendInteractive(sessionId:number,slideId:string,taskType:string,result:any){
  return request(`/api/mobile/session/${sessionId}/interactive`,jsonInit('POST',{slide_id:slideId,task_type:taskType,result}));
}

export function completeSession(sessionId:number){
  return request(`/api/mobile/session/${sessionId}/complete`,jsonInit('POST',{}));
}

export async function ttsSource(text:string,targetLanguage='ru',nativeText='',nativeLanguage='ru',sourceLanguage='ru'){
  const query=new URLSearchParams({text,target_language:targetLanguage,native_text:nativeText,native_language:nativeLanguage,source_language:sourceLanguage});
  return {
    uri:`${API_BASE}/api/mobile/tts?${query.toString()}`,
    headers:{Authorization:`Bearer ${await requiredToken()}`},
  };
}

export function translateText(text:string,targetLanguage:string,sourceLanguage='ru'){
  if(!text||!targetLanguage||targetLanguage===sourceLanguage)return Promise.resolve(text);
  return request('/api/mobile/translate',jsonInit('POST',{text,source_language:sourceLanguage,target_language:targetLanguage})).then(data=>data.text||text);
}

export function updateChildLanguages(childId:string|number,targetLanguage:string,nativeLanguage:string){
  return request(`/api/mobile/child/${childId}/language`,jsonInit('PATCH',{target_language:targetLanguage,native_language:nativeLanguage}));
}

export function choosePresetHero(childId:string|number,catalogId:string){
  return request(`/api/mobile/child/${childId}/hero/preset`,jsonInit('POST',{catalog_id:catalogId}));
}

export async function uploadHero(childId:string|number,uri:string){
  const image_base64=await FileSystem.readAsStringAsync(uri,{encoding:FileSystem.EncodingType.Base64});
  return request(`/api/mobile/child/${childId}/hero/upload`,jsonInit('POST',{image_base64,filename:'hero.jpg'}));
}

export function listMovies(childId:string|number){return request(`/api/mobile/child/${childId}/movies`)}
