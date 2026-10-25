# Android

An ARM64 NativeActivity APK using Tempest's Android backend and generated Gradle project. This is the basic platform integration; touch controls, controller input and game-file importing are not included yet. Android window destruction closes the application, including when backgrounding or locking destroys its surface.

## Build

Install JDK 17, Gradle 8.9, CMake 3.22.1 or newer, Ninja and the Android SDK with SDK 35, build-tools 35.0.0, NDK 27.0.12077973 and SDK CMake 3.22.1. Set `JAVA_HOME`, `ANDROID_HOME` and `VULKAN_SDK`. Put Gradle, Ninja and the host Vulkan SDK's `glslangValidator` on `PATH`. Vulkan headers come from the host SDK; Android links the NDK's Vulkan loader.

Clone with `git clone --recursive https://github.com/Try/OpenGothic.git`, or run `git submodule update --init --recursive` in an existing checkout.

The base importer also needs OpenSSL Crypto built for Android ARM64/API 24; the NDK does not include it, and a host OpenSSL package cannot be linked into the APK. With Perl, Make and curl installed, build the pinned dependency in a separate directory (Bash):

```bash
curl -fsSLO https://github.com/openssl/openssl/releases/download/openssl-3.5.5/openssl-3.5.5.tar.gz
echo 'b28c91532a8b65a1f983b4c28b7488174e4a01008e29ce8e69bd789f28bc2a89  openssl-3.5.5.tar.gz' | shasum -a 256 -c -
tar -xf openssl-3.5.5.tar.gz
cd openssl-3.5.5
export ANDROID_NDK_ROOT=/path/to/ndk
export PATH="$ANDROID_NDK_ROOT/toolchains/llvm/prebuilt/linux-x86_64/bin:$PATH"
./Configure android-arm64 -D__ANDROID_API__=24 no-shared no-tests no-module --prefix=/path/to/openssl-android --libdir=lib
make -j2 build_libs
make install_dev
```

Use absolute paths for the NDK and installation prefix. On macOS use `darwin-x86_64` instead of `linux-x86_64`; Windows requires an OpenSSL-supported shell such as MSYS2 (see [OpenSSL's Android build notes](https://github.com/openssl/openssl/blob/openssl-3.5.5/NOTES-ANDROID.md)). CI performs this cross-build before configuring the engine.

Return to the OpenGothic source directory. Replace `/path/to/ndk` and `/path/to/openssl-android` below with those directories. These CMake commands work in PowerShell and a Linux shell; the same OpenSSL arguments apply when configuring `scripts/convert` separately.

```sh
cmake -S . -B build/android -G Ninja -DCMAKE_TOOLCHAIN_FILE=/path/to/ndk/build/cmake/android.toolchain.cmake -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-24 -DOPENSSL_INCLUDE_DIR=/path/to/openssl-android/include -DOPENSSL_CRYPTO_LIBRARY=/path/to/openssl-android/lib/libcrypto.a -DCMAKE_BUILD_TYPE=Release
cmake --build build/android --target OpenGothic-apk
```

APK: `build/android/OpenGothic-apk/build/outputs/apk/release/OpenGothic-apk-release.apk`. Use `-DCMAKE_BUILD_TYPE=Debug` for a debuggable APK under `outputs/apk/debug/OpenGothic-apk-debug.apk`. Desktop builds do not invoke the APK helper or require Android tools.

## Install and game files

Use a legally owned Gothic II: Night of the Raven installation. No game files are included in the APK. Copy the installation root, not only its `Data` directory: `Data`, `_work` and `System` must be directly inside `Gothic2`.

```sh
adb install -r build/android/OpenGothic-apk/build/outputs/apk/release/OpenGothic-apk-release.apk
adb shell mkdir -p /sdcard/Android/data/org.opengothic.app/files/Gothic2
adb push "/path/to/Gothic II/." /sdcard/Android/data/org.opengothic.app/files/Gothic2/
adb shell am start -n org.opengothic.app/android.app.NativeActivity
adb logcat -s app AndroidRuntime DEBUG
```

Game files use app-specific external storage, without storage permissions. Logs, saves and writable settings use the app's internal files directory. Uninstalling removes both directories. Ray tracing and mesh shading are disabled for this initial Android build.

With a debug APK, read the file log using `adb shell run-as org.opengothic.app cat files/log.txt`.
