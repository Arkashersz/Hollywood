# Reviewed Manifest Schema

Read this reference only when creating the final storyboard JPG, HTML, and Markdown reports.

The renderer accepts a UTF-8 JSON object with these required top-level fields:

- `slug`, `url`, `title`, `duration`, `count_note`
- `context`, `ontology`, `chain`
- `anchors`: chronological reviewed image-base anchors
- `transcript_note`, `transcript`, `notes`

Each item in `anchors` requires:

- `time`: all chronological occurrence ranges. Include reuses in one anchor.
- `sample`: numeric proxy-frame timestamp in seconds.
- `label`: short caption for the visual storyboard.
- `still`: initial stable image composition, characters, environment, props, pose, and camera.
- `speech`: dialogue/narration and speaker attribution for the anchor.
- `motion`: ordered image-to-video beats, including character, prop, camera, effect, expression, transition, and causal continuity.
- `cause`: narrative purpose, link to adjacent events, and confidence/merge-split caveat.
- Optional `tile_time`: shorter timestamp text for the JPG header.

Optional top-level fields:

- `queried`: metric consultation date.
- `timeline`: rows of `[time, anchor_or_kind, event]` for reuses and editorial title cards.
- `transcript_title`
- `transcript_headers`: exactly three strings.

`transcript` is a list of three-cell rows. For translated dialogue use `[time/speaker, original, translation]`. For same-language analysis, use `[time/speaker, transcribed speech, evidence/uncertainty note]`.

Do not put detector candidates directly in this manifest. Every anchor must be manually reviewed. A storyboard frame is a timestamped proxy from the finished video, never proof that it is the original source still.
