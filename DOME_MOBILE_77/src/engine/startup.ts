export class StartupTimeoutError extends Error{
  stage:string;timeoutMs:number;
  constructor(stage:string,timeoutMs:number){super(`Startup ${stage} timed out after ${timeoutMs}ms`);this.name='StartupTimeoutError';this.stage=stage;this.timeoutMs=timeoutMs}
}

export type BootStage='ROOT'|'STORE_RESTORE'|'SESSION_RESTORE'|'BACKEND_HEALTH'|'PROFILE_LOAD'|'NAVIGATION_READY'|'APP_READY';

export type StartupStage='ENTRY_EVALUATION'|'APP_MODULE_LOADED'|'APP_MODULE_LOAD_FAILED'|'ROOT_REGISTERED'|'APP_MOUNT'|'APP_RUNTIME_LOADED'|'APP_RUNTIME_LOAD_FAILED'|'ROOT_RUNTIME_FAILED'|'ROOT_RUNTIME_RETRY'|'SECURESTORE_DONE'|'BACKEND_BOOTSTRAP_DONE'|'NAV_READY'|'FIRST_SCREEN_RENDERED'|BootStage;

export type StartupFailure={
  code:string;
  reason:string;
  message:string;
  stage:BootStage;
  errorName:string;
  errorMessage:string;
  stack:string;
  failingFunction:string;
  failingLocation:string;
};

const startupTraceStartedAt=Date.now();

export function logStartupStage(stage:StartupStage,details:Record<string,unknown>={}):void{
  const payload={elapsed_ms:Date.now()-startupTraceStartedAt,...details};
  console.log('[DOME_STARTUP]',stage,JSON.stringify(payload));
  const origin=String((globalThis as any).__DOME_STARTUP_BEACON_ORIGIN__||'');
  if(origin){
    const query=`stage=${encodeURIComponent(stage)}&payload=${encodeURIComponent(JSON.stringify(payload))}`;
    void fetch(`${origin}/__dome_startup?${query}`).catch(()=>undefined);
  }
}

export function withStartupTimeout<T>(promise:Promise<T>,stage:string,timeoutMs:number):Promise<T>{
  return new Promise<T>((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new StartupTimeoutError(stage,timeoutMs)),timeoutMs);
    promise.then(value=>{clearTimeout(timer);resolve(value)},error=>{clearTimeout(timer);reject(error)});
  });
}

export function startupErrorText(error:unknown):string{
  if(error instanceof StartupTimeoutError){
    if(error.stage==='secure_store')return 'Телефон слишком долго восстанавливал сохранённый вход.';
    if(error.stage==='bootstrap')return 'Сервер DOME не ответил вовремя.';
  }
  return 'Не удалось завершить запуск приложения.';
}

function errorMessage(error:unknown):string{
  if(error instanceof Error)return error.message||error.name;
  if(typeof error==='string')return error;
  try{return JSON.stringify(error)}catch{return 'Unknown startup error'}
}

function shortReason(value:string):string{
  return value.replace(/\s+/g,' ').trim().slice(0,180)||'Unknown startup error';
}

function errorDiagnostics(error:unknown,componentStack=''){
  const errorName=error instanceof Error?(error.name||'Error'):typeof error;
  const errorMessage=errorMessageText(error);
  const stack=[error instanceof Error?error.stack||'':'',componentStack].filter(Boolean).join('\n').slice(0,4000);
  const frames=stack.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
  const applicationFrame=frames.find(line=>/\.(?:tsx?|jsx?):\d+(?::\d+)?/.test(line))||frames[0]||'';
  const functionMatch=applicationFrame.match(/^at\s+([^\s(]+)|^([^@]+)@/);
  const locationMatch=applicationFrame.match(/((?:[A-Za-z]:[\\/]|https?:\/\/|file:\/\/\/)?[^()\s]+\.(?:tsx?|jsx?):\d+(?::\d+)?)/);
  return {
    errorName:shortReason(errorName),
    errorMessage:shortReason(errorMessage),
    stack,
    failingFunction:shortReason(functionMatch?.[1]||functionMatch?.[2]||'unknown'),
    failingLocation:shortReason(locationMatch?.[1]||'unavailable'),
  };
}

function errorMessageText(error:unknown):string{
  return errorMessage(error);
}

export function rootRuntimeFailure(error:unknown,stage:BootStage='ROOT',componentStack=''):StartupFailure{
  const reason=shortReason(errorMessage(error));
  const searchable=`${error instanceof Error?error.name:''} ${reason}`;
  const code=/expoaudio|expo-audio|audioplayer|shared.?object|native module.+audio/i.test(searchable)
    ?'UI_AUDIO_INIT'
    :/loading chunk|dynamic import|module.+(load|resolve)|unable to resolve/i.test(searchable)
      ?'APP_RUNTIME_LOAD'
      :'ROOT_RUNTIME';
  return {code,reason,message:'Не удалось открыть приложение. Попробуйте ещё раз.',stage,...errorDiagnostics(error,componentStack)};
}

export function startupFailure(error:unknown,stage:BootStage='SESSION_RESTORE'):StartupFailure{
  const reason=shortReason(errorMessage(error));
  const code=error instanceof StartupTimeoutError
    ?`${error.stage.toUpperCase()}_TIMEOUT`
    :/network request failed|failed to fetch|networkerror/i.test(reason)
      ?'BACKEND_NETWORK'
      :/secure.?store|keystore|keychain/i.test(reason)
        ?'SECURESTORE_ERROR'
        :'BOOTSTRAP_ERROR';
  return {code,reason,message:startupErrorText(error),stage,...errorDiagnostics(error)};
}
