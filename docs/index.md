---
title: VoiceType
---

# VoiceType

Persian-first voice typing for Linux. Press a shortcut, speak, and the text appears at the cursor of any application.

## Install (any Linux distribution)

```bash
git clone https://github.com/isalehgholami/voicetype.git && cd voicetype
./scripts/install-user.sh --system-deps
voicetype doctor
```

The installer detects your distribution (pacman, apt, dnf, zypper, apk, xbps), installs the missing
system packages, sets up a pipx environment, the systemd user unit, the config file and the shortcut.
It also starts the daemon and waits until it answers, so `voicetype reload` works right afterwards.
Without `--system-deps` it stays root-free and prints the commands you would have to run.

## Guides

- [Overview & quick start](../README.md)
- [Installation](installation.md) · [Configuration reference](configuration.md)
- [Translation](translation.md) · [Theming](theming.md)
- [Troubleshooting](troubleshooting.md)
- [Theming](theming.md) · [Compatibility](compatibility.md) · [Injection matrix](inject-matrix.md) · [Known issues](known-issues.md)
- [Post-processing (Persian)](postprocess.md) · [Translation](translation.md) · [Privacy](privacy.md) · [Benchmarks](benchmarks.md)
- [Architecture](VoiceType-Architecture.md) · [Decisions (ADRs)](adr/0001-language-and-framework.md) · [ADR-0007: OSD visual language](adr/0007-overlay-visual-language.md) · [ADR-0008: the floating HUD](adr/0008-hud-visual-language.md)
