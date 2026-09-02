import * as SecureStore from 'expo-secure-store';

const DEFAULT_BASE='https://dome-telegram-bot-production-e6f6.up.railway.app';
const TOKEN_KEY='dome_mobile_token';
const PENDING_VOICE_KEY='dome_pending_voice_v1';
const MAX_LOCAL_VOICE_BYTES=32*1024*1024;

export const API_BASE=(process.env.EXPO_PUBLIC_DOME_API_BASE_URL||process.env.EXPO_PUBLIC_API_URL||DEFAULT_BASE).replace(/\/$/,'');

type SessionInvalidatedListener=()=>void;

let cachedToken:string|undefined;
let invalidationPromise:Promise<void>|null=null;
const sessionInvalidatedListeners=new Set<SessionInvalidatedListener>();

async function readUriBase64(uri:string):Promise<string>{
  const FileSystem=require('expo-file-system/legacy');
  return FileSystem.readAsStringAsync(uri,{encoding:FileSystem.EncodingType.Base64});
}

export type PendingVoiceRecording={
  version:1;recordingId:string;uri:string;size:number;mimeType:string;createdAt:number;
  sessionId:number;slideId:string;phraseId?:string;prompt:string;conversationTurn:number;
  runtimeContext:Record<string,unknown>;retake:boolean;
};

async function readPendingVoiceQueue():Promise<PendingVoiceRecording[]>{
  try{
    const raw=await SecureStore.getItemAsync(PENDING_VOICE_KEY);const parsed=raw?JSON.parse(raw):[];
    return Array.isArray(parsed)?parsed.filter(item=>item&&item.version===1&&item.recordingId&&item.uri):[];
  }catch(error){console.warn('VOICE_PENDING_METADATA_READ_FAILED',error);return []}
}

async function writePendingVoiceQueue(items:PendingVoiceRecording[]):Promise<void>{
  if(items.length)await SecureStore.setItemAsync(PENDING_VOICE_KEY,JSON.stringify(items.slice(-24)));
  else await SecureStore.deleteItemAsync(PENDING_VOICE_KEY);
}

function voiceExtension(uri:string):string{
  const clean=String(uri||'').split(/[?#]/,1)[0]||'';const match=clean.match(/\.([a-z0-9]{2,5})$/i);const value=String(match?.[1]||'m4a').toLowerCase();return ['m4a','caf','wav','aac'].includes(value)?value:'m4a';
}

function voiceMimeType(extension:string):string{return extension==='wav'?'audio/wav':extension==='caf'?'audio/x-caf':extension==='aac'?'audio/aac':'audio/mp4'}

export async function finalizeLocalVoiceRecording(sourceUri:string,metadata:Omit<PendingVoiceRecording,'version'|'recordingId'|'uri'|'size'|'mimeType'|'createdAt'>):Promise<PendingVoiceRecording>{
  const FileSystem=require('expo-file-system/legacy');const source=await FileSystem.getInfoAsync(sourceUri,{size:true});const sourceSize=Number(source.size||0);
  if(!source.exists||sourceSize<=0)throw new MobileApiError(0,'Файл записи не создан','VOICE_LOCAL_EMPTY');
  if(sourceSize>MAX_LOCAL_VOICE_BYTES)throw new MobileApiError(0,'Запись слишком длинная','VOICE_LOCAL_TOO_LARGE');
  const root=String(FileSystem.documentDirectory||'');if(!root)throw new MobileApiError(0,'Хранилище записи недоступно','VOICE_LOCAL_STORAGE_UNAVAILABLE');
  const extension=voiceExtension(sourceUri);const recordingId=`v1-${metadata.sessionId}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,10)}`;
  const directory=`${root}dome-pending-voice/`;const destination=`${directory}${recordingId}.${extension}`;const temporary=`${destination}.uploading`;
  await FileSystem.makeDirectoryAsync(directory,{intermediates:true});await FileSystem.deleteAsync(temporary,{idempotent:true}).catch(()=>{});await FileSystem.copyAsync({from:sourceUri,to:temporary});
  const copied=await FileSystem.getInfoAsync(temporary,{size:true});const size=Number(copied.size||0);if(!copied.exists||size<=0){await FileSystem.deleteAsync(temporary,{idempotent:true}).catch(()=>{});throw new MobileApiError(0,'Файл записи не создан','VOICE_LOCAL_COPY_EMPTY')}
  await FileSystem.moveAsync({from:temporary,to:destination});
  const pending:PendingVoiceRecording={version:1,recordingId,uri:destination,size,mimeType:voiceMimeType(extension),createdAt:Date.now(),...metadata};
  const queue=(await readPendingVoiceQueue()).filter(item=>item.recordingId!==recordingId);queue.push(pending);
  try{await writePendingVoiceQueue(queue)}catch(error){await FileSystem.deleteAsync(destination,{idempotent:true}).catch(()=>{});console.error('VOICE_PENDING_METADATA_SAVE_FAILED',{recording_id:recordingId,error});throw new MobileApiError(0,'Не удалось надёжно сохранить запись на телефоне','VOICE_LOCAL_METADATA_SAVE_FAILED',{cause:String((error as any)?.message||error)})}
  console.info('VOICE_LOCAL_FINALIZED',{recording_id:recordingId,session_id:pending.sessionId,slide_id:pending.slideId,phrase_id:pending.phraseId,size,path:destination,mime_type:pending.mimeType});return pending;
}

export async function pendingLocalVoiceRecording(sessionId:number,slideId:string,phraseId?:string):Promise<PendingVoiceRecording|undefined>{
  const FileSystem=require('expo-file-system/legacy');const queue=await readPendingVoiceQueue();let changed=false;const valid:PendingVoiceRecording[]=[];
  for(const item of queue){const info=await FileSystem.getInfoAsync(item.uri,{size:true}).catch(()=>({exists:false,size:0}));if(info.exists&&Number(info.size||0)>0)valid.push({...item,size:Number(info.size||item.size||0)});else changed=true}
  if(changed)await writePendingVoiceQueue(valid).catch(error=>console.warn('VOICE_PENDING_METADATA_PRUNE_FAILED',error));
  return [...valid].reverse().find(item=>item.sessionId===sessionId&&item.slideId===slideId&&String(item.phraseId||'')===String(phraseId||''));
}

export async function acknowledgeLocalVoiceRecording(recording:PendingVoiceRecording):Promise<void>{
  const FileSystem=require('expo-file-system/legacy');const queue=(await readPendingVoiceQueue()).filter(item=>item.recordingId!==recording.recordingId);await writePendingVoiceQueue(queue);await FileSystem.deleteAsync(recording.uri,{idempotent:true}).catch((error:unknown)=>console.warn('VOICE_ACK_LOCAL_DELETE_FAILED',{recording_id:recording.recordingId,error}));console.info('VOICE_SERVER_ACKNOWLEDGED',{recording_id:recording.recordingId,session_id:recording.sessionId,slide_id:recording.slideId,phrase_id:recording.phraseId});
}

export type TutorAudioSource={uri:string;headers?:Record<string,string>;name?:string};

// The mobile endpoint can perform two first-use provider syntheses and join
// them before sending a byte. Its backend contract permits 90 seconds, so the
// generic 18-second lesson timeout is not valid for this media transfer. The
// download remains bounded and is explicitly cancelled if it does expire.
export const TUTOR_AUDIO_CACHE_TIMEOUT_MS=95_000;
const CHILD_AUDIO_CACHE_TIMEOUT_MS=30_000;
const audioCacheInFlight=new Map<string,Promise<TutorAudioSource>>();

export function tutorAudioCacheKey(uri:string):string{
  const value=String(uri||'');let forward=2166136261;let backward=2166136261;
  for(let index=0;index<value.length;index++){forward=Math.imul(forward^value.charCodeAt(index),16777619);backward=Math.imul(backward^value.charCodeAt(value.length-index-1),16777619)}
  return `${(forward>>>0).toString(16)}${(backward>>>0).toString(16)}-${value.length}`;
}

function remoteAudioLogUrl(uri:string):string{
  // Query values contain lesson phrases; retain a useful request identifier
  // without copying child content into device diagnostics.
  try{const parsed=new URL(uri);return `${parsed.origin}${parsed.pathname}`}
  catch{return String(uri||'').split('?')[0]||''}
}

function headerValue(headers:Record<string,string>|undefined,name:string):string{
  const expected=name.toLowerCase();const entry=Object.entries(headers||{}).find(([key])=>key.toLowerCase()===expected);return String(entry?.[1]||'');
}

function delay(ms:number):Promise<void>{return new Promise(resolve=>setTimeout(resolve,ms))}

async function cancelAudioDownload(task:any,label:string):Promise<void>{
  try{await Promise.race([Promise.resolve(task.cancelAsync()),delay(1_000)]);console.warn('TUTOR_AUDIO_CACHE_CANCELLED',{label})}
  catch(error){console.warn('TUTOR_AUDIO_CACHE_CANCEL_FAILED',{label,error:String((error as any)?.message||error)})}
}

async function foregroundAudioDownload(FileSystem:any,source:TutorAudioSource,temporary:string,label:string,timeoutMs:number):Promise<any>{
  const sessionType=FileSystem.FileSystemSessionType?.FOREGROUND;
  const task=FileSystem.createDownloadResumable(source.uri,temporary,{headers:source.headers||{},...(sessionType===undefined?{}:{sessionType})},(progress:any)=>{
    console.info('TUTOR_AUDIO_CACHE_PROGRESS',{label,bytes_written:Number(progress?.totalBytesWritten||0),bytes_expected:Number(progress?.totalBytesExpectedToWrite||0)});
  });
  const started=Promise.resolve(task.downloadAsync()).then(result=>({kind:'result' as const,result}),error=>({kind:'error' as const,error}));
  const outcome=await Promise.race([started,delay(timeoutMs).then(()=>({kind:'timeout' as const}))]);
  if(outcome.kind==='timeout'){
    console.error('TUTOR_AUDIO_CACHE_TIMEOUT',{label,timeout_ms:timeoutMs,remote_url:remoteAudioLogUrl(source.uri)});
    await cancelAudioDownload(task,label);
    // Keep rejection handled even when the Android native task settles later.
    void started.then(()=>{});
    throw new Error('TTS_CACHE_TIMEOUT');
  }
  if(outcome.kind==='error')throw outcome.error;
  if(!outcome.result)throw new Error('TTS_DOWNLOAD_CANCELLED');
  return outcome.result;
}

async function cacheRemoteAudioSource(source:TutorAudioSource,namespace:string,extension:string,timeoutMs=CHILD_AUDIO_CACHE_TIMEOUT_MS):Promise<TutorAudioSource>{
  const FileSystem=require('expo-file-system/legacy');const cacheRoot=String(FileSystem.cacheDirectory||'');
  if(!cacheRoot){console.warn('TUTOR_AUDIO_CACHE_UNAVAILABLE',{namespace,remote_url:remoteAudioLogUrl(source.uri)});return source}
  const directory=`${cacheRoot}${namespace}/`;const destination=`${directory}${tutorAudioCacheKey(source.uri)}.${extension}`;const temporary=`${destination}.download`;const cacheKey=`${namespace}:${destination}`;
  const existing=await FileSystem.getInfoAsync(destination,{size:true});
  if(existing.exists&&Number(existing.size||0)>0){console.info('TUTOR_AUDIO_CACHE_HIT',{namespace,local_path:destination,byte_size:Number(existing.size||0)});return {uri:destination,name:source.name||`dome-audio.${extension}`}}
  const running=audioCacheInFlight.get(cacheKey);if(running)return running;
  const operation=(async()=>{
    const label=namespace==='dome-tutor-audio'?'tutor voice cache':'child recording cache';
    console.info('TUTOR_AUDIO_CACHE_REQUEST',{label,remote_url:remoteAudioLogUrl(source.uri),local_path:destination,timeout_ms:timeoutMs});
    await FileSystem.makeDirectoryAsync(directory,{intermediates:true});
    await FileSystem.deleteAsync(temporary,{idempotent:true}).catch(()=>{});
    try{
      const downloaded=await foregroundAudioDownload(FileSystem,source,temporary,label,timeoutMs);
      const status=Number(downloaded.status||0);const contentType=headerValue(downloaded.headers,'content-type');
      console.info('TUTOR_AUDIO_HTTP_RESPONSE',{label,http_status:status,content_type:contentType||null,remote_url:remoteAudioLogUrl(source.uri)});
      if(status<200||status>=300)throw new Error(`TTS_DOWNLOAD_HTTP_${status}`);
      if(contentType&&!/^audio\//i.test(contentType))throw new Error(`TTS_DOWNLOAD_CONTENT_TYPE_${contentType}`);
      const info=await FileSystem.getInfoAsync(temporary,{size:true});const byteSize=Number(info.size||0);
      if(!info.exists||byteSize<=0)throw new Error('TTS_DOWNLOAD_EMPTY');
      await FileSystem.deleteAsync(destination,{idempotent:true}).catch(()=>{});
      await FileSystem.moveAsync({from:temporary,to:destination});
      const committed=await FileSystem.getInfoAsync(destination,{size:true});
      if(!committed.exists||Number(committed.size||0)!==byteSize)throw new Error('TTS_CACHE_COMMIT_FAILED');
      console.info('TUTOR_AUDIO_CACHE_READY',{label,local_path:destination,byte_size:byteSize,content_type:contentType||null});
      return {uri:destination,name:source.name||`dome-audio.${extension}`};
    }catch(error){
      await FileSystem.deleteAsync(temporary,{idempotent:true}).catch(()=>{});
      console.error('TUTOR_AUDIO_CACHE_FAILED',{label,remote_url:remoteAudioLogUrl(source.uri),error:String((error as any)?.message||error)});
      throw error;
    }
  })();
  audioCacheInFlight.set(cacheKey,operation);try{return await operation}finally{if(audioCacheInFlight.get(cacheKey)===operation)audioCacheInFlight.delete(cacheKey)}
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
  return cacheRemoteAudioSource(source,'dome-tutor-audio','ogg',TUTOR_AUDIO_CACHE_TIMEOUT_MS);
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

export async function sendVoice(sessionId:number,uri:string,slideId:string,phraseId:string|undefined,prompt:string,conversationTurn=0,runtimeContext:Record<string,unknown>={},retake=false,clientRecordingId='',mimeType='audio/mp4'){
  const form=new FormData();
  form.append('audio',{uri,name:`${clientRecordingId||'voice'}.m4a`,type:mimeType||'audio/mp4'} as any);
  form.append('slide_id',slideId);form.append('phrase_id',phraseId||'');form.append('prompt',prompt||'');form.append('conversation_turn',String(conversationTurn));form.append('runtime_context',JSON.stringify(runtimeContext||{}));form.append('retake',retake?'true':'false');
  console.info('VOICE_UPLOAD_REQUEST',{recording_id:clientRecordingId||null,session_id:sessionId,slide_id:slideId,phrase_id:phraseId||null,path:uri,mime_type:mimeType});
  const response=await request(`/api/mobile/session/${sessionId}/voice`,{method:'POST',headers:clientRecordingId?{'Idempotency-Key':clientRecordingId}:{},body:form});
  console.info('VOICE_UPLOAD_RESPONSE',{http_status:200,recording_id:clientRecordingId||null,session_id:sessionId,slide_id:slideId,phrase_id:phraseId||null,accepted:Boolean(response?.accepted),movie_take_accepted:Boolean(response?.movie_take_accepted)});
  return response;
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
