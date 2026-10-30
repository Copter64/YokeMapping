#!/usr/bin/env bash
# Phase 1b capture-stage test, run ON the headset (normally via tools/run_frame_test.ps1).
# Streams the passthrough cameras with ptgrab for SECS seconds and reports per-camera
# frame rate, hottest-zone temperature over time, and SteamVR state.
# Needs VK_LAYER_MRHAND installed + enabled and the cameras active (room view on);
# idle cameras report 0 fps.
SECS=${SECS:-20}
LOG=$(mktemp /tmp/ptstream.XXXXXX)

hot() {
  for z in /sys/class/thermal/thermal_zone*; do
    t=$(cat "$z/type")
    case $t in cpu*-thermal|camera*|gpuss-0*) echo "$(cat "$z/temp") $t";; esac
  done | sort -n -r | head -n 1 | awk '{printf "%.1fC %s", $1/1000, $2}'
}

echo "== env =="
echo "date: $(date -Is)"
echo "steamvr: $(systemctl --user is-active steamvr)  compositor: $(pgrep -c vrcompositor) proc"
echo "layer flag: $([ -f "$HOME/.config/mrhand/enable-capture" ] && echo enabled || echo DISABLED)"
echo "guard: $([ -f "$HOME/.config/mrhand/guard-tripped" ] && echo TRIPPED || echo ok)"
echo "== run ${SECS}s =="
echo "t=0s   hottest: $(hot)"
cd "$HOME/handtrack/src" || exit 1
timeout "$SECS" ./capture/build/ptgrab stream 30 640 > "$LOG" 2>&1 &
pid=$!
t=0
while kill -0 "$pid" 2>/dev/null; do
  sleep 5
  t=$((t + 5))
  kill -0 "$pid" 2>/dev/null && echo "t=${t}s  hottest: $(hot)"
done
wait "$pid" 2>/dev/null
echo "t=end  hottest: $(hot)"
echo "== frame rate =="
grep -E "fps" "$LOG" | awk '
  { c0=$2; c1=$5; n++; s0+=c0; s1+=c1; if (n==1||c0<m0) m0=c0; if (n==1||c1<m1) m1=c1
    if (c0>0||c1>0) live++ }
  END { if (n==0) { print "no fps lines (ptgrab did not start streaming)"; exit }
        printf "samples=%d live=%d  cam0 avg=%.1f min=%.1f  cam1 avg=%.1f min=%.1f fps\n", n, live, s0/n, m0, s1/n, m1 }'
grep -E "streaming|error|fail|connect" "$LOG" | head -n 5
rm -f "$LOG"
