---
title: VoiceType
---

# VoiceType

Persian-first voice typing for Linux. Press a shortcut, speak, and the text appears at the cursor of any application.

## Install (Arch Linux)

```bash
git clone https://github.com/isalehgholami/voicetype.git && cd voicetype
./scripts/setup-arch.sh
./scripts/install-user.sh
systemctl --user enable --now ydotool
voicetype doctor
```

## Guides

- [Quick start & commands](../README.md)
- [Troubleshooting](troubleshooting.md)
- [Theming](theming.md) · [Compatibility](compatibility.md) · [Injection matrix](inject-matrix.md) · [Known issues](known-issues.md)
- [Post-processing (Persian)](postprocess.md) · [Translation](translation.md) · [Privacy](privacy.md) · [Benchmarks](benchmarks.md)
- [Architecture](VoiceType-Architecture.md) · [Decisions (ADRs)](adr/0001-language-and-framework.md) · [ADR-0007: OSD visual language](adr/0007-overlay-visual-language.md)
