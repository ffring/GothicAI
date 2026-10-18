#pragma once

#include <Tempest/SoundEffect>

#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <vector>

#include "graphics/mesh/lipsync.h"

// Audio of a line that is still being generated (streaming TTS): chunks are appended on the main
// thread while the audio thread plays what has arrived. Underruns play silence.
class VoiceBuffer final {
  public:
    explicit VoiceBuffer(uint32_t rate):freq(rate), lips(std::make_shared<LipSync::Track>(rate)) {
      lips->setPlayed(0);  // the mouth waits for the audio thread to start playing
      }

    void     append(const uint8_t* pcm16, size_t bytes);
    void     finish();

    uint32_t rate()        const { return freq; }
    bool     isFinished()  const;  // all audio has arrived
    bool     isDrained()   const;  // ...and has been played
    uint64_t durationMs()  const;  // of the audio that has arrived

    // the audio thread: `n` mono samples, silence where none has arrived yet
    void     render(int16_t* out, size_t n);

    // Installed before play, only for bench voice stems. Receives the actual rendered PCM,
    // including streaming underruns, but not padding after the finished line.
    void     record(std::function<void(const int16_t*,size_t)> sink);

    // its loudness for the speaker's mouth, filled as the audio arrives and plays
    std::shared_ptr<LipSync::Track> lipTrack() const { return lips; }

    // a sound source for Tempest::SoundDevice that plays this buffer
    static std::unique_ptr<Tempest::SoundProducer> producer(std::shared_ptr<VoiceBuffer> buf);

  private:
    mutable std::mutex   sync;
    std::vector<int16_t> pcm;
    size_t               pos   = 0;
    bool                 ended = false;
    uint32_t             freq  = 0;
    std::shared_ptr<LipSync::Track> lips;
    std::function<void(const int16_t*,size_t)> recording;
  };
