# AI agent guidance: optional Openverse sound assets

JIZURA's browser app is a lyric-motion video maker. Never imply it can automatically mix multiple audio tracks or automatically publish finished shorts.

When working on short-video audio selection (BGM / sound effects), use the optional tools/openverse_audio.py helper documented in docs/OPENVERSE_AUDIO.md:
1. Search to create a reviewable JSON candidate list using the official anonymous Openverse API.
2. Inspect the original source page and rights metadata for a selected candidate, including commercial rights, license terms, and attribution obligations.
3. Only then run explicit download for the chosen candidate. Retain its JSON/attribution sidecar.
4. Import BGM into JIZURA by hand; layer multiple sound effects using a separate editor or FFmpeg stage after export. Do not claim these last steps have been automated.

Keep source provenance and the review step in any generated videos. Avoid auto-publishing. Preserve the browser app's existing local-only media processing and do not add silent external requests in its UI.
