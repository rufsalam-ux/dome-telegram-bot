# DOME content contract

`lesson.schema.json` is the portable lesson contract. Lesson data and assets live
under `content/lessons/<lesson_id>/`; application code contains renderers only.

The production loader serves `published` content. `draft` content is available
to the existing administrator preview flow, and publication is blocked by the
Python semantic validator. If a persistent edit is malformed, production falls
back to the bundled last-known-good lesson instead of crashing the runtime.

`media_sequence` is ordered and supports `image`, local/remote `video`,
`animation`, `youtube`, and `audio`. A sequence such as local intro video then
image is content configuration, not a mobile code change.
