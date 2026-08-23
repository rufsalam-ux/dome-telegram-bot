# DOME v79 lesson runtime — reference and reuse review

Reviewed before implementation on 2026-08-23.

## Open-source references

- OpenLanguage (`challenga-org/openlanguage`) declares the MIT License. Its public architecture was used only to validate the direction of a bounded conversational, voice-first tutor and adaptive learner state.
- `adrianhajdin/react-native-lingua` did not expose a repository license in the GitHub metadata or root tree at review time. It was treated as architecture-only reference material.
- `abdessamed122/Arynta_v1` did not expose a repository license in the GitHub metadata or root tree at review time. It was treated as architecture-only reference material.

No source code, visual asset, prompt, trademark, or proprietary application content was copied from these projects or from Duolingo, Lingokids, Studycat, or Mondly. DOME uses its own lesson data, assets, UI, state machine, and backend services.

## Public UX principles applied

- one child action at a time;
- short voice-first PRE_A1 prompts;
- immediate audio, haptic, and selected-state feedback;
- scaffolded help: target language, then a short home-language hint, then a model answer;
- large selectable/drag targets and pinned primary controls;
- narration and animation in place of long child-facing instructions;
- deterministic progression with no interaction dead ends.

## Production runtime contract

Every interactive slide uses:

`ENTER → AI_SPEAKING → WAITING_ACTION / WAITING_VOICE → PROCESSING → FEEDBACK → FOLLOW_UP / RETRY → COMPLETE`

Recording remains disabled during tutor speech and processing. Acoustic/VAD and transcription-confidence gates run before semantic grading. A third no-speech attempt can advance as `skipped/no_speech`, but can never be stored as an accepted answer or used as a child voice take in a movie.

Hero placement, protected content boxes, fallback anchors, card question sets, animal question sequences, and suitcase drag items are lesson-data contracts rather than per-device coordinates.
