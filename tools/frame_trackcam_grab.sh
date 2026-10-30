#!/usr/bin/env bash
# Phase 1b' ONE-FRAME grab from the 1152x4608 (10-bit, four stacked quarters) buffers.
# Run ON the headset via:
#   tools\run_frame_test.ps1 -Script frame_trackcam_grab.sh -FetchDir /dev/shm/ptframes -Label ...
# Saves ONE still (the first buffer that changed between two reads) as an 8-bit PGM
# (the high byte of each 10-bit-in-16-bit sample) into /dev/shm/ptframes (RAM). The
# runner copies it to the PC (gitignored data\frames) and deletes the headset copy.
# The image shows the room as seen by the tracking cameras: only run it with the
# owner's consent. Same patched private ptgrab as frame_trackcam_probe.sh, plus a
# one-shot PGM writer. Nothing else is saved; the build dir is deleted on exit.
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
SRC="$HOME/handtrack/src/capture"
W=/tmp/ptprobe
OUT=/dev/shm/ptframes
trap 'rm -rf "$W"; rm -f /dev/shm/mrhand-pt' EXIT

echo "== env =="
echo "date: $(date -Is)"
echo "steamvr: $(systemctl --user is-active steamvr)  compositor pid: $(pgrep -o vrcompositor)"
[ -f "$SRC/ptgrab.cpp" ] || { echo "RESULT: layer source missing"; exit 3; }
[ -f "$HOME/.config/mrhand/enable-capture" ] && [ -f "$HOME/.local/share/vulkan/implicit_layer.d/mrhand.json" ] || { echo "RESULT: layer not installed/enabled"; exit 4; }
[ -S "$XDG_RUNTIME_DIR/mrhand/cam.sock" ] || { echo "RESULT: no cam.sock; restart SteamVR"; exit 5; }

rm -rf "$W" "$OUT"; mkdir -p "$W" "$OUT"; cp "$SRC/ptgrab.cpp" "$SRC/handcam_proto.h" "$W/"; cd "$W" || exit 1
sed -i '/#include "handcam_proto.h"/a static uint32_t envdim(const char *n, uint32_t d) { const char *v = getenv(n); return v ? (uint32_t)atoi(v) : d; }' ptgrab.cpp
sed -i '/#include "handcam_proto.h"/a static double meanY(const uint8_t *y, uint64_t n, uint32_t bpp) { double s = 0; uint64_t c = 0; for (uint64_t k = (bpp == 2); k < n; k += 97 * bpp) { s += y[k]; c++; } return c ? s / c : 0.0; }' ptgrab.cpp
sed -i '/#include "handcam_proto.h"/a static int g_saved = 0; static void writeY8(FILE *f, const uint8_t *y, uint64_t w, uint64_t h, uint32_t bpp) { std::vector<uint8_t> o(w * h); for (uint64_t k = 0; k < w * h; k++) o[k] = bpp == 2 ? y[2 * k + 1] : y[k]; fwrite(o.data(), 1, o.size(), f); }' ptgrab.cpp
sed -i 's/b\.info\.width == 2464 && b\.info\.height == 2464/b.info.width == envdim("PTW", 2464) \&\& b.info.height == envdim("PTH", 2464)/' ptgrab.cpp
sed -i 's|VkDeviceSize one = 2464ull \* 2464;|VkDeviceSize one = (VkDeviceSize)envdim("PTW", 2464) * envdim("PTH", 2464) * envdim("PTBPP", 1);|' ptgrab.cpp
sed -i 's|off += (VkDeviceSize)ci\.info\.width \* ci\.info\.height;|off += (VkDeviceSize)ci.info.width * ci.info.height * envdim("PTBPP", 1);|' ptgrab.cpp
sed -i 's|for (VkDeviceSize k = 0; k < one; k += 97) sum += y\[k\];|sum = meanY(y, one, envdim("PTBPP", 1)) * (one / 97.0);|' ptgrab.cpp
sed -i 's|snprintf(fn, sizeof fn, "%s/pt%u_pass%d.pgm", out.c_str(), ims\[i\].info.id, pass);|snprintf(fn, sizeof fn, "%s/pt%u_pass%d.pgm", (pass == 1 \&\& changed \&\& !g_saved++) ? out.c_str() : "/nonexistent-dir", ims[i].info.id, pass);|' ptgrab.cpp
sed -i 's|fprintf(f, "P5 2464 2464 255\\n");|fprintf(f, "P5 %u %u 255\\n", envdim("PTW", 2464), envdim("PTH", 2464));|' ptgrab.cpp
sed -i 's|fwrite(y, 1, one, f);|writeY8(f, y, envdim("PTW", 2464), envdim("PTH", 2464), envdim("PTBPP", 1));|' ptgrab.cpp
if [ "$(grep -c envdim ptgrab.cpp)" != 7 ] || [ "$(grep -c 'g_saved' ptgrab.cpp)" != 2 ] || [ "$(grep -c writeY8 ptgrab.cpp)" != 2 ]; then
  echo "RESULT: patch did not apply cleanly (upstream ptgrab changed?)"; exit 6
fi
g++ -std=c++17 -O2 -Wall ptgrab.cpp -o ptprobe -lvulkan -lpthread 2>&1 | tail -n 5
[ -x ptprobe ] || { echo "RESULT: build failed"; exit 7; }

export PTW=1152 PTH=4608 PTBPP=2
for try in 1 2 3 4 5; do
  timeout 60 ./ptprobe identify "$OUT" > "$W/id.log" 2>&1
  [ -n "$(ls "$OUT" 2>/dev/null)" ] && break
  sleep 0.3
done
grep -E "pass|CHANGED" "$W/id.log" | grep -E "pass|CHANGED" | head -n 6
echo "== saved (stays in $OUT until the runner copies and deletes it) =="
ls -l "$OUT"
[ -n "$(ls "$OUT" 2>/dev/null)" ] || { echo "RESULT: no buffer changed between reads; nothing saved"; exit 8; }
echo "== done =="
