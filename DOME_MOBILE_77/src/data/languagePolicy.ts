/**
 * Product policy for the current DOME release.
 *
 * Russian remains the only selectable studied language in this release, but
 * an authoritative session may carry another valid target.  Keeping runtime
 * resolution generic lets the adaptive engine support any configured pair
 * without changing the current add-child UI.
 */
export const STUDIED_LANGUAGE_CODE='ru' as const;

export const STUDIED_LANGUAGE_OPTIONS=[
  ['ru','Русский'],
] as const;

export const EXPLANATION_LANGUAGE_OPTIONS=[
  ['ru','Русский'],['en','English'],['es','Español'],['de','Deutsch'],['fr','Français'],
  ['it','Italiano'],['pt','Português'],['tr','Türkçe'],['ar','العربية'],['zh','中文'],
] as const;

/** Honor a valid server/profile target; current product selection still defaults to Russian. */
export function studiedLanguageForMobile(requested?:unknown):string{
  const code=String(requested||'').trim().toLowerCase();
  return /^[a-z]{2,3}(?:-[a-z]{2,4})?$/.test(code)?code:STUDIED_LANGUAGE_CODE;
}
