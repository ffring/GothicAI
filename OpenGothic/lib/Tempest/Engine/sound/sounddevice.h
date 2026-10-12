#pragma once

#include <Tempest/Vec>

#include <cstdint>
#include <vector>
#include <memory>
#include <string>

namespace Tempest {

class Sound;
class SoundProducer;
class SoundEffect;
class IDevice;

class SoundDevice final {
  public:
    SoundDevice ();
    SoundDevice (std::string_view name);
    SoundDevice (const SoundDevice&) = delete;
    ~SoundDevice();

    struct Props {
      char name[256] = {};
      };

    static std::vector<Props> devices();

    SoundDevice& operator = ( const SoundDevice& s) = delete;

    SoundEffect load(const char* fname);
    SoundEffect load(Tempest::IDevice& d);
    SoundEffect load(const Sound& snd);
    SoundEffect load(std::unique_ptr<SoundProducer> &&p);

    void process();
    void suspend();

    void setListenerPosition(const Tempest::Vec3& p);
    void setListenerPosition(float x,float y,float z);

    void setListenerDirection(const Tempest::Vec3& forward, const Tempest::Vec3& up);
    void setListenerDirection(float dx, float dy, float dz, float ux, float uy, float uz);

    void setGlobalVolume(float v);

    // TEMPEST_AUDIO_LOOPBACK=<rate>: nothing plays; the mix of every context waits until the
    // application renders it (recording in step with the frames). 0 when off
    static uint32_t loopbackRate();
    // the next `frames` frames of the mix, 16-bit stereo; false if loopback is off
    static bool     renderLoopback(int16_t* out, size_t frames);

  private:
    struct Data;
    struct Device;
    struct PhysicalDeviceList;

    std::unique_ptr<Data> data;

    void*  context();

  friend class Sound;
  friend class SoundEffect;
  };

}
