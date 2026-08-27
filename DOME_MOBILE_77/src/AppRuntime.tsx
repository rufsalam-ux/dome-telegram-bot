import React from 'react';
import {AppStoreProvider} from './store/AppStore';
import {RootApp} from './screens/RootApp';
import {BootStage,StartupFailure} from './engine/startup';

type Props={
  onBootStage:(stage:BootStage,failure?:StartupFailure|null)=>void;
  retryCount:number;
  onRetryReceived:()=>number;
};

export function AppRuntime(props:Props){
  return <AppStoreProvider><RootApp {...props}/></AppStoreProvider>;
}
