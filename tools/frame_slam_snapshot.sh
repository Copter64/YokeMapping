#!/usr/bin/env bash
# Phase 1b' "2-second look": what do the tracking (SLAM/upper) cameras see?
# Run ON the headset via:
#   tools\run_frame_test.ps1 -Script frame_slam_snapshot.sh -FetchDir /dev/shm/ptsnap -Label ...
#
# Uses Valve's own tracking-dataset recorder for ~SECS seconds (default 2). The recorder
# writes a dataset (IMU, one SLAM camera's video, poses) plus Begin/End snapshots with a
# full-resolution PNG of every camera. This script copies ONLY the SLAM and upper camera
# snapshot PNGs (+ cameraMetadata.json) into /dev/shm/ptsnap (RAM) for the runner to fetch,
# then deletes the ENTIRE dataset this run created (it contains room video). If the PNG file
# names do not match 'slam' or 'upper', nothing is copied and the folder listing is printed.
# The snapshots show the room: run only with the owner's consent.
SECS=${SECS:-2}
V=/opt/steamvr/bin/linuxarm64/vrcmd
D="$HOME/.config/openvr/config/cv/xrservice/datasets"
OUT=/dev/shm/ptsnap
MARK=$(mktemp /tmp/snapmark.XXXXXX)
NEW=""
cleanup() {
  # Remove only datasets created by this run (newer than MARK), by exact name pattern.
  for d in "$D"/Deckard_*.dataset; do
    [ -d "$d" ] && [ "$d" -nt "$MARK" ] && rm -rf -- "$d"
  done
  rm -f "$MARK"
}
trap cleanup EXIT

echo "== env =="
echo "date: $(date -Is)"
echo "steamvr: $(systemctl --user is-active steamvr)"
[ -x "$V" ] || { echo "RESULT: vrcmd not found"; exit 3; }
ls "$D" 2>/dev/null | head -n 3 | sed 's/^/pre-existing dataset: /'

rm -rf "$OUT"; mkdir -p "$OUT"
sleep 0.5
echo "== recording ${SECS}s (hold still, looking at the yoke) =="
"$V" --cmdhmd "start_tracking_recording 1 1 1" 2>&1 | tail -n 1
sleep "$SECS"
"$V" --cmdhmd "stop_tracking_recording" 2>&1 | tail -n 1
sleep 6

for d in "$D"/Deckard_*.dataset; do
  [ -d "$d" ] && [ "$d" -nt "$MARK" ] && NEW="$d"
done
[ -n "$NEW" ] || { echo "RESULT: no new dataset appeared (headset in standby? requests queue while not worn)"; exit 4; }
echo "dataset: $(basename "$NEW")   size: $(du -sh "$NEW" | cut -f1)"

echo "== snapshot folders =="
find "$NEW/snapshots" -maxdepth 3 -type f -printf '%P  %s bytes\n' 2>/dev/null | sort | head -n 60

echo "== copying SLAM/upper snapshot images only =="
n=0
while IFS= read -r f; do
  rel=${f#"$NEW/snapshots/"}
  flat=$(echo "$rel" | tr '/' '_')
  cp -- "$f" "$OUT/$flat" && n=$((n + 1))
done < <(find "$NEW/snapshots" -type f \( -iname '*.png' -o -iname 'cameraMetadata.json' \) \( -ipath '*slam*' -o -ipath '*upper*' -o -iname 'cameraMetadata.json' \))
echo "copied $n file(s) to $OUT"
ls -l "$OUT"
[ "$n" -gt 0 ] || { echo "RESULT: no file names matched slam/upper; see the listing above"; exit 5; }
echo "== done (dataset will be deleted on exit) =="
