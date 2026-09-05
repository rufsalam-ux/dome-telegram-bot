/**
 * DOME Mascot State Registry & Manifest
 *
 * Defines all 14 emotional and interactive states for the DOME Cat Mascot.
 * All states use local, pre-loaded transparent PNG assets.
 * Zero token cost, zero AI runtime overhead.
 */

export type MascotState =
  | 'LETS_GO'
  | 'HELLO'
  | 'THINKING'
  | 'LISTENING'
  | 'CELEBRATE'
  | 'SLEEPING'
  | 'WAVE'
  | 'IDEA'
  | 'CONFUSED'
  | 'APPLAUSE'
  | 'SAD'
  | 'GREAT'
  | 'LOVE'
  | 'PLAYING';

export type MascotAnimationType =
  | 'bounce'
  | 'wave'
  | 'float'
  | 'pulse'
  | 'celebrate'
  | 'breathing'
  | 'tilt'
  | 'applause'
  | 'heartbeat'
  | 'sad_droop'
  | 'wiggle';

export interface MascotStateMeta {
  id: MascotState;
  titleRu: string;
  meaning: string;
  animationType: MascotAnimationType;
  asset: any;
}

export const MASCOT_REGISTRY: Record<MascotState, MascotStateMeta> = {
  LETS_GO: {
    id: 'LETS_GO',
    titleRu: 'Поехали!',
    meaning: 'Начало урока, переход к приключению, готовность начать',
    animationType: 'bounce',
    asset: require('../../assets/mascot/mascot_lets_go.png'),
  },
  HELLO: {
    id: 'HELLO',
    titleRu: 'Привет!',
    meaning: 'Приветствие, появление на экране, дружеское обращение',
    animationType: 'wave',
    asset: require('../../assets/mascot/mascot_hello.png'),
  },
  THINKING: {
    id: 'THINKING',
    titleRu: 'Думаю...',
    meaning: 'Ребёнку нужно подумать, вопрос учителя, ожидание ответа',
    animationType: 'float',
    asset: require('../../assets/mascot/mascot_thinking.png'),
  },
  LISTENING: {
    id: 'LISTENING',
    titleRu: 'Слушаю...',
    meaning: 'Слушаю ребёнка, идёт запись голоса, ожидание голосового ответа',
    animationType: 'pulse',
    asset: require('../../assets/mascot/mascot_listening.png'),
  },
  CELEBRATE: {
    id: 'CELEBRATE',
    titleRu: 'Победа!',
    meaning: 'Отличный результат, важная победа, завершение этапа, награда',
    animationType: 'celebrate',
    asset: require('../../assets/mascot/mascot_celebrate.png'),
  },
  SLEEPING: {
    id: 'SLEEPING',
    titleRu: 'Сплю',
    meaning: 'Пауза, отдых, длительное ожидание',
    animationType: 'breathing',
    asset: require('../../assets/mascot/mascot_sleeping.png'),
  },
  WAVE: {
    id: 'WAVE',
    titleRu: 'Машет лапкой',
    meaning: 'Привет/пока, короткое дружеское обращение, привлечение внимания',
    animationType: 'wave',
    asset: require('../../assets/mascot/mascot_wave.png'),
  },
  IDEA: {
    id: 'IDEA',
    titleRu: 'Идея / Подсказка',
    meaning: 'Подсказка, новая идея, объяснение, "я знаю!"',
    animationType: 'bounce',
    asset: require('../../assets/mascot/mascot_idea.png'),
  },
  CONFUSED: {
    id: 'CONFUSED',
    titleRu: 'Не понял',
    meaning: 'Ответ непонятен, AI не распознал, попробовать ещё раз',
    animationType: 'tilt',
    asset: require('../../assets/mascot/mascot_confused.png'),
  },
  APPLAUSE: {
    id: 'APPLAUSE',
    titleRu: 'Аплодисменты',
    meaning: 'Правильный ответ, похвала, успешное выполнение задания',
    animationType: 'applause',
    asset: require('../../assets/mascot/mascot_applause.png'),
  },
  SAD: {
    id: 'SAD',
    titleRu: 'Грустно',
    meaning: 'Сюжетная игровая грусть (не для наказания за ошибки)',
    animationType: 'sad_droop',
    asset: require('../../assets/mascot/mascot_sad.png'),
  },
  GREAT: {
    id: 'GREAT',
    titleRu: 'Отлично!',
    meaning: 'Правильно, молодец, хороший результат, палец вверх',
    animationType: 'bounce',
    asset: require('../../assets/mascot/mascot_great.png'),
  },
  LOVE: {
    id: 'LOVE',
    titleRu: 'Сердечко / Любовь',
    meaning: 'Эмоциональная награда, поддержка, завершение, "ты молодец"',
    animationType: 'heartbeat',
    asset: require('../../assets/mascot/mascot_love.png'),
  },
  PLAYING: {
    id: 'PLAYING',
    titleRu: 'Играем!',
    meaning: 'Игровое действие, клубничек/игра с клубком',
    animationType: 'wiggle',
    asset: require('../../assets/mascot/mascot_playing.png'),
  },
};

export const ALL_MASCOT_STATES: MascotState[] = [
  'LETS_GO',
  'HELLO',
  'THINKING',
  'LISTENING',
  'CELEBRATE',
  'SLEEPING',
  'WAVE',
  'IDEA',
  'CONFUSED',
  'APPLAUSE',
  'SAD',
  'GREAT',
  'LOVE',
  'PLAYING',
];

/**
 * Returns static asset for a mascot state.
 * Gracefully falls back to HELLO if state is unknown or missing.
 */
export function getMascotAsset(state?: MascotState | string | null): any {
  if (state && state in MASCOT_REGISTRY) {
    return MASCOT_REGISTRY[state as MascotState].asset;
  }
  return MASCOT_REGISTRY.HELLO.asset;
}

/**
 * Returns metadata for a mascot state.
 * Gracefully falls back to HELLO metadata if state is unknown or missing.
 */
export function getMascotMeta(state?: MascotState | string | null): MascotStateMeta {
  if (state && state in MASCOT_REGISTRY) {
    return MASCOT_REGISTRY[state as MascotState];
  }
  return MASCOT_REGISTRY.HELLO;
}
