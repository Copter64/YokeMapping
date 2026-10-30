#!/usr/bin/env bash
# Phase 1b' tracking-camera probe. Run ON the headset (normally via
#   tools\run_frame_test.ps1 -Script frame_trackcam_probe.sh -Label ...).
#
# Question: do the compositor-imported buffers that match the tracking cameras update
# live? Two candidates: 1056x1024 (8-bit NV12, linear) and 1152x4608 (10-bit 2-plane
# 4:2:0, UBWC; possibly four stacked 1152x1152 images). Builds a private patched copy
# of ptgrab in /tmp/ptprobe and deletes it afterwards. Saves NO frames: identify is
# pointed at a non-existent directory so its PGM dump is skipped; stream only counts
# frame changes in a thin strip of rows.
# Needs the VK_LAYER_MRHAND layer installed + enabled and SteamVR running.
#
# Patch (all verified against upstream ptgrab.cpp @ 8852ec9):
#   PTW/PTH   size filter (upstream hard-codes 2464x2464)
#   PTBPP     bytes per luma sample (1 = 8-bit, 2 = 10-bit stored in 16 bits); fixes
#             buffer sizing/offsets so 16-bit planes do not overlap or overflow
#   PTS0      first row of the 16-row change-detection strip (stream)
#   PTBANDS   identify also prints mean brightness per horizontal band
SECS=${SECS:-8}
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
SRC="$HOME/handtrack/src/capture"
W=/tmp/ptprobe
trap 'rm -rf "$W"; rm -f /dev/shm/mrhand-pt' EXIT

hot() {
  for z in /sys/class/thermal/thermal_zone*; do
    t=$(cat "$z/type")
    case $t in cpu*-thermal|camera*|gpuss-0*) echo "$(cat "$z/temp") $t";; esac
  done | sort -n -r | head -n 1 | awk '{printf "%.1fC %s", $1/1000, $2}'
}

echo "== env =="
echo "date: $(date -Is)"
echo "steamvr: $(systemctl --user is-active steamvr)  compositor pid: $(pgrep -o vrcompositor)"
echo "layer manifest: $([ -f "$HOME/.local/share/vulkan/implicit_layer.d/mrhand.json" ] && echo yes || echo NO)"
echo "layer enabled:  $([ -f "$HOME/.config/mrhand/enable-capture" ] && echo yes || echo NO)"
echo "guard tripped:  $([ -f "$HOME/.config/mrhand/guard-tripped" ] && echo YES || echo no)"
echo "cam.sock:       $([ -S "$XDG_RUNTIME_DIR/mrhand/cam.sock" ] && echo yes || echo NO)"
[ -f "$SRC/ptgrab.cpp" ] || { echo "RESULT: layer source missing (~/handtrack/src); install the layer first"; exit 3; }
[ -f "$HOME/.local/share/vulkan/implicit_layer.d/mrhand.json" ] || { echo "RESULT: layer not installed"; exit 4; }
[ -f "$HOME/.config/mrhand/enable-capture" ] || { echo "RESULT: layer installed but disabled"; exit 4; }
[ -S "$XDG_RUNTIME_DIR/mrhand/cam.sock" ] || { echo "RESULT: no cam.sock; restart SteamVR so the compositor loads the layer"; exit 5; }

echo "== images the compositor imported (count fmt size modifier planes) =="
LOG=/tmp/mrhandcam-$(pgrep -o vrcompositor).log
if [ -f "$LOG" ]; then
  grep "CreateImage" "$LOG" | awk '{k=$5" "$6" "$10" "$11; c[k]++} END{for(k in c) printf "%4d  %s\n", c[k], k}' | sort -rn | head -n 12
else
  echo "(no layer log $LOG)"
fi

echo "== build patched probe =="
rm -rf "$W"; mkdir -p "$W"; cp "$SRC/ptgrab.cpp" "$SRC/handcam_proto.h" "$W/"; cd "$W" || exit 1
sed -i '/#include "handcam_proto.h"/a static uint32_t envdim(const char *n, uint32_t d) { const char *v = getenv(n); return v ? (uint32_t)atoi(v) : d; }' ptgrab.cpp
sed -i '/#include "handcam_proto.h"/a static double meanY(const uint8_t *y, uint64_t n, uint32_t bpp) { double s = 0; uint64_t c = 0; for (uint64_t k = (bpp == 2); k < n; k += 97 * bpp) { s += y[k]; c++; } return c ? s / c : 0.0; }' ptgrab.cpp
sed -i '/#include "handcam_proto.h"/a static void bandMeans(uint32_t id, const uint8_t *y, uint64_t n, uint32_t bpp, uint32_t bands) { printf("  #%u bands:", id); for (uint32_t b = 0; b < bands; b++) { uint64_t lo = n / bands * b, hi = n / bands * (b + 1); double s = 0; uint64_t c = 0; for (uint64_t k = lo + (bpp == 2); k < hi; k += 97 * bpp) { s += y[k]; c++; } printf(" %.1f", c ? s / c : 0.0); } printf("\\n"); }' ptgrab.cpp
sed -i 's/b\.info\.width == 2464 && b\.info\.height == 2464/b.info.width == envdim("PTW", 2464) \&\& b.info.height == envdim("PTH", 2464)/' ptgrab.cpp
sed -i 's|VkDeviceSize one = 2464ull \* 2464;|VkDeviceSize one = (VkDeviceSize)envdim("PTW", 2464) * envdim("PTH", 2464) * envdim("PTBPP", 1);|' ptgrab.cpp
sed -i 's|off += (VkDeviceSize)ci\.info\.width \* ci\.info\.height;|off += (VkDeviceSize)ci.info.width * ci.info.height * envdim("PTBPP", 1);|' ptgrab.cpp
sed -i 's|for (VkDeviceSize k = 0; k < one; k += 97) sum += y\[k\];|sum = meanY(y, one, envdim("PTBPP", 1)) * (one / 97.0); if (envdim("PTBANDS", 1) > 1) bandMeans(ims[i].info.id, y, one, envdim("PTBPP", 1), envdim("PTBANDS", 1));|' ptgrab.cpp
sed -i 's|const uint32_t S0 = H / 2 - 8, SR = 16;|const uint32_t S0 = envdim("PTS0", H / 2 - 8), SR = 16;|' ptgrab.cpp
sed -i 's|const VkDeviceSize strip = (VkDeviceSize)W \* SR;|const VkDeviceSize strip = (VkDeviceSize)W * SR * envdim("PTBPP", 1);|' ptgrab.cpp
sed -i 's|const VkDeviceSize frame = (VkDeviceSize)OW \* OH;|const VkDeviceSize frame = (VkDeviceSize)OW * OH * envdim("PTBPP", 1);|' ptgrab.cpp
if [ "$(grep -c envdim ptgrab.cpp)" != 8 ] || [ "$(grep -c 'meanY\|bandMeans' ptgrab.cpp)" != 3 ]; then
  echo "RESULT: patch did not apply cleanly (upstream ptgrab changed?)"; exit 6
fi
g++ -std=c++17 -O2 -Wall ptgrab.cpp -o ptprobe -lvulkan -lpthread 2>&1 | tail -n 5
[ -x ptprobe ] || { echo "RESULT: build failed"; exit 7; }

# run_size label W H bytes-per-sample bands strip-rows...
run_size() {
  local label=$1 w=$2 h=$3 bpp=$4 bands=$5; shift 5
  export PTW=$w PTH=$h PTBPP=$bpp PTBANDS=$bands
  echo "== $label ${w}x${h} bpp=$bpp: identify (mean per buffer, bands = horizontal quarters; nothing saved) =="
  timeout 60 ./ptprobe identify /nonexistent-dir 2>&1 | grep -E "imported|pass|mean|bands|no passthrough" | head -n 140
  local s0
  for s0 in "$@"; do
    export PTS0=$s0
    echo "== $label ${w}x${h}: stream ${SECS}s, change-strip rows ${s0}-$((s0 + 15)) =="
    echo "start hottest: $(hot)"
    timeout "$SECS" ./ptprobe stream 60 0 > "$W/stream.log" 2>&1
    echo "end   hottest: $(hot)"
    grep -E "fps" "$W/stream.log" | awk '
      { c0=$2; c1=$5; n++; s0+=c0; s1+=c1; if (n==1||c0<m0) m0=c0; if (n==1||c1<m1) m1=c1; if (c0>0||c1>0) live++ }
      END { if (n==0) { print "no fps lines (nothing streamed in this window)"; exit }
            printf "samples=%d live=%d  half0 avg=%.1f min=%.1f  half1 avg=%.1f min=%.1f fps\n", n, live, s0/n, m0, s1/n, m1 }'
    grep -E "streaming|no passthrough|error|fail" "$W/stream.log" | head -n 2
  done
  unset PTS0
}

run_size "A:linear-nv12" 1056 1024 1 1 504
run_size "B:10bit-stack" 1152 4608 2 4 568 1720 2872 4024
echo "== done =="
