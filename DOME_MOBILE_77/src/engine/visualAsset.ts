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
