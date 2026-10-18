#!/usr/bin/env bash
# CI stand-in for the P30 load run: rebuild the engine in a loop with 6 parallel jobs.
# Run inside: systemd-run --user --unit p30-load-ci -p CPUQuota=600% <this script>
set -euo pipefail
root=$HOME/gothic-ai/p30-load
export VULKAN_SDK="$HOME/gothic-ai/body-baseline/toolchain/vulkan-1.4.341.0"
export PATH="$VULKAN_SDK/bin:$PATH"
[[ -f "$root/build/build.ninja" ]] || cmake -S "$HOME/gothic-ai/body-video/source-merged/OpenGothic" -B "$root/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DCMAKE_CXX_FLAGS="-I$VULKAN_SDK/include" -DCMAKE_EXE_LINKER_FLAGS="-L$VULKAN_SDK/lib" > "$root/configure.log" 2>&1
[[ "${1:-}" == configure ]] && exit 0
round=0
while true; do
  round=$((round + 1)); echo "round $round $(date -u +%FT%TZ)"
  ninja -C "$root/build" -t clean > /dev/null
  ninja -C "$root/build" -j6 gothic-body-runner Gothic2Notr > "$root/build.log" 2>&1 || tail -5 "$root/build.log"
done
