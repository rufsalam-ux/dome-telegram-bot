import React from 'react';
import { SafeAreaView, StatusBar } from 'react-native';
import { AppStoreProvider } from './src/store/AppStore';
import { RootApp } from './src/screens/RootApp';

export default function App() {
  return (
    <AppStoreProvider>
      <SafeAreaView style={{ flex: 1, backgroundColor: '#F7F7FB' }}>
        <StatusBar barStyle="dark-content" />
        <RootApp />
      </SafeAreaView>
    </AppStoreProvider>
  );
}
