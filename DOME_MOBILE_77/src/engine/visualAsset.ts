export type VisualAssetKind='none'|'original'|'localized';
export type VisualAssetStatus='idle'|'loading'|'ready'|'fallback'|'unavailable';

export type VisualAssetState<T>={
  assetKey:string;
  source?:T;
  original?:T;
  kind:VisualAssetKind;
  status:VisualAssetStatus;
  error?:string;
};

export function beginVisualAssetLoad<T>(original:T|undefined,wantsLocalized:boolean,assetKey=''):VisualAssetState<T>{
  if(original)return {assetKey,source:original,original,kind:'original',status:wantsLocalized?'loading':'ready'};
  return {assetKey,kind:'none',status:wantsLocalized?'loading':'unavailable'};
}

export function visualAssetSourceForKey<T>(state:VisualAssetState<T>,assetKey:string,original:T|undefined):T|undefined{
  return state.assetKey===assetKey?state.source:original;
}

export function useLocalizedVisualAsset<T>(state:VisualAssetState<T>,localized:T):VisualAssetState<T>{
  return {...state,source:localized,kind:'localized',status:'ready',error:undefined};
}

export function failVisualAsset<T>(state:VisualAssetState<T>,error:string):VisualAssetState<T>{
  if(state.kind==='localized'&&state.original){
    return {...state,source:state.original,kind:'original',status:'fallback',error};
  }
  return {...state,source:undefined,kind:'none',status:'unavailable',error};
}

function timed<T>(loader:()=>Promise<T>,timeoutMs:number):Promise<T>{
  return new Promise<T>((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Visual asset preload timed out')),Math.max(1,timeoutMs));
    loader().then(value=>{clearTimeout(timer);resolve(value)},error=>{clearTimeout(timer);reject(error)});
  });
}

export async function loadVisualAssetWithRetry<T>(loader:()=>Promise<T>,attempts=2,timeoutMs=18_000,retryDelayMs=650):Promise<T>{
  let lastError:unknown;
  for(let attempt=0;attempt<Math.max(1,attempts);attempt+=1){
    try{return await timed(loader,timeoutMs)}catch(error){lastError=error}
    if(attempt+1<attempts&&retryDelayMs>0)await new Promise(resolve=>setTimeout(resolve,retryDelayMs));
  }
  throw lastError instanceof Error?lastError:new Error(String(lastError||'Visual asset unavailable'));
}
