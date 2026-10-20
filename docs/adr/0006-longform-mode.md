# ADR-0006: Long-form dictation mode (T-4.4)

**Status:** proposed — needs the owner's confirmation

## Context
hyprwhspr's `longform_controller.py` supports long recordings with pause/resume. Architecture non-goal **NG1**
says VoiceType is for short bursts (1–60 s), not long transcription.

## Decision (recommended)
**Do not add a separate long-form mode.** Instead rely on what already covers the useful part of it:
- `[stt] silence_threshold_seconds` up to 30 s for dictation with long pauses;
- `[audio] max_buffer_seconds` (default 120) as the hard cap;
- automatic chunking at ≤ 50 s for the Google endpoint, so a two-minute dictation works.

A real long-form mode (continuous capture, paste-on-pause like hyprwhspr's *continuous* mode) conflicts with NG1 and with the
single-session state machine; if wanted later it should be a new state `CONTINUOUS` plus a per-pause flush using `SilenceDetector`,
which is already written for it.

## Consequences
No new code; T-4.4 closes when the owner accepts this ADR.
