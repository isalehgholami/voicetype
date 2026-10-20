---
title: VoiceType
---

# VoiceType

Persian-first voice typing for Linux. Press a shortcut, speak, and the text appears at the cursor of any application.

## Install (Arch Linux)

```bash
git clone <repo> voicetype && cd voicetype
./scripts/setup-arch.sh
pip install --user -e .
voicetype config init
./scripts/install-hotkey.sh Meta+Alt+V
systemctl --user enable --now ydotool voicetype
voicetype doctor
```

## Guides

- [Quick start & commands](../README.md)
- [Troubleshooting](troubleshooting.md)
- [Compatibility](compatibility.md) · [Injection matrix](inject-matrix.md) · [Known issues](known-issues.md)
- [Post-processing (Persian)](postprocess.md) · [Privacy](privacy.md) · [Benchmarks](benchmarks.md)
- [Architecture](VoiceType-Architecture.md) · [Decisions (ADRs)](adr/0001-language-and-framework.md)
