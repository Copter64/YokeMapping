#!/usr/bin/env bash
# Read-only reconnaissance of a Steam Frame. Changes nothing.
# Run from the PC:  ssh steamos@frame 'bash -s' < tools/frame_recon.sh > frame_recon.txt
echo "=== os ==="; head -6 /etc/os-release; uname -a
echo "=== user/disk ==="; id; df -h /home / 2>/dev/null | head -4
echo "=== tools ==="
for t in g++ gcc glslc python3 git cmake pip3 curl; do printf "%s: " "$t"; command -v "$t" || echo MISSING; done
python3 --version 2>&1
echo "=== video devices ==="; ls -l /dev/video* 2>&1 | head -30
echo "=== steamvr ==="; systemctl --user is-active steamvr 2>&1
ls ~/.local/share/Steam/steamapps/common 2>/dev/null | head
pgrep -a vrcompositor | head -2; pgrep -a XRService | head -2
echo "=== calibration files ==="; find / -name 'sensorRigCalibration*' 2>/dev/null | head
echo "=== vulkan layers ==="
ls ~/.local/share/vulkan/implicit_layer.d 2>&1 | head
ls /usr/share/vulkan/implicit_layer.d 2>&1 | head
echo "=== temps (millidegrees C) ==="
for z in /sys/class/thermal/thermal_zone*; do printf "%s %s\n" "$(cat "$z/type" 2>/dev/null)" "$(cat "$z/temp" 2>/dev/null)"; done | sort | head -40
echo "=== load/mem ==="; uptime; free -m | head -2
echo "=== third-party artefacts present? ==="; ls ~/handtrack ~/.config/mrhand 2>&1 | head -5
