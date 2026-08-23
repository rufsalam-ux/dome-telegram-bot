import React from 'react';
import { LogBox, StatusBar } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { AppStoreProvider } from './src/store/AppStore';
import { RootApp } from './src/screens/RootApp';

// Expo Go can briefly lose its development websocket while the production API
// remains healthy. Keep the warning in Metro logs, but never cover the child's
// QA screen with an unrelated development toast.
LogBox.ignoreLogs(['Cannot connect to Expo CLI']);
if (!__DEV__) LogBox.ignoreAllLogs(true);

export default function App() {
  return (
    <SafeAreaProvider>
      <AppStoreProvider>
        <SafeAreaView edges={['top', 'left', 'right']} style={{ flex: 1, backgroundColor: '#F7F7FB' }}>
          <StatusBar barStyle="dark-content" />
          <RootApp />
        </SafeAreaView>
      </AppStoreProvider>
    </SafeAreaProvider>
  );
}
