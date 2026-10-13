#include "testbench.h"

#include <Tempest/Pixmap>
#include <Tempest/SoundDevice>
#include <Tempest/SystemApi>

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <deque>
#include <memory>
#include <mutex>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <thread>
#include <vector>

#include "game/gamescript.h"
#include "game/playercontrol.h"
#include "game/definitions/sounddefinitions.h"
#include "resources.h"
#include "graphics/drawcommands.h"
#include "graphics/mesh/footik.h"
#include "graphics/mesh/gaze.h"
#include "graphics/mesh/lipsync.h"
#include "graphics/mesh/talkgestures.h"
#include "graphics/mesh/eyeblink.h"
#include "graphics/mesh/lean.h"
#include "graphics/mesh/pose.h"
#include "graphics/mesh/animblend.h"
#include "graphics/mesh/aim.h"
#include "graphics/mesh/bowdraw.h"
#include "graphics/mesh/hitzones.h"
#include "game/archery.h"
#include "game/archerai.h"
#include "graphics/mesh/flinch.h"
#include "graphics/mesh/skeleton.h"
#include "physics/dynamicworld.h"
#include "world/objects/npc.h"
#include "world/objects/item.h"
#include "world/world.h"
#include "world/aiqueue.h"
#include "ui/crosshair.h"
#include "utils/keycodec.h"
#include "camera.h"
#include "gothic.h"
#include "marvin.h"

using namespace Tempest;

namespace {

const char* envStr(const char* name) {
  const char* v = std::getenv(name);
  return (v!=nullptr && v[0]!='\0') ? v : nullptr;
  }

const char*       scenarioPath = envStr("OPENGOTHIC_BENCH");
const std::string outDir       = envStr("OPENGOTHIC_BENCH_OUT") ? envStr("OPENGOTHIC_BENCH_OUT") : ".";
const bool        keepRunning  = envStr("OPENGOTHIC_BENCH_KEEP")!=nullptr;
constexpr int     Fps          = 30;
// the fixed steps keep to the wall clock (never faster): the AI server and its voices live in real time
const bool        realtime     = envStr("OPENGOTHIC_BENCH_REALTIME")!=nullptr;
constexpr uint64_t SpeakTailMs = 400;  // the talk key is held this long after the recording ends

std::FILE* logFile = nullptr;

void say(const std::string& s) {
  std::printf("[bench] %s\n",s.c_str());
  std::fflush(stdout);
  if(logFile==nullptr)
    logFile = std::fopen((outDir+"/bench.log").c_str(),"w");
  if(logFile!=nullptr) {
    std::fprintf(logFile,"%s\n",s.c_str());
    std::fflush(logFile);
    }
  }

std::vector<std::string> split(std::string_view s) {
  std::vector<std::string> ret;
  size_t i = 0;
  while(i<s.size()) {
    while(i<s.size() && std::isspace(uint8_t(s[i])))
      ++i;
    size_t e = i;
    while(e<s.size() && !std::isspace(uint8_t(s[e])))
      ++e;
    if(e>i)
      ret.emplace_back(s.substr(i,e-i));
    i = e;
    }
  return ret;
  }

float num(const std::vector<std::string>& a, size_t i, float def) {
  return i<a.size() ? std::strtof(a[i].c_str(),nullptr) : def;
  }

bool isNumber(const std::string& s) {
  char* end = nullptr;
  std::strtof(s.c_str(),&end);
  return end!=s.c_str() && *end=='\0';
  }

std::string rest(const std::string& line) {
  const size_t sp = line.find(' ');
  return sp==std::string::npos ? "" : line.substr(sp+1);
  }

void replaceAll(std::string& s, std::string_view from, std::string_view to) {
  for(size_t p=s.find(from); p!=std::string::npos; p=s.find(from,p+to.size()))
    s.replace(p,from.size(),to);
  }

std::string jsonStr(std::string_view s) {
  std::string r = "\"";
  for(char ch:s) {
    switch(ch) {
      case '"':  r += "\\\""; break;
      case '\\': r += "\\\\"; break;
      case '\n': r += "\\n"; break;
      case '\r': r += "\\r"; break;
      case '\t': r += "\\t"; break;
      default:
        if(uint8_t(ch)<0x20) {
          char buf[8];
          std::snprintf(buf,sizeof(buf),"\\u%04x",unsigned(uint8_t(ch)));
          r += buf;
          } else {
          r += ch;
          }
      }
    }
  return r + "\"";
  }

// length of a 16-bit PCM wav in ms, 0 if it is not one
uint64_t wavMs(const std::string& path) {
  std::ifstream f(path,std::ios::binary);
  char riff[12];
  if(!f.read(riff,12) || std::memcmp(riff,"RIFF",4)!=0 || std::memcmp(riff+8,"WAVE",4)!=0)
    return 0;
  uint32_t byteRate = 0;
  char     hdr[8];
  while(f.read(hdr,8)) {
    uint32_t size = 0;
    std::memcpy(&size,hdr+4,4);
    if(std::memcmp(hdr,"fmt ",4)==0) {
      std::vector<char> fmt(size);
      if(!f.read(fmt.data(),size) || size<16)
        return 0;
      std::memcpy(&byteRate,fmt.data()+8,4);
      }
    else if(std::memcmp(hdr,"data",4)==0) {
      return byteRate>0 ? uint64_t(size)*1000/byteRate : 0;
      }
    else {
      f.seekg(size + (size&1),std::ios::cur);
      }
    }
  return 0;
  }

void writeWavHeader(std::FILE* f, uint32_t rate, uint64_t frames) {
  const uint32_t data = uint32_t(frames*4), riff = 36+data, fmtSize = 16, byteRate = rate*4;
  const uint16_t pcm = 1, channels = 2, align = 4, bits = 16;
  std::fseek(f,0,SEEK_SET);
  std::fwrite("RIFF",1,4,f); std::fwrite(&riff,4,1,f); std::fwrite("WAVE",1,4,f);
  std::fwrite("fmt ",1,4,f); std::fwrite(&fmtSize,4,1,f); std::fwrite(&pcm,2,1,f); std::fwrite(&channels,2,1,f);
  std::fwrite(&rate,4,1,f); std::fwrite(&byteRate,4,1,f); std::fwrite(&align,2,1,f); std::fwrite(&bits,2,1,f);
  std::fwrite("data",1,4,f); std::fwrite(&data,4,1,f);
  }

float groundY(World& w, float x, float z, float y) {
  auto r = w.physic()->ray({x,y+150,z},{x,y-300,z});
  return r.hasCol ? r.v.y : NAN;
  }

// slope at a point: angle in degrees, horizontal direction down the slope, roughness (steps)
bool slopeAt(World& w, const Vec3& p, float& angle, Vec3& down, float& rough) {
  const float d  = 40;
  const float h0 = groundY(w,p.x,p.z,p.y);
  const float x0 = groundY(w,p.x-d,p.z,p.y), x1 = groundY(w,p.x+d,p.z,p.y);
  const float z0 = groundY(w,p.x,p.z-d,p.y), z1 = groundY(w,p.x,p.z+d,p.y);
  if(std::isnan(h0) || std::isnan(x0) || std::isnan(x1) || std::isnan(z0) || std::isnan(z1))
    return false;
  const float gx = (x1-x0)/(2*d), gz = (z1-z0)/(2*d);
  const float g  = std::sqrt(gx*gx+gz*gz);
  angle = std::atan(g)*180.f/float(M_PI);
  down  = g>0 ? Vec3(-gx/g,0,-gz/g) : Vec3(1,0,0);
  rough = std::max(std::abs((x0+x1)*0.5f-h0), std::abs((z0+z1)*0.5f-h0));
  return true;
  }

Vec3 facing(const Npc& pl) {
  const float a = pl.rotation()*float(M_PI)/180.f;
  return {std::cos(a),0,std::sin(a)};
  }

struct Spot {
  std::string name;
  float       angle = 0;
  float       rough = 0;
  float       dist  = 0;
  Vec3        pos;
  };

enum SpotKind { K_Slope, K_Stairs, K_Name };

// waypoints of a kind, `count` of them spread from near to far, not closer than 5 m to each other
std::vector<Spot> findSpots(World& w, const Npc& pl, SpotKind kind, float a0, float a1, std::string_view part, size_t count) {
  std::vector<Spot> all;
  const Vec3 at = pl.position();
  w.findWayPoint(at,[&](const WayPoint& wp) {
    if(kind==K_Name && wp.name.find(part)==std::string::npos)
      return false;
    Spot s;
    Vec3 d;
    s.name = wp.name;
    s.pos  = wp.pos;
    s.dist = (wp.pos-at).length();
    if(!slopeAt(w,wp.pos,s.angle,d,s.rough))
      return false;
    if(kind==K_Slope  && !(s.angle>=a0 && s.angle<=a1 && s.rough<4))
      return false;
    if(kind==K_Stairs && !(s.angle>=18 && s.angle<=40 && s.rough>=6 && s.rough<=25))
      return false;
    all.push_back(s);
    return false;
    });
  std::sort(all.begin(),all.end(),[](const Spot& a, const Spot& b){ return a.dist<b.dist; });

  std::vector<Spot> far;
  for(auto& s:all) {
    bool close = false;
    for(auto& f:far)
      close |= (f.pos-s.pos).length()<500;
    if(!close)
      far.push_back(s);
    }
  if(far.size()<=count)
    return far;
  std::vector<Spot> ret;
  for(size_t i=0; i<count; ++i)
    ret.push_back(far[(i*far.size())/count]);
  return ret;
  }

// --- overlay ---------------------------------------------------------------------------------------

struct Rgba { uint8_t r,g,b,a; };

const Rgba White  = {255,255,255,170};
const Rgba LegL   = { 60,200,255,255};
const Rgba LegR   = {255,140, 40,255};
const Rgba LegFL  = {150,140,255,255};
const Rgba LegFR  = {255,110,190,255};
const Rgba LegColor[] = {LegL, LegR, LegFL, LegFR};
const char* const LegName[] = {"L", "R", "FL", "FR"};
const Rgba Plant  = { 60,230, 80,255};
const Rgba Swing  = {240,220, 60,255};
const Rgba Red    = {255, 50, 50,255};
const Rgba Shade  = {  0,  0,  0,150};
const Rgba Text   = {255,255,255,255};

// 3x5 font, rows top to bottom
struct Glyph { char c; const char* rows; };
const Glyph glyphs[] = {
  {'0',"XXXX.XX.XX.XXXX"},{'1',".X.XX..X..X.XXX"},{'2',"XXX..XXXXX..XXX"},{'3',"XXX..XXXX..XXXX"},
  {'4',"X.XX.XXXX..X..X"},{'5',"XXXX..XXX..XXXX"},{'6',"XXXX..XXXX.XXXX"},{'7',"XXX..X..X..X..X"},
  {'8',"XXXX.XXXXX.XXXX"},{'9',"XXXX.XXXX..XXXX"},{'A',".X.X.XXXXX.XX.X"},{'B',"XX.X.XXX.X.XXX."},
  {'C',"XXXX..X..X..XXX"},{'D',"XX.X.XX.XX.XXX."},{'E',"XXXX..XX.X..XXX"},{'F',"XXXX..XX.X..X.."},
  {'G',"XXXX..X.XX.XXXX"},{'H',"X.XX.XXXXX.XX.X"},{'I',"XXX.X..X..X.XXX"},{'J',"..X..X..XX.XXXX"},
  {'K',"X.XX.XXX.X.XX.X"},{'L',"X..X..X..X..XXX"},{'M',"X.XXXXXXXX.XX.X"},{'N',"XX.X.XX.XX.XX.X"},
  {'O',"XXXX.XX.XX.XXXX"},{'P',"XXXX.XXXXX..X.."},{'Q',"XXXX.XX.XXXX..X"},{'R',"XX.X.XXX.X.XX.X"},
  {'S',"XXXX..XXX..XXXX"},{'T',"XXX.X..X..X..X."},{'U',"X.XX.XX.XX.XXXX"},{'V',"X.XX.XX.XX.X.X."},
  {'W',"X.XX.XXXXXXXX.X"},{'X',"X.XX.X.X.X.XX.X"},{'Y',"X.XX.X.X..X..X."},{'Z',"XXX..X.X.X..XXX"},
  {'-',"......XXX......"},{'.',".............X."},{':',"....X.....X...."},{'=',"...XXX...XXX..."},
  {'_',"............XXX"},{'/',"..X..X.X.X..X.."},{'+',"....X.XXX.X...."},{'%',"X.X..X.X.X..X.X"},
  };

struct Canvas {
  uint8_t* px = nullptr;
  int      w  = 0;
  int      h  = 0;

  void blend(int x, int y, Rgba c) {
    if(x<0 || y<0 || x>=w || y>=h)
      return;
    uint8_t*  p = px + (size_t(y)*size_t(w) + size_t(x))*4;
    const int a = c.a;
    p[0] = uint8_t((c.r*a + p[0]*(255-a))/255);
    p[1] = uint8_t((c.g*a + p[1]*(255-a))/255);
    p[2] = uint8_t((c.b*a + p[2]*(255-a))/255);
    p[3] = 255;
    }

  void rect(int x, int y, int rw, int rh, Rgba c) {
    for(int j=y; j<y+rh; ++j)
      for(int i=x; i<x+rw; ++i)
        blend(i,j,c);
    }

  void dot(float x, float y, int r, Rgba c) {
    const int cx = int(std::lround(x)), cy = int(std::lround(y));
    for(int j=-r; j<=r; ++j)
      for(int i=-r; i<=r; ++i)
        if(i*i+j*j<=r*r+r)
          blend(cx+i,cy+j,c);
    }

  void line(float x0, float y0, float x1, float y1, int r, Rgba c) {
    const float len = std::max(std::abs(x1-x0),std::abs(y1-y0));
    if(len>4000)
      return;
    const int n = std::max(1,int(len));
    for(int i=0; i<=n; ++i) {
      const float t = float(i)/float(n);
      if(r<=0)
        blend(int(std::lround(x0+(x1-x0)*t)),int(std::lround(y0+(y1-y0)*t)),c); else
        dot(x0+(x1-x0)*t,y0+(y1-y0)*t,r,c);
      }
    }

  // a polyline stroked as the game strokes the crosshair: white over a dark edge, smooth; each pixel once
  void stroke(const std::vector<Vec2>& pt, float alpha, const Crosshair::Pen& pen) {
    if(pt.size()<2)
      return;
    float x0 = pt[0].x, y0 = pt[0].y, x1 = x0, y1 = y0;
    for(auto& p:pt) {
      x0 = std::min(x0,p.x); x1 = std::max(x1,p.x);
      y0 = std::min(y0,p.y); y1 = std::max(y1,p.y);
      }
    const float m = pen.half+pen.edge+1.f;
    for(int y=int(std::floor(y0-m)); y<=int(std::ceil(y1+m)); ++y)
      for(int x=int(std::floor(x0-m)); x<=int(std::ceil(x1+m)); ++x) {
        const Vec2 q = Vec2(float(x),float(y));
        float      d = 1e9f;
        for(size_t i=0; i+1<pt.size(); ++i) {
          const Vec2  ab = pt[i+1]-pt[i];
          const float ll = ab.x*ab.x+ab.y*ab.y;
          const float t  = ll>0.f ? std::clamp(((q.x-pt[i].x)*ab.x+(q.y-pt[i].y)*ab.y)/ll,0.f,1.f) : 0.f;
          d = std::min(d,(pt[i]+ab*t-q).length());
          }
        const float edge = std::clamp(pen.half+pen.edge+0.5f-d,0.f,1.f);
        const float core = std::clamp(pen.half+0.5f-d,0.f,1.f);
        if(edge>0.f)
          blend(x,y,{0,0,0,uint8_t(255.f*alpha*pen.outline*edge)});
        if(core>0.f)
          blend(x,y,{255,255,255,uint8_t(255.f*alpha*core)});
        }
    }

  int text(int x, int y, std::string_view s, int sc, Rgba c) {
    rect(x-sc,y-sc,int(s.size())*4*sc+sc,7*sc,Shade);
    for(char ch:s) {
      ch = char(std::toupper(uint8_t(ch)));
      for(auto& g:glyphs) {
        if(g.c!=ch)
          continue;
        for(int j=0; j<5; ++j)
          for(int i=0; i<3; ++i)
            if(g.rows[j*3+i]=='X')
              rect(x+i*sc,y+j*sc,sc,sc,c);
        }
      x += 4*sc;
      }
    return x;
    }
  };

// frames go to ffmpeg from a thread of their own: the game goes on while a frame is written
class VideoPipe final {
  public:
    explicit VideoPipe(std::FILE* f):f(f),th([this]{ loop(); }) {}
    ~VideoPipe() {
      {
        std::lock_guard<std::mutex> g(sync);
        done = true;
      }
      cv.notify_all();
      th.join();
      pclose(f);
      }

    void push(std::vector<uint8_t>&& frame) {
      std::unique_lock<std::mutex> g(sync);
      cv.wait(g,[this]{ return queue.size()<MaxQueued; });
      queue.push_back(std::move(frame));
      cv.notify_all();
      }

  private:
    static constexpr size_t MaxQueued = 4;

    void loop() {
      for(;;) {
        std::vector<uint8_t> frame;
        {
          std::unique_lock<std::mutex> g(sync);
          cv.wait(g,[this]{ return done || !queue.empty(); });
          if(queue.empty())
            return;
          frame = std::move(queue.front());
          queue.pop_front();
        }
        cv.notify_all();
        std::fwrite(frame.data(),1,frame.size(),f);
        }
      }

    std::FILE*                       f;
    std::mutex                       sync;
    std::condition_variable          cv;
    std::deque<std::vector<uint8_t>> queue;
    bool                             done = false;
    std::thread                      th;
  };

// --- the bench ---------------------------------------------------------------------------------------

enum CamMode : uint8_t {
  C_Game,
  C_Follow, // around the player, relative to its facing
  C_World,  // around the player, fixed world direction
  C_Fixed,  // fixed position, looking at the player
  C_Hold,   // the camera stays as it is: the position and where it looks
  C_Face,   // close to the face, relative to the facing of the body
  C_Shoulder, // over the hero's shoulder at an npc (camNpc)
  C_NpcFace,  // the npc's face, from the hero's side
  C_Two,      // the hero and the npc from the side
  };

struct Hold {
  KeyCodec::Action a;
  uint64_t         until;
  };

struct Bench {
  std::vector<std::string> prog;
  size_t      pc        = 0;
  bool        loaded    = false;
  bool        started   = false;
  bool        done      = false;
  uint64_t    time      = 0;    // game time since the start, ms
  uint64_t    step      = 0;    // fixed steps made

  uint64_t    waitUntil = 0;
  bool        waitArrive = false;
  uint64_t    arriveEnd = 0;

  std::vector<Hold>             holds;
  std::vector<KeyCodec::Action> taps;

  CamMode     cam       = C_Game;
  float       camDist   = 220;
  float       camHeight = 40;
  float       camAngle  = 90;   // C_Follow: degrees from the facing (0 in front, 90 on the left); C_World: world yaw
  float       camLook   = 45;   // look at this height above the player origin
  bool        camAuto   = false;
  int         occlTime  = 0;
  Vec3        camPos;

  bool        rec       = false;
  bool        recVideo  = false;
  std::string recName;
  uint64_t    recFrame  = 0;
  uint64_t    recStart  = 0;
  std::unique_ptr<VideoPipe> video;
  uint32_t    videoW    = 0;
  uint32_t    videoH    = 0;
  std::FILE*  csv       = nullptr;
  std::string shot;
  bool        overlay   = true;
  bool        ui        = false;  // the interface is in the captured frame
  bool        zoom      = true;
  bool        bright    = true;  // auto exposure: forests and interiors are too dark to see the legs
  float       clip      = 0;     // > 0: everything closer to the camera than the subject minus this is clipped away

  Vec3        prevPos;
  bool        hasPrev   = false;
  bool        inDialog  = false;
  Npc*        pinned    = nullptr;  // pin: held there every frame
  Npc*        hearNpc   = nullptr;  // hear: it listens to the hero (words every hearEvery ms) until hearUntil
  uint64_t    hearUntil = 0, hearEvery = 600, hearNext = 0;
  Npc*        thinkNpc  = nullptr;  // think: it thinks of an answer until thinkUntil
  uint64_t    thinkUntil = 0;
  Vec3        pinAt     = {};
  float       speed     = 0;
  uint8_t     occl      = 0;    // bit per leg: the ankle is hidden from the camera
  std::string label;            // free text on the overlay
  Npc*        subject   = nullptr; // the camera watches this npc instead of the player (gaze)
  PlayerControl* ctrl     = nullptr; // the player's input: the crosshair of the manual aim
  // volley: shots by hand in a row - press, draw to volleyDraw, hold volleyHold, let go, wait for the
  // next arrow
  int         volleyLeft  = 0;
  int         volleyPhase = 0;
  uint64_t    volleyHold  = 0;
  float       volleyDraw  = 1;
  uint64_t    volleyJitter = 0;  // ms more to hold, at random up to that (the sway is a function of time)
  uint64_t    volleyHoldNow = 0;
  uint32_t    lcg          = 1;  // the bench's own random numbers (seed)
  std::vector<Npc*> crew;        // arc: the npcs set out, `all` of fight
  Npc*        downNpc      = nullptr; // waitdown: till it falls
  uint64_t    downFrom = 0, downEnd = 0;
  std::vector<std::pair<Npc*,Vec3>> archers; // archer: they stand there and shoot at archerAt...
  Npc*        archerAt     = nullptr;
  bool        archerDice   = false;       // ...by the dice
  uint64_t    volleyT     = 0;
  // aimat: the manual aim aims there instead of the mouse, every frame
  Npc*           aimNpc   = nullptr;
  HitZones::Zone aimZone  = HitZones::Body;
  bool           aimFixed = false;
  bool           aimRaw   = false; // no holding over for the drop of the arrow
  bool           crossValid = false;
  Vec3           crossPrev  = {}; // on the screen, -1..1
  float          aspect     = 4.f/3.f; // of the captured frame
  float          uiScale    = 1;       // of the game's interface
  Vec3           aimAt    = {};
  // joints of the watched one's animated pose in the last two frames: a pop is a jump of the second difference
  std::vector<Vec3> anim1, anim2;
  // the hero placed around the subject: distance, angle from its facing (0 in front), orbiting
  float       placeDist  = 0;
  float       placeAngle = 0;
  float       orbitSpeed = 0;     // degrees per second
  uint64_t    orbitUntil = 0;

  // talking (scripts/shorts)
  std::string camNpc;           // C_Shoulder, C_NpcFace, C_Two
  std::string zonesNpc;         // showzones: the npc whose hit zones the overlay draws
  bool        zonesOnly = false; // the overlay draws the hit zones only
  float       camSmooth = 0.12f; // share of the way to the wanted camera per frame; 1: no smoothing
  bool        smValid   = false;
  Vec3        smOrg, smTgt;
  bool        speaking  = false;  // the talk key is held while a recording is said
  uint64_t    speakUntil = 0;
  uint64_t    heroEndAt = 0;      // the hero's last phrase ended
  uint64_t    lastLineAt = 0;     // an npc line of the AI started
  uint64_t    busyAt    = 0;      // an npc of the AI spoke (or had a line waiting)
  bool        waitReply = false;
  std::string waitAiKey, waitAiStage;  // waitai: until the bridge says it got that far
  uint64_t    waitAiEnd = 0;
  bool        replyNeed = false;  // a line has to start after replyFrom first
  uint64_t    replyFrom = 0;
  uint64_t    replyEnd  = 0;
  uint64_t    replyQuiet = 0;
  std::FILE*  events    = nullptr;

  // sound: the mix of the game (Tempest loopback), recorded with the video
  std::FILE*  audio     = nullptr;
  uint64_t    audioFrames = 0;
  uint64_t    audioAcc  = 0;
  std::vector<int16_t> mix;

  std::chrono::steady_clock::time_point wallStart;
  std::chrono::steady_clock::time_point recWall;  // when the recording started
  int64_t     lagMs     = 0;      // how far the game fell behind the wall clock (realtime)
  };

Bench bench;

// a line of events.jsonl: what happened when, in the recording's frames (the cut of a short lines up its
// subtitles and sound by them); `fields` are more json members, without braces
void event(std::string_view kind, const std::string& fields = {}) {
  if(bench.events==nullptr)
    bench.events = std::fopen((outDir+"/events.jsonl").c_str(),"w");
  if(bench.events==nullptr)
    return;
  std::fprintf(bench.events,"{\"t\":%llu,\"seg\":%s,\"frame\":%lld,\"kind\":%s%s%s}\n",
               (unsigned long long)bench.time,jsonStr(bench.rec ? bench.recName : "").c_str(),
               bench.rec ? (long long)bench.recFrame : -1LL,jsonStr(kind).c_str(),fields.empty() ? "" : ",",fields.c_str());
  std::fflush(bench.events);
  }

Npc* npcBySymbol(World& w, std::string_view symbol) {
  const size_t id = w.script().findSymbolIndex(symbol);
  return id==size_t(-1) ? nullptr : w.findNpcByInstance(id);
  }

bool aiCommand(const std::string& cmd, std::string& reply) {
  auto& fn = Gothic::inst().consoleCommandFn;
  return fn && fn(cmd,reply);
  }

void loadScenario() {
  bench.loaded = true;
  std::ifstream fin(scenarioPath);
  if(!fin) {
    say(std::string("can not read scenario: ") + scenarioPath);
    return;
    }
  const char*       tv   = std::getenv("OPENGOTHIC_BENCH_TAKE");
  const std::string take = tv!=nullptr && tv[0]!='\0' ? tv : "on";
  std::string line;
  while(std::getline(fin,line)) {
    const size_t hash = line.find('#');
    if(hash!=std::string::npos)
      line.resize(hash);
    while(!line.empty() && std::isspace(uint8_t(line.back())))
      line.pop_back();
    size_t b = 0;
    while(b<line.size() && std::isspace(uint8_t(line[b])))
      ++b;
    // `off: <command>` / `on: <command>`: in that take of a before/after short only (shorts/compare/film.sh
    // sets OPENGOTHIC_BENCH_TAKE; without it the take is `on`)
    for(const std::string_view tk:{std::string_view("off:"),std::string_view("on:")}) {
      if(line.compare(b,tk.size(),tk)!=0)
        continue;
      if(tk.substr(0,tk.size()-1)!=take)
        b = line.size(); else
        b += tk.size();
      while(b<line.size() && std::isspace(uint8_t(line[b])))
        ++b;
      break;
      }
    if(b<line.size())
      bench.prog.push_back(line.substr(b));
    }
  say("scenario " + std::string(scenarioPath) + ", " + std::to_string(bench.prog.size()) + " lines");
  }

Marvin& marvin() {
  static Marvin m;
  return m;
  }

bool keyAction(std::string_view k, KeyCodec::Action& a) {
  static const struct { std::string_view name; KeyCodec::Action a; } keys[] = {
    {"forward",KeyCodec::Forward}, {"back",KeyCodec::Back},       {"left",KeyCodec::Left},  {"right",KeyCodec::Right},
    {"turnl",  KeyCodec::RotateL}, {"turnr",KeyCodec::RotateR},   {"jump",KeyCodec::Jump},  {"walk", KeyCodec::Walk},
    {"sneak",  KeyCodec::Sneak},   {"weapon",KeyCodec::Weapon},   {"action",KeyCodec::ActionGeneric},
    {"melee",  KeyCodec::WeaponMele},{"bow",KeyCodec::WeaponBow}, {"spell",KeyCodec::WeaponMage3},
    {"parade", KeyCodec::Parade},  {"breath",KeyCodec::Walk},
    };
  for(auto& i:keys)
    if(i.name==k) {
      a = i.a;
      return true;
      }
  return false;
  }

void press(PlayerControl& ctrl, KeyCodec::Action a) {
  ctrl.onKeyPressed(a,KeyEvent::K_NoKey,KeyCodec::Mapping::Primary);
  }

void release(PlayerControl& ctrl, KeyCodec::Action a) {
  ctrl.onKeyReleased(a,KeyCodec::Mapping::Primary);
  }

void stopRecording() {
  if(!bench.rec)
    return;
  bench.video.reset();  // waits for the frames still queued
  if(bench.audio!=nullptr) {
    writeWavHeader(bench.audio,SoundDevice::loopbackRate(),bench.audioFrames);
    std::fclose(bench.audio);
    }
  bench.audio = nullptr;
  // how fast the game ran against the wall clock while recording (the AI server keeps to the wall clock)
  const auto wall  = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-bench.recWall).count();
  const int  speed = wall>0 ? int((bench.time-bench.recStart)*100/uint64_t(wall)) : 100;
  event("rec_stop","\"frames\":" + std::to_string(bench.recFrame) + ",\"speed\":" + std::to_string(speed));
  say("rec stop " + bench.recName + " frames=" + std::to_string(bench.recFrame) +
      (realtime ? " speed=" + std::to_string(speed) + "%" : std::string()));
  bench.rec = false;
  }

void finish() {
  if(bench.speaking) {
    std::string reply;
    aiCommand("ai ptt cancel",reply);
    bench.speaking = false;
    }
  stopRecording();
  if(bench.csv!=nullptr)
    std::fclose(bench.csv);
  bench.csv  = nullptr;
  if(bench.events!=nullptr)
    std::fclose(bench.events);
  bench.events = nullptr;
  if(realtime)
    say("realtime: " + std::string(bench.lagMs>200 ? "fell behind the wall clock by " + std::to_string(bench.lagMs) + " ms"
                                                   : "kept to the wall clock"));
  bench.done = true;
  DrawCommands::hiddenTypes = 0;
  if(auto c = Gothic::inst().camera()) {
    c->setMarvinMode(Camera::M_Normal);
    c->setNearClip(10);
    }
  say("done");
  if(!keepRunning)
    SystemApi::exit();
  }

Vec3 ankle(const Npc& pl, size_t leg) {
  auto& p = FootIk::playerProbe;
  if(p.valid && pl.isPlayer())
    return p.leg[leg].final[2];
  return pl.position() + Vec3(0,10,0);
  }

Npc& subjectOf(Npc& pl) {
  return bench.subject!=nullptr ? *bench.subject : pl;
  }

// "nearest": the nearest living npc but the player; else a script symbol (NONE_100_XARDAS)
Npc* findNpc(World& w, Npc& pl, const std::string& name) {
  if(name=="player")
    return &pl;
  if(name=="nearest") {
    Npc*  best = nullptr;
    float dist = 0;
    for(uint32_t i=0; i<w.npcCount(); ++i) {
      Npc* n = w.npcById(i);
      if(n==nullptr || n==&pl || n->isDead())
        continue;
      const float d = (n->position()-pl.position()).quadLength();
      if(best==nullptr || d<dist) {
        best = n;
        dist = d;
        }
      }
    return best;
    }
  // by its script symbol; of several (two sheep inserted) a living one, the nearest
  const size_t id = w.script().findSymbolIndex(name);
  if(id==size_t(-1))
    return nullptr;
  Npc*  best = nullptr;
  float dist = 0;
  for(uint32_t i=0; i<w.npcCount(); ++i) {
    Npc* n = w.npcById(i);
    if(n==nullptr || n->instanceSymbol()!=id)
      continue;
    const float d = (n->position()-pl.position()).quadLength() + (n->isDead() ? 1e12f : 0.f);
    if(best==nullptr || d<dist) {
      best = n;
      dist = d;
      }
    }
  return best!=nullptr ? best : w.findNpcByInstance(id);
  }

// an npc's name as the player sees it, for the log and events.jsonl (UTF-8): the game's strings are cp1251
// (the russian localization). The engine has no codec (ai_bridge's TextCodec is above it); a name only
// needs the letters, other bytes above ASCII become '?'
std::string nameUtf8(const Npc& n) {
  std::string r;
  for(char ch:n.displayName()) {
    const auto b = uint8_t(ch);
    if(b<0x80) {
      r += ch;
      continue;
      }
    uint32_t cp = '?';
    if(b>=0xC0)
      cp = 0x410 + (uint32_t(b) - 0xC0);  // А..я
    else if(b==0xA8)
      cp = 0x401;                         // Ё
    else if(b==0xB8)
      cp = 0x451;                         // ё
    if(cp<0x80) {
      r += char(cp);
      continue;
      }
    r += char(0xC0 | (cp>>6));
    r += char(0x80 | (cp & 0x3F));
    }
  return r;
  }

std::string symName(World& w, const Npc* n) {
  if(n==nullptr)
    return "-";
  if(auto sym = w.script().findSymbol(size_t(n->instanceSymbol())))
    return sym->name();
  return nameUtf8(*n);
  }

const char* gazeSource(Gaze::Source s) {
  switch(s) {
    case Gaze::Source::None:      return "none";
    case Gaze::Source::Talk:      return "talk";
    case Gaze::Source::Script:    return "script";
    case Gaze::Source::Focus:     return "focus";
    case Gaze::Source::Attention: return "attention";
    }
  return "?";
  }

// whom the gaze of that source looks at, "-" for none or a point
std::string gazeWho(World& w, const Npc& n, Gaze::Source s) {
  return symName(w,s==Gaze::Source::Talk ? n.talkPartner() : s==Gaze::Source::Script ? n.lookAtTarget() :
                   s==Gaze::Source::Attention ? n.attentionTarget() : nullptr);
  }

// how much of its AI runs, by its distance to the hero (worldobjects.cpp): far ones say nothing, far2 ones
// barely move
const char* policyName(NpcProcessPolicy p) {
  switch(p) {
    case NpcProcessPolicy::Player:   return "player";
    case NpcProcessPolicy::AiNormal: return "normal";
    case NpcProcessPolicy::AiFar:    return "far";
    case NpcProcessPolicy::AiFar2:   return "far2";
    }
  return "?";
  }

// the script state it is in (ZS_SMALLTALK), "-" for none
std::string stateName(World& w, const Npc& n) {
  const ScriptFn fn  = n.currentState();
  auto           sym = fn.isValid() ? w.script().findSymbol(fn.ptr) : nullptr;
  return sym!=nullptr ? sym->name() : "-";
  }

// a point is hidden if something is between it and the camera (not counting the ground right at the point)
bool hidden(World& w, const Vec3& from, const Vec3& to) {
  auto r = w.physic()->ray(from,to);
  return r.hasCol && (r.v-to).length()>15.f;
  }

Vec3 camOrigin(World& w, const Npc& pl, float angle) {
  const Vec3  p = pl.position();
  Vec3        dir;
  if(bench.cam==C_World) {
    const float a = angle*float(M_PI)/180.f;
    dir = {std::cos(a),0,std::sin(a)};
    } else {
    const Vec3  f    = facing(pl);
    const Vec3  side = {-f.z,0,f.x};
    const float b    = angle*float(M_PI)/180.f;
    dir = f*std::cos(b) + side*std::sin(b);
    }
  Vec3 org = p + dir*bench.camDist + Vec3(0,bench.camHeight,0);
  // keep the camera above the ground
  const float g = groundY(w,org.x,org.z,org.y+200);
  if(!std::isnan(g) && org.y<g+25)
    org.y = g+25;
  return org;
  }

int hiddenCount(World& w, const Npc& pl, const Vec3& org) {
  const Vec3 p = pl.position();
  int n = 0;
  n += hidden(w,org,ankle(pl,0)+Vec3(0,5,0)) ? 1 : 0;
  n += hidden(w,org,ankle(pl,1)+Vec3(0,5,0)) ? 1 : 0;
  n += hidden(w,org,p+Vec3(0,bench.camLook,0)) ? 1 : 0;
  return n;
  }

// side views first
void pickCamAngle(World& w, const Npc& pl) {
  static const float angles[] = {90,270,60,120,240,300,30,150,210,330,0,180};
  float best = bench.camAngle;
  int   bn   = 100;
  for(float a:angles) {
    const int n = hiddenCount(w,pl,camOrigin(w,pl,a));
    if(n<bn) {
      bn   = n;
      best = a;
      }
    if(n==0)
      break;
    }
  bench.camAngle = best;
  }

void lookFrom(const Vec3& org, const Vec3& tgt) {
  auto c = Gothic::inst().camera();
  if(c==nullptr)
    return;
  const Vec3  d   = tgt-org;
  const float lxz = std::sqrt(d.x*d.x+d.z*d.z);
  const float yaw = std::atan2(d.z,d.x)*180.f/float(M_PI);
  const float pit = std::atan2(d.y,lxz)*180.f/float(M_PI);
  c->setMarvinMode(Camera::M_Freeze);
  c->setPosition(org);
  c->setSpin(PointF(-pit,yaw));
  c->setAngles(PointF(-pit,yaw));
  bench.camPos = org;
  }

Vec3 rotY(const Vec3& v, float deg) {
  const float a = deg*float(M_PI)/180.f, c = std::cos(a), s = std::sin(a);
  return {v.x*c - v.z*s, v.y, v.x*s + v.z*c};
  }

// a shot of the hero and camNpc; false if the npc is not there
bool npcShot(World& w, Npc& pl, Vec3& org, Vec3& tgt) {
  Npc* npc = npcBySymbol(w,bench.camNpc);
  if(npc==nullptr)
    return false;
  const Vec3 hero  = pl.mapHeadBone();
  const Vec3 other = npc->mapHeadBone();
  Vec3 d = other-hero;
  d.y = 0;
  const float len = d.length();
  d = len>1 ? d/len : facing(pl);
  const Vec3 side = {-d.z,0,d.x};
  const Vec3 up   = {0,1,0};
  if(bench.cam==C_Shoulder) {
    // behind the hero, off his shoulder (camAngle: +1 or -1 picks the shoulder), at the npc's face
    org = hero - d*bench.camDist + side*(40.f*bench.camAngle) + up*bench.camHeight;
    tgt = other - up*8.f;
    }
  else if(bench.cam==C_NpcFace) {
    // in front of the npc's face (where it looks), turned camAngle degrees towards the hero's side
    const Vec3 f  = facing(*npc);
    const Vec3 c0 = rotY(f, bench.camAngle), c1 = rotY(f,-bench.camAngle);
    const Vec3 c  = (c0.x*-d.x + c0.z*-d.z) >= (c1.x*-d.x + c1.z*-d.z) ? c0 : c1;  // the turn towards the hero
    org = other + c*bench.camDist + up*bench.camHeight;
    tgt = other - up*8.f;
    }
  else {
    const Vec3  mid  = (hero+other)*0.5f;
    const float dist = std::max(bench.camDist,len*1.2f);
    org = mid + side*(dist*(bench.camAngle<0 ? -1.f : 1.f)) + up*bench.camHeight;
    tgt = mid - up*15.f;
    }
  const float g = groundY(w,org.x,org.z,org.y+200);
  if(!std::isnan(g) && org.y<g+25)
    org.y = g+25;
  return true;
  }

void updateCamera(World& w, Npc& pl) {
  bench.occl = 0;
  if(bench.cam==C_Game || bench.cam==C_Hold)
    return;
  if(bench.cam==C_Shoulder || bench.cam==C_NpcFace || bench.cam==C_Two) {
    // shots of the hero himself, also while the camera watches a subject (gaze)
    Npc* hero = Gothic::inst().player();
    Vec3 org, tgt;
    if(hero==nullptr || !npcShot(w,*hero,org,tgt))
      return;
    if(!bench.smValid) {
      bench.smOrg   = org;
      bench.smTgt   = tgt;
      bench.smValid = true;
      } else {
      bench.smOrg += (org-bench.smOrg)*bench.camSmooth;
      bench.smTgt += (tgt-bench.smTgt)*bench.camSmooth;
      }
    lookFrom(bench.smOrg,bench.smTgt);
    if(bench.clip>0)
      if(auto cam = Gothic::inst().camera())
        cam->setNearClip(std::max(10.f, (bench.smOrg-bench.smTgt).length() - bench.clip));
    return;
    }
  Vec3 tgt = pl.position() + Vec3(0,bench.camLook,0);
  Vec3 org = bench.camPos;
  if(bench.cam==C_Follow || bench.cam==C_World)
    org = camOrigin(w,pl,bench.camAngle);
  if(bench.cam==C_Face) {
    // the mouth (a bit above the head bone), seen from the front or at an angle
    const Vec3  f    = facing(pl);
    const Vec3  side = {-f.z,0,f.x};
    const float b    = bench.camAngle*float(M_PI)/180.f;
    tgt = pl.mapHeadBone() + Vec3(0,bench.camLook,0);
    org = tgt + (f*std::cos(b) + side*std::sin(b))*bench.camDist + Vec3(0,bench.camHeight,0);
    }
  lookFrom(org,tgt);
  if(bench.clip>0)
    if(auto cam = Gothic::inst().camera())
      cam->setNearClip(std::max(10.f, (org-tgt).length() - bench.clip));

  for(size_t i=0; i<2; ++i)
    if(hidden(w,org,ankle(pl,i)+Vec3(0,5,0)))
      bench.occl |= uint8_t(1u<<i);
  bench.occlTime = bench.occl!=0 ? bench.occlTime+1 : 0;
  if(bench.camAuto && bench.cam==C_Follow && bench.occlTime>Fps/3) {
    pickCamAngle(w,pl);
    bench.occlTime = 0;
    }
  }

// degrees between where the arrow points and the aim (sway, the turn lagging); -1 not aiming by hand
float manualAimOff(Npc& pl) {
  Vec3 pos, dir;
  if(!pl.hasManualAim() || !pl.arrowRay(pos,dir))
    return -1;
  const Vec3 want = Vec3::normalize(pl.manualAimPoint()-pos);
  return std::acos(std::clamp(Vec3::dotProduct(dir,want),-1.f,1.f))*180.f/float(M_PI);
  }

const char* archerStage(Archer::Stage s) {
  switch(s) {
    case Archer::Lowered:   return "lowered";
    case Archer::Raising:   return "raising";
    case Archer::Nocked:    return "nocked";
    case Archer::Drawing:   return "drawing";
    case Archer::Held:      return "held";
    case Archer::Reloading: return "reloading";
    }
  return "?";
  }
// the faces a mood shows (the bridge's say.mood)
struct FaceColumn { const char* column; const char* anim; };
const FaceColumn FaceColumns[] = {
  {"face_neutral","S_NEUTRAL"}, {"face_friendly","S_FRIENDLY"}, {"face_angry","S_ANGRY"},
  {"face_hostile","S_HOSTILE"}, {"face_afraid","S_FRIGHTENED"}, {"face_pain","T_HURT"},
  };

void writeHeader() {
  std::fprintf(bench.csv,"seg,frame,t,x,y,z,rot,speed,bs,walk,occl,ik_valid,ik_on,ik_hardoff,ik_w,nlegs,bad,pelvis,chest,pelvis_target,pelvis_limit,floor");
  for(const char* s:{"l","r","fl","fr"}) {
    std::fprintf(bench.csv,",%s_plant,%s_has,%s_gy,%s_nx,%s_ny,%s_nz,%s_toehas,%s_toey,%s_smooth,%s_err,%s_knee,%s_tilt,%s_roll,%s_yaw,%s_out,%s_slack",
                 s,s,s,s,s,s,s,s,s,s,s,s,s,s,s,s);
    for(const char* j:{"hip","kneea","anka","toea","kneef","ankf","toef"})
      std::fprintf(bench.csv,",%s_%s_x,%s_%s_y,%s_%s_z",s,j,s,j,s,j);
    }
  std::fprintf(bench.csv,",g_on,g_src,g_w,g_err,g_yaw,g_pitch,g_yawgoal,g_pitchgoal,g_head,g_chest,g_waist,g_animyaw,g_animpitch,g_hipsyaw,g_fyaw,g_fpitch,pg_on,pg_src,pg_w,pg_err,pg_yaw");
  std::fprintf(bench.csv,",ln_on,ln_roll,ln_pitch,ln_speed,ln_turn,ln_slope");
  std::fprintf(bench.csv,",an_pop,an_bone,an_speed,an_layers,bl_on,bl_active,bl_started,bl_angle,bl_time");
  std::fprintf(bench.csv,",a_on,a_w,a_yaw,a_pitch,a_animyaw,a_animpitch,a_turn,a_bend,a_err,a_steady,a_lean,a_side,ik_crouch,m_crouch,m_peek,m_cant,m_walk,aw_w,aw_speed,aw_twist,aw_chest,aw_drop");
  std::fprintf(bench.csv,",m_stage,m_draw,m_hold,m_fatigue,m_breath,m_holding,m_short,m_disturb,m_amp,m_sway_yaw,m_sway_pitch,m_off,m_spread,m_cross");
  std::fprintf(bench.csv,",bd_w,bd_draw,bd_span,bd_travel,bd_miss,bd_follow,bd_fit,bd_offset,bd_angle,hd_hand,hd_fore,hd_arrow,bd_hold,bd_nock,rh_x,rh_y,rh_z,bd_held,bd_overgrip,bd_atmiddle,bd_nockgap,bd_square,bd_cant,bd_bowmove,bd_bowmiss");
  std::fprintf(bench.csv,",lip_valid,lip_on,lip_t,lip_level,lip_mouth,lip_open,lip_f0,lip_f1,lip_alpha,lip_int,lip_picks");
  std::fprintf(bench.csv,",ge_on,ge_talk,ge_id,ge_mood,ge_starts,ge_cuts,ge_stops,ge_nods,ge_pitch,ge_yaw,ge_roll");
  std::fprintf(bench.csv,",ge_listen,ge_think,ge_lnods,ge_aside");
  std::fprintf(bench.csv,",bk_on,bk_w,bk_n,bk_pause,bk_shift,bk_double");
  for(auto& f:FaceColumns)
    std::fprintf(bench.csv,",%s",f.column);
  std::fprintf(bench.csv,",f_on,f_bend,f_yaw,f_twist,f_head,f_hits,f_str,f_ht");
  std::fprintf(bench.csv,",hp_player,hp_subject");
  std::fprintf(bench.csv,",ai_on,ai_want,ai_sigma,ai_errx,ai_erry,ai_lead,ai_pv,ai_ps");
  std::fprintf(bench.csv,"\n");
  }

// the animated pose of the watched one (or the hero): the largest second difference of a joint over
// three frames (cm; a pop), which joint, the fastest joint (cm/s) and the playing animations
void writeAnim() {
  const auto&  a    = bench.subject!=nullptr ? Pose::subjectProbe : Pose::playerProbe;
  const size_t n    = a.valid ? a.numBones : 0;
  float        pop  = 0, speed = 0;
  size_t       bone = size_t(-1);
  if(bench.anim2.size()==n && bench.anim1.size()==n) {
    for(size_t i=0; i<n; ++i) {
      const float d2 = (a.joint[i] - bench.anim1[i]*2.f + bench.anim2[i]).length();
      if(d2>pop) {
        pop  = d2;
        bone = i;
        }
      speed = std::max(speed,(a.joint[i] - bench.anim1[i]).length()*float(Fps));
      }
    }
  bench.anim2 = std::move(bench.anim1);
  bench.anim1.assign(a.joint,a.joint+n);

  std::string name = "-";
  if(bone!=size_t(-1) && a.skeleton!=nullptr && bone<a.skeleton->nodes.size())
    name = a.skeleton->nodes[bone].name;
  for(auto& c:name)
    if(c==' ' || c==',')
      c = '_';
  std::fprintf(bench.csv,",%.2f,%s,%.0f,%s",pop,name.c_str(),speed,a.layers.empty() ? "-" : a.layers.c_str());
  std::fprintf(bench.csv,",%d,%u,%u,%.1f,%.3f",int(AnimBlend::isEnabled()),a.blend.active,a.blend.started,a.blend.angle,a.blend.time);
  }

void writeRow(Npc& pl) {
  if(bench.csv==nullptr) {
    bench.csv = std::fopen((outDir+"/metrics.csv").c_str(),"w");
    if(bench.csv==nullptr)
      return;
    writeHeader();
    }
  auto&      p   = FootIk::playerProbe;
  const Vec3 pos = pl.position();
  std::fprintf(bench.csv,"%s,%llu,%llu,%.2f,%.2f,%.2f,%.1f,%.1f,%u,%u,%u,%d,%d,%d,%.3f,%u,%u,%.2f,%.2f,%.2f,%.2f,%.2f",
               bench.recName.c_str(),(unsigned long long)bench.recFrame,(unsigned long long)(bench.time-bench.recStart),
               pos.x,pos.y,pos.z,pl.rotation(),bench.speed,unsigned(pl.bodyStateMasked()),unsigned(pl.walkMode()),unsigned(bench.occl),
               int(p.valid),int(p.enable),int(p.hardOff),p.weight,unsigned(p.numLegs),unsigned(p.badBones),p.pelvis,p.chest,p.target,p.limit,p.floorY);
  for(auto& l:p.leg) {
    std::fprintf(bench.csv,",%.3f,%d,%.2f,%.3f,%.3f,%.3f,%d,%.2f,%.2f,%.2f,%.1f,%.1f,%.1f,%.1f,%.1f,%.2f",
                 l.plant,int(l.hasCol),l.groundY,l.n.x,l.n.y,l.n.z,int(l.toeCol),l.toeY,l.smoothY,l.err,l.kneeDev,l.tilt,l.roll,l.yaw,l.outward,l.slack);
    const Vec3* pts[] = {&l.final[0],&l.anim[1],&l.anim[2],&l.anim[3],&l.final[1],&l.final[2],&l.final[3]};
    for(auto v:pts)
      std::fprintf(bench.csv,",%.2f,%.2f,%.2f",v->x,v->y,v->z);
    }
  // gaze of the watched npc (or the hero) and of the hero
  const auto& g  = bench.subject!=nullptr ? Gaze::subjectProbe : Gaze::playerProbe;
  const auto& pg = Gaze::playerProbe;
  // where the face looks in the world: a jerk is a jump of this, whatever the body does
  const float fyaw   = std::atan2(g.facing.z,g.facing.x)*180.f/float(M_PI);
  const float fpitch = std::asin(std::clamp(g.facing.y,-1.f,1.f))*180.f/float(M_PI);
  std::fprintf(bench.csv,",%d,%d,%.3f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%d,%d,%.3f,%.2f,%.2f",
               int(g.active),int(g.source),g.weight,g.error,g.yaw,g.pitch,g.yawGoal,g.pitchGoal,g.headYaw,g.chestYaw,g.waistYaw,
               g.animYaw,g.animPitch,g.hipsYaw,fyaw,fpitch,int(pg.active),int(pg.source),pg.weight,pg.error,pg.yaw);
  // lean of the watched npc (or the hero)
  const auto& ln = bench.subject!=nullptr ? Lean::subjectProbe : Lean::playerProbe;
  std::fprintf(bench.csv,",%d,%.2f,%.2f,%.1f,%.1f,%.1f",int(ln.active),ln.roll,ln.pitch,ln.speed,ln.turn,ln.slope);
  writeAnim();
  // aim of the watched npc (or the hero)
  const auto& am = bench.subject!=nullptr ? Aim::subjectProbe : Aim::playerProbe;
  std::fprintf(bench.csv,",%d,%.3f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f",int(am.active),am.weight,am.yaw,am.pitch,
               am.animYaw,am.animPitch,am.turn,am.bend,am.error);
  std::fprintf(bench.csv,",%d,%.2f,%.2f,%.1f,%.2f,%.2f,%.2f",int(am.steady),am.lean,am.side,FootIk::playerProbe.crouch,
               double(pl.aimCrouch()),double(pl.aimPeek()),double(pl.aimCant()));
  // steps with the bow drawn
  const auto& aw = AimWalk::playerProbe;
  std::fprintf(bench.csv,",%.2f,%.2f,%.1f,%.1f,%.1f,%.1f",double(pl.aimWalk()),aw.weight,aw.speed,aw.twist,aw.chest,aw.drop);
  // the manual aim: the bow's stage, the sway, how far the arrow points off the aim
  const auto& mp = Archer::playerProbe;
  // the crosshair: how far it is open, degrees; how far it moved on the screen since the last frame,
  // percent of the screen's height
  Vec3  cross;
  float spread = 0;
  auto  cam    = Gothic::inst().camera();
  bool  hasCross = cam!=nullptr && bench.ctrl!=nullptr && bench.ctrl->crosshair(cross,spread);
  float moved  = -1;
  if(hasCross) {
    // as the game draws it: the center of the screen, unless the bench aims elsewhere
    float x = cross.x, y = cross.y, z = cross.z, w = 1;
    cam->viewProj().project(x,y,z,w);
    if(bench.ctrl->isCrosshairCentered()) {
      x = 0;
      y = 0;
      w = 1;
      }
    hasCross = w>1e-3f;
    const Vec3 at = hasCross ? Vec3(x/w,y/w,0) : Vec3();
    if(hasCross && bench.crossValid)
      moved = std::hypot((at.x-bench.crossPrev.x)*bench.aspect,at.y-bench.crossPrev.y)*50.f;
    bench.crossPrev = at;
    }
  bench.crossValid = hasCross;
  std::fprintf(bench.csv,",%d,%.3f,%.2f,%.3f,%.2f,%d,%d,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f",mp.valid ? int(mp.stage) : -1,mp.draw,mp.hold,
               mp.fatigue,mp.breath,int(mp.holding),int(mp.short_),mp.disturb,mp.amp,mp.yaw,mp.pitch,manualAimOff(pl),
               hasCross ? spread*180.f/float(M_PI) : -1.f,moved);
  // the hand drawing the string
  const auto& bd = BowDraw::playerProbe;
  std::fprintf(bench.csv,",%.3f,%.3f,%.1f,%.1f,%.1f,%.1f,%.1f,%.1f,%.1f",bd.valid ? bd.weight : -1.f,bd.draw,bd.span,bd.travel,bd.miss,
               bd.follow,bd.fit,bd.offset,bd.angle);
  std::fprintf(bench.csv,",%.1f,%.1f,%.1f,%d,%.2f",bd.head,bd.fore,bd.arrow,int(bd.holding),bd.nock);
  std::fprintf(bench.csv,",%.2f,%.2f,%.2f",bd.handAt.x,bd.handAt.y,bd.handAt.z);
  std::fprintf(bench.csv,",%.2f,%.1f,%.1f,%.1f,%.1f,%.1f,%.1f,%.1f",bd.aligned,bd.overGrip,bd.atMiddle,bd.nockGap,bd.square,bd.cant,bd.bowMove,bd.bowMiss);
  // lips of the watched one
  const auto& lp = LipSync::subjectProbe;
  std::fprintf(bench.csv,",%d,%d,%llu,%.3f,%.3f,%.3f,%u,%u,%.2f,%.3f,%u",
               int(lp.valid),int(lp.active),(unsigned long long)lp.time,lp.level,lp.mouth,lp.open,
               lp.shape.frame0,lp.shape.frame1,lp.shape.alpha,lp.shape.intensity,lp.picks);
  // its hands while it speaks
  const auto& ge = TalkGestures::subjectProbe;
  std::fprintf(bench.csv,",%d,%d,%u,%u,%u,%u,%u,%u,%.2f,%.2f,%.2f",int(ge.engaged),int(ge.speaking),unsigned(ge.gesture),
               unsigned(ge.mood),ge.starts,ge.cuts,ge.stops,ge.nods,double(ge.head.pitch*180.f/float(M_PI)),
               double(ge.head.yaw*180.f/float(M_PI)),double(ge.head.roll*180.f/float(M_PI)));
  std::fprintf(bench.csv,",%d,%d,%u,%.3f",int(ge.listening),int(ge.thinking),ge.listenNods,ge.aside.weight);
  // its blinks: how closed the eyes are now, counters
  const auto& bk = EyeBlink::subjectProbe;
  std::fprintf(bench.csv,",%d,%.3f,%u,%u,%u,%u",int(bk.valid && bk.enabled),subjectOf(pl).faceWeight("R_EYESBLINK"),
               bk.blinks,bk.pauses,bk.shifts,bk.doubles);
  // faces of the watched one, as shown (fading in and out)
  Npc& s = subjectOf(pl);
  for(auto& f:FaceColumns)
    std::fprintf(bench.csv,",%.3f",s.faceWeight(f.anim));
  // flinch of the watched one (or the hero)
  const auto& fl = bench.subject!=nullptr ? Flinch::subjectProbe : Flinch::playerProbe;
  std::fprintf(bench.csv,",%d,%.2f,%.1f,%.2f,%.2f,%u,%.2f,%.2f",int(fl.active),fl.bend,fl.bendYaw,fl.twist,fl.head,
               fl.hits,fl.strength,fl.height);
  // hit points: the hero's, the watched one's (a fight's course)
  std::fprintf(bench.csv,",%d,%d",pl.attribute(ATR_HITPOINTS),bench.subject!=nullptr ? bench.subject->attribute(ATR_HITPOINTS) : -1);
  // the watched npc's archer by hand (game/archerai)
  const auto& ai = ArcherAi::subjectProbe;
  std::fprintf(bench.csv,",%d,%d,%.3f,%.3f,%.3f,%.0f,%.2f,%.2f",int(ai.valid && ai.active),int(ai.want),ai.sigma,ai.err.x,ai.err.y,
               ai.lead,ai.pv,ai.ps);
  std::fprintf(bench.csv,"\n");
  }

void exec(const std::string& line, World& w, Npc& pl, PlayerControl& ctrl);

// the hero stands around the watched npc, facing it
void placeHero(World& w, Npc& pl) {
  Npc& s = subjectOf(pl);
  if(&s==&pl || bench.placeDist<=0)
    return;
  const float a   = (s.rotation()+bench.placeAngle)*float(M_PI)/180.f;
  Vec3        pos = s.position() + Vec3(std::cos(a),0,std::sin(a))*bench.placeDist;
  const float g   = groundY(w,pos.x,pos.z,pos.y+100);
  if(std::isnan(g))
    return; // inside a rock: the hero would fall through the world
  pos.y = g;
  pl.setPosition(pos);
  const Vec3 d = s.position()-pos;
  pl.setDirection(Vec3(d.x,0,d.z));
  pl.updateTransform();
  }

// foreach slope <min deg> <max deg> <count> | foreach stairs <count> | foreach wp <name part> <count>
// ... end: the body runs at each spot, after a teleport; $NAME, $I, $ANGLE are replaced
void expandForeach(size_t at, World& w, Npc& pl) {
  const auto a     = split(bench.prog[at]);
  size_t     end   = at+1;
  int        depth = 1;
  for(; end<bench.prog.size(); ++end) {
    const auto c = split(bench.prog[end]);
    if(c[0]=="foreach")
      ++depth;
    if(c[0]=="end" && --depth==0)
      break;
    }
  std::vector<std::string> body(bench.prog.begin()+long(at)+1, bench.prog.begin()+long(std::min(end,bench.prog.size())));

  std::vector<Spot> spots;
  const std::string kind = a.size()>1 ? a[1] : "";
  if(kind=="slope")
    spots = findSpots(w,pl,K_Slope,num(a,2,10),num(a,3,35),"",size_t(num(a,4,5)));
  else if(kind=="stairs")
    spots = findSpots(w,pl,K_Stairs,0,0,"",size_t(num(a,2,5)));
  else if(kind=="wp" && a.size()>2)
    spots = findSpots(w,pl,K_Name,0,90,a[2],size_t(num(a,3,5)));
  else
    say("foreach: unknown kind '" + kind + "'");

  std::vector<std::string> out;
  for(size_t i=0; i<spots.size(); ++i) {
    char ang[32];
    std::snprintf(ang,sizeof(ang),"%.0f",spots[i].angle);
    char info[256];
    std::snprintf(info,sizeof(info),"say spot %zu %s angle=%.1f rough=%.1f dist=%.0f",i,spots[i].name.c_str(),spots[i].angle,spots[i].rough,spots[i].dist);
    out.push_back(info);
    out.push_back("goto " + spots[i].name);
    out.push_back("wait 300");
    for(auto s:body) {
      replaceAll(s,"$NAME",spots[i].name);
      replaceAll(s,"$ANGLE",ang);
      replaceAll(s,"$I",std::to_string(i));
      out.push_back(s);
      }
    }
  say("foreach " + kind + ": " + std::to_string(spots.size()) + " spots");
  const size_t last = std::min(end+1,bench.prog.size());
  bench.prog.erase(bench.prog.begin()+long(at),bench.prog.begin()+long(last));
  bench.prog.insert(bench.prog.begin()+long(at),out.begin(),out.end());
  bench.pc = at;
  }

void face(Npc& pl, const Vec3& dir) {
  if(dir.x==0 && dir.z==0)
    return;
  pl.setDirection(dir);
  pl.updateTransform();
  }

void exec(const std::string& line, World& w, Npc& pl, PlayerControl& ctrl) {
  const auto a = split(line);
  if(a.empty())
    return;
  const std::string& c = a[0];

  if(c=="wait") {
    bench.waitUntil = bench.time + uint64_t(num(a,1,1000));
    }
  else if(c=="waitarrive") {
    bench.waitArrive = true;
    bench.arriveEnd  = bench.time + uint64_t(num(a,1,10000));
    }
  else if(c=="say" || c=="log") {
    say(rest(line));
    }
  else if(c=="label") {
    bench.label = rest(line);
    }
  else if(c=="goto") {
    const bool ok = marvin().exec("goto waypoint " + rest(line));
    bench.hasPrev = false;
    say(std::string(ok ? "goto " : "goto failed: ") + rest(line));
    }
  else if(c=="walkto") {
    auto wp = w.findPoint(rest(line));
    if(wp!=nullptr)
      pl.aiPush(AiQueue::aiGoToPoint(*wp));
    say(std::string(wp ? "walkto " : "walkto: no waypoint ") + rest(line));
    }
  else if(c=="near") {
    // near <npc> [dist cm 170] [degrees off the npc's facing 0]: the hero stands in front of the npc, facing it
    Npc* npc = a.size()>1 ? npcBySymbol(w,a[1]) : nullptr;
    if(npc==nullptr) {
      say("near: no npc " + (a.size()>1 ? a[1] : std::string()));
      return;
      }
    Vec3 at = npc->position() + rotY(facing(*npc),num(a,3,0))*num(a,2,170);
    const float g = groundY(w,at.x,at.z,at.y+100);
    if(!std::isnan(g))
      at.y = g;
    pl.setPosition(at);
    face(pl,npc->position()-at);
    bench.hasPrev = false;
    say("near " + a[1]);
    }
  else if(c=="approach") {
    // approach <npc>: walk up to the npc (AI path), then waitarrive
    if(Npc* npc = a.size()>1 ? npcBySymbol(w,a[1]) : nullptr)
      pl.aiPush(AiQueue::aiGoToNpc(npc)); else
      say("approach: no npc " + (a.size()>1 ? a[1] : std::string()));
    }
  else if(c=="walkmode") {
    const std::string m = a.size()>1 ? a[1] : "run";
    pl.setWalkMode(m=="walk" ? WalkBit::WM_Walk : m=="sneak" ? WalkBit::WM_Sneak : WalkBit::WM_Run);
    }
  else if(c=="face") {
    const std::string m = a.size()>1 ? a[1] : "";
    float an = 0, r = 0;
    Vec3  d;
    if(m=="uphill" || m=="downhill" || m=="across") {
      if(slopeAt(w,pl.position(),an,d,r))
        face(pl, m=="uphill" ? d*-1.f : m=="downhill" ? d : Vec3(d.z,0,-d.x));
      }
    else if(m=="wp" && a.size()>2) {
      if(auto wp = w.findPoint(a[2]))
        face(pl,wp->pos-pl.position());
      }
    else if(m=="npc" && a.size()>2) {
      if(auto npc = npcBySymbol(w,a[2]))
        face(pl,npc->position()-pl.position()); else
        say("face: no npc " + a[2]);
      }
    else if(m=="rel") {
      // face rel <deg>: along the facing of the watched npc turned by that much (the angles of place)
      const float y = (subjectOf(pl).rotation()+num(a,2,0))*float(M_PI)/180.f;
      face(pl,Vec3(std::cos(y),0,std::sin(y)));
      }
    else {
      const float y = num(a,1,0)*float(M_PI)/180.f;
      face(pl,Vec3(std::cos(y),0,std::sin(y)));
      }
    }
  else if(c=="turn") {
    const float y = (pl.rotation()+num(a,1,90))*float(M_PI)/180.f;
    face(pl,Vec3(std::cos(y),0,std::sin(y)));
    }
  else if(c=="wheel") {
    // the mouse wheel, as the window gets it: -1 down, 1 up
    ctrl.onMouseWheel(int(num(a,1,-1)));
    }
  else if(c=="press" || c=="release" || c=="tap" || c=="hold") {
    if(c=="release" && a.size()>1 && a[1]=="all") {
      for(auto& h:bench.holds)
        release(ctrl,h.a);
      bench.holds.clear();
      return;
      }
    KeyCodec::Action k = KeyCodec::Idle;
    if(a.size()<2 || !keyAction(a[1],k)) {
      say("unknown key: " + line);
      return;
      }
    if(c=="press") {
      press(ctrl,k);
      bench.holds.push_back({k,uint64_t(-1)});
      }
    else if(c=="release") {
      release(ctrl,k);
      bench.holds.erase(std::remove_if(bench.holds.begin(),bench.holds.end(),[k](const Hold& h){ return h.a==k; }),bench.holds.end());
      }
    else if(c=="tap") {
      press(ctrl,k);
      bench.taps.push_back(k);
      }
    else {
      press(ctrl,k);
      bench.holds.push_back({k,bench.time+uint64_t(num(a,2,1000))});
      }
    }
  else if(c=="cam") {
    const std::string m = a.size()>1 ? a[1] : "";
    bench.camAuto = false;
    if(m!="shoulder" && m!="face" && m!="two" && m!="smooth")
      bench.smValid = false;
    if(m=="game") {
      bench.cam = C_Game;
      if(auto cam = Gothic::inst().camera())
        cam->setMarvinMode(Camera::M_Normal);
      }
    else if(m=="follow" || m=="front" || m=="auto") {
      bench.cam       = C_Follow;
      bench.camDist   = num(a,2,220);
      bench.camHeight = num(a,3,40);
      bench.camAngle  = m=="front" ? 0 : num(a,4,90);
      if(m=="auto") {
        bench.camAuto = true;
        pickCamAngle(w,subjectOf(pl));
        }
      }
    else if(m=="world") {
      bench.cam       = C_World;
      bench.camDist   = num(a,2,220);
      bench.camHeight = num(a,3,40);
      bench.camAngle  = num(a,4,0);
      }
    else if(m=="fixed") {
      bench.cam = C_Fixed;
      }
    else if(m=="hold") {
      bench.cam = C_Hold;  // a shot of a scene: the subject may fall out of it, the frame stays
      }
    else if(m=="shoulder" || m=="two" || (m=="face" && a.size()>2 && !isNumber(a[2]))) {
      // cam shoulder <npc> [dist 120] [height 15] [shoulder 1|-1]
      // cam face <npc> [dist 140] [height 0] [degrees from the hero 30]
      // cam two <npc> [dist 250] [height 10] [side 1|-1]
      if(a.size()<3) {
        say("cam " + m + ": no npc");
        return;
        }
      bench.cam       = m=="shoulder" ? C_Shoulder : (m=="face" ? C_NpcFace : C_Two);
      bench.camNpc    = a[2];
      bench.camDist   = num(a,3,m=="shoulder" ? 120 : (m=="face" ? 140 : 250));
      bench.camHeight = num(a,4,m=="shoulder" ? 15 : (m=="face" ? 0 : 10));
      bench.camAngle  = num(a,5,m=="face" ? 30 : 1);
      if(npcBySymbol(w,bench.camNpc)==nullptr)
        say("cam " + m + ": no npc " + bench.camNpc);
      }
    else if(m=="cut") {
      bench.smValid = false;  // the next shot starts where it is, without moving there
      }
    else if(m=="smooth") {
      bench.camSmooth = std::clamp(num(a,2,0.12f),0.01f,1.f);
      }
    else if(m=="face") {
      // cam face <dist> [angle] [height]: close to the face of the watched one, looking at the mouth
      bench.cam       = C_Face;
      bench.camDist   = num(a,2,60);
      bench.camAngle  = num(a,3,0);
      bench.camHeight = num(a,4,0);
      bench.camLook   = 5;
      }
    else if(m=="look") {
      bench.camLook = num(a,2,45);
      }
    else {
      say("unknown cam mode: " + line);
      }
    }
  else if(c=="view") {
    const std::string m = a.size()>1 ? a[1] : "full";
    // Static and Movable objects share draw commands (same shaders), hiding them by type hides
    // parts of creatures too: what stands between the camera and the subject is clipped instead
    bench.clip = 0;
    DrawCommands::hiddenTypes = 0;
    if(m=="clean") {
      bench.clip = num(a,2,100);
      DrawCommands::hiddenTypes = (1u<<DrawCommands::Pfx);
      }
    else if(m=="nopfx")
      DrawCommands::hiddenTypes = (1u<<DrawCommands::Pfx);
    else if(m=="hide") {
      // view hide static movable morph pfx ...
      DrawCommands::hiddenTypes = 0;
      for(size_t i=2; i<a.size(); ++i) {
        const std::string& t = a[i];
        if(t=="static")   DrawCommands::hiddenTypes |= (1u<<DrawCommands::Static);
        if(t=="movable")  DrawCommands::hiddenTypes |= (1u<<DrawCommands::Movable);
        if(t=="morph")    DrawCommands::hiddenTypes |= (1u<<DrawCommands::Morph);
        if(t=="pfx")      DrawCommands::hiddenTypes |= (1u<<DrawCommands::Pfx);
        if(t=="animated") DrawCommands::hiddenTypes |= (1u<<DrawCommands::Animated);
        }
      }
    if(bench.clip<=0)
      if(auto cam = Gothic::inst().camera())
        cam->setNearClip(10);
    }
  else if(c=="overlay") {
    const std::string m = a.size()>1 ? a[1] : "on";
    const bool on = !(a.size()>2 && a[2]=="off");
    if(m=="zoom")
      bench.zoom = on;
    else if(m=="bright")
      bench.bright = on;
    else if(m=="zones")
      bench.zonesOnly = on; // the hit zones only, no legs, no text
    else
      bench.overlay = (m!="off");
    }
  else if(c=="ui") {
    bench.ui = !(a.size()>1 && a[1]=="off");
    }
  else if(c=="rec") {
    stopRecording();
    bench.rec      = true;
    bench.recVideo = !(a.size()>2 && a[2]=="novideo");
    bench.recName  = a.size()>1 ? a[1] : "rec";
    bench.recFrame = 0;
    bench.recStart = bench.time;
    bench.anim1.clear();
    bench.anim2.clear();
    bench.recWall  = std::chrono::steady_clock::now();
    if(bench.recVideo && SoundDevice::loopbackRate()>0) {
      bench.audio       = std::fopen((outDir+"/"+bench.recName+".wav").c_str(),"wb");
      bench.audioFrames = 0;
      if(bench.audio!=nullptr)
        writeWavHeader(bench.audio,SoundDevice::loopbackRate(),0);
      }
    event("rec","\"fps\":" + std::to_string(Fps) + ",\"audio\":" + (bench.audio!=nullptr ? "true" : "false"));
    say("rec " + bench.recName + (bench.recVideo ? "" : " (metrics only)") + (bench.audio!=nullptr ? " with sound" : ""));
    }
  else if(c=="stop") {
    stopRecording();
    }
  else if(c=="shot") {
    bench.shot = a.size()>1 ? a[1] : "shot";
    }
  else if(c=="anims") {
    // anims: what the watched one (or the hero) plays, its body state, weapon and target
    Npc& s = subjectOf(pl);
    char buf[160];
    std::snprintf(buf,sizeof(buf),"anims %s bs=%u ws=%d target=%s: ",symName(w,&s).c_str(),unsigned(s.bodyStateMasked() & BS_MAX),
                  int(s.weaponState()),symName(w,s.target()).c_str());
    say(buf + s.animNames());
    }
  else if(c=="cansee") {
    // cansee <npc|nearest>: can the hero see it (from the head, leaning over an edge), and what is between
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    if(n==nullptr || n==&pl) {
      say("cansee: no npc " + line);
      return;
      }
    const Vec3  head = pl.mapHeadBone();
    const Vec3  tg   = n->centerPosition();
    const Vec3  d    = tg-head;
    const float h    = std::sqrt(d.x*d.x+d.z*d.z);
    auto        r    = w.physic()->ray(head,tg);
    const auto& fr = w.script().focusRange();
    char buf[300];
    std::snprintf(buf,sizeof(buf),"cansee %s: head %d, over the edge %d; dist %.0f, down %.0f deg; the ray from the head hits at %.0f cm of %.0f; bow focus %.0f..%.0f",
                  symName(w,n).c_str(),int(pl.canSeeNpc(*n,true)),int(pl.canSeeNpcOverEdge(*n)),double(d.length()),
                  double(-std::atan2(d.y,h)*180.f/float(M_PI)),r.hasCol ? double((r.v-head).length()) : -1.0,double(d.length()),
                  double(fr.npc_range1),double(fr.npc_range2));
    say(buf);
    // the lean over the edge, as Npc::canSeeNpcOverEdge does it at full lean
    const float an   = pl.rotationRad();
    const Vec3  dir  = Vec3::normalize(Vec3(d.x,0,d.z)*(1.f/std::max(h,1.f)) + Vec3(std::cos(an),0,std::sin(an))*0.25f);
    const Vec3  eye  = head + dir*Aim::LeanReach - Vec3(0,Aim::LeanDrop,0);
    auto        r1   = w.physic()->ray(head,eye);
    auto        r2   = w.physic()->ray(eye,tg);
    std::snprintf(buf,sizeof(buf),"  leaned head: room %d (hit at %.0f of %.0f), to it %d (hit at %.0f of %.0f), head at %.0f above the feet",
                  int(!r1.hasCol),r1.hasCol ? double((r1.v-head).length()) : -1.0,double((eye-head).length()),
                  int(!r2.hasCol),r2.hasCol ? double((r2.v-eye).length()) : -1.0,double((tg-eye).length()),double(head.y-pl.position().y));
    say(buf);
    }
  else if(c=="hp") {
    // hp <npc> [full]: hit points of the 3 nearest npcs of that script symbol (sheep are all over the world),
    // and how far (did the arrow hit); full: the nearest gets them all back first
    const std::string name = a.size()>1 ? a[1] : "";
    const size_t      id   = w.script().findSymbolIndex(name);
    std::vector<std::pair<float,Npc*>> list;
    for(uint32_t i=0; i<w.npcCount(); ++i)
      if(Npc* n = w.npcById(i); n!=nullptr && n->instanceSymbol()==id)
        list.emplace_back((n->position()-pl.position()).length(),n);
    std::sort(list.begin(),list.end(),[](auto& x, auto& y) { return x.first<y.first; });
    if(a.size()>2 && a[2]=="full" && !list.empty() && !list[0].second->isDown()) {
      // the nearest gets its hit points back
      Npc& n = *list[0].second;
      n.changeAttribute(ATR_HITPOINTS,n.attribute(ATR_HITPOINTSMAX)-n.attribute(ATR_HITPOINTS),false);
      }
    std::string out = "hp " + name + ":";
    for(size_t i=0; i<list.size() && i<3; ++i)
      out += " " + std::to_string(list[i].second->attribute(ATR_HITPOINTS)) + (list[i].second->isDead() ? " dead" : "") +
             " at " + std::to_string(int(list[i].first/100.f)) + " m;";
    say(out);
    }
  else if(c=="give" || c=="equip") {
    // give <item> [count]: into the hero's inventory; equip <item>: give one and put it on (a bow, a rune)
    const std::string name = a.size()>1 ? a[1] : "";
    const size_t      id   = w.script().findSymbolIndex(name);
    if(id==size_t(-1)) {
      say(c + ": no item " + name);
      return;
      }
    pl.addItem(id,size_t(c=="give" ? num(a,2,1) : 1));
    if(c=="equip")
      pl.useItem(id,Item::NSLOT,true); // whatever the hero's strength
    say(c + " " + name);
    }
  else if(c=="put") {
    // put <npc|nearest> <dist> <angle> [dy]: the npc stands there from the hero (angle from the hero's
    // facing, 0 in front), on the ground, frozen, facing the hero
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    if(n==nullptr || n==&pl) {
      say("put: no npc " + line);
      return;
      }
    const float d   = num(a,2,500);
    const float an  = (pl.rotation()+num(a,3,0))*float(M_PI)/180.f;
    Vec3        pos = pl.position() + Vec3(std::cos(an),0,std::sin(an))*d;
    // the ground there may be far above or below (a slope): the first one from above, by short rays
    // (a long one misses the landscape)
    DynamicWorld::RayLandResult r;
    for(float y=pos.y+num(a,4,0)+1500; y>pos.y-1500 && !r.hasCol; y-=400)
      r = w.physic()->ray({pos.x,y,pos.z},{pos.x,y-450,pos.z});
    if(r.hasCol)
      pos.y = r.v.y;
    n->setInteraction(nullptr,true);
    n->clearAiQueue();
    n->clearState(false);
    n->setExternalControl(true);
    n->setPosition(pos);
    const Vec3 dir = pl.position()-pos;
    n->setDirection(Vec3(dir.x,0,dir.z));
    n->updateTransform();
    char buf[200];
    std::snprintf(buf,sizeof(buf),"put %s at %.0f %.0f %.0f (%.0f above the hero%s)",symName(w,n).c_str(),pos.x,pos.y,pos.z,
                  pos.y-pl.position().y,r.hasCol ? "" : ", no ground found");
    say(buf);
    }
  else if(c=="pin") {
    // pin <npc|nearest> <dist> <angle> <height> | pin off: the npc hangs there from the hero (angle from
    // the hero's facing, height above the hero's feet), frozen, every frame: a target above or below
    if(a.size()>1 && a[1]=="off") {
      bench.pinned = nullptr;
      say("pin off");
      return;
      }
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    if(n==nullptr || n==&pl) {
      say("pin: no npc " + line);
      return;
      }
    n->setInteraction(nullptr,true);
    n->clearAiQueue();
    n->clearState(false);
    n->setExternalControl(true);
    const float an = (pl.rotation()+num(a,3,0))*float(M_PI)/180.f;
    bench.pinned   = n;
    bench.pinAt    = pl.position() + Vec3(std::cos(an)*num(a,2,800),num(a,4,0),std::sin(an)*num(a,2,800));
    char buf[200];
    std::snprintf(buf,sizeof(buf),"pin %s at %.0f %.0f %.0f",symName(w,n).c_str(),bench.pinAt.x,bench.pinAt.y,bench.pinAt.z);
    say(buf);
    }
  else if(c=="npcgo") {
    // npcgo <npc|nearest> <waypoint> [walk|run]: the npc goes there (AI path), like a script sends it
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    auto wp = a.size()>2 ? w.findPoint(a[2]) : nullptr;
    if(n==nullptr || n==&pl || wp==nullptr) {
      say("npcgo: no npc or waypoint " + line);
      return;
      }
    n->setWalkMode(a.size()>3 && a[3]=="walk" ? WalkBit::WM_Walk : WalkBit::WM_Run);
    n->aiPush(AiQueue::aiGoToPoint(*wp));
    say("npcgo " + symName(w,n) + " " + a[2]);
    }
  else if(c=="manual") {
    // manual on|off: the hero aims a bow by hand (game/archery)
    const std::string m = a.size()>1 ? a[1] : "";
    if(m=="on" || m=="off")
      Archery::setEnabled(m=="on"); else
      say("unknown manual command: " + line);
    }
  else if(c=="sfx") {
    // sfx <name>...: the files a sound effect plays (its variants _A1.. too), whether they load
    for(size_t k=1; k<a.size(); ++k) {
      std::string out = "sfx " + a[k] + ":";
      for(int v=0; v<10; ++v) {
        const std::string name = v==0 ? a[k] : a[k]+"_A"+std::to_string(v);
        auto& d = Gothic::sfx()[name];
        if(d.file.empty())
          break;
        out += " " + d.file + (Resources::loadSoundBuffer(d.file).isEmpty() ? " (missing)" : "") +
               " vol " + std::to_string(d.vol);
        }
      say(out);
      }
    }
  else if(c=="animspeed") {
    // animspeed <anim> <k>: the hero's animation of that name plays k times as fast from its next start
    // (the reload's tempo is set by the manual aim from dexterity: this overrides it until then)
    if(a.size()<3) {
      say("animspeed: <anim> <k>");
      return;
      }
    pl.setAnimSpeed(a[1],num(a,2,1));
    say("animspeed " + a[1] + " " + a[2]);
    }
  else if(c=="bowdraw") {
    // bowdraw on|off: the hand draws the string as far as the bow is drawn, the reload holds the aim
    // (graphics/mesh/bowdraw)
    const std::string m = a.size()>1 ? a[1] : "";
    if(m=="on" || m=="off")
      BowDraw::setEnabled(m=="on"); else
      say("unknown bowdraw command: " + line);
    }
  else if(c=="showzones") {
    // showzones <npc symbol>|player|off: the overlay draws the npc's hit zones (capsules on its bones: the head
    // red, the trunk yellow, the limbs blue)
    bench.zonesNpc = a.size()>1 && a[1]!="off" ? a[1] : "";
    say("showzones " + (bench.zonesNpc.empty() ? std::string("off") : bench.zonesNpc));
    }
  else if(c=="animinfo") {
    // animinfo <name>...: the hero's animations of those names (frames, the nodes, how far they go, the
    // hips)
    for(size_t i=1; i<a.size(); ++i)
      say("animinfo " + pl.animInfo(a[i]));
    }
  else if(c=="aimwalk") {
    // aimwalk on|off: the legs step under the aim (graphics/mesh/aimwalk)
    AimWalk::setEnabled(a.size()<2 || a[1]!="off");
    }
  else if(c=="bowgeom") {
    // bowgeom: the bow and the bow hand in the arrow's frame, as posed now
    say(pl.bowDump());
    }
  else if(c=="stats") {
    // stats [npc <npc>] [str <n>] [dex <n>] [bow <n>] [cbow <n>] [hp <n>] [hpmax <n>] [talent auto|0|1|2]:
    // the hero's (an npc's) strength, dexterity, hit chances (the skill of the manual aim), hit points;
    // talent: the animations of the trained (G2 sets level 1 at 30%, 2 at 60%: _BOWT1, _BOWT2), auto as
    // the hit chances say
    Npc*   np = &pl;
    size_t i0 = 1;
    if(a.size()>1 && a[1]=="npc") {
      np = a.size()>2 ? findNpc(w,pl,a[2]) : nullptr;
      if(np==nullptr) {
        say("stats: no npc " + line);
        return;
        }
      i0 = 3;
      }
    Npc&   n    = *np;
    auto&  h    = n.handle();
    bool   more = &n!=&pl;
    for(size_t i=i0; i+1<a.size(); i+=2) {
      if(a[i]=="talent") {
        auto level = [&](int32_t chance) {
          if(a[i+1]=="auto")
            return chance>=60 ? 2 : (chance>=30 ? 1 : 0);
          return int(num(a,i+1,0));
          };
        n.setTalentSkill(TALENT_BOW,     level(h.hitchance[TALENT_BOW]));
        n.setTalentSkill(TALENT_CROSSBOW,level(h.hitchance[TALENT_CROSSBOW]));
        more = true;
        continue;
        }
      const int v = int(num(a,i+1,0));
      if(a[i]=="str")
        h.attribute[ATR_STRENGTH] = v;
      else if(a[i]=="dex")
        h.attribute[ATR_DEXTERITY] = v;
      else if(a[i]=="bow")
        h.hitchance[TALENT_BOW] = v;
      else if(a[i]=="cbow")
        h.hitchance[TALENT_CROSSBOW] = v;
      else if(a[i]=="hp")
        h.attribute[ATR_HITPOINTS] = v;
      else if(a[i]=="hpmax")
        h.attribute[ATR_HITPOINTSMAX] = v;
      else
        say("stats: unknown " + a[i]);
      }
    const auto  wp = n.currentRangedWeapon();
    const auto  st = Archery::stats(n,wp);
    char buf[440];
    if(more) {
      // whose, the reload as long as the animation is (the trained ones' is shorter), the hit points
      const bool cbow = wp!=nullptr && wp->isCrossbow();
      std::snprintf(buf,sizeof(buf),"stats of %s: talent bow %d cbow %d, reload %.0f ms, hp %d of %d, point protection %d",
                    symName(w,&n).c_str(),n.talentSkill(TALENT_BOW),n.talentSkill(TALENT_CROSSBOW),
                    double(n.animTime(Archery::reloadAnim(cbow))),n.attribute(ATR_HITPOINTS),n.attribute(ATR_HITPOINTSMAX),
                    n.protection(PROT_POINT));
      say(buf);
      }
    // an npc's: by name (the report takes `stats str` for the hero's)
    const std::string who = &n!=&pl ? " npc " + symName(w,&n) : "";
    std::snprintf(buf,sizeof(buf),"stats%s str %d dex %d bow %d cbow %d, weapon damage %d: skill %.2f strength %.2f sway %.2f deg settle %.2f s breath %.1f s draw %.2f s hold %.1f s speed %.1f m/s cone %.2f deg reload x%.2f",
                  who.c_str(),n.attribute(ATR_STRENGTH),n.attribute(ATR_DEXTERITY),n.hitChance(TALENT_BOW),n.hitChance(TALENT_CROSSBOW),
                  wp!=nullptr ? int(wp->handle().damage_total) : -1,
                  double(st.skill),double(st.strength),double(st.sway*180.f/float(M_PI)),double(st.settle),double(st.breath),
                  double(st.drawTime),double(std::min(st.holdLimit,999.f)),double(st.speed*10.f),double(st.cone*180.f/float(M_PI)),
                  double(st.reload));
    say(buf);
    }
  else if(c=="seed") {
    // seed <n>: the random numbers start over: the scripts' (the dice, the release), the bench's, the
    // hero's sway
    const uint32_t s = uint32_t(num(a,1,1));
    w.script().setSeed(s);
    bench.lcg = s*2654435761u + 1u;
    pl.archer().reseed(s);
    say("seed " + std::to_string(s));
    }
  else if(c=="npcequip") {
    // npcequip <npc> <item> [count]: into its inventory, the first one put on (a bow and its arrows)
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    const size_t id = w.script().findSymbolIndex(a.size()>2 ? a[2] : "");
    if(n==nullptr || id==size_t(-1)) {
      say("npcequip: no npc or item " + line);
      return;
      }
    n->addItem(id,size_t(num(a,3,1)));
    n->useItem(id,Item::NSLOT,true);
    say("npcequip " + symName(w,n) + " " + a[2]);
    }
  else if(c=="arc") {
    // arc <npc> <count> <dist> <step deg>: that many of it stand that far in front of the hero, a step
    // apart round it, facing it (`all` of fight)
    const size_t id = w.script().findSymbolIndex(a.size()>1 ? a[1] : "");
    if(id==size_t(-1)) {
      say("arc: no npc " + line);
      return;
      }
    const int   cnt  = int(num(a,2,4));
    const float d    = num(a,3,1500);
    const float step = num(a,4,15);
    std::string out  = "arc";
    bench.crew.clear();
    for(int i=0; i<cnt; ++i) {
      const float an  = (pl.rotation() + (float(i)-float(cnt-1)*0.5f)*step)*float(M_PI)/180.f;
      Vec3        pos = pl.position() + Vec3(std::cos(an),0,std::sin(an))*d;
      DynamicWorld::RayLandResult r;
      for(float y=pos.y+1500; y>pos.y-1500 && !r.hasCol; y-=400)
        r = w.physic()->ray({pos.x,y,pos.z},{pos.x,y-450,pos.z});
      if(r.hasCol)
        pos.y = r.v.y;
      Npc* n = w.addNpc(id,pos);
      if(n==nullptr)
        continue;
      const Vec3 dir = pl.position()-pos;
      n->setDirection(Vec3(dir.x,0,dir.z));
      n->updateTransform();
      bench.crew.push_back(n);
      char buf[100];
      std::snprintf(buf,sizeof(buf)," %.0f %.0f %.0f",pos.x,pos.y,pos.z);
      out += buf;
      }
    say(out);
    }
  else if(c=="fight") {
    // fight <npc|all> <target|player>: it attacks as the game's fight starts (ZS_ATTACK, to kill: an
    // archer readies its bow from 9 m); all: the npcs of arc
    std::vector<Npc*> who;
    if(a.size()>1 && a[1]=="all")
      who = bench.crew; else
    if(Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest"))
      who.push_back(n);
    Npc*         tg     = findNpc(w,pl,a.size()>2 ? a[2] : "player");
    const size_t state  = w.script().findSymbolIndex("ZS_ATTACK");
    auto         reason = w.script().findSymbol("AIV_ATTACKREASON");
    auto         kill   = w.script().findSymbol("AR_KILL");
    if(tg==nullptr || state==size_t(-1)) {
      say("fight: no target or no ZS_ATTACK " + line);
      return;
      }
    size_t sent = 0;
    for(auto n:who) {
      if(n==tg || n->isDown())
        continue;
      ++sent;
      if(reason!=nullptr && kill!=nullptr && size_t(reason->get_int())<std::size(n->handle().aivar))
        n->handle().aivar[size_t(reason->get_int())] = kill->get_int();
      n->setExternalControl(false);
      n->setTarget(tg);
      n->aiPush(AiQueue::aiStartState(ScriptFn(state),0,tg,nullptr,""));
      }
    say("fight " + std::to_string(sent) + " at " + symName(w,tg));
    }
  else if(c=="waitdown") {
    // waitdown <npc|player> [ms]: the scenario waits till it is down (dead, unconscious): `fight over`
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "player");
    if(n==nullptr) {
      say("waitdown: no npc " + line);
      return;
      }
    bench.downNpc  = n;
    bench.downFrom = bench.time;
    bench.downEnd  = bench.time + uint64_t(num(a,2,120000));
    }
  else if(c=="npcaim") {
    // npcaim on [second|arrow] | off | sigma <deg> | sigma off | lead on|off: npc archers aim by hand
    // (game/archerai): as many hits a second as G2's dice (second) or as sure an arrow (arrow); an error
    // forced (degrees per axis); the lead on a runner
    const std::string m = a.size()>1 ? a[1] : "";
    ++ArcherAi::epoch;
    if(m=="on") {
      ArcherAi::setEnabled(true);
      if(a.size()>2)
        ArcherAi::mode = a[2]=="arrow" ? ArcherAi::PerArrow : ArcherAi::PerSecond;
      }
    else if(m=="off")
      ArcherAi::setEnabled(false);
    else if(m=="sigma")
      ArcherAi::forcedSigma = a.size()>2 && a[2]!="off" ? num(a,2,0) : -1.f;
    else if(m=="lead")
      ArcherAi::leadOn = a.size()<3 || a[2]!="off";
    say("npcaim " + std::string(ArcherAi::isEnabled() ? "on" : "off") + (ArcherAi::mode==ArcherAi::PerArrow ? " arrow" : " second") +
        " sigma " + std::to_string(ArcherAi::forcedSigma) + " lead " + (ArcherAi::leadOn ? "on" : "off"));
    }
  else if(c=="archer") {
    // archer <npc|all> at <npc|player> [dice] | archer off: it stands where it is and shoots at the target
    // every time it can, by hand (npcaim on) or by the dice: no fight AI, no perception's wait (the aim's
    // calibration); all: the npcs of arc
    auto release = [&]() {
      for(auto& [n,at]:bench.archers)
        if(n!=nullptr) {
          n->archerAi().cancel(*n);
          n->setExternalControl(false);
          }
      bench.archers.clear();
      };
    if(a.size()>1 && a[1]=="off") {
      release();
      bench.archerAt = nullptr;
      say("archer off");
      return;
      }
    std::vector<Npc*> who;
    if(a.size()>1 && a[1]=="all")
      who = bench.crew; else
    if(Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest"))
      who.push_back(n);
    release();
    bench.archerAt   = findNpc(w,pl,a.size()>3 ? a[3] : "player");
    bench.archerDice = a.size()>4 && a[4]=="dice";
    for(auto n:who) {
      if(n==bench.archerAt || n->isDown())
        continue;
      n->setInteraction(nullptr,true);
      n->clearAiQueue();
      n->clearState(false);
      n->setExternalControl(true);
      bench.archers.emplace_back(n,n->position());
      }
    say("archer " + std::to_string(bench.archers.size()) + " at " + symName(w,bench.archerAt) + (bench.archerDice ? " by the dice" : ""));
    }
  else if(c=="still") {
    // still <npc|player> on|off: hit, it neither stumbles nor flinches (a target that keeps its pose, as
    // its zones were dumped)
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "player");
    if(n==nullptr) {
      say("still: no npc " + line);
      return;
      }
    n->setBenchStill(a.size()<3 || a[2]!="off");
    say("still " + symName(w,n) + (n->isBenchStill() ? " on" : " off"));
    }
  else if(c=="zones" && a.size()>2 && a[1]=="fit") {
    // zones fit on|off: the hit zones as thick as the body's mesh, or by a guess from the bone names (the
    // hero refitted now, the others when they change their body)
    HitZones::setFitEnabled(a[2]=="on");
    pl.fitHitZones();
    say("zones fit " + a[2]);
    }
  else if(c=="zones") {
    // zones <npc|nearest|player>: where its hit zones are now (the capsules on its bones), how well they
    // cover its body
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    if(n==nullptr) {
      say("zones: no npc " + line);
      return;
      }
    Vec3 h, b;
    n->hitZoneCenter(HitZones::Head,h);
    n->hitZoneCenter(HitZones::Body,b);
    char buf[200];
    std::snprintf(buf,sizeof(buf),"zones %s at %.0f %.0f %.0f: head %.0f %.0f %.0f body %.0f %.0f %.0f",symName(w,n).c_str(),
                  n->position().x,n->position().y,n->position().z,h.x,h.y,h.z,b.x,b.y,b.z);
    say(buf + std::string("\n") + n->hitZonesDump());
    }
  else if(c=="open") {
    // open [dist cm]: how far it is open around the hero at head height and at the knees, every 15
    // degrees from the facing (a target for the bow needs open ground)
    const float dist = num(a,1,4000);
    const Vec3  p0   = pl.position();
    std::string out  = "open (yaw from the facing: head/knees m):";
    for(int i=0; i<24; ++i) {
      const float an = (pl.rotation()+float(i*15))*float(M_PI)/180.f;
      const Vec3  d  = Vec3(std::cos(an),0,std::sin(an));
      float       m[2];
      for(int k=0; k<2; ++k) {
        const Vec3 from = p0 + Vec3(0,k==0 ? 160.f : 40.f,0);
        auto       r    = w.physic()->ray(from,from+d*dist);
        m[k] = r.hasCol ? (r.v-from).length()/100.f : dist/100.f;
        }
      char buf[48];
      std::snprintf(buf,sizeof(buf)," %d:%.0f/%.0f",i*15,double(m[0]),double(m[1]));
      out += buf;
      }
    say(out);
    }
  else if(c=="volley") {
    // volley <shots> [hold ms] [draw 0..1] [jitter ms]: shots by hand in a row (manual on): the string drawn
    // that far (1: full), held (up to jitter longer, at random), let go; the scenario goes on after the last
    // one is reloaded
    bench.volleyLeft  = int(num(a,1,5));
    bench.volleyHold  = uint64_t(num(a,2,300));
    bench.volleyDraw  = num(a,3,1);
    bench.volleyJitter = uint64_t(num(a,4,0));
    bench.volleyPhase = 0;
    bench.volleyT     = bench.time;
    say("volley " + std::to_string(bench.volleyLeft));
    }
  else if(c=="aimat") {
    // aimat <npc|nearest> [head|body|limb] | aimat <x> <y> <z> | aimat ahead [dist] [height] | aimat off: the
    // manual aim aims there
    // instead of the mouse - the middle of a zone of the npc, every frame
    bench.aimNpc   = nullptr;
    bench.aimFixed = false;
    if(a.size()>1 && a[1]=="off") {
      ctrl.setAimOverride(false);
      say("aimat off");
      return;
      }
    if(a.size()>1 && a[1]=="ahead") {
      // aimat ahead [dist cm 2000] [height cm 140]: a point that far ahead of the hero as it faces now
      const Vec3 f   = facing(pl);
      bench.aimFixed = true;
      bench.aimAt    = pl.position() + f*num(a,2,2000) + Vec3(0,num(a,3,140),0);
      ctrl.setAimOverride(true,bench.aimAt);
      say("aimat ahead");
      return;
      }
    if(a.size()>3 && (std::isdigit(uint8_t(a[1][0])) || a[1][0]=='-')) {
      bench.aimFixed = true;
      bench.aimAt    = Vec3(num(a,1,0),num(a,2,0),num(a,3,0));
      ctrl.setAimOverride(true,bench.aimAt);
      say("aimat " + a[1] + " " + a[2] + " " + a[3]);
      return;
      }
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    if(n==nullptr || n==&pl) {
      say("aimat: no npc " + line);
      return;
      }
    const std::string z = a.size()>2 ? a[2] : "body";
    bench.aimNpc  = n;
    bench.aimZone = z=="head" ? HitZones::Head : (z=="limb" ? HitZones::Limb : HitZones::Body);
    bench.aimRaw  = a.size()>3 && a[3]=="raw";
    say("aimat " + symName(w,n) + " " + std::string(HitZones::name(bench.aimZone)) + (bench.aimRaw ? " raw" : ""));
    }
  else if(c=="immortal") {
    // immortal <npc|nearest> on|off: arrows hit it, it does not die (counting hits)
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    if(n==nullptr) {
      say("immortal: no npc " + line);
      return;
      }
    auto& h = n->handle();
    if(a.size()>2 && a[2]=="off")
      h.flags = zenkit::NpcFlag(uint32_t(h.flags) & ~uint32_t(zenkit::NpcFlag::IMMORTAL)); else
      h.flags = zenkit::NpcFlag(uint32_t(h.flags) |  uint32_t(zenkit::NpcFlag::IMMORTAL));
    say("immortal " + symName(w,n) + " " + (n->isImmortal() ? "on" : "off"));
    }
  else if(c=="clear") {
    // clear [radius]: the npcs around the hero leave the world (rats, wolves that would join in), not the
    // watched one
    const float r = num(a,1,2500);
    std::vector<Npc*> gone;
    for(uint32_t i=0; i<w.npcCount(); ++i)
      if(Npc* n = w.npcById(i); n!=nullptr && n!=&pl && n!=bench.subject && (n->position()-pl.position()).length()<r)
        gone.push_back(n);
    std::string out = "clear:";
    for(auto n:gone) {
      out += " " + symName(w,n);
      bench.crew.erase(std::remove(bench.crew.begin(),bench.crew.end(),n),bench.crew.end());
      bench.archers.erase(std::remove_if(bench.archers.begin(),bench.archers.end(),[n](auto& x){ return x.first==n; }),bench.archers.end());
      if(bench.archerAt==n) {
        // their target gone: archer off
        for(auto& [x,at]:bench.archers)
          if(x!=nullptr) {
            x->archerAi().cancel(*x);
            x->setExternalControl(false);
            }
        bench.archers.clear();
        bench.archerAt = nullptr;
        }
      if(bench.pinned==n)
        bench.pinned = nullptr;
      if(bench.downNpc==n)
        bench.downNpc = nullptr;
      w.removeNpc(*n);
      }
    say(out);
    }
  else if(c=="flinch") {
    // flinch on|off: the upper body gives way to a blow (graphics/mesh/flinch)
    const std::string m = a.size()>1 ? a[1] : "";
    if(m=="on" || m=="off")
      Flinch::setEnabled(m=="on"); else
      say("unknown flinch command: " + line);
    }
  else if(c=="hit") {
    // hit <npc|player> <angle> [strength] [height] [twist]: a blow from that side of it (angle from its
    // facing, 0 in front, as place), only the flinch: no damage, no stumble
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "player");
    if(n==nullptr) {
      say("hit: no npc " + line);
      return;
      }
    const float an = (n->rotation()+num(a,2,0))*float(M_PI)/180.f;
    Flinch::Hit h;
    h.dir      = Vec3(-std::cos(an),0,-std::sin(an));
    h.strength = num(a,3,1);
    h.height   = num(a,4,0.6f);
    h.twist    = num(a,5,0);
    n->flinch(h);
    char buf[160];
    std::snprintf(buf,sizeof(buf),"hit %s from %.0f strength %.2f height %.2f twist %.2f",symName(w,n).c_str(),
                  double(num(a,2,0)),double(h.strength),double(h.height),double(h.twist));
    say(buf);
    }
  else if(c=="strike") {
    // strike <victim|player> [attacker|player]: the attacker's blow lands as the game deals it (melee: the
    // stumble, the damage, the flinch); the victim gets its hit points back below a third
    Npc* n  = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    Npc* by = findNpc(w,pl,a.size()>2 ? a[2] : "player");
    if(n==nullptr || by==nullptr || n==by) {
      say("strike: no npc " + line);
      return;
      }
    const int32_t hp0 = n->attribute(ATR_HITPOINTS);
    n->takeDamage(*by,nullptr);
    const int32_t hp1 = n->attribute(ATR_HITPOINTS);
    say("strike " + symName(w,n) + " by " + symName(w,by) + ": hp " + std::to_string(hp0) + " -> " + std::to_string(hp1) +
        " anim " + n->animNames());
    if(!n->isDown() && hp1*3<n->attribute(ATR_HITPOINTSMAX))
      n->changeAttribute(ATR_HITPOINTS,n->attribute(ATR_HITPOINTSMAX)-hp1,false);
    }
  else if(c=="aim") {
    // aim on|off: the upper body turns and bends to the target with a bow, a crossbow, a spell
    const std::string m = a.size()>1 ? a[1] : "";
    if(m=="on" || m=="off")
      Aim::setEnabled(m=="on"); else
      say("unknown aim command: " + line);
    }
  else if(c=="lean") {
    // lean on|off: the lean into turns and on slopes (graphics/mesh/lean)
    const std::string m = a.size()>1 ? a[1] : "";
    if(m=="on" || m=="off")
      Lean::setEnabled(m=="on"); else
      say("unknown lean command: " + line);
    }
  else if(c=="blend") {
    // blend on|off, blend param <min|scale|max> <value>: transitions between animations (AnimBlend)
    const std::string m = a.size()>1 ? a[1] : "";
    if(m=="on" || m=="off")
      AnimBlend::setEnabled(m=="on");
    else if(m=="param" && a.size()>3)
      say(std::string(AnimBlend::setParam(a[2],num(a,3,0)) ? "blend param " : "blend: unknown param ") + a[2] + " " + a[3]);
    else
      say("unknown blend command: " + line);
    }
  else if(c=="ik") {
    const std::string m = a.size()>1 ? a[1] : "";
    if(m=="on" || m=="off")
      FootIk::setEnabled(m=="on");
    else if(m=="param" && a.size()>3)
      say(std::string(FootIk::setParam(a[2],num(a,3,0)) ? "ik param " : "ik: unknown param ") + a[2] + " " + a[3]);
    else if(m=="params")
      say("ik params: " + FootIk::paramList());
    else
      say("unknown ik command: " + line);
    }
  else if(c=="foreach") {
    expandForeach(bench.pc-1,w,pl);
    }
  else if(c=="end") {
    }
  else if(c=="slopes" || c=="stairs") {
    for(auto& s:findSpots(w,pl,c=="stairs" ? K_Stairs : K_Slope,num(a,1,10),num(a,2,35),"",30)) {
      char buf[256];
      std::snprintf(buf,sizeof(buf),"%s %s angle=%.1f rough=%.1f dist=%.0f",c.c_str(),s.name.c_str(),s.angle,s.rough,s.dist);
      say(buf);
      }
    }
  else if(c=="ledges") {
    // ledges [min drop] [max drop] [count] [steep]: waypoints at an edge, open ground that much lower
    // 1.5..14 m away and seen from there (a target below); prints the waypoint, the yaw to face
    // (`face <yaw>`), the distance, the drop and the angle down; near first, or the steepest first
    const float minDrop = num(a,1,300), maxDrop = num(a,2,1000);
    const size_t count  = size_t(num(a,3,15));
    struct Ledge { std::string name; float yaw, dist, drop, down, from; };
    std::vector<Ledge> all;
    const Vec3 at = pl.position();
    w.findWayPoint(at,[&](const WayPoint& wp) {
      const float g0 = groundY(w,wp.pos.x,wp.pos.z,wp.pos.y);
      if(std::isnan(g0))
        return false;
      float an = 0, rough = 0;
      Vec3  dn;
      if(!slopeAt(w,Vec3(wp.pos.x,g0,wp.pos.z),an,dn,rough) || an>15)
        return false;
      if(w.physic()->ray({wp.pos.x,g0+200,wp.pos.z},{wp.pos.x,g0+1500,wp.pos.z}).hasCol)
        return false; // a roof above: indoors
      Ledge best;
      best.down = 0;
      for(int k=0; k<16; ++k) {
        const float y = float(k)*22.5f*float(M_PI)/180.f;
        for(float d:{150.f,250.f,400.f,550.f,700.f,1000.f,1400.f}) {
          const Vec3 p = Vec3(wp.pos.x+std::cos(y)*d, g0, wp.pos.z+std::sin(y)*d);
          auto r = w.physic()->ray({p.x,g0+200,p.z},{p.x,g0-maxDrop-100,p.z});
          if(!r.hasCol)
            continue;
          const float drop = g0-r.v.y;
          if(drop<minDrop || drop>maxDrop)
            continue;
          const Vec3 eye = Vec3(wp.pos.x,g0+170,wp.pos.z), tg = r.v+Vec3(0,60,0);
          auto los = w.physic()->ray(eye,tg);
          if(los.hasCol && (los.v-tg).length()>40)
            continue;
          const float down = std::atan2(drop,d)*180.f/float(M_PI);
          if(down>best.down)
            best = Ledge{wp.name, float(k)*22.5f, d, drop, down, (wp.pos-at).length()};
          }
        }
      if(best.down>0)
        all.push_back(best);
      return false;
      });
    if(a.size()>4 && a[4]=="steep")
      std::sort(all.begin(),all.end(),[](const Ledge& x, const Ledge& y) { return x.down>y.down; }); else
      std::sort(all.begin(),all.end(),[](const Ledge& x, const Ledge& y) { return x.from<y.from; });
    say("ledges " + std::to_string(all.size()));
    for(size_t i=0; i<all.size() && i<count; ++i) {
      char buf[256];
      std::snprintf(buf,sizeof(buf),"ledge %s yaw=%.1f dist=%.0f drop=%.0f down=%.0f from=%.0f",all[i].name.c_str(),
                    double(all[i].yaw),double(all[i].dist),double(all[i].drop),double(all[i].down),double(all[i].from));
      say(buf);
      }
    }
  else if(c=="corner") {
    // corner <left|right> [count] | corner <left|right> go <i>: spots at a corner of a wall (near waypoints):
    // a wall right in front of the head, open far ahead leaning out to that side (Aim::SideReach): where
    // the manual aim leans out round it. Lists them (nearest first) or puts the hero at the i-th, facing
    // along it
    const bool  right = a.size()>1 && a[1]=="right";
    const bool  go    = a.size()>2 && a[2]=="go";
    const size_t pick = size_t(num(a,3,0));
    const size_t count = size_t(num(a,2,10));
    struct Spot { std::string name; Vec3 at; float yaw, front, from; };
    std::vector<Spot> all;
    const Vec3 at0 = pl.position();
    w.findWayPoint(at0,[&](const WayPoint& wp) {
      if((wp.pos-at0).length()>8000.f)
        return false;
      for(int k=0; k<16; ++k) {
        const float y    = float(k)*22.5f*float(M_PI)/180.f;
        const Vec3  f    = Vec3(std::cos(y),0,std::sin(y));
        Vec3        sd   = Vec3::crossProduct(Vec3(0,1,0),f);
        sd = Vec3::normalize(sd)*(right ? 1.f : -1.f);
        for(float shift=-90; shift<=90; shift+=15) {
          const Vec3  p0 = Vec3(wp.pos.x,wp.pos.y+50,wp.pos.z);
          const Vec3  p1 = p0 + sd*shift;
          if(w.physic()->ray(p0,p1).hasCol)
            continue;
          const float g  = groundY(w,p1.x,p1.z,p1.y+50);
          if(std::isnan(g) || std::abs(g-wp.pos.y)>40)
            continue;
          const Vec3 head = Vec3(p1.x,g+148,p1.z);
          auto       fr   = w.physic()->ray(head,head+f*400.f);
          if(!fr.hasCol || (fr.v-head).length()<50.f)
            continue; // no wall in front, or right at the face
          const Vec3 out = head + sd*Aim::SideReach;
          if(w.physic()->ray(head,out+sd*15.f).hasCol)
            continue; // no room to lean out
          if(w.physic()->ray(out,out+f*600.f).hasCol)
            continue; // walled leaning out too
          if(w.physic()->ray(Vec3(p1.x,g+60,p1.z),Vec3(p1.x,g+60,p1.z)+f*60.f).hasCol)
            continue; // a low step in front: not a corner
          all.push_back(Spot{wp.name,Vec3(p1.x,g,p1.z),float(k)*22.5f,(fr.v-head).length(),(p1-at0).length()});
          break;
          }
        }
      return false;
      });
    std::sort(all.begin(),all.end(),[](const Spot& x, const Spot& y) { return x.from<y.from; });
    if(go) {
      if(pick>=all.size()) {
        say("corner: no spot " + std::to_string(pick));
        return;
        }
      auto& sp = all[pick];
      pl.setPosition(sp.at);
      face(pl,Vec3(std::cos(sp.yaw*float(M_PI)/180.f),0,std::sin(sp.yaw*float(M_PI)/180.f)));
      bench.hasPrev = false;
      char buf[200];
      std::snprintf(buf,sizeof(buf),"corner %s %zu: %s yaw=%.1f wall in front %.0f cm",right ? "right" : "left",pick,sp.name.c_str(),
                    double(sp.yaw),double(sp.front));
      say(buf);
      return;
      }
    say("corners " + std::to_string(all.size()));
    for(size_t i=0; i<all.size() && i<count; ++i) {
      char buf[200];
      std::snprintf(buf,sizeof(buf),"corner %zu %s yaw=%.1f front=%.0f from=%.0f",i,all[i].name.c_str(),double(all[i].yaw),
                    double(all[i].front),double(all[i].from));
      say(buf);
      }
    }
  else if(c=="walls") {
    // walls [count]: spots at a wall's edge (from a waypoint, walking to where the ground falls away) with
    // the foot of the wall 2.5..10 m below, 1 m out: hidden from the head, seen leaning over (the aim's
    // lean); prints the waypoint and the yaw for `goto`, `edge <yaw>`
    const size_t count = size_t(num(a,1,15));
    struct Wall { std::string name; float yaw, drop, from; };
    std::vector<Wall> all;
    const Vec3 at = pl.position();
    auto ground = [&](const Vec3& p, float y0, float up, float down) {
      auto r = w.physic()->ray({p.x,y0+up,p.z},{p.x,y0-down,p.z});
      return r.hasCol ? r.v.y : NAN;
      };
    w.findWayPoint(at,[&](const WayPoint& wp) {
      const float g0 = groundY(w,wp.pos.x,wp.pos.z,wp.pos.y);
      if(std::isnan(g0))
        return false;
      for(int k=0; k<16; ++k) {
        const float y   = float(k)*22.5f*float(M_PI)/180.f;
        const Vec3  dir = Vec3(std::cos(y),0,std::sin(y));
        Vec3 e = Vec3(wp.pos.x,g0,wp.pos.z);
        bool edge = false;
        for(float d=25; d<=600; d+=25) {
          const Vec3  p = Vec3(wp.pos.x,g0,wp.pos.z) + dir*d;
          const float g = ground(p,e.y,100,150);
          if(std::isnan(g) || std::abs(g-e.y)>60) {
            edge = true;
            break;
            }
          e = Vec3(p.x,g,p.z);
          }
        if(!edge)
          continue;
        const Vec3  st   = e - dir*35.f;
        const Vec3  t    = st + dir*135.f;
        const float tg   = ground(t,st.y,-60,1000);
        if(std::isnan(tg) || st.y-tg<250 || st.y-tg>1000)
          continue;
        const Vec3 head = st + Vec3(0,148,0); // drawing a bow the head is that low
        const Vec3 aimT = Vec3(t.x,tg+60,t.z);
        if(!w.physic()->ray(head,aimT).hasCol)
          continue; // seen from the head anyway
        const Vec3 eye = head + dir*Aim::LeanReach - Vec3(0,Aim::LeanDrop,0);
        if(w.physic()->ray(head,eye).hasCol || w.physic()->ray(eye,aimT).hasCol)
          continue;
        if(w.physic()->ray({t.x,tg+100,t.z},{t.x,tg+1500,t.z}).hasCol)
          continue; // under a roof, an arch
        all.push_back(Wall{wp.name, float(k)*22.5f, st.y-tg, (wp.pos-at).length()});
        break;
        }
      return false;
      });
    std::sort(all.begin(),all.end(),[](const Wall& x, const Wall& y) { return x.from<y.from; });
    say("walls " + std::to_string(all.size()));
    for(size_t i=0; i<all.size() && i<count; ++i) {
      char buf[256];
      std::snprintf(buf,sizeof(buf),"wall %s yaw=%.1f drop=%.0f from=%.0f",all[i].name.c_str(),double(all[i].yaw),
                    double(all[i].drop),double(all[i].from));
      say(buf);
      }
    }
  else if(c=="edge") {
    // edge <yaw> [max cm] [margin cm]: the hero is put that way to where the ground falls away, and stands
    // that far from the edge (35: closer the body slips over it) facing it; prints how deep it goes down
    // right past the edge
    const float y   = num(a,1,0)*float(M_PI)/180.f;
    const Vec3  dir = Vec3(std::cos(y),0,std::sin(y));
    const float max = num(a,2,2000);
    Vec3        pos = pl.position();
    const float g0  = pos.y;
    bool        found = false;
    for(float d=25; d<=max; d+=25) {
      const Vec3  p = pl.position() + dir*d;
      auto        r = w.physic()->ray({p.x,g0+100,p.z},{p.x,g0-150,p.z});
      if(!r.hasCol || std::abs(r.v.y-g0)>60) {
        found = true;
        break;
        }
      pos = Vec3(p.x,r.v.y,p.z);
      }
    if(!found) {
      say("edge: no edge that way");
      return;
      }
    // a step back from the very edge, the feet on the ground
    pos -= dir*num(a,3,35);
    pl.setPosition(pos);
    face(pl,dir);
    pl.updateTransform();
    const Vec3 past = pos + dir*80.f;
    auto       r    = w.physic()->ray({past.x,pos.y-60,past.z},{past.x,pos.y-3000,past.z});
    char buf[200];
    std::snprintf(buf,sizeof(buf),"edge at %.0f %.0f %.0f, down past it %.0f",pos.x,pos.y,pos.z,r.hasCol ? pos.y-r.v.y : -1.f);
    say(buf);
    }
  else if(c=="findwp") {
    for(auto& s:findSpots(w,pl,K_Name,0,90,rest(line),40)) {
      char buf[256];
      std::snprintf(buf,sizeof(buf),"wp %s angle=%.1f rough=%.1f dist=%.0f",s.name.c_str(),s.angle,s.rough,s.dist);
      say(buf);
      }
    }
  else if(c=="pos") {
    const Vec3 p = pl.position();
    char buf[256];
    std::snprintf(buf,sizeof(buf),"pos %.0f %.0f %.0f rot=%.1f bs=%u air=%d swim=%d slide=%d mob=%d guild=%u aligned=%d",p.x,p.y,p.z,pl.rotation(),
                  unsigned(pl.bodyStateMasked()),int(pl.isInAir()),int(pl.isSwim()),int(pl.isSlide()),int(pl.interactive()!=nullptr),
                  unsigned(pl.guild()),int(w.script().guildVal().surface_align[pl.guild()]));
    say(buf);
    }
  else if(c=="talk") {
    // talk <npc|nearest> <ms>: the npc and the hero talk to each other (no words): they look at each other
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    if(n==nullptr || n==&pl) {
      say("talk: no npc " + line);
      return;
      }
    const uint64_t ms = uint64_t(num(a,2,3000));
    n->setTalkPartner(&pl,ms);
    pl.setTalkPartner(n,ms);
    say("talk " + symName(w,n) + " " + std::to_string(ms));
    }
  else if(c=="speak") {
    // speak <npc|nearest|player> <output name|$SVM> [to]: it says a voiced line of the game, like AI_Output in a
    // script, or an SVM ($SMALLTALK01, like AI_OutputSvm). To another npc it turns to it first, as a line of
    // the AI said to an npc does (v15 say.to)
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    if(n==nullptr || a.size()<3) {
      say("speak: no npc or line " + line);
      return;
      }
    Npc* to = (n==&pl) ? &subjectOf(pl) : &pl;
    if(a.size()>3) {
      to = findNpc(w,pl,a[3]);
      if(to==nullptr || to==n) {
        say("speak: no one to say it to " + line);
        return;
        }
      if(to!=&pl)
        n->aiPush(AiQueue::aiTurnToNpc(to));
      }
    if(a[2].starts_with("$")) {
      std::string svm = a[2];
      for(auto& ch:svm)
        ch = char(std::toupper(uint8_t(ch)));
      n->aiPush(AiQueue::aiOutputSvm(*to,svm,0));
      } else {
      n->aiPush(AiQueue::aiOutput(*to,a[2],0));
      }
    say("speak " + nameUtf8(*n) + " " + a[2] + (a.size()>3 ? " -> " + symName(w,to) : ""));
    }
  else if(c=="faceanim") {
    // faceanim <npc|nearest|player> <anim> [ms] [intensity]: a face animation of the head (S_ANGRY, S_FRIENDLY...)
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    if(n==nullptr || a.size()<3) {
      say("faceanim: no npc or anim " + line);
      return;
      }
    n->startFaceAnim(a[2],num(a,4,1),uint64_t(num(a,3,3000)));
    say("faceanim " + nameUtf8(*n) + " " + a[2]);
    }
  else if(c=="ani") {
    // ani <npc|nearest|player> <anim>: it plays an animation of its model script (T_YES, T_DIALOGGESTURE_03)
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    if(n==nullptr || a.size()<3) {
      say("ani: no npc or anim " + line);
      return;
      }
    auto sq = n->playAnimByName(a[2],BS_STAND);
    say("ani " + symName(w,n) + " " + a[2] + (sq==nullptr ? " failed" : ""));
    }
  else if(c=="dialog") {
    // dialog <npc|nearest>: the hero talks to it, as with the action key (important infos start by themselves)
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    if(n==nullptr || n==&pl) {
      say("dialog: no npc " + line);
      return;
      }
    n->startDialog(pl);
    say("dialog " + symName(w,n));
    }
  else if(c=="choose") {
    // choose <n>: runs the n-th dialog choice (from 1, as listed) of the watched npc, as if the player picked it
    Npc&       n   = subjectOf(pl);
    auto       chs = w.script().dialogChoices(pl.handlePtr(),n.handlePtr(),{},false);
    std::sort(chs.begin(),chs.end(),[](const GameScript::DlgChoice& l, const GameScript::DlgChoice& r){ return l.sort<r.sort; });
    const size_t i = size_t(num(a,1,1));
    if(i<1 || i>chs.size()) {
      say("choose: no choice " + std::to_string(i) + " of " + std::to_string(chs.size()));
      return;
      }
    say("choose " + std::to_string(i) + " of " + std::to_string(chs.size()));
    Gothic::inst().dialogExec(chs[i-1],pl,n);
    }
  else if(c=="lips") {
    const std::string m = a.size()>1 ? a[1] : "";
    if(m=="on" || m=="off")
      LipSync::setEnabled(m=="on");
    else if(m=="param" && a.size()>3)
      say(std::string(LipSync::setParam(a[2],num(a,3,0)) ? "lips param " : "lips: unknown param ") + a[2] + " " + a[3]);
    else if(m=="params")
      say("lips params: " + LipSync::paramList());
    else
      say("unknown lips command: " + line);
    }
  else if(c=="gestures") {
    // gestures on|off|param <name> <value>|params: the hands while one speaks (off: a random gesture per line)
    const std::string m = a.size()>1 ? a[1] : "";
    if(m=="on" || m=="off")
      TalkGestures::setEnabled(m=="on");
    else if(m=="param" && a.size()>3)
      say(std::string(TalkGestures::setParam(a[2],num(a,3,0)) ? "gestures param " : "gestures: unknown param ") + a[2] + " " + a[3]);
    else if(m=="params")
      say("gestures params: " + TalkGestures::paramList());
    else
      say("unknown gestures command: " + line);
    }
  else if(c=="hear") {
    // hear <npc|nearest> <ms> [every ms]: the hero speaks to it that long, his words come every so often
    // (as the bridge does with his voice: it listens, nods now and then)
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    if(n==nullptr || n==&pl) {
      say("hear: no npc " + line);
      return;
      }
    bench.hearNpc   = n;
    bench.hearUntil = bench.time + uint64_t(num(a,2,4000));
    bench.hearEvery = uint64_t(std::max(100.f,num(a,3,600)));
    bench.hearNext  = bench.time;
    say("hear " + symName(w,n));
    }
  else if(c=="think") {
    // think <npc|nearest> <ms>: it thinks of an answer that long (as on the AI server's `thinking`)
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    if(n==nullptr || n==&pl) {
      say("think: no npc " + line);
      return;
      }
    bench.thinkNpc   = n;
    bench.thinkUntil = bench.time + uint64_t(num(a,2,2500));
    say("think " + symName(w,n));
    }
  else if(c=="blink") {
    // blink on|off|param <name> <value>|params: blinking (off: nobody blinks, as OpenGothic was)
    const std::string m = a.size()>1 ? a[1] : "";
    if(m=="on" || m=="off")
      EyeBlink::setEnabled(m=="on");
    else if(m=="param" && a.size()>3)
      say(std::string(EyeBlink::setParam(a[2],num(a,3,0)) ? "blink param " : "blink: unknown param ") + a[2] + " " + a[3]);
    else if(m=="params")
      say("blink params: " + EyeBlink::paramList());
    else
      say("unknown blink command: " + line);
    }
  else if(c=="watch") {
    // watch <npc|nearest|player>: the camera and the gaze metrics follow this one
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "player");
    bench.subject  = (n==&pl) ? nullptr : n;
    Gaze::probeNpc = bench.subject;
    LipSync::probeNpc     = n;
    LipSync::subjectProbe = LipSync::Probe();
    TalkGestures::probeNpc     = n;
    TalkGestures::subjectProbe = TalkGestures::Probe();
    EyeBlink::probeNpc         = n;
    EyeBlink::subjectProbe     = EyeBlink::Probe();
    Lean::probeNpc = bench.subject;
    Pose::probeNpc = bench.subject;
    bench.anim1.clear();
    bench.anim2.clear();
    Aim::probeNpc  = bench.subject;
    ArcherAi::probeNpc     = bench.subject;
    ArcherAi::subjectProbe = ArcherAi::Probe();
    Flinch::probeNpc = bench.subject;
    char buf[160] = "watch nobody";
    if(n!=nullptr)
      if(auto sym = w.script().findSymbol(size_t(n->instanceSymbol())))
        std::snprintf(buf,sizeof(buf),"watch %s dist=%.0f",sym->name().c_str(),double((n->position()-pl.position()).length()));
    say(buf);
    }
  else if(c=="gazeinfo") {
    Npc&         s = subjectOf(pl);
    const auto&  g = &s==&pl ? Gaze::playerProbe : Gaze::subjectProbe;
    Vec3         at;
    const auto   src = s.gazeTarget(at);
    char buf[300];
    std::snprintf(buf,sizeof(buf),"gazeinfo %s source=%s partner=%s script=%s attention=%s probe valid=%d active=%d w=%.2f err=%.1f hero focus=%s",
                  symName(w,&s).c_str(),gazeSource(src),symName(w,s.talkPartner()).c_str(),symName(w,s.lookAtTarget()).c_str(),
                  symName(w,s.attentionTarget()).c_str(),int(g.valid),int(g.active),double(g.weight),double(g.error),
                  symName(w,pl.target()).c_str());
    say(buf);
    }
  else if(c=="nearby") {
    // nearby [radius]: the npcs around the hero, where they look and why
    const float r = num(a,1,1500);
    std::vector<std::pair<float,Npc*>> list;
    w.detectNpc(pl.position(),r,[&](Npc& n) {
      if(&n!=&pl)
        list.emplace_back((n.position()-pl.position()).length(),&n);
      });
    std::sort(list.begin(),list.end(),[](auto& x, auto& y) { return x.first<y.first; });
    say("nearby " + std::to_string(list.size()));
    for(auto& [d,n]:list) {
      const Vec3  dp  = pl.position()-n->position();
      const float ang = std::remainder(std::atan2(dp.z,dp.x)*180.f/float(M_PI) - n->rotation(), 360.f);
      Vec3 at;
      const auto src = n->gazeTarget(at);
      char buf[300];
      std::snprintf(buf,sizeof(buf),"  %-28s dist=%5.0f hero_at=%5.0f bs=%2u mob=%d look=%s at=%s",
                    symName(w,n).c_str(),double(d),double(ang),unsigned(n->bodyStateMasked() & BS_MAX),
                    int(n->interactive()!=nullptr),gazeSource(src),
                    symName(w,src==Gaze::Source::Talk ? n->talkPartner() : src==Gaze::Source::Script ? n->lookAtTarget() :
                                src==Gaze::Source::Attention ? n->attentionTarget() : nullptr).c_str());
      say(buf);
      }
    }
  else if(c=="state") {
    // state <npc> <ZS_*> <waypoint> [minutes 60]: it does that now at that place, as a routine entry would
    // (TA_*), for that many game minutes; its routine goes on afterwards (npc_act start_activity)
    Npc* n = a.size()>1 ? findNpc(w,pl,a[1]) : nullptr;
    std::string name = a.size()>2 ? a[2] : "";
    for(auto& ch:name)
      ch = char(std::toupper(uint8_t(ch)));
    const size_t fn = name.starts_with("ZS_") ? w.script().findSymbolIndex(name) : size_t(-1);
    auto         wp = a.size()>3 ? w.findPoint(a[3],false) : nullptr;
    if(n==nullptr || n==&pl || fn==size_t(-1) || wp==nullptr) {
      say("state: no npc, state or waypoint " + line);
      return;
      }
    auto until = w.time();
    until.addMilis(uint64_t(std::clamp(num(a,4,60),1.f,24*60.f))*60*1000);
    const bool ok = n->startState(ScriptFn(fn),wp->name,until,false);
    say("state " + symName(w,n) + " " + name + " " + wp->name + (ok ? "" : " failed"));
    }
  else if(c=="come") {
    // come <npc>: it walks up to the hero and turns to him (npc_act go_to_npc): a free one goes back to its
    // routine afterwards
    Npc* n = a.size()>1 ? findNpc(w,pl,a[1]) : nullptr;
    if(n==nullptr || n==&pl) {
      say("come: no npc " + line);
      return;
      }
    n->aiPush(AiQueue::aiGoToNpc(&pl));
    n->aiPush(AiQueue::aiTurnToNpc(&pl));
    char buf[160];
    std::snprintf(buf,sizeof(buf),"come %s dist=%.0f",symName(w,n).c_str(),double((n->position()-pl.position()).length()));
    say(buf);
    }
  else if(c=="pair") {
    // pair <a> <b>: how two npcs stand to each other: the distance; for each, where it faces (0: right at the
    // other), where it looks and why, its state and for how long (ms), its body state, a mob it uses, how much
    // of its AI runs
    Npc* x = a.size()>1 ? findNpc(w,pl,a[1]) : nullptr;
    Npc* y = a.size()>2 ? findNpc(w,pl,a[2]) : nullptr;
    if(x==nullptr || y==nullptr || x==y) {
      say("pair: no npcs " + line);
      return;
      }
    auto one = [&](Npc& n, Npc& o) {
      const Vec3  dp  = o.position()-n.position();
      const float ang = std::remainder(std::atan2(dp.z,dp.x)*180.f/float(M_PI) - n.rotation(), 360.f);
      Vec3        at;
      const auto  src = n.gazeTarget(at);
      char buf[400];
      std::snprintf(buf,sizeof(buf),"%s at_other=%.0f look=%s at=%s state=%s t=%llu bs=%u mob=%d ai=%s",
                    symName(w,&n).c_str(),double(ang),gazeSource(src),gazeWho(w,n,src).c_str(),stateName(w,n).c_str(),
                    (unsigned long long)n.stateTime(),unsigned(n.bodyStateMasked() & BS_MAX),
                    int(n.interactive()!=nullptr),policyName(n.processPolicy()));
      return std::string(buf);
      };
    char head[64];
    std::snprintf(head,sizeof(head),"pair dist=%.0f | ",double((x->position()-y->position()).length()));
    say(head + one(*x,*y) + " | " + one(*y,*x));
    }
  else if(c=="where") {
    // where <npc> [waypoint]: where it is, how far from the hero (and from the waypoint), its state, whether
    // it is busy with its queue (walking there), how much of its AI runs
    Npc* n = a.size()>1 ? findNpc(w,pl,a[1]) : nullptr;
    if(n==nullptr) {
      say("where: no npc " + line);
      return;
      }
    const Vec3 p = n->position();
    char buf[400];
    int  len = std::snprintf(buf,sizeof(buf),"where %s at %.0f %.0f %.0f hero=%.0f",symName(w,n).c_str(),
                             double(p.x),double(p.y),double(p.z),double((p-pl.position()).length()));
    if(a.size()>2 && len>0 && size_t(len)<sizeof(buf)) {
      if(auto wp = w.findPoint(a[2],false))
        len += std::snprintf(buf+len,sizeof(buf)-size_t(len)," %s=%.0f",wp->name.c_str(),double((p-wp->position()).length())); else
        len += std::snprintf(buf+len,sizeof(buf)-size_t(len)," %s=?",a[2].c_str());
      }
    if(len>0 && size_t(len)<sizeof(buf))
      std::snprintf(buf+len,sizeof(buf)-size_t(len)," state=%s t=%llu queue=%d bs=%u ai=%s",stateName(w,*n).c_str(),
                    (unsigned long long)n->stateTime(),int(!n->isAiQueueEmpty()),unsigned(n->bodyStateMasked() & BS_MAX),
                    policyName(n->processPolicy()));
    say(buf);
    }
  else if(c=="lookat") {
    // lookat <npc|nearest> <npc|player|off>: as the script AI_LookAtNpc, AI_StopLookAt
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    if(n==nullptr || n==&pl) {
      say("lookat: no npc " + line);
      return;
      }
    const std::string t = a.size()>2 ? a[2] : "player";
    if(t=="off") {
      n->aiPush(AiQueue::aiStopLookAt());
      say("lookat " + symName(w,n) + " off");
      return;
      }
    Npc* o = findNpc(w,pl,t);
    if(o==nullptr || o==n) {
      say("lookat: no target " + line);
      return;
      }
    n->aiPush(AiQueue::aiLookAtNpc(o));
    say("lookat " + symName(w,n) + " " + symName(w,o));
    }
  else if(c=="freeze") {
    // freeze <npc|nearest> [off]: it leaves what it does and stands still, like a body the AI server attaches
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "nearest");
    if(n==nullptr || n==&pl) {
      say("freeze: no npc " + line);
      return;
      }
    if(a.size()>2 && a[2]=="off") {
      n->setExternalControl(false);
      say("freeze off");
      return;
      }
    n->setInteraction(nullptr,true);
    n->clearAiQueue();
    n->clearState(false);
    n->setExternalControl(true);
    say("freeze " + symName(w,n));
    }
  else if(c=="place") {
    // place <dist> <angle>: the hero stands that far from the watched npc, at that angle from its facing
    bench.placeDist  = num(a,1,150);
    bench.placeAngle = num(a,2,0);
    placeHero(w,pl);
    bench.hasPrev = false;
    }
  else if(c=="orbit") {
    // orbit <deg> <ms>: the hero goes around the watched npc
    const float ms = std::max(1.f,num(a,2,3000));
    bench.orbitSpeed = num(a,1,90)*1000.f/ms;
    bench.orbitUntil = bench.time + uint64_t(ms);
    }
  else if(c=="transform") {
    // the player becomes a creature, like the shape change spell; "transform back"
    const std::string name = a.size()>1 ? a[1] : "";
    if(name=="back") {
      pl.transformBack();
      say("transform back");
      return;
      }
    const size_t id = w.script().findSymbolIndex(name);
    const bool   ok = id!=size_t(-1) && pl.transformInto(id);
    bench.hasPrev = false;
    say(std::string(ok ? "transform " : "transform failed: ") + name);
    }
  else if(c=="bones") {
    // skeleton of the player: index, parent, name
    auto sk = FootIk::playerProbe.skeleton;
    if(sk==nullptr) {
      say("bones: no skeleton");
      return;
      }
    say("bones: " + std::to_string(sk->nodes.size()) + " nodes, foot ik " + (FootIk::playerProbe.valid ? "valid" : "not valid"));
    for(size_t i=0; i<sk->nodes.size(); ++i) {
      char buf[256];
      const auto& t = sk->tr[i];
      std::snprintf(buf,sizeof(buf),"bone %zu parent %d %s at %.0f %.0f %.0f",i,int(sk->nodes[i].parent==size_t(-1) ? -1 : int(sk->nodes[i].parent)),
                    sk->nodes[i].name.c_str(),t.at(3,0),t.at(3,1),t.at(3,2));
      say(buf);
      }
    }
  else if(c=="ptt") {
    // ptt <wav> [text]: the hero holds the talk key while the recording is said; the AI server takes the
    // file instead of the microphone (--voice-files), the npc's hear it through speech recognition.
    // The text only goes into events.jsonl (subtitles)
    const std::string wav = a.size()>1 ? a[1] : "";
    const uint64_t    ms  = wavMs(wav);
    if(ms==0) {
      say("ptt: not a 16-bit wav: " + wav);
      return;
      }
    std::string reply;
    if(!aiCommand("ai ptt start " + wav,reply)) {
      say("ptt: no AI bridge");
      return;
      }
    const std::string text = rest(rest(line));
    bench.speaking   = true;
    bench.speakUntil = bench.time + ms + SpeakTailMs;
    event("hero","\"wav\":" + jsonStr(wav) + ",\"ms\":" + std::to_string(ms) + ",\"text\":" + jsonStr(text));
    say("ptt " + std::to_string(ms) + " ms: " + text + " (" + reply + ")");
    }
  else if(c=="waitreply" || c=="waitquiet") {
    // waitreply [timeout ms 30000] [quiet ms 1200]: until an npc of the AI has answered the hero's last
    // phrase and has been quiet that long; waitquiet [quiet ms 1200] [timeout ms 30000]: only the quiet
    const bool reply = c=="waitreply";
    bench.waitReply  = true;
    bench.replyNeed  = reply;
    bench.replyFrom  = bench.heroEndAt>0 ? bench.heroEndAt : bench.time;
    bench.replyEnd   = bench.time + uint64_t(num(a,reply ? 1 : 2,30000));
    bench.replyQuiet = uint64_t(num(a,reply ? 2 : 1,1200));
    }
  else if(c=="waitai") {
    // waitai <key> <stage> [timeout ms 20000]: until a thing of the AI bridge has got that far (waitai necro risen)
    if(a.size()<3) {
      say("waitai: <key> <stage> [timeout]");
      return;
      }
    bench.waitAiKey   = a[1];
    bench.waitAiStage = a[2];
    bench.waitAiEnd   = bench.time + uint64_t(num(a,3,20000));
    }
  else if(c=="caption") {
    // text on the screen of a short from here (scripts/shorts cut): caption <text>, caption off
    event("caption","\"text\":" + jsonStr(rest(line)=="off" ? "" : rest(line)));
    }
  else if(c=="mark") {
    event("mark","\"text\":" + jsonStr(rest(line)));
    }
  else if(c=="ai") {
    std::string reply;
    say(std::string(aiCommand(line,reply) ? "" : "no AI bridge: ") + line + " -> " + reply);
    }
  else if(c=="quit") {
    bench.pc = bench.prog.size();
    }
  else {
    const bool ok = marvin().exec(line);
    say(std::string(ok ? "marvin: " : "marvin failed: ") + line);
    }
  }

void tickVolley(PlayerControl& ctrl) {
  const auto& mp = Archer::playerProbe;
  const auto  t  = bench.time - bench.volleyT;
  auto stop = [&](const char* why) {
    say(std::string("volley: ") + why);
    release(ctrl,KeyCodec::ActionGeneric);
    bench.volleyLeft = 0;
    };
  switch(bench.volleyPhase) {
    case 0:
      press(ctrl,KeyCodec::ActionGeneric);
      bench.volleyPhase = 1;
      bench.volleyT     = bench.time;
      break;
    case 1: {
      const bool drawn = mp.valid && (mp.stage==Archer::Held || (mp.stage==Archer::Drawing && mp.draw>=bench.volleyDraw));
      if(drawn) {
        bench.volleyPhase   = 2;
        bench.volleyT       = bench.time;
        bench.volleyHoldNow = bench.volleyHold;
        if(bench.volleyJitter>0) {
          bench.lcg = bench.lcg*1664525u + 1013904223u;
          bench.volleyHoldNow += (bench.lcg>>8)%(bench.volleyJitter+1);
          }
        }
      else if(t>10000)
        stop("the string is not drawn");
      break;
      }
    case 2:
      if(t>=bench.volleyHoldNow) {
        if(bench.aimNpc!=nullptr) {
          // where it aims, the middle of the zone: the log compares the arrow's flight with it
          Vec3 c = bench.aimNpc->collosionCenter();
          bench.aimNpc->hitZoneCenter(bench.aimZone,c);
          char buf[160];
          std::snprintf(buf,sizeof(buf),"aim zone %.1f %.1f %.1f",c.x,c.y,c.z);
          TestBench::note(buf);
          }
        release(ctrl,KeyCodec::ActionGeneric);
        bench.volleyPhase = 3;
        bench.volleyT     = bench.time;
        }
      break;
    case 3:
      if(mp.valid && mp.stage==Archer::Nocked && t>200) {
        --bench.volleyLeft;
        bench.volleyPhase = 0;
        }
      else if(t>8000)
        stop("no next arrow");
      break;
    }
  }

// the hero's phrase is said: the talk key goes up
void endSpeaking() {
  std::string reply;
  aiCommand("ai ptt stop",reply);
  bench.speaking  = false;
  bench.heroEndAt = bench.time;
  event("hero_end");
  }

bool waitingForReply() {
  if(!bench.waitReply)
    return false;
  const bool replied = !bench.replyNeed || bench.lastLineAt>bench.replyFrom;
  const bool quiet   = bench.time>=bench.busyAt+bench.replyQuiet;
  if(replied && quiet) {
    bench.waitReply = false;
    return false;
    }
  if(bench.time<bench.replyEnd)
    return true;
  say(std::string(bench.replyNeed ? "waitreply" : "waitquiet") + ": timeout" + (replied ? "" : ", nobody answered"));
  event("timeout");
  bench.waitReply = false;
  return false;
  }

bool waitingForAi() {
  if(bench.waitAiKey.empty())
    return false;
  if(TestBench::aiReached && TestBench::aiReached(bench.waitAiKey,bench.waitAiStage)) {
    say("waitai " + bench.waitAiKey + " " + bench.waitAiStage);
    bench.waitAiKey.clear();
    return false;
    }
  if(bench.time<bench.waitAiEnd)
    return true;
  say("waitai " + bench.waitAiKey + " " + bench.waitAiStage + ": timeout");
  bench.waitAiKey.clear();
  return false;
  }

void run(World& w, Npc& pl, PlayerControl& ctrl) {
  if(bench.volleyLeft>0) {
    tickVolley(ctrl);
    if(bench.volleyLeft>0)
      return;
    }
  while(bench.pc<bench.prog.size() || bench.speaking || bench.waitReply || !bench.waitAiKey.empty() ||
        bench.downNpc!=nullptr) {
    if(bench.speaking) {
      if(bench.time<bench.speakUntil)
        return;
      endSpeaking();
      }
    if(waitingForReply() || waitingForAi())
      return;
    if(bench.downNpc!=nullptr) {
      if(!bench.downNpc->isDown() && bench.time<bench.downEnd)
        return;
      char buf[160];
      if(bench.downNpc->isDown())
        std::snprintf(buf,sizeof(buf),"fight over: %s down after %llu",symName(w,bench.downNpc).c_str(),
                      (unsigned long long)(bench.time-bench.downFrom)); else
        std::snprintf(buf,sizeof(buf),"waitdown: timeout, %s hp %d",symName(w,bench.downNpc).c_str(),
                      bench.downNpc->attribute(ATR_HITPOINTS));
      TestBench::note(buf);
      bench.downNpc = nullptr;
      }
    if(bench.pc>=bench.prog.size())
      break;
    if(bench.time<bench.waitUntil || bench.volleyLeft>0)
      return;
    if(bench.waitArrive) {
      if(!pl.isAiQueueEmpty() && bench.time<bench.arriveEnd)
        return;
      if(!pl.isAiQueueEmpty())
        say("waitarrive: timeout");
      bench.waitArrive = false;
      }
    const std::string line = bench.prog[bench.pc++];
    exec(line,w,pl,ctrl);
    }
  if(bench.time>=bench.waitUntil && !bench.waitArrive && bench.volleyLeft==0 && bench.downNpc==nullptr)
    finish();
  }

void drawLeg(Canvas& cv, const Matrix4x4& vp, const FootIk::Probe::Leg& l, Rgba color) {
  auto proj = [&](const Vec3& p, float& x, float& y) {
    float px = p.x, py = p.y, pz = p.z, pw = 1;
    vp.project(px,py,pz,pw);
    if(pw<=1e-3f)
      return false;
    x = (0.5f*px/pw+0.5f)*float(cv.w);
    y = (0.5f*py/pw+0.5f)*float(cv.h);
    return true;
    };
  auto seg = [&](const Vec3& a, const Vec3& b, int r, Rgba c) {
    float x0,y0,x1,y1;
    if(proj(a,x0,y0) && proj(b,x1,y1))
      cv.line(x0,y0,x1,y1,r,c);
    };
  auto pt = [&](const Vec3& a, int r, Rgba c) {
    float x,y;
    if(proj(a,x,y))
      cv.dot(x,y,r,c);
    };

  // the animation before IK: thin white
  for(size_t k=0; k<3; ++k)
    seg(l.anim[k],l.anim[k+1],0,White);
  // after IK: thick, in the leg color
  for(size_t k=0; k<3; ++k)
    seg(l.final[k],l.final[k+1],1,color);
  for(size_t k=1; k<4; ++k)
    pt(l.final[k],2,color);

  // ground under the ankle and the toes: green under a planted foot, yellow under a swinging one
  const Rgba g = l.plant>0.5f ? Plant : Swing;
  if(l.hasCol) {
    const Vec3 at = {l.anim[2].x, l.groundY, l.anim[2].z};
    seg(at-Vec3(8,0,0),at+Vec3(8,0,0),0,g);
    seg(at-Vec3(0,0,8),at+Vec3(0,0,8),0,g);
    seg(at,at+l.n*15.f,0,g);
    }
  if(l.toeCol)
    pt({l.anim[3].x, l.toeY, l.anim[3].z},1,g);
  }

// stretches the brightness so that the brightest 2% of the image are near white
void autoExposure(Canvas& cv) {
  uint32_t hist[256] = {};
  uint32_t n = 0;
  for(int y=0; y<cv.h; y+=4)
    for(int x=0; x<cv.w; x+=4) {
      const uint8_t* p = cv.px + (size_t(y)*size_t(cv.w) + size_t(x))*4;
      hist[(p[0]*54 + p[1]*183 + p[2]*19) >> 8]++;
      ++n;
      }
  uint32_t acc = 0;
  int      top = 255;
  for(; top>0; --top) {
    acc += hist[top];
    if(acc>n/50)
      break;
    }
  const float gain = std::clamp(230.f/float(std::max(top,1)), 1.f, 6.f);
  if(gain<1.05f)
    return;
  uint8_t lut[256];
  for(int i=0; i<256; ++i)
    lut[i] = uint8_t(std::min(255.f, 255.f*std::pow(std::min(1.f, float(i)*gain/255.f), 0.85f)));
  const size_t size = size_t(cv.w)*size_t(cv.h)*4;
  for(size_t i=0; i<size; i+=4) {
    cv.px[i+0] = lut[cv.px[i+0]];
    cv.px[i+1] = lut[cv.px[i+1]];
    cv.px[i+2] = lut[cv.px[i+2]];
    }
  }

// hit zones of an npc (showzones): capsule outlines, the head red, the trunk yellow, the limbs blue
void drawZones(Canvas& cv, Camera* cam) {
  if(cam!=nullptr && !bench.zonesNpc.empty()) {
    auto* w = Gothic::inst().world();
    Npc*  n = bench.zonesNpc=="player" ? Gothic::inst().player() : (w!=nullptr ? npcBySymbol(*w,bench.zonesNpc) : nullptr);
    if(n!=nullptr) {
      const Matrix4x4 vp = cam->viewProj();
      auto proj = [&](const Vec3& p, float& x, float& y) {
        float px = p.x, py = p.y, pz = p.z, pw = 1;
        vp.project(px,py,pz,pw);
        if(pw<=1e-3f)
          return false;
        x = (0.5f*px/pw+0.5f)*float(cv.w);
        y = (0.5f*py/pw+0.5f)*float(cv.h);
        return true;
        };
      std::vector<HitZones::Posed> caps;
      n->hitZonesPosed(caps);
      for(auto& c:caps) {
        const Rgba col = c.zone==HitZones::Head ? Rgba{255,60,60,230} : (c.zone==HitZones::Body ? Rgba{255,220,40,170} : Rgba{60,190,255,170});
        float ax,ay,bx,by,ux,uy;
        if(!proj(c.a,ax,ay) || !proj(c.b,bx,by) || !proj(c.a+Vec3(0,c.r,0),ux,uy))
          continue;
        const float r  = std::hypot(ux-ax,uy-ay);
        float       vx = bx-ax, vy = by-ay;
        const float l  = std::hypot(vx,vy);
        for(int i=0; i<32; ++i) {
          const float t0 = float(i)*float(M_PI)/16.f, t1 = float(i+1)*float(M_PI)/16.f;
          cv.line(ax+r*std::cos(t0),ay+r*std::sin(t0),ax+r*std::cos(t1),ay+r*std::sin(t1),0,col);
          if(l>0.5f)
            cv.line(bx+r*std::cos(t0),by+r*std::sin(t0),bx+r*std::cos(t1),by+r*std::sin(t1),0,col);
          }
        if(l>0.5f) {
          vx /= l;
          vy /= l;
          cv.line(ax-vy*r,ay+vx*r,bx-vy*r,by+vx*r,0,col);
          cv.line(ax+vy*r,ay-vx*r,bx+vy*r,by-vx*r,0,col);
          }
        }
      }
    }
  }

void drawOverlay(Pixmap& pm) {
  Canvas cv;
  cv.px = reinterpret_cast<uint8_t*>(pm.data());
  cv.w  = int(pm.w());
  cv.h  = int(pm.h());
  if(cv.h>0)
    bench.aspect = float(cv.w)/float(cv.h);
  auto  cam = Gothic::inst().camera();
  auto& p   = FootIk::playerProbe;
  if(bench.bright)
    autoExposure(cv);
  if(bench.zonesOnly) {
    drawZones(cv,cam);
    return;
    }

  float zx = -1, zy = -1;
  if(cam!=nullptr && p.valid) {
    const Matrix4x4 vp = cam->viewProj();
    for(size_t i=0; i<p.numLegs; ++i)
      drawLeg(cv,vp,p.leg[i],LegColor[i]);
    // zoom center: between the feet
    Vec3 m = {};
    for(size_t i=0; i<p.numLegs; ++i)
      m += p.leg[i].final[2]*(1.f/float(p.numLegs));
    float x = m.x, y = m.y, z = m.z, ww = 1;
    vp.project(x,y,z,ww);
    if(ww>1e-3f) {
      zx = (0.5f*x/ww+0.5f)*float(cv.w);
      zy = (0.5f*y/ww+0.5f)*float(cv.h);
      }
    }

  // gaze: where the face looks (yellow) and where the partner is (red)
  const auto& g = bench.subject!=nullptr ? Gaze::subjectProbe : Gaze::playerProbe;
  if(cam!=nullptr && g.valid && g.active) {
    const Matrix4x4 vp = cam->viewProj();
    auto proj = [&](const Vec3& p, float& x, float& y) {
      float px = p.x, py = p.y, pz = p.z, pw = 1;
      vp.project(px,py,pz,pw);
      if(pw<=1e-3f)
        return false;
      x = (0.5f*px/pw+0.5f)*float(cv.w);
      y = (0.5f*py/pw+0.5f)*float(cv.h);
      return true;
      };
    float x0,y0,x1,y1,x2,y2;
    const bool h = proj(g.head,x0,y0);
    if(h && proj(g.head+g.facing*60.f,x1,y1))
      cv.line(x0,y0,x1,y1,1,{255,230,40,255});
    if(h && proj(g.target,x2,y2)) {
      cv.line(x0,y0,x2,y2,0,{255,80,80,140});
      cv.dot(x2,y2,3,Red);
      }
    }

  drawZones(cv,cam);

  // the manual aim: the crosshair as the game draws it in its interface (not captured): a chevron at where
  // it aims, brackets as far apart as the arrow may go off; a dot where the arrow points now (debugging)
  auto  hero = Gothic::inst().player();
  Vec3  cross;
  float spread = 0;
  if(cam!=nullptr && hero!=nullptr && bench.ctrl!=nullptr && bench.ctrl->crosshair(cross,spread)) {
    const Matrix4x4 vp = cam->viewProj();
    auto proj = [&](const Vec3& p, float& x, float& y) {
      float px = p.x, py = p.y, pz = p.z, pw = 1;
      vp.project(px,py,pz,pw);
      if(pw<=1e-3f)
        return false;
      x = (0.5f*px/pw+0.5f)*float(cv.w);
      y = (0.5f*py/pw+0.5f)*float(cv.h);
      return true;
      };
    Vec3  pos, dir;
    float x, y;
    if(hero->arrowRay(pos,dir)) {
      auto r = hero->world().physic()->rayArrow(pos,pos+dir*10000.f,[hero](Npc& n, const Vec3& a, const Vec3& b, float& t) {
        return &n!=hero && n.rayHitZone(a,b,t)!=HitZones::None;
        });
      if(proj(r.hasCol ? r.v : pos+dir*10000.f,x,y))
        cv.dot(x,y,1,{255,120,120,200});
      }
    const bool centered = bench.ctrl->isCrosshairCentered();
    if(centered) {
      x = 0.5f*float(cv.w);
      y = 0.5f*float(cv.h);
      }
    if(centered || proj(cross,x,y)) {
      const float r = Crosshair::radius(spread,cam->fovY(),float(cv.h),bench.uiScale);
      for(auto& l:Crosshair::shape(x,y,r,bench.uiScale))
        cv.stroke(l.pt,l.alpha,Crosshair::pen(bench.uiScale));
      }
    }

  // zoomed copy of the feet in the bottom right corner
  if(bench.zoom && zx>=0 && zy>=0 && zx<float(cv.w) && zy<float(cv.h)) {
    const int sc = 2;
    const int sw = cv.w/5, sh = cv.h/5;
    const int sx = std::clamp(int(zx)-sw/2, 0, cv.w-sw);
    const int sy = std::clamp(int(zy)-sh/2, 0, cv.h-sh);
    std::vector<uint8_t> src(size_t(sw*sh*4));
    for(int j=0; j<sh; ++j)
      std::memcpy(&src[size_t(j*sw*4)], cv.px + (size_t(sy+j)*size_t(cv.w) + size_t(sx))*4, size_t(sw*4));
    const int dx = cv.w - sw*sc - 4, dy = cv.h - sh*sc - 4;
    cv.rect(dx-2,dy-2,sw*sc+4,sh*sc+4,{255,255,255,255});
    for(int j=0; j<sh*sc; ++j) {
      uint8_t*       row = cv.px + (size_t(dy+j)*size_t(cv.w) + size_t(dx))*4;
      const uint8_t* s   = &src[size_t((j/sc)*sw*4)];
      for(int i=0; i<sw*sc; ++i)
        std::memcpy(row+i*4, s+(i/sc)*4, 4);
      }
    }

  // text
  char buf[160];
  std::snprintf(buf,sizeof(buf),"F%llu T%.2f",(unsigned long long)bench.recFrame,double(bench.time-bench.recStart)/1000.0);
  cv.text(6,6,buf,3,Text);
  cv.text(6,30,bench.recName,2,Text);
  if(!bench.label.empty())
    cv.text(6,46,bench.label,2,Text);

  int y = 6;
  const int x = cv.w - 4*2*24 - 6;
  if(p.numLegs>2)
    std::snprintf(buf,sizeof(buf),"IK %s W%.2f P%.1f C%.1f",p.enable ? "ON" : "OFF",double(p.weight),double(p.pelvis),double(p.chest)); else
    std::snprintf(buf,sizeof(buf),"IK %s W%.2f P%.1f",p.enable ? "ON" : "OFF",double(p.weight),double(p.pelvis));
  cv.text(x,y,buf,2,Text);
  y += 16;
  for(size_t i=0; i<p.numLegs; ++i) {
    auto& l = p.leg[i];
    std::snprintf(buf,sizeof(buf),"%s %s E%.1f K%.0f",LegName[i],l.plant>0.5f ? "PLANT" : "SWING",double(l.err),double(l.kneeDev));
    cv.text(x,y,buf,2,LegColor[i]);
    y += 16;
    }
  if(g.valid && (g.active || bench.subject!=nullptr)) {
    std::snprintf(buf,sizeof(buf),"GAZE W%.2f E%.0f Y%.0f P%.0f",double(g.weight),double(g.error),double(g.yaw),double(g.pitch));
    cv.text(x,y,buf,2,{255,230,40,255});
    y += 16;
    std::snprintf(buf,sizeof(buf),"H%.0f C%.0f W%.0f",double(g.headYaw),double(g.chestYaw),double(g.waistYaw));
    cv.text(x,y,buf,2,{255,230,40,255});
    y += 16;
    }
  const auto& am = bench.subject!=nullptr ? Aim::subjectProbe : Aim::playerProbe;
  if(am.valid && am.active) {
    std::snprintf(buf,sizeof(buf),"AIM W%.2f E%.1f Y%.0f P%.0f T%.0f B%.0f L%.0f S%.0f",double(am.weight),double(am.error),double(am.yaw),
                  double(am.pitch),double(am.turn),double(am.bend),double(am.lean),double(am.side));
    cv.text(x,y,buf,2,{255,140,60,255});
    y += 16;
    }
  const auto& mp = Archer::playerProbe;
  if(mp.valid && bench.subject==nullptr) {
    std::snprintf(buf,sizeof(buf),"BOW %s D%.2f H%.1f F%.2f B%.1f%s%s A%.2f",archerStage(mp.stage),double(mp.draw),double(mp.hold),
                  double(mp.fatigue),double(mp.breath),mp.holding ? " HOLD" : "",mp.short_ ? " SHORT" : "",double(mp.amp));
    cv.text(x,y,buf,2,{140,200,255,255});
    y += 16;
    }
  const auto& bd = BowDraw::playerProbe;
  if(bd.valid && bd.weight>0.f && bench.subject==nullptr) {
    std::snprintf(buf,sizeof(buf),"DRAW%s W%.2f D%.2f S%.0f T%.0f M%.1f",BowDraw::isEnabled() ? "" : " OFF",double(bd.weight),
                  double(bd.draw),double(bd.span),double(bd.travel),double(bd.miss));
    cv.text(x,y,buf,2,{140,200,255,255});
    y += 16;
    }
  const auto& fl = bench.subject!=nullptr ? Flinch::subjectProbe : Flinch::playerProbe;
  if(fl.valid && (fl.active || fl.hits>0)) {
    std::snprintf(buf,sizeof(buf),"HIT%s #%u S%.2f H%.2f B%.1f Y%.0f T%.1f N%.1f",Flinch::isEnabled() ? "" : " OFF",fl.hits,
                  double(fl.strength),double(fl.height),double(fl.bend),double(fl.bendYaw),double(fl.twist),double(fl.head));
    cv.text(x,y,buf,2,{255,90,90,255});
    y += 16;
    }
  // lips: shapes and intensity, then the voice (thin bar) and the mouth as shown (thick bar)
  const auto& lp = LipSync::subjectProbe;
  if(lp.valid && LipSync::probeNpc!=nullptr) {
    const Rgba c = {120,230,255,255};
    std::snprintf(buf,sizeof(buf),"LIPS %s F%u F%u A%.1f I%.2f",lp.active ? "ON" : "OFF",lp.shape.frame0,lp.shape.frame1,
                  double(lp.shape.alpha),double(lp.shape.intensity));
    cv.text(x,y,buf,2,c);
    y += 16;
    const int bw = 160;
    cv.rect(x,y,bw,14,Shade);
    cv.rect(x,y+1,int(float(bw)*std::clamp(lp.level,0.f,1.f)),3,{255,255,255,255});
    cv.rect(x,y+6,int(float(bw)*std::clamp(lp.open,0.f,1.f)),7,c);
    y += 20;
    }
  const auto& bk = EyeBlink::subjectProbe;
  if(bk.valid && EyeBlink::probeNpc!=nullptr) {
    std::snprintf(buf,sizeof(buf),"BLINK%s N%u P%u S%u D%u",bk.enabled ? "" : " OFF",bk.blinks,bk.pauses,bk.shifts,bk.doubles);
    cv.text(x,y,buf,2,{200,200,255,255});
    y += 16;
    }
  const auto& ge = TalkGestures::subjectProbe;
  if(ge.valid && TalkGestures::probeNpc!=nullptr) {
    const auto mood = TalkGestures::moodName(ge.mood);
    std::snprintf(buf,sizeof(buf),"GEST%s %s G%02u %.*s S%u C%u X%u N%u P%.1f",ge.enabled ? "" : " OFF",
                  ge.speaking ? "TALK" : (ge.thinking ? "THINK" : (ge.listening ? "LISTEN" : "-")),
                  unsigned(ge.gesture),int(mood.size()),mood.data(),ge.starts,ge.cuts,ge.stops,ge.nods,
                  double(ge.head.pitch*180.f/float(M_PI)));
    cv.text(x,y,buf,2,{255,200,120,255});
    y += 16;
    }
  const auto& ln = bench.subject!=nullptr ? Lean::subjectProbe : Lean::playerProbe;
  if(ln.valid) {
    std::snprintf(buf,sizeof(buf),"LEAN%s R%.1f P%.1f V%.0f T%.0f S%.0f",Lean::isEnabled() ? "" : " OFF",double(ln.roll),
                  double(ln.pitch),double(ln.speed),double(ln.turn),double(ln.slope));
    cv.text(x,y,buf,2,{120,255,160,255});
    y += 16;
    }
  const auto& an = bench.subject!=nullptr ? Pose::subjectProbe : Pose::playerProbe;
  if(an.valid) {
    std::snprintf(buf,sizeof(buf),"BLEND%s N%u A%.0f",AnimBlend::isEnabled() ? "" : " OFF",an.blend.active,double(an.blend.angle));
    cv.text(x,y,buf,2,{200,160,255,255});
    y += 16;
    }
  if(bench.occl!=0) {
    std::snprintf(buf,sizeof(buf),"HIDDEN %s%s",(bench.occl&1) ? "L" : "",(bench.occl&2) ? "R" : "");
    cv.text(x,y,buf,2,Red);
    }
  }

void writeVideo(const Pixmap& pm) {
  if(bench.video==nullptr) {
    bench.videoW = pm.w() & ~1u;
    bench.videoH = pm.h() & ~1u;
    const char* ff = envStr("OPENGOTHIC_BENCH_FFMPEG") ? envStr("OPENGOTHIC_BENCH_FFMPEG") : "ffmpeg";
    char cmd[2048];
    // OPENGOTHIC_BENCH_ENCODER: ffmpeg's video encoder options instead of x264 (the hardware encoder keeps
    // a large frame in real time: "-c:v h264_videotoolbox -b:v 24M")
    const char* enc = envStr("OPENGOTHIC_BENCH_ENCODER") ? envStr("OPENGOTHIC_BENCH_ENCODER") : "-c:v libx264 -preset veryfast -crf 18";
    std::snprintf(cmd,sizeof(cmd),
                  "\"%s\" -loglevel error -y -f rawvideo -pix_fmt rgba -s %ux%u -framerate %d -i - "
                  "%s -pix_fmt yuv420p \"%s/%s.mp4\"",
                  ff,bench.videoW,bench.videoH,Fps,enc,outDir.c_str(),bench.recName.c_str());
    std::FILE* f = popen(cmd,"w");
    if(f==nullptr) {
      say("can not start ffmpeg");
      bench.recVideo = false;
      return;
      }
    bench.video = std::make_unique<VideoPipe>(f);
    }
  const size_t row = size_t(bench.videoW)*4;
  std::vector<uint8_t> frame(row*bench.videoH,0);
  if(pm.w()>=bench.videoW && pm.h()>=bench.videoH && pm.data()!=nullptr) {
    auto px = reinterpret_cast<const uint8_t*>(pm.data());
    for(uint32_t j=0; j<bench.videoH; ++j)
      std::memcpy(frame.data() + j*row, px + size_t(j)*size_t(pm.w())*4, row);
    }
  bench.video->push(std::move(frame));
  }

}

bool TestBench::isActive() {
  return scenarioPath!=nullptr;
  }

void TestBench::note(const char* text) {
  if(scenarioPath!=nullptr && bench.started && !bench.done) {
    say(text);
    event("note","\"text\":"+jsonStr(text));
    }
  }

std::string_view TestBench::who(const Npc* npc) {
  auto w = Gothic::inst().world();
  if(npc==nullptr || w==nullptr)
    return "none";
  auto sym = w->script().findSymbol(size_t(npc->instanceSymbol()));
  return sym!=nullptr ? std::string_view(sym->name()) : std::string_view("npc");
  }

bool TestBench::filming() {
  return scenarioPath!=nullptr && realtime;
  }

uint64_t TestBench::frameDt(uint64_t dt) {
  if(!bench.started || bench.done)
    return dt;
  ++bench.step;
  if(realtime) {
    using namespace std::chrono;
    const auto now = steady_clock::now();
    if(bench.step==1)
      bench.wallStart = now;
    const auto due = bench.wallStart + microseconds(int64_t(bench.step)*1000000/Fps);
    if(now<due)
      std::this_thread::sleep_until(due); else
      bench.lagMs = std::max<int64_t>(bench.lagMs,duration_cast<milliseconds>(now-due).count());
    }
  return (bench.step*1000)/Fps - ((bench.step-1)*1000)/Fps;
  }

namespace {
// the game's sound for `dt` ms of game time, into the recording when there is one
void renderSound(uint64_t dt) {
  const uint32_t rate = SoundDevice::loopbackRate();
  if(rate==0 || dt==0)
    return;
  bench.audioAcc += uint64_t(rate)*dt;
  const size_t n = size_t(bench.audioAcc/1000);
  bench.audioAcc -= uint64_t(n)*1000;
  bench.mix.resize(n*2);
  SoundDevice::renderLoopback(bench.mix.data(),n);
  if(bench.audio!=nullptr) {
    std::fwrite(bench.mix.data(),sizeof(int16_t),bench.mix.size(),bench.audio);
    bench.audioFrames += n;
    }
  }
}

std::function<bool()> TestBench::aiBusy;
std::function<bool(std::string_view,std::string_view)> TestBench::aiReached;

void TestBench::aiLine(std::string_view npc, std::string_view name, std::string_view text, uint64_t ms,
                       std::string_view to, bool filler) {
  if(scenarioPath==nullptr)
    return;
  bench.lastLineAt = std::max<uint64_t>(bench.time,1);
  bench.busyAt     = bench.time;
  event("npc_line","\"npc\":" + jsonStr(npc) + ",\"name\":" + jsonStr(name) + ",\"text\":" + jsonStr(text) +
                   ",\"ms\":" + std::to_string(ms) + ",\"to\":" + jsonStr(to) + (filler ? ",\"filler\":true" : ""));
  say("npc line " + std::string(npc) + (to.empty() ? "" : " -> " + std::string(to)) + ": " + std::string(text));
  }

void TestBench::npcOutput(const Npc& npc, std::string_view name, const Npc* target, uint64_t ms) {
  World* w = Gothic::inst().world();
  if(scenarioPath==nullptr || !bench.started || bench.done || w==nullptr)
    return;
  const auto who    = symName(*w,&npc);
  const auto whom   = symName(*w,target);
  const auto policy = npc.processPolicy();
  say("output " + who + " -> " + whom + " " + std::string(name) + " " + std::to_string(ms) +
      (policy==NpcProcessPolicy::AiNormal || policy==NpcProcessPolicy::Player ? "" : std::string(" ") + policyName(policy)));
  event("output","\"npc\":" + jsonStr(who) + ",\"target\":" + jsonStr(whom) + ",\"name\":" + jsonStr(name) +
                 ",\"ms\":" + std::to_string(ms) + ",\"ai\":" + jsonStr(policyName(policy)));
  }

void TestBench::aiVoiceEnd(std::string_view npc, uint64_t ms) {
  if(scenarioPath==nullptr)
    return;
  bench.busyAt = bench.time;
  event("npc_voice_end","\"npc\":" + jsonStr(npc) + ",\"ms\":" + std::to_string(ms));
  }

void TestBench::aiHeard(std::string_view text, std::string_view to) {
  if(scenarioPath==nullptr)
    return;
  event("heard","\"text\":" + jsonStr(text) + ",\"to\":" + jsonStr(to));
  say("heard: " + std::string(text) + " -> " + std::string(to));
  }

bool TestBench::tick(uint64_t dt, PlayerControl& ctrl) {
  if(scenarioPath==nullptr || bench.done)
    return false;
  if(!bench.loaded)
    loadScenario();

  auto world  = Gothic::inst().world();
  auto player = Gothic::inst().player();
  if(world==nullptr || player==nullptr || Gothic::inst().checkLoading()!=Gothic::LoadState::Idle) {
    renderSound(dt);
    return false;
    }
  if(!bench.started) {
    bench.started = true;
    FootIk::setProbing(true);
    say("start");
    renderSound(dt);
    return false;
    }
  if(dt==0)
    return false;
  bench.time += dt;
  if(aiBusy && aiBusy())
    bench.busyAt = bench.time;

  for(auto k:bench.taps)
    release(ctrl,k);
  bench.taps.clear();
  for(size_t i=0; i<bench.holds.size();) {
    if(bench.holds[i].until<=bench.time) {
      release(ctrl,bench.holds[i].a);
      bench.holds.erase(bench.holds.begin()+long(i));
      } else {
      ++i;
      }
    }

  bench.ctrl = &ctrl;
  run(*world,*player,ctrl);
  renderSound(dt);
  if(bench.done)
    return false;
  // a dialog (an npc with an important one comes to the hero) holds the player: say so
  if(Gothic::inst().isInDialog()!=bench.inDialog) {
    bench.inDialog = !bench.inDialog;
    std::string who;
    if(bench.inDialog)
      world->detectNpc(player->position(),2000,[&](Npc& n) {
        if(&n!=player && Gothic::inst().isNpcInDialog(n))
          who += " " + symName(*world,&n);
        });
    say(std::string(bench.inDialog ? "dialog started:" : "dialog ended") + who);
    }
  for(auto& [n,at]:bench.archers) {
    // the archer harness: where it was set, at the target, the bow drawn, arrows; an arrow every time it
    // can (no fight AI, no perception's wait)
    if(n==nullptr || bench.archerAt==nullptr || n->isDown())
      continue;
    const Vec3 d = bench.archerAt->position()-at;
    n->setPosition(at);
    n->setDirection(Vec3(d.x,0,d.z));
    n->updateTransform();
    n->setTarget(bench.archerAt);
    const auto ws = n->weaponState();
    if(auto rw = n->currentRangedWeapon(); rw!=nullptr && n->itemCount(size_t(rw->handle().munition))<5)
      n->addItem(size_t(rw->handle().munition),20);
    if(ws!=WeaponState::Bow && ws!=WeaponState::CBow) {
      n->drawWeaponBow();
      continue;
      }
    if(!bench.archerDice && n->archerAi().isDriving(*n))
      n->archerAi().attack(*n);
    else if(!n->shootBow())
      n->aimBow();
    }
  if(bench.pinned!=nullptr) {
    const Vec3 d = player->position()-bench.pinAt;
    bench.pinned->setPosition(bench.pinAt);
    bench.pinned->setDirection(Vec3(d.x,0,d.z));
    bench.pinned->updateTransform();
    }
  if(bench.aimNpc!=nullptr) {
    Vec3 at;
    if(!bench.aimNpc->hitZoneCenter(bench.aimZone,at) && !bench.aimNpc->hitZoneCenter(HitZones::Body,at))
      at = bench.aimNpc->collosionCenter();
    if(!bench.aimRaw) {
      // as an archer does: over it by how much the arrow drops on the way (at full draw), from the nock
      const auto st = Archery::stats(*player,player->currentRangedWeapon());
      Vec3       nock, dir;
      if(!player->arrowRay(nock,dir))
        nock = player->position() + Vec3(0,145,0);
      at = Archery::launch(nock,at,Archery::speedAt(st,1.f));
      }
    ctrl.setAimOverride(true,at);
    }
  // the hero speaks to an npc (as the bridge does with his voice), it thinks of an answer
  if(bench.hearNpc!=nullptr) {
    if(bench.time<bench.hearUntil) {
      bench.hearNpc->listenTo(300);
      bench.hearNpc->setTalkPartner(player,1000);
      player->setTalkPartner(bench.hearNpc,1000);
      if(bench.time>=bench.hearNext) {
        bench.hearNpc->heardWords();
        bench.hearNext = bench.time + bench.hearEvery;
        }
      } else {
      bench.hearNpc->stopListening();
      bench.hearNpc = nullptr;
      }
    }
  if(bench.thinkNpc!=nullptr) {
    if(bench.time<bench.thinkUntil) {
      bench.thinkNpc->thinkOfAnswer(300);
      bench.thinkNpc->setTalkPartner(player,1000);
      } else {
      bench.thinkNpc->thinkOfAnswer(0);
      bench.thinkNpc = nullptr;
      }
    }
  if(bench.orbitUntil>bench.time) {
    bench.placeAngle += bench.orbitSpeed*float(dt)/1000.f;
    placeHero(*world,*player);
    }

  const Vec3 pos = player->position();
  if(bench.hasPrev) {
    const Vec3 d = pos-bench.prevPos;
    bench.speed  = std::sqrt(d.x*d.x+d.z*d.z)*1000.f/float(dt);
    }
  bench.prevPos = pos;
  bench.hasPrev = true;

  updateCamera(*world,subjectOf(*player));

  bool capture = !bench.shot.empty();
  if(bench.rec) {
    writeRow(*player);
    capture |= bench.recVideo;
    if(!bench.recVideo)
      ++bench.recFrame;
    }
  return capture;
  }

bool TestBench::withUi() {
  return isActive() && bench.ui;
  }

void TestBench::capture(Pixmap& frame, float uiScale) {
  bench.uiScale = uiScale;
  Pixmap pm;
  if(frame.w()>0 && frame.format()!=TextureFormat::RGBA8)
    pm = Pixmap(frame,TextureFormat::RGBA8); else
    pm = std::move(frame);

  if(pm.w()>0 && bench.overlay)
    drawOverlay(pm);
  if(!bench.shot.empty()) {
    if(pm.w()>0)
      pm.save((outDir+"/"+bench.shot+".png").c_str());
    say("shot " + bench.shot);
    bench.shot.clear();
    }
  if(bench.rec && bench.recVideo) {
    writeVideo(pm);
    ++bench.recFrame;
    }
  }
