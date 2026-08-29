const React=require('react');
const {AppRegistry,NativeModules,ScrollView,Text}=require('react-native');

function errorDetails(error){
  const name=error instanceof Error?(error.name||'Error'):typeof error;
  const message=error instanceof Error?(error.message||error.name):String(error);
  const stack=error instanceof Error?String(error.stack||''):'unavailable';
  return {name,message,stack};
}

function registerEntryFailure(error){
  const failure=errorDetails(error);
  const EntryFailure=()=>React.createElement(
    ScrollView,
    {contentContainerStyle:{flexGrow:1,justifyContent:'center',padding:28,backgroundColor:'#F7F7FB'}},
    React.createElement(Text,{style:{fontSize:28,fontWeight:'900',color:'#20243A',marginBottom:12}},'DOME ENTRY ERROR'),
    React.createElement(Text,{selectable:true,testID:'entry-error-name',style:{fontSize:13,color:'#8A2942',marginBottom:8}},`NAME: ${failure.name}`),
    React.createElement(Text,{selectable:true,testID:'entry-error-message',style:{fontSize:13,color:'#8A2942',marginBottom:8}},`MESSAGE: ${failure.message}`),
    React.createElement(Text,{selectable:true,testID:'entry-error-stack',style:{fontSize:10,lineHeight:14,color:'#42475C'}},`STACK: ${failure.stack}`),
  );
  AppRegistry.registerComponent('main',()=>EntryFailure);
}

try {
  const {registerRootComponent}=require('expo');
  const {logStartupStage}=require('./src/engine/startup');
  globalThis.__DOME_STARTUP_BEACON_ORIGIN__=String(NativeModules?.SourceCode?.scriptURL||'').match(/^https?:\/\/[^/]+/i)?.[0]||'';
  logStartupStage('ENTRY_EVALUATION');
  const App=require('./App').default;
  logStartupStage('APP_MODULE_LOADED');
  registerRootComponent(App);
  logStartupStage('ROOT_REGISTERED');
} catch (error) {
  console.error('[DOME_STARTUP] APP_MODULE_LOAD_FAILED',error);
  try{registerEntryFailure(error)}catch{throw error}
}
