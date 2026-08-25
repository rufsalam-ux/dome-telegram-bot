import { registerRootComponent } from 'expo';
import { NativeModules } from 'react-native';
import { logStartupStage } from './src/engine/startup';

globalThis.__DOME_STARTUP_BEACON_ORIGIN__=String(NativeModules?.SourceCode?.scriptURL||'').match(/^https?:\/\/[^/]+/i)?.[0]||'';
logStartupStage('ENTRY_EVALUATION');

try {
  const App = require('./App').default;
  logStartupStage('APP_MODULE_LOADED');
  registerRootComponent(App);
  logStartupStage('ROOT_REGISTERED');
} catch (error) {
  console.error('[DOME_STARTUP] APP_MODULE_LOAD_FAILED',error);
  logStartupStage('APP_MODULE_LOAD_FAILED',{error_name:error instanceof Error?error.name:'UnknownError'});
  throw error;
}
