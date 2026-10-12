#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

// The hands of a character that speaks: the game's dialog gestures (T_DIALOGGESTURE_xx, 2..10 s of
// moving hands each). The original starts a random one when a line starts and none plays: after a
// short one the hands hang still while the voice goes on (the AI server says a reply in several
// lines, the next one may start only seconds later), a long one goes on while the other one speaks.
// Here one gesture follows another while the voice lasts, with a rest between them, the next one
// starting on a syllable; the mood of the line decides how lively they are and how long the rests;
// when the voice is over, the hands rest. Only decides: the caller plays and stops the animations.
class TalkGestures final {
  public:
    enum class Mood : uint8_t { Neutral, Friendly, Angry, Hostile, Afraid, Pain };

    struct Input final {
      uint64_t now      = 0;
      bool     allowed  = false;  // it stands with its hands free
      bool     speaking = false;  // its line is being said
      bool     voiced   = false;  // a voice moves the mouth: a gesture starts on a syllable
      bool     syllable = false;  // a syllable of the voice starts this frame
      bool     playing  = false;  // the gesture started last still plays
      uint64_t left     = 0;      // ms the line still lasts, as far as known (0: not known)
      bool     more     = false;  // another line of it is waiting
      };

    enum class Act : uint8_t { None, Start, Stop };
    struct Decision final {
      Act     act     = Act::None;
      uint8_t gesture = 0;  // to start: 1 is T_DIALOGGESTURE_01
      };

    // State for the test bench
    struct Probe final {
      bool     valid    = false;
      bool     enabled  = false;
      bool     engaged  = false;  // gestures go with its lines now
      bool     speaking = false;
      uint8_t  gesture  = 0;      // playing, 0: none
      Mood     mood     = Mood::Neutral;
      uint32_t starts   = 0;      // gestures started
      uint32_t cuts     = 0;      // started over one that still played
      uint32_t stops    = 0;      // stopped before their end (the voice is over)
      };

    static bool    isEnabled();
    static void    setEnabled(bool e);
    static bool    setParam(std::string_view name, float value);
    static auto    paramList() -> std::string;
    static Mood    moodOf(std::string_view mood);  // the bridge's say.mood; unknown: neutral
    static auto    moodName(Mood m) -> std::string_view;

    // how long each gesture of the model plays, ms (0: it has not this one); the liveliness of the
    // Gothic II ones is known, others count as lively
    void           setGestures(std::vector<uint32_t> durationMs, bool gothic2);
    void           setMood(Mood m) { mood = m; }
    bool           isIdle() const { return !engaged && current==0; }
    void           lineStart(uint64_t now);  // a line starts: the gestures go with it
    void           stop();                   // cut off: the other one speaks, a weapon is drawn...
    Decision       update(const Input& in);

    // the original way (switched off): a random gesture started with a line
    void           started(uint8_t gesture, bool overPlaying);
    void           probe(Probe& p) const;

    static Probe       subjectProbe;  // the npc the test bench watches
    static const void* probeNpc;

  private:
    enum Kind : uint8_t { Calm, Lively, Sharp };

    uint8_t        pick(uint64_t left, bool more);
    uint64_t       rest();
    uint32_t       random();

    std::vector<uint32_t> duration;
    std::vector<uint8_t>  kind;
    Mood                  mood       = Mood::Neutral;
    bool                  engaged    = false;
    bool                  speaking   = false;
    uint8_t               current    = 0;
    uint8_t               recent[2]  = {};
    uint64_t              lastSpeech = 0;
    uint64_t              nextAt     = 0;
    uint32_t              starts     = 0;
    uint32_t              cuts       = 0;
    uint32_t              stops      = 0;
    uint32_t              rnd        = 1;
  };
