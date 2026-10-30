#!/usr/bin/env bash
# One-shot grayscale still of every colour passthrough camera buffer (2464x2464 luma, PGM),
# for locating the yoke faceplate. Run ON the headset via:
#   tools\run_frame_test.ps1 -Script frame_colour_snapshot.sh -FetchDir /dev/shm/ptcolour -Label ...
#
# Needs the capture layer (VK_LAYER_MRHAND) installed and enabled, and the cameras active
# (room view / passthrough ON); idle cameras give black frames. The images show the room:
# run only with the owner's consent. Frames go to /dev/shm (RAM); the runner fetches them
# and deletes the directory. Only the second pass of each buffer is kept (~6 MB each).
OUT=/dev/shm/ptcolour
rm -rf "$OUT"; mkdir -p "$OUT"
echo "== env =="
echo "date: $(date -Is)"
echo "steamvr: $(systemctl --user is-active steamvr)  compositor: $(pgrep -c vrcompositor) proc"
echo "layer flag: $([ -f "$HOME/.config/mrhand/enable-capture" ] && echo enabled || echo DISABLED)"
cd "$HOME/handtrack/src" || exit 1
timeout 30 ./capture/build/ptgrab identify "$OUT" 2>&1 | tail -n 20
rm -f "$OUT"/*_pass0.pgm
echo "== files =="
ls -l "$OUT"
n=$(ls "$OUT"/*.pgm 2>/dev/null | wc -l)
[ "$n" -gt 0 ] || { echo "RESULT: no images written (are the cameras active? room view on?)"; exit 5; }
echo "RESULT: $n image(s) in $OUT"
