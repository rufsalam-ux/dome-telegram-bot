# DOME v68 — FIXED UNIVERSAL 3 COURSES

This build is based on v67 and closes the critical audit items before production testing.

## Fixed
- Persistent Railway storage: `DATA_DIR=/data` is now understood; explicit `STORAGE_ROOT` still wins. If the bundled DB URL is unchanged, SQLite follows the same persistent storage automatically. Recommended: `STORAGE_ROOT=/data` and `DATABASE_URL=sqlite+aiosqlite:////data/app.db`.
- New lessons remain addable without a ZIP: `/addlesson` → lesson → teacher instruction → homework → review → `/publishlesson`.
- Admin can now edit not only activity type but any interactive parameters: `/slideconfig lesson_id N {JSON}` and `/slideprompt lesson_id N text`. This covers hotspots, pairs, items, targets, correct answers, role assignments and prompts without redeploy.
- Homework is analyzed separately by AI instead of forcing every page to tracing. Writing, choice, matching and drag/drop can be inferred and then corrected by admin.
- `sound_position` and `syllable_split` have concrete renderer fallbacks. `interactive_scene` uses hotspot interaction.
- `photo_task` and `real_world_find` are handled in Telegram (photo; real-world find can also accept voice).
- `video_pause_question` is executable as a video step and can carry follow-up content in the next configured activity.
- Reading by roles: authored slides can contain `roles`, `child_role`, `bot_role_text`; DOME speaks its roles before the child turn. Reading support dynamically shifts the child's reading share down/up based on difficulty.
- Two completed runs maximum and 10-month expiry remain configurable in persistent pricing settings. Cartoon generation stays first-run only.
- Prices remain editable without ZIP via `/dome_price`; billing switch via `/dome_billing`.

## Important author workflow
AI import is intentionally REVIEW_REQUIRED. For complex slides inspect `/lessonmap`, then correct with `/slidetype` and `/slideconfig`. This is what makes future lessons editable without Python or a new ZIP.

## Current test prices
1×/week €39; 2× €79; 3× €109; 4× €139. Billing is OFF in test mode.

## Payment provider
Stripe Checkout + signed webhook support is already included. During free testing no credentials are needed. Later add `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`, set payment provider to `stripe`, run a test payment, then `/dome_billing on`. Prices are read from persistent settings at checkout, so changing `/dome_price` does not require a ZIP or Stripe Price objects.

### Admin payment switch without ZIP
`/dome_provider stripe` selects Stripe later; `/dome_provider custom` returns to a hosted custom payment URL.

### Homework editor without ZIP
`/hwmap lesson_id`, `/hwtype lesson_id N type`, `/hwconfig lesson_id N {JSON}` let the admin correct AI-authored homework without code.
