#!/usr/bin/env bash
# Run on dev-ubuntu: bootstrap the user-local Vulkan shader build toolchain.
# No apt, sudo, Docker, service changes, engine builds, or model processes.
# Sources, builds, installation, and provenance stay under ~/gothic-ai/.
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  printf '%s\n' \
    'Usage: bash scripts/body-baseline-toolchain.sh' \
    'Run on dev-ubuntu as its ordinary user; no arguments or privileged installation.' \
    'Prefix: ~/gothic-ai/body-baseline/toolchain/vulkan-1.4.341.0' \
    'Existing matching builds are reused. Builds use nice 10 and at most 2 jobs.'
  exit 0
fi
[[ $# -eq 0 ]] || { printf 'Unexpected arguments; use --help.\n' >&2; exit 2; }

die() { printf 'body-baseline toolchain: %s\n' "$*" >&2; exit 1; }
[[ "$(uname -s)/$(uname -m)" == "Linux/x86_64" ]] || die 'Run this on dev-ubuntu (Linux x86_64).'
[[ "$(id -u)" != 0 ]] || die 'Run as an ordinary user, without sudo.'

for required in git gcc g++ cmake ninja nice python3 sha256sum readlink realpath flock awk cmp; do
  command -v "$required" >/dev/null || die "Missing $required; this script does not install system packages."
done
gcc_version="$(gcc -dumpfullversion -dumpversion)"
cxx_version="$(g++ -dumpfullversion -dumpversion)"
cmake_version="$(cmake --version | awk 'NR == 1 {print $3}')"
python3 - "$gcc_version" "$cxx_version" "$cmake_version" <<'PY'
import sys

for name, actual, minimum in (
    ("gcc", sys.argv[1], (8,)),
    ("g++", sys.argv[2], (8,)),
    ("cmake", sys.argv[3], (3, 22)),
):
    version = tuple(int(part) for part in actual.split("."))
    if version < minimum:
        raise SystemExit(f"{name} {actual} is too old; required: {'.'.join(map(str, minimum))}+")
PY

ldconfig_command="$(command -v ldconfig || true)"
if [[ -z "$ldconfig_command" ]]; then
  for candidate in /sbin/ldconfig /usr/sbin/ldconfig; do
    if [[ -x "$candidate" ]]; then ldconfig_command="$candidate"; break; fi
  done
fi
[[ -n "$ldconfig_command" ]] || die 'Cannot inspect the installed Vulkan loader (ldconfig missing).'
loader="$($ldconfig_command -p | awk '$1 == "libvulkan.so.1" && /x86-64/ && !found {print $NF; found = 1}')"
[[ -n "$loader" && -r "$loader" ]] || die 'Installed x86_64 libvulkan.so.1 not found; no system packages will be changed.'
loader="$(readlink -f "$loader")"

readonly sdk_tag='vulkan-sdk-1.4.341.0'
readonly glslang_commit='f0bd0257c308b9a26562c1a30c4748a0219cc951'
readonly headers_commit='b5c8f996196ba4aa6d8f97e52b5d3b6e70f7e4e2'
readonly glslang_url='https://github.com/KhronosGroup/glslang.git'
readonly headers_url='https://github.com/KhronosGroup/Vulkan-Headers.git'
readonly base="$HOME/gothic-ai/body-baseline/toolchain"
readonly prefix="$base/vulkan-1.4.341.0"
[[ "$(realpath -m "$base")" == "$base" ]] || die 'Toolchain path must not redirect through symlinks.'
mkdir -p "$base/src" "$base/build"
[[ ! -L "$base/bootstrap.lock" ]] || die 'Bootstrap lock must not be a symlink.'
exec 9>"$base/bootstrap.lock"
flock -n 9 || die 'Another toolchain bootstrap is already running.'
for managed in "$base/src" "$base/build" "$prefix"; do
  [[ "$(realpath -m "$managed")" == "$managed" ]] || die "Refusing redirected managed path: $managed"
done

checkout() {
  local url="$1" path="$2" expected="$3"
  [[ ! -L "$path" ]] || die "Source directory must not be a symlink: $path"
  if [[ ! -e "$path" ]]; then
    git -c advice.detachedHead=false clone --depth 1 --branch "$sdk_tag" "$url" "$path"
  fi
  [[ -d "$path/.git" ]] || die "Not a source checkout: $path"
  [[ "$(git -C "$path" remote get-url origin)" == "$url" ]] || die "Unexpected source origin: $path"
  [[ "$(git -C "$path" rev-parse HEAD)" == "$expected" ]] || die "Source commit mismatch: $path"
  [[ -z "$(git -C "$path" status --porcelain)" ]] || die "Source checkout has local changes: $path"
}
checkout "$glslang_url" "$base/src/glslang-1.4.341.0" "$glslang_commit"
checkout "$headers_url" "$base/src/Vulkan-Headers-1.4.341.0" "$headers_commit"

if [[ -x "$prefix/bin/glslangValidator" ]] && \
   "$prefix/bin/glslangValidator" --version | awk '/^Glslang Version:/ {found = ($3 == "11:16.2.0")} END {exit !found}'; then
  printf 'Reusing glslang 16.2.0 at %s\n' "$prefix/bin/glslangValidator"
else
  nice -n 10 cmake -S "$base/src/glslang-1.4.341.0" \
    -B "$base/build/glslang-1.4.341.0" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" \
    -DBUILD_SHARED_LIBS=OFF -DENABLE_OPT=OFF -DENABLE_HLSL=OFF \
    -DBUILD_EXTERNAL=OFF -DGLSLANG_TESTS=OFF -DGLSLANG_ENABLE_INSTALL=ON
  nice -n 10 cmake --build "$base/build/glslang-1.4.341.0" --parallel 2
  nice -n 10 cmake --install "$base/build/glslang-1.4.341.0"
fi

# Compare the installed header with the exact pinned source, not just its number.
if [[ ! -r "$prefix/include/vulkan/vulkan.h" ]] || \
   ! cmp -s "$base/src/Vulkan-Headers-1.4.341.0/include/vulkan/vulkan_core.h" \
     "$prefix/include/vulkan/vulkan_core.h"; then
  nice -n 10 cmake -S "$base/src/Vulkan-Headers-1.4.341.0" \
    -B "$base/build/Vulkan-Headers-1.4.341.0" -G Ninja \
    -DCMAKE_INSTALL_PREFIX="$prefix" -DVULKAN_HEADERS_ENABLE_TESTS=OFF
  nice -n 10 cmake --install "$base/build/Vulkan-Headers-1.4.341.0"
fi

mkdir -p "$prefix/lib"
if [[ -e "$prefix/lib/libvulkan.so" && ! -L "$prefix/lib/libvulkan.so" ]]; then
  die 'Refusing to replace a regular file at prefix/lib/libvulkan.so.'
fi
ln -sfn "$loader" "$prefix/lib/libvulkan.so"
"$prefix/bin/glslangValidator" --version

python3 - "$prefix" "$sdk_tag" "$glslang_commit" "$headers_commit" \
  "$glslang_url" "$headers_url" "$loader" "$gcc_version" "$cxx_version" "$cmake_version" <<'PY'
import datetime
import hashlib
import json
import pathlib
import platform
import subprocess
import sys

(prefix_str, tag, glslang_commit, headers_commit, glslang_url, headers_url,
 loader_str, gcc_version, cxx_version, cmake_version) = sys.argv[1:]
prefix = pathlib.Path(prefix_str)
binary = (prefix / "bin/glslangValidator").resolve()
header = prefix / "include/vulkan/vulkan_core.h"
loader = pathlib.Path(loader_str)

def sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()

manifest = {
    "schema_version": 1,
    "recorded_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "prefix": str(prefix),
    "platform": {"system": platform.system(), "machine": platform.machine()},
    "glslang": {
        "version": "16.2.0", "source_url": glslang_url, "source_tag": tag,
        "source_commit": glslang_commit, "binary": str(binary), "binary_sha256": sha256(binary),
        "version_output": subprocess.check_output([str(binary), "--version"], text=True).strip(),
        "options": {"BUILD_SHARED_LIBS": False, "ENABLE_OPT": False, "ENABLE_HLSL": False,
                    "BUILD_EXTERNAL": False, "GLSLANG_TESTS": False, "GLSLANG_ENABLE_INSTALL": True},
    },
    "vulkan_headers": {
        "version": "1.4.341", "source_url": headers_url, "source_tag": tag,
        "source_commit": headers_commit, "header": str(header), "header_sha256": sha256(header),
    },
    "vulkan_loader": {"system_library": str(loader), "sha256": sha256(loader),
                      "link": str(prefix / "lib/libvulkan.so")},
    "build_tools": {"gcc": gcc_version, "g++": cxx_version, "cmake": cmake_version,
                    "ninja": subprocess.check_output(["ninja", "--version"], text=True).strip(),
                    "parallel_jobs": 2, "nice": 10},
}
temporary = prefix / "manifest.json.tmp"
temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
temporary.replace(prefix / "manifest.json")
PY

printf 'Manifest: %s/manifest.json\n' "$prefix"
printf 'Use for the engine build:\nexport VULKAN_SDK=%q\nexport PATH="$VULKAN_SDK/bin:$PATH"\n' "$prefix"
