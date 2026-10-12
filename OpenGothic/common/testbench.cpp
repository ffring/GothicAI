#include "testbench.h"

#include <Tempest/Pixmap>
#include <Tempest/SystemApi>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#include "game/gamescript.h"
#include "game/playercontrol.h"
#include "graphics/drawcommands.h"
#include "graphics/mesh/footik.h"
#include "graphics/mesh/gaze.h"
#include "graphics/mesh/skeleton.h"
#include "physics/dynamicworld.h"
#include "world/objects/npc.h"
#include "world/world.h"
#include "world/aiqueue.h"
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

std::string rest(const std::string& line) {
  const size_t sp = line.find(' ');
  return sp==std::string::npos ? "" : line.substr(sp+1);
  }

void replaceAll(std::string& s, std::string_view from, std::string_view to) {
  for(size_t p=s.find(from); p!=std::string::npos; p=s.find(from,p+to.size()))
    s.replace(p,from.size(),to);
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

// --- the bench ---------------------------------------------------------------------------------------

enum CamMode : uint8_t {
  C_Game,
  C_Follow, // around the player, relative to its facing
  C_World,  // around the player, fixed world direction
  C_Fixed,  // fixed position, looking at the player
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
  std::FILE*  video     = nullptr;
  uint32_t    videoW    = 0;
  uint32_t    videoH    = 0;
  std::FILE*  csv       = nullptr;
  std::string shot;
  bool        overlay   = true;
  bool        zoom      = true;
  bool        bright    = true;  // auto exposure: forests and interiors are too dark to see the legs
  float       clip      = 0;     // > 0: everything closer to the camera than the subject minus this is clipped away

  Vec3        prevPos;
  bool        hasPrev   = false;
  float       speed     = 0;
  uint8_t     occl      = 0;    // bit per leg: the ankle is hidden from the camera
  std::string label;            // free text on the overlay
  Npc*        subject   = nullptr; // the camera watches this npc instead of the player (gaze)
  // the hero placed around the subject: distance, angle from its facing (0 in front), orbiting
  float       placeDist  = 0;
  float       placeAngle = 0;
  float       orbitSpeed = 0;     // degrees per second
  uint64_t    orbitUntil = 0;
  };

Bench bench;

void loadScenario() {
  bench.loaded = true;
  std::ifstream fin(scenarioPath);
  if(!fin) {
    say(std::string("can not read scenario: ") + scenarioPath);
    return;
    }
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
  if(bench.video!=nullptr)
    pclose(bench.video);
  bench.video = nullptr;
  say("rec stop " + bench.recName + " frames=" + std::to_string(bench.recFrame));
  bench.rec = false;
  }

void finish() {
  stopRecording();
  if(bench.csv!=nullptr)
    std::fclose(bench.csv);
  bench.csv  = nullptr;
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
  const size_t id = w.script().findSymbolIndex(name);
  return id==size_t(-1) ? nullptr : w.findNpcByInstance(id);
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

void updateCamera(World& w, Npc& pl) {
  bench.occl = 0;
  if(bench.cam==C_Game)
    return;
  const Vec3 tgt = pl.position() + Vec3(0,bench.camLook,0);
  Vec3       org = bench.camPos;
  if(bench.cam==C_Follow || bench.cam==C_World)
    org = camOrigin(w,pl,bench.camAngle);
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

void writeHeader() {
  std::fprintf(bench.csv,"seg,frame,t,x,y,z,rot,speed,bs,walk,occl,ik_valid,ik_on,ik_hardoff,ik_w,nlegs,bad,pelvis,chest,pelvis_target,pelvis_limit,floor");
  for(const char* s:{"l","r","fl","fr"}) {
    std::fprintf(bench.csv,",%s_plant,%s_has,%s_gy,%s_nx,%s_ny,%s_nz,%s_toehas,%s_toey,%s_smooth,%s_err,%s_knee,%s_tilt,%s_roll,%s_yaw,%s_out,%s_slack",
                 s,s,s,s,s,s,s,s,s,s,s,s,s,s,s,s);
    for(const char* j:{"hip","kneea","anka","toea","kneef","ankf","toef"})
      std::fprintf(bench.csv,",%s_%s_x,%s_%s_y,%s_%s_z",s,j,s,j,s,j);
    }
  std::fprintf(bench.csv,",g_on,g_w,g_err,g_yaw,g_pitch,g_yawgoal,g_pitchgoal,g_head,g_chest,g_waist,pg_on,pg_w,pg_err,pg_yaw");
  std::fprintf(bench.csv,"\n");
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
  std::fprintf(bench.csv,",%d,%.3f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%d,%.3f,%.2f,%.2f",
               int(g.active),g.weight,g.error,g.yaw,g.pitch,g.yawGoal,g.pitchGoal,g.headYaw,g.chestYaw,g.waistYaw,
               int(pg.active),pg.weight,pg.error,pg.yaw);
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
  if(!std::isnan(g))
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
    else {
      const float y = num(a,1,0)*float(M_PI)/180.f;
      face(pl,Vec3(std::cos(y),0,std::sin(y)));
      }
    }
  else if(c=="turn") {
    const float y = (pl.rotation()+num(a,1,90))*float(M_PI)/180.f;
    face(pl,Vec3(std::cos(y),0,std::sin(y)));
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
    else
      bench.overlay = (m!="off");
    }
  else if(c=="rec") {
    stopRecording();
    bench.rec      = true;
    bench.recVideo = !(a.size()>2 && a[2]=="novideo");
    bench.recName  = a.size()>1 ? a[1] : "rec";
    bench.recFrame = 0;
    bench.recStart = bench.time;
    say("rec " + bench.recName + (bench.recVideo ? "" : " (metrics only)"));
    }
  else if(c=="stop") {
    stopRecording();
    }
  else if(c=="shot") {
    bench.shot = a.size()>1 ? a[1] : "shot";
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
    say("talk " + std::string(n->displayName()) + " " + std::to_string(ms));
    }
  else if(c=="watch") {
    // watch <npc|nearest|player>: the camera and the gaze metrics follow this one
    Npc* n = findNpc(w,pl,a.size()>1 ? a[1] : "player");
    bench.subject  = (n==&pl) ? nullptr : n;
    Gaze::probeNpc = bench.subject;
    char buf[160] = "watch nobody";
    if(n!=nullptr)
      if(auto sym = w.script().findSymbol(size_t(n->instanceSymbol())))
        std::snprintf(buf,sizeof(buf),"watch %s dist=%.0f",sym->name().c_str(),double((n->position()-pl.position()).length()));
    say(buf);
    }
  else if(c=="gazeinfo") {
    Npc& s = subjectOf(pl);
    char buf[200];
    std::snprintf(buf,sizeof(buf),"gazeinfo partner=%d probe valid=%d active=%d w=%.2f hero partner=%d",
                  int(s.talkPartner()!=nullptr),int(Gaze::subjectProbe.valid),int(Gaze::subjectProbe.active),
                  double(Gaze::subjectProbe.weight),int(pl.talkPartner()!=nullptr));
    say(buf);
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
    say("freeze " + std::string(n->displayName()));
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
  else if(c=="quit") {
    bench.pc = bench.prog.size();
    }
  else {
    const bool ok = marvin().exec(line);
    say(std::string(ok ? "marvin: " : "marvin failed: ") + line);
    }
  }

void run(World& w, Npc& pl, PlayerControl& ctrl) {
  while(bench.pc<bench.prog.size()) {
    if(bench.time<bench.waitUntil)
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
  if(bench.time>=bench.waitUntil && !bench.waitArrive)
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

void drawOverlay(Pixmap& pm) {
  Canvas cv;
  cv.px = reinterpret_cast<uint8_t*>(pm.data());
  cv.w  = int(pm.w());
  cv.h  = int(pm.h());
  auto  cam = Gothic::inst().camera();
  auto& p   = FootIk::playerProbe;
  if(bench.bright)
    autoExposure(cv);

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
    std::snprintf(cmd,sizeof(cmd),
                  "\"%s\" -loglevel error -y -f rawvideo -pix_fmt rgba -s %ux%u -framerate %d -i - "
                  "-c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p \"%s/%s.mp4\"",
                  ff,bench.videoW,bench.videoH,Fps,outDir.c_str(),bench.recName.c_str());
    bench.video = popen(cmd,"w");
    if(bench.video==nullptr) {
      say("can not start ffmpeg");
      bench.recVideo = false;
      return;
      }
    }
  const size_t row = size_t(bench.videoW)*4;
  std::vector<uint8_t> black;
  if(pm.w()<bench.videoW || pm.h()<bench.videoH || pm.data()==nullptr) {
    black.resize(row*bench.videoH,0);
    std::fwrite(black.data(),1,black.size(),bench.video);
    return;
    }
  auto px = reinterpret_cast<const uint8_t*>(pm.data());
  for(uint32_t j=0; j<bench.videoH; ++j)
    std::fwrite(px + size_t(j)*size_t(pm.w())*4, 1, row, bench.video);
  }

}

bool TestBench::isActive() {
  return scenarioPath!=nullptr;
  }

uint64_t TestBench::frameDt(uint64_t dt) {
  if(!bench.started || bench.done)
    return dt;
  ++bench.step;
  return (bench.step*1000)/Fps - ((bench.step-1)*1000)/Fps;
  }

bool TestBench::tick(uint64_t dt, PlayerControl& ctrl) {
  if(scenarioPath==nullptr || bench.done)
    return false;
  if(!bench.loaded)
    loadScenario();

  auto world  = Gothic::inst().world();
  auto player = Gothic::inst().player();
  if(world==nullptr || player==nullptr || Gothic::inst().checkLoading()!=Gothic::LoadState::Idle)
    return false;
  if(!bench.started) {
    bench.started = true;
    FootIk::setProbing(true);
    say("start");
    return false;
    }
  if(dt==0)
    return false;
  bench.time += dt;

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

  run(*world,*player,ctrl);
  if(bench.done)
    return false;
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

void TestBench::capture(Pixmap& frame) {
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
