#!/usr/bin/env bash
# remo palette check — audit a terminal theme's legibility from inside a real
# terminal, which is the only place the answer is trustworthy. Contrast maths
# tells you what SHOULD be readable; this tells you what is.
#
# Run it in a remo web terminal, switch themes from the swatch in the card
# header, and re-run.
#
# Read it like this:
#   - Row labels print in the DEFAULT foreground, so a row stays identifiable
#     even when every sample in it is invisible.
#   - The `bold` and `bright` columns should look IDENTICAL. That is the whole
#     mechanism: xterm.js draws bold text with the bright palette
#     (drawBoldTextInBrightColors), so "bold green" actually paints brightGreen.
#     When bold text vanishes, it is the BRIGHT variant that is failing —
#     emphasising text can make it LESS readable, not more.
#   - The background strip is the tell-tale: a colour that is invisible as text
#     but shows as a solid block there is being drawn correctly, and the problem
#     is purely its contrast against the background.
#
# This found a real bug: Remo Light shipped `white` at 1.27:1 and `brightWhite`
# at 1.09:1, hiding Claude Code's "Cooked for 9s" and "(shift+tab to cycle)"
# hints. See the contrast floor in frontend/src/theme/terminalThemes.test.ts.
#
# Output is 45 columns wide, so it fits a tile in a 2x2 grid.

e=$'\033'; r="${e}[0m"; names=(black red green yellow blue magenta cyan white)

printf '\n %spalette check%s  bold = the BRIGHT colour\n\n' "${e}[1m" "$r"
printf ' %-8s %-8s %-8s %-8s %-8s\n' '' normal bold dim bright
for i in 0 1 2 3 4 5 6 7; do
  printf ' %-8s %b%-8s%b %b%-8s%b %b%-8s%b %b%-8s%b\n' "${names[$i]}" \
    "${e}[0;3${i}m" normal "$r" "${e}[1;3${i}m" bold "$r" \
    "${e}[2;3${i}m" dim    "$r" "${e}[0;9${i}m" bright "$r"
done

printf '\n %ssame colours as backgrounds%s\n ' "${e}[1m" "$r"
for i in 0 1 2 3 4 5 6 7; do printf '%b  %b' "${e}[4${i}m" "$r"; done
printf '\n'

# Reverse video is included deliberately: it swaps foreground and background by
# definition, so on a light theme it is a dark box. That is the sequence working
# correctly, not a palette bug, and it is worth being able to point at.
printf '\n %sdefault fg%s   %sreverse video%s   %sunderline%s\n' \
  "${e}[39m" "$r" "${e}[7m" "$r" "${e}[4m" "$r"

printf '\n %swhere bold actually shows up%s\n\n' "${e}[1m" "$r"
printf ' %bremo%b@%bhost%b:%b~/code/remo%b (%bmain*%b)$ ls -l\n' \
  "${e}[1;32m" "$r" "${e}[1;32m" "$r" "${e}[1;34m" "$r" "${e}[1;33m" "$r"
printf ' drwxr-xr-x  %bfrontend/%b\n' "${e}[1;34m" "$r"
printf ' -rwxr-xr-x  %binstall.sh%b\n' "${e}[1;32m" "$r"
printf ' lrwxrwxrwx  %blatest%b -> v4.0.1\n' "${e}[1;36m" "$r"
printf ' -rw-r--r--  %bremo-4.0.1.tar.gz%b\n' "${e}[1;31m" "$r"

printf '\n On branch %bmain%b\n' "${e}[1;32m" "$r"
printf '   %bmodified:%b   TerminalCard.tsx\n' "${e}[31m" "$r"
printf '   %bdeleted:%b    GhosttyRenderer.ts\n' "${e}[1;31m" "$r"
printf '   %b(use "git add <file>..." to stage)%b\n' "${e}[90m" "$r"

printf '\n %b@@ -24,7 +24,9 @@%b :root {\n' "${e}[1;36m" "$r"
printf ' %b-  --bg-term: oklch(0.12 ...);%b\n' "${e}[31m" "$r"
printf ' %b+  --bg-term: light-dark(...);%b\n' "${e}[32m" "$r"

printf '\n %b✓%b built in 3.61s   %b✓%b 147 tests\n' "${e}[1;32m" "$r" "${e}[1;32m" "$r"
printf ' %bwarning%b: chunk larger than 500 kB\n' "${e}[1;33m" "$r"
printf ' %bERROR%b: failed to resolve %b"ghostty-web"%b\n' "${e}[1;31m" "$r" "${e}[1;35m" "$r"
printf ' ➜  Local: %bhttp://localhost:5173/%b\n\n' "${e}[1;36m" "$r"
