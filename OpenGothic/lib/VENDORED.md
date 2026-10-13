# Engine libraries

The libraries are plain files in this repository (no git submodules). Only what the build uses is
kept: examples, tests, documentation and sample data were left out. They are not updated
automatically; to update one, copy the needed parts of a newer upstream version over its directory.

| Directory | Upstream | Commit | License | Left out |
|---|---|---|---|---|
| `Tempest/` | https://github.com/Try/Tempest | a4ac4029b848ef9f4e5636049dd657663c499a45 | MIT | `Examples/`, `Tests/` |
| `ZenKit/` | https://github.com/GothicKit/ZenKit | 083cc5f5592726aed435f8e863a6e09e92748a4d (v1.3.0-63) | MIT | `tests/`, `docs/`, `examples/`, `assets/` |
| `ZenKit/vendor/libsquish/` | https://github.com/lmichaelis/phoenix-libsquish | 53167b39beee3c7ff441c4ee6ba522c2db27dad4 | MIT | — |
| `ZenKit/vendor/doctest/` | https://github.com/doctest/doctest | 1da23a3e8119ec5cce4f9388e91b065e20bf06f5 (v2.4.12) | MIT | `scripts/data/` (kept because ZenKit's CMake would download it otherwise) |
| `bullet3/` | https://github.com/bulletphysics/bullet3 | d1a4256b3a019117f2bb6cb8c63d6367aaf512e2 (3.25-26) | zlib | `examples/`, `data/`, `docs/`, `test/`, `Extras/`, `build3/` |
| `dmusic/` | https://github.com/GothicKit/dmusic | b73b270b94714b8ec8d9d0b2a9f3a5c445da52d1 | MIT | `docs/`, `examples/` |
| `rapidjson/` | https://github.com/Tencent/rapidjson | f54b0e47a08782a6131cc3d60f94d038fa6e0a51 (v1.1.0) | MIT | everything but `include/` (header-only) |
| `TinySoundFont/` | https://github.com/schellingb/TinySoundFont | 92a8f0e9fe3c98358be7d8564db21fc4b1142d04 | MIT | `examples/`, `sfotool/` |
| `edd-dbg/`, `miniz/` | as in upstream OpenGothic | — | see the directories | — |

Git metadata of the libraries (`.git`, `.github`, `.gitmodules`, `.gitignore`) was not copied.

## Local changes

Keep these when updating a library from upstream.

- `Tempest/Engine/system/api/macosapi.mm`: `TEMPEST_BACKGROUND=1` starts the app without taking the
  focus and without a Dock icon (used by the test bench, `scripts/bench.sh`).
- `Tempest/Engine/system/api/macosapi.mm`: `TEMPEST_WINDOW_SIZE=<w>x<h>` sets the size of a window created
  by show mode (points; shorts are filmed in a 9:16 window, `shorts/`).
- `Tempest/Engine/sound/sounddevice.{h,cpp}`: `TEMPEST_AUDIO_LOOPBACK=<rate>` opens an OpenAL loopback
  device instead of the speakers; `SoundDevice::renderLoopback` mixes the next frames of every context
  (the test bench records the game's sound in step with the frames).
- `Tempest/Engine/sound/sound.{h,cpp}`: `Sound::samples16` gives the decoded samples of a sound (lip sync
  measures the loudness of a voice line, `common/graphics/mesh/lipsync.cpp`).
- `rapidjson/include/rapidjson/document.h`: `GenericStringRef::operator=` deleted (GCC 15 diagnoses the
  old one), and `memcpy` into `GenericValue`/`Member` storage goes through `void*`, as upstream does now
  (clang's `-Wnontrivial-memcall`; the engine builds with `-Werror`).
- `ZenKit/` `DaedalusVm::unsafe_call_external` (`DaedalusVm.hh`, `DaedalusVm.cc`): calls an engine
  function the way the `BE` instruction does. The story graph (`ai_bridge/storygraph`) computes the AI
  director's conditions with the engine's own functions through it.
