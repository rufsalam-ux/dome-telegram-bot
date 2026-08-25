import React from 'react';
import {AppStoreProvider} from './store/AppStore';
import {RootApp} from './screens/RootApp';

export function AppRuntime(){
  return <AppStoreProvider><RootApp/></AppStoreProvider>;
}
