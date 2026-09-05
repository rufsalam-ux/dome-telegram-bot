import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  ScrollView,
  Pressable,
  PanResponder,
  useWindowDimensions,
  Alert,
  StyleSheet
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useAudioPlayer, useAudioRecorder, AudioModule, RecordingPresets } from 'expo-audio';
import { H1, H2, Body, Button, Card } from '../components/Ui';
import { useAppStore } from '../store/AppStore';
import { ttsSource, cacheTutorAudioSource, getLessonHomework, submitLessonHomework } from '../api/mobile';
import { playExperience } from '../experience/experience';

interface Point {
  x: number;
  y: number;
}

interface Stroke {
  color: string;
  width: number;
  points: Point[];
}

const PALETTE = ['#246BFD', '#FF5A5F', '#13A864', '#FFB800', '#9B51E0', '#20243A'];

export function HomeworkScreen({ lessonId, onBack }: { lessonId?: string; onBack: () => void }) {
  const store = useAppStore();
  const child = store.selectedChild;
  const insets = useSafeAreaInsets();
  const dimensions = useWindowDimensions();

  // Drawing state
  const [currentColor, setCurrentColor] = useState(PALETTE[0]);
  const [strokes, setStrokes] = useState<Stroke[]>([]);
  const currentStrokeRef = useRef<Point[]>([]);
  const currentColorRef = useRef<string>(PALETTE[0]);
  currentColorRef.current = currentColor;

  // Audio recording state
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const voicePlayer = useAudioPlayer();
  const [recording, setRecording] = useState(false);
  const [recordedUri, setRecordedUri] = useState<string | null>(null);
  const [playingVoice, setPlayingVoice] = useState(false);

  // Lifecycle guards to eliminate "Cannot use shared object that was already released"
  const isMountedRef = useRef(true);
  const activeIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const activeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const playbackSubRef = useRef<{ remove: () => void } | null>(null);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      if (activeIntervalRef.current) {
        clearInterval(activeIntervalRef.current);
        activeIntervalRef.current = null;
      }
      if (activeTimeoutRef.current) {
        clearTimeout(activeTimeoutRef.current);
        activeTimeoutRef.current = null;
      }
      if (playbackSubRef.current) {
        try { playbackSubRef.current.remove(); } catch {}
        playbackSubRef.current = null;
      }
      try { voicePlayer.pause(); } catch {}
    };
  }, [voicePlayer]);


  // PanResponder for smooth drawing
  const panResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderGrant: (evt) => {
        const { locationX, locationY } = evt.nativeEvent;
        const pt: Point = { x: locationX, y: locationY };
        currentStrokeRef.current = [pt];
        // Snapshot color at stroke START — never changes for this stroke even if
        // user taps another color mid-draw. All previous strokes stay intact.
        const strokeColor = String(currentColorRef.current || '#246BFD');
        setStrokes((prev: Stroke[]) => [
          ...prev,
          { color: strokeColor, width: 4, points: [pt] },
        ]);
      },
      onPanResponderMove: (evt) => {
        const { locationX, locationY } = evt.nativeEvent;
        const pt: Point = { x: locationX, y: locationY };
        currentStrokeRef.current.push(pt);
        // Snapshot points so closure always has latest array reference
        const snapPoints = [...currentStrokeRef.current];
        setStrokes((prev: Stroke[]) => {
          if (!prev.length) return prev;
          const lastIndex = prev.length - 1;
          const last = prev[lastIndex];
          if (!last) return prev;
          const next = [...prev];
          // Preserve color captured at grant — never inherit currentColorRef here
          next[lastIndex] = { color: last.color, width: last.width, points: snapPoints };
          return next;
        });
      },
      onPanResponderRelease: () => {
        // Commit final snapshot of all points before clearing working ref.
        // This guarantees the stroke survives any subsequent renders/color changes.
        const finalPoints = [...currentStrokeRef.current];
        currentStrokeRef.current = [];
        if (finalPoints.length > 0) {
          setStrokes((prev: Stroke[]) => {
            if (!prev.length) return prev;
            const lastIndex = prev.length - 1;
            const last = prev[lastIndex];
            if (!last) return prev;
            const next = [...prev];
            next[lastIndex] = { color: last.color, width: last.width, points: finalPoints };
            return next;
          });
        }
      },
      onPanResponderTerminate: () => {
        // Safety: also finalize if gesture is stolen (e.g. scroll parent)
        const finalPoints = [...currentStrokeRef.current];
        currentStrokeRef.current = [];
        if (finalPoints.length > 0) {
          setStrokes((prev: Stroke[]) => {
            if (!prev.length) return prev;
            const lastIndex = prev.length - 1;
            const last = prev[lastIndex];
            if (!last) return prev;
            const next = [...prev];
            next[lastIndex] = { color: last.color, width: last.width, points: finalPoints };
            return next;
          });
        }
      },
    })
  ).current;

  // Dynamic homework title & instructions
  const [hwTitle, setHwTitle] = useState('Путешествие: Мадагаскар и Исландия');
  const [aiTaskTextRu, setAiTaskTextRu] = useState('Нарисуй место, куда ты хотел бы отправиться, и назови три вещи, которые возьмёшь с собой.');
  const [aiTaskTextEn, setAiTaskTextEn] = useState('Draw a place you would like to travel to, and name three things you will take with you.');
  const [slides, setSlides] = useState<any[]>([]);
  const [currentSlideIndex, setCurrentSlideIndex] = useState(0);

  useEffect(() => {
    if (!lessonId) return;
    let active = true;
    getLessonHomework(lessonId)
      .then((data: any) => {
        if (!active || !data?.homework) return;
        const hw = data.homework;
        if (hw.title) setHwTitle(hw.title);
        if (Array.isArray(hw.slides) && hw.slides.length > 0) {
          setSlides(hw.slides);
          const first = hw.slides[0];
          if (first.bot_says_native) setAiTaskTextRu(first.bot_says_native);
          if (first.prompt || first.bot_says_target) setAiTaskTextEn(first.prompt || first.bot_says_target);
        } else if (hw.description) {
          setAiTaskTextRu(hw.description);
        }
      })
      .catch((err: any) => console.warn('Could not load homework data:', err));
    return () => { active = false; };
  }, [lessonId]);

  const onSelectSlide = (idx: number) => {
    if (idx < 0 || idx >= slides.length) return;
    setCurrentSlideIndex(idx);
    const s = slides[idx];
    if (s.bot_says_native) setAiTaskTextRu(s.bot_says_native);
    if (s.prompt || s.bot_says_target) setAiTaskTextEn(s.prompt || s.bot_says_target);
  };

  // Speak AI task instruction (bilingual: native first, then target language)
  const speakingAiRef = useRef(false);
  const [speakingAi, setSpeakingAi] = useState(false);

  const speakAiTask = useCallback(async () => {
    if (speakingAiRef.current || !isMountedRef.current) return;
    speakingAiRef.current = true;
    try {
      if (isMountedRef.current) setSpeakingAi(true);
      await AudioModule.setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
      if (!isMountedRef.current) return;

      const nativeLang = String(child?.nativeLanguage || 'ru').toLowerCase();
      const targetLang = String(child?.learningLanguage || 'en').toLowerCase();
      const sameLanguage = nativeLang === targetLang;

      // 1. Play in native (explanation) language
      const nativeText = nativeLang === 'en' ? aiTaskTextEn : aiTaskTextRu;
      const remoteNative = await ttsSource(nativeText, nativeLang, '', nativeLang, nativeLang, nativeLang, 'encouraging');
      if (!isMountedRef.current) return;

      const cachedNative = await cacheTutorAudioSource(remoteNative);
      if (!isMountedRef.current) return;

      try {
        voicePlayer.replace(cachedNative);
        voicePlayer.play();
      } catch (err) {
        console.warn('AI Homework native playback error', err);
      }

      // 2. If target language differs — wait for native playback to finish, then play target
      if (!sameLanguage && isMountedRef.current) {
        const targetText = targetLang === 'ru' ? aiTaskTextRu : aiTaskTextEn;
        // Pre-cache target audio while native is playing
        const remoteTarget = await ttsSource(targetText, targetLang, '', nativeLang, nativeLang, targetLang, 'encouraging');
        if (!isMountedRef.current) return;

        const cachedTarget = await cacheTutorAudioSource(remoteTarget);
        if (!isMountedRef.current) return;

        // Wait for native audio to finish (safely polled with mounted and try-catch guards)
        await new Promise<void>((resolve) => {
          let elapsed = 0;
          if (activeIntervalRef.current) {
            clearInterval(activeIntervalRef.current);
            activeIntervalRef.current = null;
          }
          const iv = setInterval(() => {
            if (!isMountedRef.current) {
              clearInterval(iv);
              activeIntervalRef.current = null;
              resolve();
              return;
            }
            elapsed += 200;
            let isPlaying = false;
            try {
              isPlaying = Boolean(voicePlayer.playing);
            } catch {
              isPlaying = false;
            }
            if (!isPlaying || elapsed >= 15000) {
              clearInterval(iv);
              activeIntervalRef.current = null;
              resolve();
            }
          }, 200);
          activeIntervalRef.current = iv;
        });

        if (!isMountedRef.current) return;

        // Small gap between the two languages
        await new Promise<void>((r) => {
          if (activeTimeoutRef.current) clearTimeout(activeTimeoutRef.current);
          activeTimeoutRef.current = setTimeout(() => {
            activeTimeoutRef.current = null;
            r();
          }, 400);
        });

        if (!isMountedRef.current) return;

        try {
          voicePlayer.replace(cachedTarget);
          voicePlayer.play();
        } catch (err) {
          console.warn('AI Homework target playback error', err);
        }
      }
    } catch (e) {
      console.warn('AI Homework TTS error', e);
    } finally {
      if (isMountedRef.current) {
        setSpeakingAi(false);
      }
      speakingAiRef.current = false;
    }
  }, [voicePlayer, child, aiTaskTextEn, aiTaskTextRu]);

  // Auto-speak the homework prompt when screen opens
  useEffect(() => {
    const timer = setTimeout(() => {
      if (isMountedRef.current) speakAiTask();
    }, 600);
    return () => clearTimeout(timer);
  }, [speakAiTask]);

  // Voice recording handlers
  const startRecording = async () => {
    try {
      const perm = await AudioModule.requestRecordingPermissionsAsync();
      if (!perm.granted) {
        Alert.alert('Микрофон', 'Разрешите доступ к микрофону, чтобы записать голосовой ответ.');
        return;
      }
      await AudioModule.setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      if (!isMountedRef.current) return;
      await recorder.prepareToRecordAsync();
      recorder.record();
      if (isMountedRef.current) setRecording(true);
    } catch (e: any) {
      Alert.alert('Ошибка микрофона', e.message || 'Не удалось начать запись');
    }
  };

  const stopRecording = async () => {
    try {
      await recorder.stop();
      await AudioModule.setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
      if (isMountedRef.current) {
        setRecording(false);
        setRecordedUri(recorder.uri || null);
      }
      playExperience('TASK_COMPLETE');
    } catch (e: any) {
      if (isMountedRef.current) setRecording(false);
      Alert.alert('Ошибка', 'Не удалось остановить запись');
    }
  };

  const playRecording = async () => {
    if (!recordedUri || playingVoice || !isMountedRef.current) return;
    try {
      setPlayingVoice(true);
      await AudioModule.setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
      if (!isMountedRef.current) return;

      try {
        voicePlayer.replace({ uri: recordedUri });
        voicePlayer.play();
      } catch (err) {
        setPlayingVoice(false);
        return;
      }

      if (playbackSubRef.current) {
        try { playbackSubRef.current.remove(); } catch {}
        playbackSubRef.current = null;
      }

      const sub = voicePlayer.addListener('playbackStatusUpdate', (st) => {
        if (!isMountedRef.current || st.didJustFinish) {
          if (isMountedRef.current) setPlayingVoice(false);
          try { sub.remove(); } catch {}
          playbackSubRef.current = null;
        }
      });
      playbackSubRef.current = sub;
    } catch (e) {
      if (isMountedRef.current) setPlayingVoice(false);
    }
  };

  const [completed, setCompleted] = useState(false);

  const submitHomework = async () => {
    if (!strokes.length && !recordedUri) {
      Alert.alert('Домашнее задание', 'Сделай рисунок или запиши свой голос перед отправкой!');
      return;
    }
    if (child && lessonId) {
      try {
        await submitLessonHomework(child.id, lessonId, {
          completed: true,
          strokes_count: strokes.length,
          has_voice: Boolean(recordedUri),
        });
      } catch (e) {
        console.warn('Failed to submit homework to server:', e);
      }
    }
    playExperience('EXCELLENT');
    setCompleted(true);
    Alert.alert('Молодец! 🎉', 'Домашнее задание сохранено и отправлено.', [
      { text: 'Отлично', onPress: onBack },
    ]);
  };

  return (
    <ScrollView
      testID="homework-screen"
      contentContainerStyle={{
        padding: 20,
        paddingTop: insets.top + 10,
        paddingBottom: insets.bottom + 30,
        gap: 14,
      }}
    >
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
        <H1 compact>📝 Домашнее задание</H1>
        <Button compact secondary title="✕ Закрыть" onPress={onBack} />
      </View>

      <Card compact>
        <H2 compact>{hwTitle}</H2>
        {slides.length > 1 ? (
          <View style={{flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginVertical: 6}}>
            <Button compact secondary disabled={currentSlideIndex <= 0} title="← Назад" onPress={() => onSelectSlide(currentSlideIndex - 1)} />
            <Body compact muted>Шаг {currentSlideIndex + 1} из {slides.length}</Body>
            <Button compact secondary disabled={currentSlideIndex >= slides.length - 1} title="Вперёд →" onPress={() => onSelectSlide(currentSlideIndex + 1)} />
          </View>
        ) : null}
        <View style={{ marginVertical: 6 }}>
          <Body compact>{aiTaskTextRu}</Body>
        </View>
        <Button
          compact
          secondary
          disabled={speakingAi}
          title={speakingAi ? '🔊 Слушаем…' : '🔊 Озвучить задание'}
          onPress={speakAiTask}
        />
      </Card>

      {/* Drawing Canvas Section */}
      <Card compact>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <H2 compact>🎨 Твой рисунок</H2>
          <Button compact secondary title="Очистить" onPress={() => setStrokes([])} />
        </View>

        {/* Color Palette */}
        <View style={{ flexDirection: 'row', gap: 10, marginBottom: 12, justifyContent: 'center' }}>
          {PALETTE.map((color) => (
            <Pressable
              key={color}
              onPress={() => setCurrentColor(color)}
              style={{
                width: 36,
                height: 36,
                borderRadius: 18,
                backgroundColor: color,
                borderWidth: currentColor === color ? 3 : 1,
                borderColor: currentColor === color ? '#000' : 'rgba(0,0,0,0.2)',
                transform: [{ scale: currentColor === color ? 1.15 : 1 }],
              }}
            />
          ))}
        </View>

        {/* Interactive Canvas */}
        <View
          {...panResponder.panHandlers}
          style={{
            width: '100%',
            height: 240,
            backgroundColor: '#FAFAFA',
            borderRadius: 14,
            borderWidth: 2,
            borderColor: '#E2E8F0',
            overflow: 'hidden',
            position: 'relative',
          }}
        >
          {strokes.map((stroke, sIdx) =>
            stroke.points.map((pt, pIdx) => {
              if (pIdx === 0) return null;
              const prev = stroke.points[pIdx - 1];
              if (!prev) return null;
              const dx = pt.x - prev.x;
              const dy = pt.y - prev.y;
              const len = Math.sqrt(dx * dx + dy * dy);
              if (len < 0.5) return null;
              const angle = Math.atan2(dy, dx) * (180 / Math.PI);
              // Position element centered at midpoint between prev and pt
              // RN default transformOrigin is center, so we rotate from the center of the segment
              const midX = (prev.x + pt.x) / 2;
              const midY = (prev.y + pt.y) / 2;
              return (
                <View
                  key={`${sIdx}-${pIdx}`}
                  pointerEvents="none"
                  style={{
                    position: 'absolute',
                    left: midX - len / 2,
                    top: midY - stroke.width / 2,
                    width: len,
                    height: stroke.width,
                    backgroundColor: stroke.color,
                    borderRadius: stroke.width / 2,
                    transform: [{ rotate: `${angle}deg` }],
                  }}
                />
              );
            })
          )}
        </View>
      </Card>

      {/* Voice Answer Section */}
      <Card compact>
        <H2 compact>🎙 Голосовой ответ (3 вещи с собой)</H2>
        <View style={{ marginVertical: 4 }}>
          <Body compact muted>
            Назови 3 вещи, которые ты возьмёшь в путешествие:
          </Body>
        </View>

        <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
          <View style={{ flex: 1 }}>
            <Button
              compact
              disabled={playingVoice}
              title={recording ? '⏹ Закончить' : '🎙 Записать голос'}
              onPress={recording ? stopRecording : startRecording}
            />
          </View>
          {recordedUri ? (
            <View style={{ flex: 1 }}>
              <Button
                compact
                secondary
                disabled={recording || playingVoice}
                title={playingVoice ? 'Слушаем…' : '▶ Прослушать'}
                onPress={playRecording}
              />
            </View>
          ) : null}
        </View>
        {recordedUri ? (
          <View style={{ marginTop: 6 }}>
            <Text style={{ color: '#13A864', fontWeight: '700', fontSize: 13 }}>
              ✓ Запись голоса сохранена
            </Text>
          </View>
        ) : null}
      </Card>

      {/* Submit */}
      <Button
        title="Готово! Сохранить и отправить"
        disabled={completed}
        onPress={submitHomework}
      />
    </ScrollView>
  );
}
