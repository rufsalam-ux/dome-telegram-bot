import React, { useEffect, useRef } from 'react';
import { AppState, View, Text, StyleSheet } from 'react-native';
import { useVideoPlayer, VideoView } from 'expo-video';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { DomePressable } from '../components/DomePressable';

const startSource = require('../../assets/videos/start.mov');
// start.mov duration is ~5 s; watchdog is 15 s safety-net only
const WATCHDOG_MS = 15_000;

export function StartVideoScreen({ onDone }: { onDone: () => void }) {
  const insets = useSafeAreaInsets();
  const finishedRef = useRef(false);
  // Guard: never fire onDone if component was re-mounted from a background transition
  const mountedRef = useRef(true);

  const finish = () => {
    if (finishedRef.current || !mountedRef.current) return;
    finishedRef.current = true;
    onDone();
  };

  const player = useVideoPlayer(startSource, (current) => {
    current.loop = false;
    current.play();
  });

  useEffect(() => {
    mountedRef.current = true;
    finishedRef.current = false;

    // Primary: real playback-end event
    const ended = player.addListener('playToEnd', () => {
      finish();
    });

    // Secondary: error — skip video
    const status = player.addListener('statusChange', (event) => {
      if (event.status === 'error') finish();
    });

    // Safety watchdog — fires only if playToEnd never fires
    const watchdog = setTimeout(finish, WATCHDOG_MS);

    // Resume from background: just continue playback, don't finish early
    const appStateSub = AppState.addEventListener('change', (state) => {
      if (state === 'active' && !finishedRef.current) {
        try { player.play(); } catch { /* ok */ }
      }
    });

    return () => {
      mountedRef.current = false;
      clearTimeout(watchdog);
      ended.remove();
      status.remove();
      appStateSub.remove();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <View testID="start-video-screen" style={styles.container}>
      <VideoView
        player={player}
        nativeControls={false}
        contentFit="cover"
        style={StyleSheet.absoluteFill}
      />
      <View style={[styles.skipContainer, { top: Math.max(20, insets.top + 10) }]}>
        <DomePressable
          testID="start-video-skip"
          accessibilityRole="button"
          onPress={finish}
          style={styles.skipButton}
        >
          <Text style={styles.skipText}>Пропустить ›</Text>
        </DomePressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#000' },
  skipContainer: { position: 'absolute', right: 20, zIndex: 10 },
  skipButton: {
    minHeight: 40,
    paddingHorizontal: 18,
    paddingVertical: 8,
    borderRadius: 20,
    backgroundColor: 'rgba(0,0,0,0.55)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  skipText: { color: '#fff', fontSize: 15, fontWeight: '700' },
});
