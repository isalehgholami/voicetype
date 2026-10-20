#!/usr/bin/env bash
# T-0.3 — اسپایک تزریق متن روی KDE Plasma Wayland.
# اجرا: ./spikes/inject_matrix.sh   سپس در ۵ ثانیه موس را در فیلد برنامه‌ی هدف بگذارید.
# برای هر روش و هر برنامه، نتیجه (OK/FAIL) را در docs/inject-matrix.md ثبت کنید.
set -u
TEXT_FA='سلام دنیا، امروز با Python و Qt کار کردم.'
DELAY="${DELAY:-5}"

run() {
    local name="$1"; shift
    echo "=== $name (در ${DELAY}s؛ فوکوس را روی برنامه‌ی هدف بگذارید) ==="
    sleep "$DELAY"
    "$@" || echo "!! $name خطا داد"
}

command -v wtype >/dev/null && run wtype wtype -- "$TEXT_FA"
command -v ydotool >/dev/null && run ydotool-type ydotool type -- "$TEXT_FA"

if command -v wl-copy >/dev/null && command -v ydotool >/dev/null; then
    old="$(wl-paste 2>/dev/null || true)"
    printf '%s' "$TEXT_FA" | wl-copy
    # KEY_LEFTCTRL=29, KEY_V=47
    run paste-ctrl-v ydotool key 29:1 47:1 47:0 29:0
    # KEY_LEFTSHIFT=42  (ترمینال‌ها)
    run paste-ctrl-shift-v ydotool key 29:1 42:1 47:1 47:0 42:0 29:0
    printf '%s' "$old" | wl-copy
fi
echo "تمام. جدول نتیجه را در docs/inject-matrix.md پر کنید."
