import React, { useEffect } from 'react';
import {
  View,
  Text,
  Image,
  ScrollView,
  Pressable,
  useWindowDimensions,
  Alert,
  StyleSheet
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useVideoPlayer, VideoView } from 'expo-video';
import { useAppStore } from '../store/AppStore';
import { API_BASE } from '../api/mobile';
import { DomePressable } from '../components/DomePressable';
import { menuVideo, menuBgImage } from '../data/lessonVideos';

type HomeMenuItem = {
  id: string;
  title: string;
  icon: string;
  onPress: () => void;
  disabled?: boolean;
  highlight?: boolean;
};

export function HomeScreen({
  activeLesson,
  lessonsLoading,
  lessonsError,
  openLesson,
}: {
  activeLesson: any;
  lessonsLoading: boolean;
  lessonsError: string;
  openLesson: (lessonId: string) => void;
}) {
  const store = useAppStore();
  const child = store.selectedChild;
  const insets = useSafeAreaInsets();
  const { width, height } = useWindowDimensions();
  const isLandscape = width > height;

  // Background looping video
  const bgPlayer = useVideoPlayer(menuVideo, (player) => {
    player.loop = true;
    player.muted = true;
    player.play();
  });

  const heroUri = child?.heroUrl
    ? child.heroUrl.startsWith('http')
      ? child.heroUrl
      : API_BASE + child.heroUrl
    : '';

  const menuItems: HomeMenuItem[] = [
    {
      id: 'lessons',
      title: 'Мои уроки',
      icon: '📚',
      onPress: () => store.setScreen('lessons'),
    },
    {
      id: 'homework',
      title: 'Домашнее задание',
      icon: '📝',
      onPress: () => store.setScreen('homework'),
      highlight: true,
    },
    {
      id: 'movies',
      title: 'Мультфильмы',
      icon: '🎬',
      onPress: () => store.setScreen('movies'),
    },
    {
      id: 'hero',
      title: 'Мой герой',
      icon: '🎭',
      onPress: () => store.setScreen('hero'),
    },
    {
      id: 'progress',
      title: 'Мои успехи',
      icon: '📊',
      onPress: () => store.setScreen('progress'),
    },
    {
      id: 'children',
      title: 'Сменить ребёнка',
      icon: '👨‍👩‍👧',
      onPress: () => store.setScreen('children'),
    },
  ];

  return (
    <View testID="home-menu-screen" style={styles.container}>
      {/* Background Image fallback under video */}
      <Image
        source={menuBgImage}
        resizeMode="cover"
        style={StyleSheet.absoluteFill}
      />

      {/* Looping Ambient Menu Video */}
      <VideoView
        player={bgPlayer}
        nativeControls={false}
        contentFit="cover"
        style={StyleSheet.absoluteFill}
      />

      {/* Dark overlay scrim for crisp text readability */}
      <View style={styles.scrim} pointerEvents="none" />

      <ScrollView
        contentContainerStyle={[
          styles.scrollContent,
          {
            paddingTop: Math.max(16, insets.top + 10),
            paddingBottom: Math.max(20, insets.bottom + 20),
            paddingHorizontal: isLandscape ? 36 : 18,
          },
        ]}
        showsVerticalScrollIndicator={false}
      >
        {/* Top Header Bar */}
        <View style={styles.headerRow}>
          <Pressable
            accessibilityRole="button"
            onPress={() => store.setScreen('children')}
            style={styles.profileBadge}
          >
            {heroUri ? (
              <Image source={{ uri: heroUri }} style={styles.profileAvatar} />
            ) : (
              <Text style={styles.profileAvatarPlaceholder}>🌟</Text>
            )}
            <View>
              <Text style={styles.profileName}>{child?.name || 'DOME'}</Text>
              <Text style={styles.profileSub}>
                Изучает: {child?.learningLanguage?.toUpperCase() || 'RU'}
              </Text>
            </View>
          </Pressable>

          <View style={styles.headerPills}>
            <DomePressable
              testID="home-pills-language"
              accessibilityRole="button"
              onPress={() => store.setScreen('language')}
              style={styles.pillButton}
            >
              <Text style={styles.pillText}>🌍 Языки</Text>
            </DomePressable>

            <DomePressable
              testID="home-pills-plans"
              accessibilityRole="button"
              onPress={() => store.setScreen('plans')}
              style={styles.pillButton}
            >
              <Text style={styles.pillText}>💳 Тарифы</Text>
            </DomePressable>

            <DomePressable
              testID="home-pills-sound"
              accessibilityRole="button"
              onPress={() => store.setScreen('experience_settings')}
              style={styles.pillButton}
            >
              <Text style={styles.pillText}>⚙️</Text>
            </DomePressable>
          </View>
        </View>

        {/* Main Lesson Play Card */}
        <View style={styles.lessonCard}>
          <View style={{ flex: 1 }}>
            <Text style={styles.lessonCardLabel}>
              {activeLesson
                ? activeLesson.resume_step !== null
                  ? '▶ Можно продолжить урок'
                  : '▶ Следующий урок'
                : lessonsError || 'Каталог уроков DOME'}
            </Text>
            <Text style={styles.lessonCardTitle} numberOfLines={2}>
              {activeLesson?.title || (lessonsLoading ? 'Загрузка урока…' : 'Уроки готовы к запуску')}
            </Text>
          </View>

          <DomePressable
            testID="home-start-lesson-button"
            accessibilityRole="button"
            disabled={lessonsLoading || (!activeLesson && !String(store.parent?.email||'').trim().toLowerCase().includes('krisriskrisris'))}
            onPress={() => activeLesson && openLesson(activeLesson.lesson_id)}
            style={[
              styles.startLessonBtn,
              (lessonsLoading || (!activeLesson && !String(store.parent?.email||'').trim().toLowerCase().includes('krisriskrisris'))) && styles.startLessonBtnDisabled,
            ]}
          >
            <Text style={styles.startLessonBtnText}>
              {lessonsLoading
                ? 'Загрузка…'
                : activeLesson?.resume_step !== null
                ? 'Продолжить ›'
                : 'Начать урок ›'}
            </Text>
          </DomePressable>
        </View>

        {/* Navigation Grid (All 10 Features) */}
        <View testID="home-menu-grid" style={styles.gridContainer}>
          {menuItems.map((item) => (
            <DomePressable
              key={item.id}
              testID={`home-nav-${item.id}`}
              accessibilityRole="button"
              onPress={item.onPress}
              style={[
                styles.gridTile,
                item.highlight && styles.gridTileHighlight,
                { width: isLandscape ? '31%' : '48%' },
              ]}
            >
              <Text style={styles.gridTileIcon}>{item.icon}</Text>
              <Text style={styles.gridTileTitle}>{item.title}</Text>
            </DomePressable>
          ))}
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#1E2333',
  },
  scrim: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: 'rgba(15, 23, 42, 0.45)',
  },
  scrollContent: {
    flexGrow: 1,
    gap: 16,
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 10,
  },
  profileBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: 'rgba(255, 255, 255, 0.88)',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 24,
  },
  profileAvatar: {
    width: 38,
    height: 38,
    borderRadius: 19,
    resizeMode: 'contain',
  },
  profileAvatarPlaceholder: {
    fontSize: 24,
  },
  profileName: {
    fontSize: 16,
    fontWeight: '800',
    color: '#1E293B',
  },
  profileSub: {
    fontSize: 11,
    fontWeight: '600',
    color: '#64748B',
  },
  headerPills: {
    flexDirection: 'row',
    gap: 8,
  },
  pillButton: {
    backgroundColor: 'rgba(255, 255, 255, 0.85)',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 18,
    justifyContent: 'center',
    alignItems: 'center',
  },
  pillText: {
    fontSize: 13,
    fontWeight: '700',
    color: '#1E293B',
  },
  lessonCard: {
    backgroundColor: 'rgba(255, 255, 255, 0.94)',
    borderRadius: 22,
    padding: 18,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 14,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.15,
    shadowRadius: 10,
    elevation: 4,
    borderWidth: 2,
    borderColor: '#3B82F6',
  },
  lessonCardLabel: {
    fontSize: 12,
    fontWeight: '800',
    color: '#2563EB',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  lessonCardTitle: {
    fontSize: 18,
    fontWeight: '900',
    color: '#0F172A',
    marginTop: 4,
  },
  startLessonBtn: {
    backgroundColor: '#2563EB',
    paddingHorizontal: 18,
    paddingVertical: 14,
    borderRadius: 16,
    minHeight: 48,
    justifyContent: 'center',
    alignItems: 'center',
  },
  startLessonBtnDisabled: {
    backgroundColor: '#94A3B8',
  },
  startLessonBtnText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '800',
  },
  gridContainer: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
    justifyContent: 'space-between',
  },
  gridTile: {
    backgroundColor: 'rgba(255, 255, 255, 0.90)',
    borderRadius: 18,
    paddingVertical: 16,
    paddingHorizontal: 12,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    minHeight: 90,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 6,
    elevation: 2,
  },
  gridTileHighlight: {
    borderWidth: 2,
    borderColor: '#10B981',
    backgroundColor: '#FFFFFF',
  },
  gridTileIcon: {
    fontSize: 28,
  },
  gridTileTitle: {
    fontSize: 13,
    fontWeight: '800',
    color: '#1E293B',
    textAlign: 'center',
  },
});
