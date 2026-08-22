export type ComponentType =
  | 'content'|'voice_answer'|'repeat_phrase'|'pronunciation'|'reading_aloud'|'role_reading'|'echo_reading'|'shared_reading'
  | 'comprehension_question'|'retell'|'continue_story'|'character_dialogue'|'role_play'
  | 'single_choice'|'multiple_choice'|'true_false'|'tap_select'|'tap_to_hear'|'matching'|'memory'
  | 'drag_drop'|'sorting'|'sequencing'|'connect_lines'|'tracing'|'handwriting'|'drawing'|'coloring'|'maze'
  | 'build_letter'|'build_syllable'|'build_word'|'build_sentence'|'missing_letter'|'missing_syllable'|'missing_word'
  | 'sound_to_letter'|'sound_to_image'|'sound_position'|'syllable_split'|'mini_dictation'|'image_hotspot'|'find_in_text'
  | 'highlight_text'|'interactive_scene'|'video'|'video_pause_question'|'photo_task'|'real_world_find'|'physical_activity'|'creative_task';
export interface LessonComponent { id:string; type:ComponentType; prompt?:string; voicePrompt?:string; required?:boolean; canSkip?:boolean; maxAttempts?:number; difficulty?:number; payload?:Record<string,unknown>; }
export interface LessonScene { id:string; order:number; title?:string; imageUrl?:string; audioUrl?:string; videoUrl?:string; components:LessonComponent[]; }
export interface LessonManifest {
  id:string; courseId:string; version:number; title:string; learningLanguage:string; scenes:LessonScene[]; homework?:LessonScene[];
  moviePolicy:'each_completion'; homeworkPolicy:'first_completion_only'; maxCompletions:2; accessMonths:10;
}
export interface LessonAttemptState { sessionId:string; lessonId:string; attempt:1|2; currentSceneIndex:number; completed:boolean; movieId?:string; homeworkIssued:boolean; }
