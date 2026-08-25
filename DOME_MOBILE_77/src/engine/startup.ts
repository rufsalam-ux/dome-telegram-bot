export class StartupTimeoutError extends Error{
  stage:string;timeoutMs:number;
  constructor(stage:string,timeoutMs:number){super(`Startup ${stage} timed out after ${timeoutMs}ms`);this.name='StartupTimeoutError';this.stage=stage;this.timeoutMs=timeoutMs}
}

export type StartupStage='ENTRY_EVALUATION'|'APP_MODULE_LOADED'|'APP_MODULE_LOAD_FAILED'|'ROOT_REGISTERED'|'APP_MOUNT'|'APP_RUNTIME_LOADED'|'APP_RUNTIME_LOAD_FAILED'|'SECURESTORE_DONE'|'BACKEND_BOOTSTRAP_DONE'|'NAV_READY'|'FIRST_SCREEN_RENDERED';

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
