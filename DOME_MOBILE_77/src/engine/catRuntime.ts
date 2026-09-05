import type { RuntimeStage } from './lessonRuntime';
import type { MascotState } from '../mascot/mascotRegistry';

export type CatActivityState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'happy'
  | 'encouraging'
  | 'surprised'
  | 'waiting'
  | 'playing'
  | 'sleeping';

export const CAT_ACTIVITY_STATES: CatActivityState[] = [
  'idle',
  'listening',
  'thinking',
  'happy',
  'encouraging',
  'surprised',
  'waiting',
  'playing',
  'sleeping',
];

export function catStateForStage(stage: RuntimeStage): CatActivityState {
  if (stage === 'AI_SPEAKING') return 'listening';
  if (stage === 'PROCESSING') return 'thinking';
  if (stage === 'RETRY') return 'encouraging';
  if (stage === 'FEEDBACK' || stage === 'COMPLETE') return 'happy';
  if (stage === 'WAITING_ACTION' || stage === 'WAITING_VOICE') return 'waiting';
  return 'idle';
}

export function catProcessingState(elapsedMs: number): CatActivityState {
  if (elapsedMs < 1500) return 'thinking';
  if (elapsedMs < 4000) return 'idle';
  return 'waiting';
}

export interface MascotContextOptions {
  explicitState?: MascotState | string | null;
  recording?: boolean;
  speaking?: boolean;
  hasError?: boolean;
  hasHint?: boolean;
  isComplete?: boolean;
  isCorrect?: boolean;
}

/**
 * Maps current lesson stage and interactive context to the precise DOME Mascot emotional state.
 */
export function mascotStateForStage(
  stage: RuntimeStage,
  options?: MascotContextOptions
): MascotState {
  // 1. Explicit override from lesson data (e.g. from Lesson Builder)
  if (options?.explicitState) {
    const raw = String(options.explicitState).toUpperCase();
    const validStates: MascotState[] = [
      'LETS_GO', 'HELLO', 'THINKING', 'LISTENING', 'CELEBRATE',
      'SLEEPING', 'WAVE', 'IDEA', 'CONFUSED', 'APPLAUSE',
      'SAD', 'GREAT', 'LOVE', 'PLAYING'
    ];
    if (validStates.includes(raw as MascotState)) {
      return raw as MascotState;
    }
  }

  // 2. Victory / lesson completion
  if (options?.isComplete || stage === 'COMPLETE') {
    return 'CELEBRATE';
  }

  // 3. Hint requested
  if (options?.hasHint) {
    return 'IDEA';
  }

  // 4. Voice recording or active listening
  if (options?.recording || stage === 'WAITING_VOICE') {
    return 'LISTENING';
  }

  // 5. Speech or AI tutor speaking
  if (options?.speaking || stage === 'AI_SPEAKING') {
    return 'LISTENING';
  }

  // 6. Processing / thinking
  if (stage === 'PROCESSING') {
    return 'THINKING';
  }

  // 7. Error / unrecognized / retry
  if (options?.hasError || stage === 'RETRY') {
    return 'CONFUSED';
  }

  // 8. Positive feedback
  if (stage === 'FEEDBACK') {
    return options?.isCorrect ? 'APPLAUSE' : 'GREAT';
  }

  // 9. Waiting action
  if (stage === 'WAITING_ACTION') {
    return 'THINKING';
  }

  return 'HELLO';
}
