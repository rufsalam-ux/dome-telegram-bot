/**
 * Product policy for the current DOME release.
 *
 * The domain and backend remain multilingual, but Russian is the only
 * selectable studied language in this mobile release.  The explanation
 * language intentionally remains independent and can be any supported UI
 * language.
 */
export const STUDIED_LANGUAGE_CODE='ru' as const;

export const STUDIED_LANGUAGE_OPTIONS=[
  ['ru','Русский'],
] as const;

export const EXPLANATION_LANGUAGE_OPTIONS=[
  ['ru','Русский'],['en','English'],['es','Español'],['de','Deutsch'],['fr','Français'],
  ['it','Italiano'],['pt','Português'],['tr','Türkçe'],['ar','العربية'],['zh','中文'],
] as const;

/** A session snapshot may predate this policy; never let it change the studied language at runtime. */
export function studiedLanguageForMobile(_requested?:unknown):typeof STUDIED_LANGUAGE_CODE{
  return STUDIED_LANGUAGE_CODE;
}
