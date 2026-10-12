#include "talkgestures.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>

namespace {

float Tail      = 500.f;   // ms: the voice is over this long (and no line waits): the hands rest
float Wait      = 400.f;   // ms: a gesture due waits this long at most for a syllable to start on
float MinLeft   = 1200.f;  // ms: with less of the line left (and none after it) no gesture starts
float Slack     = 600.f;   // ms: a gesture longer than the line left by more than this is unlikely...
float Long      = 0.15f;   // ...this many times as likely as one that fits
float RestScale = 1.f;     // of the rests between gestures

struct Param final {
  std::string_view name;
  float*           value;
  };

const Param params[] = {
  {"Tail", &Tail}, {"Wait", &Wait}, {"MinLeft", &MinLeft}, {"Slack", &Slack}, {"Long", &Long},
  {"RestScale", &RestScale},
  };

bool enabled = []() {
  const char* v = std::getenv("OPENGOTHIC_GESTURES");
  return v==nullptr || v[0]!='0';
  }();

// How lively the Gothic II gestures are, by how fast the arms turn (mean over the gesture, the
// fastest tenth): 06 is 9.6 s of slow hands and head, 07 and 09 are 6 s, 08 and 10 have quick jabs
constexpr uint8_t G2Kind[10] = {
  0 /*01 calm*/, 1 /*02 lively*/, 1 /*03 lively*/, 0 /*04 calm*/, 1 /*05 lively*/,
  0 /*06 calm*/, 0 /*07 calm*/,   2 /*08 sharp*/,  0 /*09 calm*/, 2 /*10 sharp*/,
  };

struct MoodStyle final {
  float    weight[3];  // of a calm, a lively and a sharp gesture
  uint32_t restMin, restMax;  // ms between two gestures
  };

// neutral, friendly, angry, hostile, afraid, pain (no gestures)
constexpr MoodStyle Styles[] = {
  {{5,4,1},  400, 1600},
  {{3,6,1},  300, 1100},
  {{1,4,5},    0,  500},
  {{2,3,5},  200,  900},
  {{8,2,0},  900, 2600},
  {{0,0,0},    0,    0},
  };

}

TalkGestures::Probe TalkGestures::subjectProbe;
const void*         TalkGestures::probeNpc = nullptr;

bool TalkGestures::isEnabled() {
  return enabled;
  }

void TalkGestures::setEnabled(bool e) {
  enabled = e;
  }

bool TalkGestures::setParam(std::string_view name, float value) {
  for(auto& p:params)
    if(p.name==name) {
      *p.value = value;
      return true;
      }
  return false;
  }

std::string TalkGestures::paramList() {
  std::string ret;
  for(auto& p:params) {
    char buf[64];
    std::snprintf(buf,sizeof(buf),"%s%.*s=%g",ret.empty() ? "" : " ",int(p.name.size()),p.name.data(),double(*p.value));
    ret += buf;
    }
  return ret;
  }

TalkGestures::Mood TalkGestures::moodOf(std::string_view m) {
  if(m=="friendly") return Mood::Friendly;
  if(m=="angry")    return Mood::Angry;
  if(m=="hostile")  return Mood::Hostile;
  if(m=="afraid")   return Mood::Afraid;
  if(m=="pain")     return Mood::Pain;
  return Mood::Neutral;
  }

std::string_view TalkGestures::moodName(Mood m) {
  switch(m) {
    case Mood::Neutral:  return "neutral";
    case Mood::Friendly: return "friendly";
    case Mood::Angry:    return "angry";
    case Mood::Hostile:  return "hostile";
    case Mood::Afraid:   return "afraid";
    case Mood::Pain:     return "pain";
    }
  return "neutral";
  }

void TalkGestures::setGestures(std::vector<uint32_t> durationMs, bool gothic2) {
  duration = std::move(durationMs);
  kind.resize(duration.size());
  for(size_t i=0; i<kind.size(); ++i)
    kind[i] = (gothic2 && i<std::size(G2Kind)) ? G2Kind[i] : Lively;
  }

void TalkGestures::lineStart(uint64_t now) {
  if(rnd==1) {
    // npcs that start talking in the same order pick the same gestures each run (the test bench)
    static uint32_t seed = 0;
    rnd = (++seed)*2654435761u | 1u;
    }
  if(!engaged && current==0)
    nextAt = now;  // the first gesture starts with the voice
  engaged    = true;
  lastSpeech = now;
  }

void TalkGestures::stop() {
  if(current!=0)
    ++stops;
  engaged = false;
  current = 0;
  mood    = Mood::Neutral;
  }

TalkGestures::Decision TalkGestures::update(const Input& in) {
  Decision d;
  speaking = in.speaking;
  if(!engaged) {
    if(!in.playing)
      current = 0;
    return d;
    }

  if(in.speaking || in.more)
    lastSpeech = in.now;
  const bool over = in.now > lastSpeech + uint64_t(std::max(0.f,Tail));
  if(!in.allowed || over || mood==Mood::Pain) {
    if(in.playing && current!=0) {
      d.act = Act::Stop;
      ++stops;
      }
    current = 0;
    if(over) {
      engaged = false;
      mood    = Mood::Neutral;
      }
    return d;
    }

  if(in.playing && current!=0)
    return d;
  if(current!=0) {
    // it has played to its end: the hands rest a moment
    current = 0;
    nextAt  = in.now + rest();
    }
  if(!in.speaking || in.now<nextAt)
    return d;
  // a moment for a syllable to start on: the stroke of the hand goes with the voice
  if(in.voiced && !in.syllable && in.now<nextAt+uint64_t(std::max(0.f,Wait)))
    return d;
  if(!in.more && in.left>0 && float(in.left)<MinLeft)
    return d;

  const uint8_t g = pick(in.left,in.more);
  if(g==0)
    return d;
  d.act     = Act::Start;
  d.gesture = g;
  current   = g;
  recent[1] = recent[0];
  recent[0] = g;
  ++starts;
  return d;
  }

void TalkGestures::started(uint8_t gesture, bool overPlaying) {
  current = gesture;
  ++starts;
  if(overPlaying)
    ++cuts;
  }

uint8_t TalkGestures::pick(uint64_t left, bool more) {
  const auto& st = Styles[size_t(mood)];
  // the weight of a kind is shared by its gestures: five calm ones are not five times as likely
  float n[3] = {};
  for(size_t i=0; i<duration.size(); ++i)
    if(duration[i]>0)
      n[kind[i]] += 1.f;
  float w[256] = {};
  float sum    = 0;
  for(size_t i=0; i<duration.size() && i<255; ++i) {
    const uint8_t g = uint8_t(i+1);
    if(duration[i]==0 || g==recent[0] || g==recent[1])
      continue;
    float wi = st.weight[kind[i]]/n[kind[i]];
    if(!more && left>0 && float(duration[i])>float(left)+Slack)
      wi *= Long;  // it would be cut off
    w[i] = wi;
    sum += wi;
    }
  if(sum<=0.f)
    return 0;
  float r = float(random()%10000)/10000.f*sum;
  for(size_t i=0; i<duration.size() && i<255; ++i) {
    if(w[i]<=0.f)
      continue;
    if(r<w[i])
      return uint8_t(i+1);
    r -= w[i];
    }
  for(size_t i=duration.size(); i>0; --i)
    if(w[i-1]>0.f)
      return uint8_t(i);
  return 0;
  }

uint64_t TalkGestures::rest() {
  const auto&    st = Styles[size_t(mood)];
  const uint32_t r  = st.restMin + (st.restMax>st.restMin ? random()%(st.restMax-st.restMin) : 0);
  return uint64_t(float(r)*std::max(0.f,RestScale));
  }

uint32_t TalkGestures::random() {
  rnd = rnd*1664525u + 1013904223u;
  return rnd>>8;
  }

void TalkGestures::probe(Probe& p) const {
  p.valid    = !duration.empty() || current!=0;
  p.enabled  = enabled;
  p.engaged  = engaged;
  p.speaking = speaking;
  p.gesture  = current;
  p.mood     = mood;
  p.starts   = starts;
  p.cuts     = cuts;
  p.stops    = stops;
  }
