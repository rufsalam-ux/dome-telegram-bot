import React, { Suspense, useEffect } from 'react';
import { LogBox, Pressable, StatusBar, Text, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { logStartupStage } from './src/engine/startup';

const LazyAppRuntime=React.lazy(()=>import('./src/AppRuntime').then(module=>{
  logStartupStage('APP_RUNTIME_LOADED');
  return {default:module.AppRuntime};
}).catch(error=>{
  logStartupStage('APP_RUNTIME_LOAD_FAILED',{error_name:error instanceof Error?error.name:'UnknownError',error_message:error instanceof Error?error.message.slice(0,240):''});
  throw error;
}));

// Expo Go can briefly lose its development websocket while the production API
// remains healthy. Keep the warning in Metro logs, but never cover the child's
// QA screen with an unrelated development toast.
LogBox.ignoreLogs(['Cannot connect to Expo CLI']);
if (!__DEV__) LogBox.ignoreAllLogs(true);

class RootErrorBoundary extends React.Component<{children:React.ReactNode},{error:Error|null}>{
  state:{error:Error|null}={error:null};
  static getDerivedStateFromError(error:Error){return {error}}
  componentDidCatch(error:Error,info:React.ErrorInfo){console.error('DOME_ROOT_RUNTIME_ERROR',error,info.componentStack)}
  render(){
    if(this.state.error)return <View style={{flex:1,alignItems:'center',justifyContent:'center',padding:28,backgroundColor:'#F7F7FB'}}><Text style={{fontSize:34,fontWeight:'900',color:'#20243A'}}>DOME</Text><Text style={{fontSize:17,textAlign:'center',marginVertical:14,color:'#42475C'}}>Не удалось открыть приложение. Попробуйте ещё раз.</Text><Pressable accessibilityRole='button' onPress={()=>this.setState({error:null})} style={{minHeight:52,minWidth:180,borderRadius:18,backgroundColor:'#246bfd',alignItems:'center',justifyContent:'center',paddingHorizontal:20}}><Text style={{color:'#fff',fontSize:18,fontWeight:'800'}}>Повторить</Text></Pressable></View>;
    return this.props.children;
  }
}

export default function App() {
  useEffect(()=>{logStartupStage('APP_MOUNT')},[]);
  return (
    <SafeAreaProvider>
      <SafeAreaView edges={['top', 'left', 'right']} style={{ flex: 1, backgroundColor: '#F7F7FB' }}>
        <StatusBar barStyle="dark-content" />
        <RootErrorBoundary><Suspense fallback={<View style={{flex:1,alignItems:'center',justifyContent:'center',padding:24}}><Text style={{fontSize:18,color:'#42475C'}}>Открываем DOME…</Text></View>}><LazyAppRuntime/></Suspense></RootErrorBoundary>
      </SafeAreaView>
    </SafeAreaProvider>
  );
}
