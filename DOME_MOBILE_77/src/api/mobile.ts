import * as SecureStore from 'expo-secure-store';

const DEFAULT_BASE='https://dome-telegram-bot-production.up.railway.app';
const TOKEN_KEY='dome_mobile_token';

export const API_BASE=(process.env.EXPO_PUBLIC_DOME_API_BASE_URL||process.env.EXPO_PUBLIC_API_URL||DEFAULT_BASE).replace(/\/$/,'');

type SessionInvalidatedListener=()=>void;

let cachedToken:string|undefined;
let invalidationPromise:Promise<void>|null=null;
const sessionInvalidatedListeners=new Set<SessionInvalidatedListener>();

async function readUriBase64(uri:string):Promise<string>{
  const FileSystem=require('expo-file-system/legacy');
  return FileSystem.readAsStringAsync(uri,{encoding:FileSystem.EncodingType.Base64});
}

export type TutorAudioSource={uri:string;headers?:Record<string,string>;name?:string};

export function tutorAudioCacheKey(uri:string):string{
  const value=String(uri||'');let forward=2166136261;let backward=2166136261;
  for(let index=0;index<value.length;index++){forward=Math.imul(forward^value.charCodeAt(index),16777619);backward=Math.imul(backward^value.charCodeAt(value.length-index-1),16777619)}
  return `${(forward>>>0).toString(16)}${(backward>>>0).toString(16)}-${value.length}`;
}

async function cacheRemoteAudioSource(source:TutorAudioSource,namespace:string,extension:string):Promise<TutorAudioSource>{
  const FileSystem=require('expo-file-system/legacy');const cacheRoot=String(FileSystem.cacheDirectory||'');
  if(!cacheRoot)return source;
  const directory=`${cacheRoot}${namespace}/`;const destination=`${directory}${tutorAudioCacheKey(source.uri)}.${extension}`;const temporary=`${destination}.download`;
  const existing=await FileSystem.getInfoAsync(destination,{size:true});
  if(existing.exists&&Number(existing.size||0)>0)return {uri:destination,name:source.name||`dome-audio.${extension}`};
  await FileSystem.makeDirectoryAsync(directory,{intermediates:true});
  await FileSystem.deleteAsync(temporary,{idempotent:true}).catch(()=>{});
  const downloaded=await FileSystem.downloadAsync(source.uri,temporary,{headers:source.headers||{}});
  if(Number(downloaded.status)<200||Number(downloaded.status)>=300){await FileSystem.deleteAsync(temporary,{idempotent:true}).catch(()=>{});throw new Error(`TTS_DOWNLOAD_HTTP_${downloaded.status}`)}
  const info=await FileSystem.getInfoAsync(temporary,{size:true});
  if(!info.exists||Number(info.size||0)<=0){await FileSystem.deleteAsync(temporary,{idempotent:true}).catch(()=>{});throw new Error('TTS_DOWNLOAD_EMPTY')}
  await FileSystem.deleteAsync(destination,{idempotent:true}).catch(()=>{});
  await FileSystem.moveAsync({from:temporary,to:destination});
  return {uri:destination,name:source.name||`dome-audio.${extension}`};
}

async function cacheProtectedVisualSource(source:TutorAudioSource,extension:string):Promise<TutorAudioSource>{
  const FileSystem=require('expo-file-system/legacy');const cacheRoot=String(FileSystem.cacheDirectory||'');
  if(!cacheRoot)return source;
  const directory=`${cacheRoot}dome-lesson-visuals/`;const destination=`${directory}${tutorAudioCacheKey(source.uri)}.${extension}`;const temporary=`${destination}.download`;
  const existing=await FileSystem.getInfoAsync(destination,{size:true});
  if(existing.exists&&Number(existing.size||0)>0)return {uri:destination};
  await FileSystem.makeDirectoryAsync(directory,{intermediates:true});
  await FileSystem.deleteAsync(temporary,{idempotent:true}).catch(()=>{});
  const downloaded=await FileSystem.downloadAsync(source.uri,temporary,{headers:source.headers||{}});
  if(Number(downloaded.status)<200||Number(downloaded.status)>=300){
    await FileSystem.deleteAsync(temporary,{idempotent:true}).catch(()=>{});
    if(Number(downloaded.status)===401)await invalidateApiSession();
    throw new MobileApiError(Number(downloaded.status),`LESSON_VISUAL_DOWNLOAD_HTTP_${downloaded.status}`,'LESSON_VISUAL_DOWNLOAD_FAILED');
  }
  const info=await FileSystem.getInfoAsync(temporary,{size:true});
  if(!info.exists||Number(info.size||0)<=0){await FileSystem.deleteAsync(temporary,{idempotent:true}).catch(()=>{});throw new Error('LESSON_VISUAL_DOWNLOAD_EMPTY')}
  await FileSystem.deleteAsync(destination,{idempotent:true}).catch(()=>{});
  await FileSystem.moveAsync({from:temporary,to:destination});
  return {uri:destination};
}

export function cacheTutorAudioSource(source:TutorAudioSource):Promise<TutorAudioSource>{
  return cacheRemoteAudioSource(source,'dome-tutor-audio','ogg');
}

export function cacheChildRecordingSource(source:TutorAudioSource):Promise<TutorAudioSource>{
  return cacheRemoteAudioSource(source,'dome-child-recordings','wav');
}

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

export function listLessons(childId:string|number){
  return request(`/api/mobile/child/${encodeURIComponent(String(childId))}/lessons`);
}

export async function lessonVisualSource(lessonId:string,imagePath:string,childId:string|number,version:string|number='1'){
  const filename=String(imagePath||'').split('/').pop()||'';
  const query=new URLSearchParams({child_id:String(childId),version:String(version)});
  const source={
    uri:`${API_BASE}/api/mobile/lesson/${encodeURIComponent(lessonId)}/visual/${encodeURIComponent(filename)}?${query.toString()}`,
    headers:{Authorization:`Bearer ${await requiredToken()}`},
  };
  const extension=(filename.split('.').pop()||'png').toLowerCase();
  return cacheProtectedVisualSource(source,/^(png|jpe?g|webp)$/.test(extension)?extension:'png');
}

export async function lessonMediaSource(lessonId:string,mediaPath:string){
  const value=String(mediaPath||'').trim();
  if(/^https?:\/\//i.test(value))return {uri:value,useCaching:true};
  const filename=value.split('/').pop()||'';
  return {
    uri:`${API_BASE}/api/mobile/lesson/${encodeURIComponent(lessonId)}/media/${encodeURIComponent(filename)}`,
    headers:{Authorization:`Bearer ${await requiredToken()}`},
    useCaching:true,
  };
}

export function startSession(childId:string|number,lessonId='demo_001'){
  return request('/api/mobile/session/start',jsonInit('POST',{child_id:Number(childId),lesson_id:lessonId}));
}

export function saveSessionProgress(sessionId:number,currentStepId:string,lessonVersion:string,currentStep?:number){
  return request(`/api/mobile/session/${sessionId}/progress`,jsonInit('POST',{current_step_id:currentStepId,lesson_version:lessonVersion,...(currentStep===undefined?{}:{current_step:currentStep})}));
}

export async function sendVoice(sessionId:number,uri:string,slideId:string,phraseId:string|undefined,prompt:string,conversationTurn=0,runtimeContext:Record<string,unknown>={},retake=false){
  const audio_base64=await readUriBase64(uri);
  return request(`/api/mobile/session/${sessionId}/voice`,jsonInit('POST',{audio_base64,slide_id:slideId,phrase_id:phraseId||null,prompt:prompt||'',conversation_turn:conversationTurn,runtime_context:runtimeContext,retake}));
}

export async function currentVoiceSource(sessionId:number,phraseId:string):Promise<TutorAudioSource>{
  return {uri:`${API_BASE}/api/mobile/session/${sessionId}/voice/${encodeURIComponent(phraseId)}`,headers:{Authorization:`Bearer ${await requiredToken()}`},name:'child-take.wav'};
}

export function sendInteractive(sessionId:number,slideId:string,taskType:string,result:any){
  return request(`/api/mobile/session/${sessionId}/interactive`,jsonInit('POST',{slide_id:slideId,task_type:taskType,result}));
}

export function completeSession(sessionId:number){
  return request(`/api/mobile/session/${sessionId}/complete`,jsonInit('POST',{}));
}

export function getMovieStatus(sessionId:number){return request(`/api/mobile/session/${sessionId}/movie`)}

export function retryMovieBuild(sessionId:number){
  return request(`/api/mobile/session/${sessionId}/movie/retry`,jsonInit('POST',{}));
}

export async function ttsSource(text:string,targetLanguage='ru',nativeText='',nativeLanguage='ru',sourceLanguage='ru',nativeSourceLanguage=sourceLanguage,style='warm'){
  const query=new URLSearchParams({text,target_language:targetLanguage,native_text:nativeText,native_language:nativeLanguage,source_language:sourceLanguage,native_source_language:nativeSourceLanguage,style});
  return {
    uri:`${API_BASE}/api/mobile/tts?${query.toString()}`,
    headers:{Authorization:`Bearer ${await requiredToken()}`},
    name:'dome-tutor.ogg',
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
  const image_base64=await readUriBase64(uri);
  return request(`/api/mobile/child/${childId}/hero/upload`,jsonInit('POST',{image_base64,filename:'hero.jpg'}));
}

export function confirmHeroGeometry(childId:string|number,characterId:string|number,metadata:Record<string,unknown>){
  return request(`/api/mobile/child/${childId}/hero/${characterId}/geometry`,jsonInit('PATCH',metadata));
}

export function listMovies(childId:string|number){return request(`/api/mobile/child/${childId}/movies`)}

export function getSubscription(childId:string|number,courseId='conversation'){
  return request(`/api/mobile/child/${childId}/subscription?course_id=${encodeURIComponent(courseId)}`);
}

export function confirmSubscriptionPlanChange(childId:string|number,planId:string,billingPeriod:string,versionId:string,courseId='conversation'){
  return request(`/api/mobile/child/${childId}/subscription/plan-change`,jsonInit('POST',{plan_id:planId,billing_period:billingPeriod,version_id:versionId,course_id:courseId}));
}

export function getSubscriptionPlanChangePreview(childId:string|number,planId:string,billingPeriod:string,versionId:string,courseId='conversation'){
  return request(`/api/mobile/child/${childId}/subscription/plan-change/preview`,jsonInit('POST',{plan_id:planId,billing_period:billingPeriod,version_id:versionId,course_id:courseId}));
}

export function cancelSubscriptionPlanChange(childId:string|number,courseId='conversation'){
  return request(`/api/mobile/child/${childId}/subscription/plan-change`,jsonInit('DELETE',{course_id:courseId}));
}
