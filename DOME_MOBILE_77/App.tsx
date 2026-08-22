import React from 'react';
import { LogBox, StatusBar } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { AppStoreProvider } from './src/store/AppStore';
import { RootApp } from './src/screens/RootApp';

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
