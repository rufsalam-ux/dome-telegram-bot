import * as SecureStore from 'expo-secure-store';
import { API_BASE, getLesson } from '../api/mobile';
import bundledLesson from '../data/botLesson.json';

const CONTENT_VERSION_KEY = 'dome_published_content_version_v1';
const CONTENT_DIR_NAME = 'dome-content/';

export type LessonVersionInfo = {
  revision: number;
  version: string;
};

export type ContentManifest = {
  content_version: string;
  lessons: Record<string, LessonVersionInfo>;
};

function getFileSystem() {
  try {
    return require('expo-file-system/legacy');
  } catch {
    try {
      return require('expo-file-system');
    } catch {
      return null;
    }
  }
}

function getLessonCachePath(lessonId: string): string | null {
  const fs = getFileSystem();
  const root = fs?.documentDirectory || fs?.cacheDirectory;
  if (!root) return null;
  const safeId = lessonId.replace(/[^a-zA-Z0-9_-]/g, '_');
  return `${root}${CONTENT_DIR_NAME}lessons/${safeId}.json`;
}

export async function fetchRemoteManifest(): Promise<ContentManifest | null> {
  try {
    const response = await fetch(`${API_BASE}/api/mobile/content/manifest`, {
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) return null;
    return await response.json();
  } catch (error) {
    console.warn('[ContentSync] Failed to fetch remote manifest:', error);
    return null;
  }
}

export async function getLocalContentVersion(): Promise<string> {
  try {
    return (await SecureStore.getItemAsync(CONTENT_VERSION_KEY)) || '';
  } catch {
    return '';
  }
}

export async function syncPublishedContent(options: { force?: boolean } = {}): Promise<{
  synced: boolean;
  version: string;
  count: number;
}> {
  const manifest = await fetchRemoteManifest();
  if (!manifest || !manifest.content_version) {
    return { synced: false, version: '', count: 0 };
  }

  const localVersion = await getLocalContentVersion();
  if (!options.force && localVersion === manifest.content_version) {
    return { synced: false, version: localVersion, count: 0 };
  }

  const fs = getFileSystem();
  let updatedCount = 0;

  for (const [lessonId] of Object.entries(manifest.lessons || {})) {
    try {
      const lessonData = await getLesson(lessonId);
      if (lessonData && fs) {
        const dest = getLessonCachePath(lessonId);
        if (dest) {
          const dir = dest.substring(0, dest.lastIndexOf('/') + 1);
          await fs.makeDirectoryAsync(dir, { intermediates: true }).catch(() => {});
          await fs.writeAsStringAsync(dest, JSON.stringify(lessonData), {
            encoding: fs.EncodingType?.UTF8 || 'utf8',
          });
          updatedCount++;
        }
      }
    } catch (err) {
      console.warn(`[ContentSync] Could not pre-cache lesson ${lessonId}:`, err);
    }
  }

  try {
    await SecureStore.setItemAsync(CONTENT_VERSION_KEY, manifest.content_version);
  } catch (err) {
    console.warn('[ContentSync] Could not persist content version:', err);
  }

  return {
    synced: true,
    version: manifest.content_version,
    count: updatedCount,
  };
}

export async function getLessonWithOfflineFallback(lessonId: string): Promise<any> {
  try {
    const remote = await getLesson(lessonId);
    if (remote && (remote.slides || remote.lesson_id)) {
      const fs = getFileSystem();
      const dest = getLessonCachePath(lessonId);
      if (dest && fs) {
        const dir = dest.substring(0, dest.lastIndexOf('/') + 1);
        fs.makeDirectoryAsync(dir, { intermediates: true })
          .then(() => fs.writeAsStringAsync(dest, JSON.stringify(remote), { encoding: fs.EncodingType?.UTF8 || 'utf8' }))
          .catch(() => {});
      }
      return remote;
    }
  } catch (error) {
    console.warn(`[ContentSync] Remote load failed for ${lessonId}, checking cache...`, error);
  }

  const fs = getFileSystem();
  const dest = getLessonCachePath(lessonId);
  if (dest && fs) {
    try {
      const info = await fs.getInfoAsync(dest);
      if (info.exists) {
        const content = await fs.readAsStringAsync(dest, { encoding: fs.EncodingType?.UTF8 || 'utf8' });
        const parsed = JSON.parse(content);
        if (parsed && (parsed.slides || parsed.lesson_id)) {
          return parsed;
        }
      }
    } catch (err) {
      console.warn(`[ContentSync] Cache read failed for ${lessonId}:`, err);
    }
  }

  if (lessonId === 'demo_001' || !lessonId) {
    return bundledLesson;
  }

  throw new Error(`Lesson ${lessonId} unavailable offline and not in cache`);
}
