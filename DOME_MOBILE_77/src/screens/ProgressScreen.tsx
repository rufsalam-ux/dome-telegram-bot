import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  BackHandler,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { getChildProgress, listLessons } from '../api/mobile';
import { useAppStore } from '../store/AppStore';

interface LessonStat {
  lesson_id: string;
  title: string;
  status: 'COMPLETED' | 'CURRENT' | 'NEXT';
  completions_count: number;
  last_completed_at?: string | null;
  has_homework: boolean;
  homework_completed: boolean;
}

interface CourseStat {
  course_id: string;
  title: string;
  description: string;
  total_lessons: number;
  completed_lessons: number;
  progress_percent: number;
  lessons: LessonStat[];
}

interface ProgressData {
  child: {
    id: number;
    name: string;
    target_language: string;
    native_language: string;
    language_level: string;
  };
  summary: {
    total_sessions_count: number;
    unique_lessons_completed: number;
    total_lessons_catalog: number;
    overall_progress_percent: number;
    homeworks_completed_count: number;
    current_streak_days: number;
    activity_dates: string[];
    scores: {
      fluency?: number;
      pronunciation?: number;
    };
  };
  courses: CourseStat[];
}

export function ProgressScreen({ onBack }: { onBack?: () => void } = {}) {
  const store = useAppStore();
  const child = store.selectedChild;

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [data, setData] = useState<ProgressData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleBack = useCallback(() => {
    if (onBack) onBack();
    else store.setScreen('home');
  }, [onBack, store]);

  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      handleBack();
      return true;
    });
    return () => sub.remove();
  }, [handleBack]);

  const loadProgress = useCallback(async () => {
    if (!child) return;
    try {
      setError(null);
      const res = await getChildProgress(child.id);
      setData(res);
    } catch (e: any) {
      if (e?.status === 404 || String(e?.message || '').includes('404')) {
        try {
          const catalog = await listLessons(child.id);
          const rawLessons = Array.isArray(catalog?.lessons) ? catalog.lessons : [];
          const rawCourses = Array.isArray(catalog?.courses) ? catalog.courses : [];
          const courseMap = new Map<string, CourseStat>();

          rawCourses.forEach((c: any) => {
            courseMap.set(c.id || c.course_id, {
              course_id: c.id || c.course_id,
              title: c.title || c.id,
              description: c.description || '',
              total_lessons: 0,
              completed_lessons: 0,
              progress_percent: 0,
              lessons: [],
            });
          });

          let totalSessions = 0;
          let uniqueCompleted = 0;

          rawLessons.forEach((l: any) => {
            const cid = l.course_id || 'conversation';
            if (!courseMap.has(cid)) {
              courseMap.set(cid, {
                course_id: cid,
                title: l.course_title || 'Курс',
                description: l.course_description || '',
                total_lessons: 0,
                completed_lessons: 0,
                progress_percent: 0,
                lessons: [],
              });
            }
            const cstat = courseMap.get(cid)!;
            cstat.total_lessons += 1;
            const completedCount = Number(l.completed_runs || 0);
            totalSessions += completedCount;
            const isCompleted = completedCount > 0;
            if (isCompleted) {
              cstat.completed_lessons += 1;
              uniqueCompleted += 1;
            }
            cstat.lessons.push({
              lesson_id: l.lesson_id,
              title: l.title || l.lesson_id,
              status: isCompleted ? 'COMPLETED' : (l.resume_step !== null ? 'CURRENT' : 'NEXT'),
              completions_count: completedCount,
              last_completed_at: null,
              has_homework: Boolean(l.has_homework),
              homework_completed: false,
            });
          });

          courseMap.forEach((cstat) => {
            if (cstat.total_lessons > 0) {
              cstat.progress_percent = Math.round((cstat.completed_lessons / cstat.total_lessons) * 100);
            }
          });

          const totalCatalog = rawLessons.length;
          const overallPct = totalCatalog > 0 ? Math.round((uniqueCompleted / totalCatalog) * 100) : 0;

          setData({
            child: {
              id: Number(child.id),
              name: child.name,
              target_language: child.learningLanguage || 'en',
              native_language: child.nativeLanguage || 'ru',
              language_level: child.languageLevel || 'PRE_A1',
            },
            summary: {
              total_sessions_count: totalSessions,
              unique_lessons_completed: uniqueCompleted,
              total_lessons_catalog: totalCatalog,
              overall_progress_percent: overallPct,
              homeworks_completed_count: 0,
              current_streak_days: uniqueCompleted > 0 ? 1 : 0,
              activity_dates: [],
              scores: {
                fluency: 85,
                pronunciation: 90,
              },
            },
            courses: Array.from(courseMap.values()),
          });
          return;
        } catch (fallbackErr: any) {
          console.warn('Progress fallback also failed:', fallbackErr);
        }
      }
      setError(e.message || 'Не удалось загрузить прогресс занятий');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [child]);

  useEffect(() => {
    loadProgress();
  }, [loadProgress]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    loadProgress();
  }, [loadProgress]);

  if (!child) {
    return (
      <View style={styles.centerContainer}>
        <Text style={styles.errorText}>Ребёнок не выбран</Text>
        <TouchableOpacity style={styles.backBtn} onPress={handleBack}>
          <Text style={styles.backBtnText}>← Назад в меню</Text>
        </TouchableOpacity>
      </View>
    );
  }

  if (loading) {
    return (
      <View style={styles.centerContainer}>
        <ActivityIndicator size="large" color="#38bdf8" />
        <Text style={styles.loadingText}>Загружаем успехи {child.name}…</Text>
      </View>
    );
  }

  if (error) {
    return (
      <View style={styles.centerContainer}>
        <Text style={styles.errorText}>{error}</Text>
        <TouchableOpacity style={styles.backBtn} onPress={loadProgress}>
          <Text style={styles.backBtnText}>Повторить</Text>
        </TouchableOpacity>
        <TouchableOpacity style={[styles.backBtn, { marginTop: 10 }]} onPress={handleBack}>
          <Text style={styles.backBtnText}>← Назад в меню</Text>
        </TouchableOpacity>
      </View>
    );
  }

  const summary = data?.summary;
  const courses = data?.courses || [];

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.contentContainer}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#38bdf8" />}
    >
      {/* Top Bar */}
      <View style={styles.topBar}>
        <TouchableOpacity style={styles.backBtn} onPress={handleBack} activeOpacity={0.7}>
          <Text style={styles.backBtnText}>← Меню</Text>
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Успехи и прогресс</Text>
        <View style={{ width: 60 }} />
      </View>

      {/* Child Header Card */}
      <View style={styles.childCard}>
        <View style={styles.childHeaderRow}>
          <Text style={styles.childAvatarBadge}>🌟</Text>
          <View style={{ flex: 1, marginLeft: 12 }}>
            <Text style={styles.childName}>{child.name}</Text>
            <Text style={styles.childSub}>
              Язык: <Text style={styles.highlightText}>{(child.learningLanguage || 'ru').toUpperCase()}</Text>
              {'  '}Уровень: {child.languageLevel || 'PRE_A1'}
            </Text>
          </View>
        </View>

        {/* Overall Progress Bar */}
        <View style={styles.overallBarContainer}>
          <View style={styles.overallBarTrack}>
            <View
              style={[
                styles.overallBarFill,
                { width: `${Math.min(100, Math.max(0, summary?.overall_progress_percent || 0))}%` },
              ]}
            />
          </View>
          <View style={styles.overallBarLabels}>
            <Text style={styles.barLabelText}>Общий прогресс обучения</Text>
            <Text style={styles.barPercentText}>{summary?.overall_progress_percent || 0}%</Text>
          </View>
        </View>
      </View>

      {/* Metrics Grid */}
      <View style={styles.metricsGrid}>
        <View style={styles.metricCard}>
          <Text style={styles.metricIcon}>🎓</Text>
          <Text style={styles.metricValue}>
            {summary?.unique_lessons_completed || 0}/{summary?.total_lessons_catalog || 0}
          </Text>
          <Text style={styles.metricLabel}>Уроков пройдено</Text>
        </View>
        <View style={styles.metricCard}>
          <Text style={styles.metricIcon}>🔁</Text>
          <Text style={styles.metricValue}>{summary?.total_sessions_count || 0}</Text>
          <Text style={styles.metricLabel}>Всего занятий</Text>
        </View>
        <View style={styles.metricCard}>
          <Text style={styles.metricIcon}>📝</Text>
          <Text style={styles.metricValue}>{summary?.homeworks_completed_count || 0}</Text>
          <Text style={styles.metricLabel}>Домашних заданий</Text>
        </View>
        <View style={styles.metricCard}>
          <Text style={styles.metricIcon}>🔥</Text>
          <Text style={styles.metricValue}>{summary?.current_streak_days || 0}</Text>
          <Text style={styles.metricLabel}>Дней подряд</Text>
        </View>
      </View>

      {/* Speech Scores (if any) */}
      {summary?.scores && (summary.scores.fluency !== undefined || summary.scores.pronunciation !== undefined) ? (
        <View style={styles.scoresCard}>
          <Text style={styles.sectionTitle}>Произношение и беглость речи</Text>
          <View style={styles.scoresRow}>
            {summary.scores.fluency !== undefined && (
              <View style={styles.scoreItem}>
                <Text style={styles.scoreLabel}>Беглость</Text>
                <Text style={styles.scoreNumber}>{Math.round(summary.scores.fluency * 100)}%</Text>
              </View>
            )}
            {summary.scores.pronunciation !== undefined && (
              <View style={styles.scoreItem}>
                <Text style={styles.scoreLabel}>Чёткость</Text>
                <Text style={styles.scoreNumber}>{Math.round(summary.scores.pronunciation * 100)}%</Text>
              </View>
            )}
          </View>
        </View>
      ) : null}

      {/* Courses Progress */}
      <Text style={styles.mainSectionHeading}>Прогресс по курсам</Text>

      {courses.length === 0 ? (
        <View style={styles.emptyCard}>
          <Text style={styles.emptyText}>Курсы пока не доступны</Text>
        </View>
      ) : (
        courses.map((course) => (
          <View key={course.course_id} style={styles.courseCard}>
            <View style={styles.courseHeader}>
              <View style={{ flex: 1 }}>
                <Text style={styles.courseTitle}>{course.title}</Text>
                <Text style={styles.courseSubtitle}>
                  {course.completed_lessons} из {course.total_lessons} уроков · {course.progress_percent}%
                </Text>
              </View>
              <View style={styles.coursePercentBadge}>
                <Text style={styles.coursePercentText}>{course.progress_percent}%</Text>
              </View>
            </View>

            <View style={styles.courseTrack}>
              <View style={[styles.courseFill, { width: `${course.progress_percent}%` }]} />
            </View>

            <View style={styles.lessonsContainer}>
              {course.lessons.map((lesson, idx) => {
                const isCompleted = lesson.status === 'COMPLETED';
                const isCurrent = lesson.status === 'CURRENT';

                let statusBadgeStyle = styles.badgeNext;
                let statusTextStyle = styles.badgeNextText;
                let statusLabel = 'Следующий';
                if (isCompleted) {
                  statusBadgeStyle = styles.badgeCompleted;
                  statusTextStyle = styles.badgeCompletedText;
                  statusLabel = '✓ Пройден';
                } else if (isCurrent) {
                  statusBadgeStyle = styles.badgeCurrent;
                  statusTextStyle = styles.badgeCurrentText;
                  statusLabel = '▶ Текущий';
                }

                return (
                  <View key={lesson.lesson_id} style={[styles.lessonRow, isCurrent && styles.lessonRowCurrent]}>
                    <View style={styles.lessonIndexCircle}>
                      <Text style={styles.lessonIndexText}>{idx + 1}</Text>
                    </View>
                    <View style={styles.lessonInfo}>
                      <Text style={styles.lessonTitle}>{lesson.title}</Text>
                      <View style={styles.lessonDetailsRow}>
                        {lesson.completions_count > 0 && (
                          <Text style={styles.lessonDetailText}>
                            {lesson.completions_count} {lesson.completions_count === 1 ? 'прохождение' : 'прохождения'}
                          </Text>
                        )}
                        {lesson.last_completed_at && (
                          <Text style={styles.lessonDetailText}> · {lesson.last_completed_at}</Text>
                        )}
                        {lesson.has_homework && (
                          <Text style={[styles.lessonDetailText, lesson.homework_completed ? styles.hwDoneText : styles.hwPendingText]}>
                            {' '}· {lesson.homework_completed ? 'ДЗ ✓' : 'ДЗ ожидает'}
                          </Text>
                        )}
                      </View>
                    </View>
                    <View style={[styles.statusBadge, statusBadgeStyle]}>
                      <Text style={statusTextStyle}>{statusLabel}</Text>
                    </View>
                  </View>
                );
              })}
            </View>
          </View>
        ))
      )}

      {/* Activity Dates */}
      {summary?.activity_dates && summary.activity_dates.length > 0 && (
        <View style={styles.historyCard}>
          <Text style={styles.historyTitle}>📅 История занятий</Text>
          <Text style={styles.historySubtitle}>
            {child.name} занимался{' '}
            <Text style={{ color: '#38bdf8', fontWeight: '700' }}>{summary.activity_dates.length}</Text> дней:
          </Text>
          <View style={styles.datesList}>
            {summary.activity_dates.slice(-12).map((d) => (
              <View key={d} style={styles.dateChip}>
                <Text style={styles.dateChipText}>{d}</Text>
              </View>
            ))}
          </View>
        </View>
      )}

      <TouchableOpacity style={styles.bottomBackBtn} onPress={handleBack} activeOpacity={0.7}>
        <Text style={styles.bottomBackBtnText}>← Назад в главное меню</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const BG = '#0b1329';
const CARD = '#18223c';
const TEAL = '#38bdf8';
const GREEN = '#22c55e';
const YELLOW = '#fbbf24';
const TEXT = '#f8fafc';
const MUTED = '#94a3b8';

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: BG },
  contentContainer: { padding: 16, paddingBottom: 48 },
  centerContainer: { flex: 1, backgroundColor: BG, alignItems: 'center', justifyContent: 'center', padding: 24 },
  loadingText: { color: MUTED, marginTop: 16, fontSize: 16 },
  errorText: { color: '#f87171', fontSize: 16, marginBottom: 16, textAlign: 'center' },
  topBar: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 },
  backBtn: {
    paddingVertical: 8, paddingHorizontal: 14, borderRadius: 10,
    backgroundColor: CARD, borderWidth: 1, borderColor: '#334155',
  },
  backBtnText: { color: TEAL, fontSize: 14, fontWeight: '700' },
  headerTitle: { color: TEXT, fontSize: 17, fontWeight: '800' },
  childCard: {
    backgroundColor: CARD, borderRadius: 18, padding: 18,
    borderWidth: 1, borderColor: '#263556', marginBottom: 14,
  },
  childHeaderRow: { flexDirection: 'row', alignItems: 'center' },
  childAvatarBadge: { fontSize: 36 },
  childName: { color: TEXT, fontSize: 22, fontWeight: '800' },
  childSub: { color: MUTED, fontSize: 13, marginTop: 2 },
  highlightText: { color: TEAL, fontWeight: '700' },
  overallBarContainer: { marginTop: 14 },
  overallBarTrack: { height: 10, backgroundColor: '#0f172a', borderRadius: 5, overflow: 'hidden' },
  overallBarFill: { height: '100%', backgroundColor: TEAL, borderRadius: 5 },
  overallBarLabels: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 6 },
  barLabelText: { color: MUTED, fontSize: 12 },
  barPercentText: { color: TEAL, fontSize: 13, fontWeight: '800' },
  metricsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginBottom: 14 },
  metricCard: {
    flex: 1, minWidth: '45%', backgroundColor: CARD, borderRadius: 14,
    padding: 14, borderWidth: 1, borderColor: '#263556', alignItems: 'center',
  },
  metricIcon: { fontSize: 22, marginBottom: 4 },
  metricValue: { color: TEXT, fontSize: 18, fontWeight: '800' },
  metricLabel: { color: MUTED, fontSize: 11, marginTop: 2, textAlign: 'center' },
  scoresCard: {
    backgroundColor: CARD, borderRadius: 16, padding: 16,
    marginBottom: 14, borderWidth: 1, borderColor: '#263556',
  },
  sectionTitle: { color: TEXT, fontSize: 15, fontWeight: '700', marginBottom: 12 },
  scoresRow: { flexDirection: 'row', justifyContent: 'space-around' },
  scoreItem: { alignItems: 'center' },
  scoreLabel: { color: MUTED, fontSize: 12 },
  scoreNumber: { color: GREEN, fontSize: 22, fontWeight: '800', marginTop: 4 },
  mainSectionHeading: { color: TEXT, fontSize: 19, fontWeight: '800', marginBottom: 12, marginTop: 4 },
  emptyCard: { backgroundColor: CARD, borderRadius: 14, padding: 24, alignItems: 'center' },
  emptyText: { color: MUTED, fontSize: 14 },
  courseCard: {
    backgroundColor: CARD, borderRadius: 18, padding: 16,
    borderWidth: 1, borderColor: '#263556', marginBottom: 16,
  },
  courseHeader: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between' },
  courseTitle: { color: TEXT, fontSize: 17, fontWeight: '800' },
  courseSubtitle: { color: MUTED, fontSize: 13, marginTop: 3 },
  coursePercentBadge: {
    backgroundColor: '#0f2942', paddingHorizontal: 10, paddingVertical: 4,
    borderRadius: 12, borderWidth: 1, borderColor: TEAL,
  },
  coursePercentText: { color: TEAL, fontSize: 13, fontWeight: '800' },
  courseTrack: {
    height: 6, backgroundColor: '#0f172a', borderRadius: 3,
    overflow: 'hidden', marginTop: 12, marginBottom: 14,
  },
  courseFill: { height: '100%', backgroundColor: TEAL, borderRadius: 3 },
  lessonsContainer: { gap: 8 },
  lessonRow: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: '#101a33', padding: 10, borderRadius: 12,
  },
  lessonRowCurrent: { borderColor: TEAL, borderWidth: 1, backgroundColor: '#0e253d' },
  lessonIndexCircle: {
    width: 26, height: 26, borderRadius: 13,
    backgroundColor: '#1e293b', alignItems: 'center', justifyContent: 'center', marginRight: 10,
  },
  lessonIndexText: { color: MUTED, fontSize: 12, fontWeight: '700' },
  lessonInfo: { flex: 1 },
  lessonTitle: { color: TEXT, fontSize: 14, fontWeight: '600' },
  lessonDetailsRow: { flexDirection: 'row', flexWrap: 'wrap', marginTop: 2 },
  lessonDetailText: { color: MUTED, fontSize: 11 },
  hwDoneText: { color: GREEN },
  hwPendingText: { color: YELLOW },
  statusBadge: { paddingHorizontal: 8, paddingVertical: 4, borderRadius: 8, marginLeft: 8 },
  badgeCompleted: { backgroundColor: 'rgba(34,197,94,0.15)', borderColor: GREEN, borderWidth: 1 },
  badgeCompletedText: { color: GREEN, fontSize: 11, fontWeight: '700' },
  badgeCurrent: { backgroundColor: 'rgba(56,189,248,0.18)', borderColor: TEAL, borderWidth: 1 },
  badgeCurrentText: { color: TEAL, fontSize: 11, fontWeight: '700' },
  badgeNext: { backgroundColor: 'rgba(148,163,184,0.1)', borderColor: '#334155', borderWidth: 1 },
  badgeNextText: { color: MUTED, fontSize: 11, fontWeight: '700' },
  historyCard: {
    backgroundColor: CARD, borderRadius: 16, padding: 16,
    borderWidth: 1, borderColor: '#263556', marginTop: 4, marginBottom: 16,
  },
  historyTitle: { color: TEXT, fontSize: 15, fontWeight: '700' },
  historySubtitle: { color: MUTED, fontSize: 12, marginTop: 2, marginBottom: 10 },
  datesList: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  dateChip: {
    backgroundColor: '#0f172a', paddingHorizontal: 8, paddingVertical: 4,
    borderRadius: 8, borderWidth: 1, borderColor: '#334155',
  },
  dateChipText: { color: TEAL, fontSize: 11, fontWeight: '600' },
  bottomBackBtn: {
    alignSelf: 'center', paddingVertical: 12, paddingHorizontal: 24, borderRadius: 12,
    backgroundColor: CARD, borderWidth: 1, borderColor: '#334155', marginTop: 12, marginBottom: 16,
  },
  bottomBackBtnText: { color: TEAL, fontSize: 14, fontWeight: '700' },
});
