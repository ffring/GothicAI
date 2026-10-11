// LOCAL TEST HARNESS for foot IK: not for commit.
// OPENGOTHIC_TEST_SCRIPT="<delay ms> <command>; ..." runs commands once the world is loaded.
// Commands: slopes, sidehill, mark <text>, or any Marvin console command.
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#include "gothic.h"
#include "marvin.h"
#include "camera.h"
#include "world/world.h"
#include "world/objects/npc.h"
#include "physics/dynamicworld.h"
#include "world/aiqueue.h"

using namespace Tempest;

namespace {

struct Step {
  uint64_t    delay = 0;
  std::string cmd;
  };

std::vector<Step> parse(const char* s) {
  std::vector<Step> ret;
  std::string all = s;
  size_t pos = 0;
  while(pos<all.size()) {
    size_t end = all.find(';',pos);
    if(end==std::string::npos)
      end = all.size();
    std::string item = all.substr(pos,end-pos);
    pos = end+1;
    while(!item.empty() && item.front()==' ')
      item.erase(item.begin());
    if(item.empty())
      continue;
    Step st;
    size_t sp = item.find(' ');
    st.delay  = std::strtoull(item.substr(0,sp).c_str(),nullptr,10);
    st.cmd    = sp==std::string::npos ? "" : item.substr(sp+1);
    ret.push_back(st);
    }
  return ret;
  }

float groundY(World& w, float x, float z, float y) {
  auto r = w.physic()->ray({x,y+150,z},{x,y-300,z});
  return r.hasCol ? r.v.y : NAN;
  }

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

void say(const std::string& s) {
  std::printf("[test] %s\n",s.c_str());
  std::fflush(stdout);
  }

struct Item { float dist, angle, rough; std::string name; };

std::vector<Item> findSlopes(World& w, Npc& pl, bool stairs = false) {
  std::vector<Item> items;
  const Vec3 at = pl.position();
  w.findWayPoint(at,[&](const WayPoint& wp) {
    const float dist = (wp.pos-at).length();
    if(dist>8000)
      return false;
    float a = 0, r = 0;
    Vec3  d;
    if(!slopeAt(w,wp.pos,a,d,r))
      return false;
    const bool ok = stairs ? (a>=18 && a<=40 && r>=6 && r<=25) : (a>=12 && a<=32 && r<4);
    if(ok)
      items.push_back({dist,a,r,wp.name});
    return false;
    });
  std::sort(items.begin(),items.end(),[](const Item& a, const Item& b){ return a.dist<b.dist; });
  if(items.size()>30)
    items.resize(30);
  return items;
  }

void listSlopes(World& w, Npc& pl, bool stairs) {
  for(auto& i:findSlopes(w,pl,stairs)) {
    char buf[256];
    std::snprintf(buf,sizeof(buf),"slope %s angle=%.1f rough=%.1f dist=%.0f",i.name.c_str(),i.angle,i.rough,i.dist);
    say(buf);
    }
  }

void sidehill(World& w, Npc& pl) {
  float a = 0, r = 0;
  Vec3  d;
  const Vec3 p = pl.position();
  if(!slopeAt(w,p,a,d,r)) {
    say("sidehill: no ground");
    return;
    }
  // face along the contour line: the slope goes sideways, from the right foot down to the left one
  const Vec3 f = {d.z,0,-d.x};
  pl.setDirection(f);
  pl.updateTransform();
  if(auto c = Gothic::inst().camera())
    c->reset(&pl);
  char buf[128];
  std::snprintf(buf,sizeof(buf),"sidehill angle=%.1f rough=%.1f",a,r);
  say(buf);
  }

void ikstate(World& w, Npc& pl) {
  char buf[512];
  const Vec3 p = pl.position();
  std::snprintf(buf,sizeof(buf),"ikstate dead=%d unc=%d air=%d swim=%d falling=%d slide=%d mob=%d bs=%u pfx=%d pos=%.0f,%.0f,%.0f",
                pl.isDead(),pl.isUnconscious(),pl.isInAir(),pl.isSwim(),pl.isFalling(),pl.isSlide(),pl.interactive()!=nullptr,
                unsigned(pl.bodyStateMasked()),w.isInPfxRange(p),p.x,p.y,p.z);
  say(buf);
  }

// frozen camera that follows the player from the side
struct Follow {
  bool  on     = false;
  float dist   = 200;
  float height = 40;
  float angle  = 90;  // degrees from the facing direction: 0 in front, 90 on the left
  };
Follow follow;

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
  }

void updateFollow(Npc& pl) {
  if(!follow.on)
    return;
  const float a    = pl.rotation()*float(M_PI)/180.f;
  const Vec3  f    = {std::cos(a),0,std::sin(a)};
  const Vec3  side = {-f.z,0,f.x};
  const float b    = follow.angle*float(M_PI)/180.f;
  const Vec3  dir  = f*std::cos(b) + side*std::sin(b);
  const Vec3  p    = pl.position();
  lookFrom(p + dir*follow.dist + Vec3(0,follow.height,0), p + Vec3(0,45,0));
  }

// free camera in front of the player, looking at the legs
void frontcam(Npc& pl, float dist, float height) {
  auto c = Gothic::inst().camera();
  if(c==nullptr)
    return;
  const float a   = pl.rotation()*float(M_PI)/180.f;
  const Vec3  f   = {std::cos(a),0,std::sin(a)};
  const Vec3  p   = pl.position();
  const Vec3  org = p + f*dist + Vec3(0,height,0);
  const Vec3  tgt = p + Vec3(0,45,0);
  const Vec3  d   = tgt-org;
  const float lxz = std::sqrt(d.x*d.x+d.z*d.z);
  const float yaw = std::atan2(d.z,d.x)*180.f/float(M_PI);
  const float pit = std::atan2(d.y,lxz)*180.f/float(M_PI);
  c->setMarvinMode(Camera::M_Freeze);
  c->setPosition(org);
  c->setSpin(PointF(-pit,yaw));
  c->setAngles(PointF(-pit,yaw));
  char buf[128];
  std::snprintf(buf,sizeof(buf),"frontcam yaw=%.1f pitch=%.1f",yaw,pit);
  say(buf);
  }

}

std::string footIkTestTick(uint64_t dt) {
  static const char* script = std::getenv("OPENGOTHIC_TEST_SCRIPT");
  static std::vector<Step> steps = script ? parse(script) : std::vector<Step>();
  static size_t   next  = 0;
  static uint64_t timer = 0;
  if(steps.empty())
    return "";

  auto world  = Gothic::inst().world();
  auto player = Gothic::inst().player();
  if(world==nullptr || player==nullptr || Gothic::inst().checkLoading()!=Gothic::LoadState::Idle)
    return "";
  updateFollow(*player);
  if(next>=steps.size())
    return "";

  timer += dt;
  if(timer<steps[next].delay)
    return "";
  timer = 0;

  const std::string cmd = steps[next++].cmd;
  if(cmd=="slopes" || cmd=="stairs")
    listSlopes(*world,*player,cmd=="stairs");
  else if(cmd.rfind("autoslope",0)==0 || cmd.rfind("autostairs",0)==0) {
    const bool stairs = cmd.rfind("autostairs",0)==0;
    const auto sp     = cmd.find(' ');
    size_t id    = sp!=std::string::npos ? std::strtoul(cmd.c_str()+sp+1,nullptr,10) : 0;
    auto   items = findSlopes(*world,*player,stairs);
    if(id<items.size()) {
      static Marvin marvin;
      marvin.exec("goto waypoint " + items[id].name);
      say("autoslope -> " + items[id].name);
      } else {
      say("autoslope: none");
      }
    }
  else if(cmd=="sidehill")
    sidehill(*world,*player);
  else if(cmd.rfind("followcam",0)==0) {
    std::sscanf(cmd.c_str(),"followcam %f %f %f",&follow.dist,&follow.height,&follow.angle);
    follow.on = true;
    say(cmd);
    }
  else if(cmd.rfind("walkto ",0)==0) {
    auto wp = world->findPoint(cmd.substr(7));
    if(wp!=nullptr)
      player->aiPush(AiQueue::aiGoToPoint(*wp));
    say(std::string(wp ? "ok: " : "no waypoint: ") + cmd);
    }
  else if(cmd.rfind("walkfar ",0)==0) {
    // walk to the waypoint whose distance is closest to the requested one
    const float     want = std::strtof(cmd.c_str()+8,nullptr);
    const Vec3      at   = player->position();
    const WayPoint* best = nullptr;
    float           bd   = 1e9f;
    world->findWayPoint(at,[&](const WayPoint& wp) {
      const float d = std::abs((wp.pos-at).length() - want);
      if(d<bd) {
        bd   = d;
        best = &wp;
        }
      return false;
      });
    if(best!=nullptr)
      player->aiPush(AiQueue::aiGoToPoint(*best));
    say(best ? "walkfar -> " + best->name : std::string("walkfar: none"));
    }
  else if(cmd=="walkmode walk" || cmd=="walkmode run") {
    player->setWalkMode(cmd=="walkmode walk" ? WalkBit::WM_Walk : WalkBit::WM_Run);
    say(cmd);
    }
  else if(cmd.rfind("findwp ",0)==0) {
    // list waypoints whose name contains the text, with the slope at them
    const std::string part = cmd.substr(7);
    int n = 0;
    world->findWayPoint(player->position(),[&](const WayPoint& wp) {
      if(n<40 && wp.name.find(part)!=std::string::npos) {
        float a = 0, r = 0;
        Vec3  d;
        slopeAt(*world,wp.pos,a,d,r);
        char buf[200];
        std::snprintf(buf,sizeof(buf),"wp %s angle=%.1f rough=%.1f",wp.name.c_str(),a,r);
        say(buf);
        ++n;
        }
      return false;
      });
    }
  else if(cmd=="uphill") {
    // face up the slope
    float an = 0, r = 0;
    Vec3  d;
    if(slopeAt(*world,player->position(),an,d,r)) {
      player->setDirection(d*-1.f);
      player->updateTransform();
      }
    say(cmd);
    }
  else if(cmd=="ikstate")
    ikstate(*world,*player);
  else if(cmd.rfind("frontcam",0)==0) {
    float dist = 200, height = 50;
    std::sscanf(cmd.c_str(),"frontcam %f %f",&dist,&height);
    frontcam(*player,dist,height);
    }
  else if(cmd.rfind("mark ",0)==0) {
    say(cmd);
    const char* dir = std::getenv("OPENGOTHIC_TEST_SHOTS");
    return std::string(dir ? dir : ".") + "/" + cmd.substr(5) + ".png";
    }
  else {
    static Marvin marvin;
    const bool ok = marvin.exec(cmd);
    say(std::string(ok ? "ok: " : "failed: ") + cmd);
    }
    return "";
  }
