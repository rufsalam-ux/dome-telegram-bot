import { registerRootComponent } from 'expo';
import { NativeModules } from 'react-native';
import { logStartupStage } from './src/engine/startup';

globalThis.__DOME_STARTUP_BEACON_ORIGIN__=String(NativeModules?.SourceCode?.scriptURL||'').match(/^https?:\/\/[^/]+/i)?.[0]||'';
logStartupStage('ENTRY_EVALUATION');

try {
  const useRootTouchDiagnostic=process.env.EXPO_PUBLIC_DOME_TOUCH_DIAGNOSTIC==='1';
  const App=useRootTouchDiagnostic
    ?require('./src/diagnostics/RootTouchDiagnostic').default
    :require('./App').default;
  if(useRootTouchDiagnostic)console.log('[DOME_TOUCH] TOUCH_DIAGNOSTIC_ROOT_SELECTED');
  logStartupStage('APP_MODULE_LOADED');
  registerRootComponent(App);
  logStartupStage('ROOT_REGISTERED');
} catch (error) {
  console.error('[DOME_STARTUP] APP_MODULE_LOAD_FAILED',error);
  logStartupStage('APP_MODULE_LOAD_FAILED',{error_name:error instanceof Error?error.name:'UnknownError'});
  throw error;
}
