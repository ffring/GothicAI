#include "navperceptionsafety.h"

#include "physics/dynamicworld.h"
#include "world/world.h"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <set>
#include <sstream>
#include <stdexcept>

namespace {
using Tempest::Vec3;
void vector(std::ostream& out,const Vec3& value) {
  out<<'['<<value.x<<','<<value.y<<','<<value.z<<']';
}
float horizontal(const Vec3& value) {return std::hypot(value.x,value.z);}

// waterRay also checks for cave roofs. Starting exactly on the floor lets that
// check hit its own floor at fraction zero and hide a genuine water surface.
DynamicWorld::RayWaterResult waterAbove(World& world,const Vec3& floor) {
  auto water=world.physic()->waterRay(floor+Vec3(0,2,0),50);
  if(water.hasCol)return water;
  const auto surface=world.physic()->navigationRay(floor+Vec3(0,20000,0),floor+Vec3(0,2,0),nullptr);
  if(surface.hasCol && surface.kind==DynamicWorld::NavigationHit::Water) {
    water.hasCol=true;
    water.wdepth=surface.point.y;
  }
  return water;
}

bool standingClear(World& world,const Vec3& floor) {
  return !world.physic()->sweep(floor+Vec3(0,50,0),floor+Vec3(0,150,0),30).hasCol;
}

void shores(const std::vector<std::string>& args,World& world,const std::string& outputDirectory) {
  std::ifstream input(args[2]);
  if(!input)throw std::runtime_error("perception-shores cannot read seed file");
  const auto path=std::filesystem::path(outputDirectory)/"shore-fixtures.txt";
  std::ofstream fixtures(path);
  if(!fixtures)throw std::runtime_error("perception-shores cannot write fixture input");
  std::ofstream diagnostics(std::filesystem::path(outputDirectory)/"shore-search.jsonl");
  if(!diagnostics)throw std::runtime_error("perception-shores cannot write search diagnostics");
  fixtures<<std::setprecision(9);
  std::string label,line;
  size_t seeds=0;
  std::vector<Vec3> selected;
  constexpr float Pi=3.14159265358979323846f;
  while(std::getline(input,line)) {
    const auto first=line.find_first_not_of(" \t\r");
    if(first==std::string::npos || line[first]=='#')continue;
    Vec3 center;
    std::string extra;
    std::istringstream row(line);
    if(!(row>>label>>center.x>>center.y>>center.z) || row>>extra || ++seeds>12)
      throw std::runtime_error("perception-shores rows LABEL X Y Z (cm), at most twelve seeds");
    for(float value:{center.x,center.y,center.z})
      if(!std::isfinite(value))throw std::runtime_error("perception-shores nonfinite seed coordinate");
    size_t found=0,sampled=0,waterHits=0,deep=0,dryHits=0,blocked=0;
    // A bounded, result-independent physical search around each named shore.
    // It prepares fixtures only; no teacher episode is attempted here.
    for(int radius=0;radius<=6000 && found<3 && selected.size()<9;radius+=200) {
      for(int angle=0;angle<(radius==0?1:48) && found<3 && selected.size()<9;++angle) {
        ++sampled;
        const float yaw=float(angle)*Pi/24.f;
        const auto query=center+Vec3(std::cos(yaw)*float(radius),3000,std::sin(yaw)*float(radius));
        const auto surface=world.physic()->navigationRay(query,query-Vec3(0,12000,0),nullptr);
        const auto wet=world.physic()->landRay(query,12000);
        if(!wet.hasCol)continue;
        const auto water=waterAbove(world,wet.v);
        const bool visibleWater=surface.hasCol && surface.kind==DynamicWorld::NavigationHit::Water;
        if(!visibleWater && !water.hasCol)continue;
        ++waterHits;
        const float surfaceY=visibleWater?surface.point.y:water.wdepth;
        if(surfaceY-wet.v.y<150)continue;
        ++deep;
        if(std::any_of(selected.begin(),selected.end(),[&](const Vec3& previous) {
          return horizontal(previous-wet.v)<500;
        }))continue;
        bool added=false;
        for(int direction=0;direction<24 && !added;++direction) {
          const float heading=float(direction)*Pi/12.f;
          const Vec3 away(std::cos(heading),0,std::sin(heading));
          for(float distance:{200.f,300.f,400.f,500.f}) {
            const auto near=wet.v+away*distance;
            const auto dry=world.physic()->landRay(Vec3(near.x,query.y,near.z),12000);
            if(!dry.hasCol || dry.n.y<.65f)continue;
            const auto dryWater=waterAbove(world,dry.v);
            if(dryWater.hasCol && dryWater.wdepth-dry.v.y>10)continue;
            ++dryHits;
            if(!standingClear(world,dry.v)) {++blocked;continue;}
            // The dry bank must be walkable: a quay/cliff above water would
            // exercise falling instead of the water boundary.
            if(dry.v.y>surfaceY+60 || dry.v.y<surfaceY-10)continue;
            const auto goal=wet.v-away*300.f;
            fixtures<<label<<'-'<<(found+1)<<" water "<<dry.v.x<<' '<<dry.v.y<<' '<<dry.v.z<<' '
                    <<goal.x<<' '<<goal.y<<' '<<goal.z<<' '
                    <<wet.v.x<<' '<<wet.v.y<<' '<<wet.v.z<<" 0\n";
            ++found;selected.push_back(wet.v);added=true;break;
          }
        }
      }
    }
    diagnostics<<"{\"seed\":"<<std::quoted(label)<<",\"sampled\":"<<sampled
               <<",\"water_hits\":"<<waterHits<<",\"deep_water_hits\":"<<deep
               <<",\"dry_candidates\":"<<dryHits<<",\"blocked_starts\":"<<blocked
               <<",\"fixtures\":"<<found<<"}\n";
  }
  if(!input.eof() || seeds==0 || !fixtures)throw std::runtime_error("perception-shores incomplete seed input/output");
  fixtures.close();
  diagnostics.close();
  if(selected.size()<3)throw std::runtime_error("perception-shores qualified only "+std::to_string(selected.size())+
                                               " physical shores; see shore-search.jsonl");
  NavPerceptionSafety::command({args[0],"perception-hazards",path.string()},world,outputDirectory);
}
}

void NavPerceptionSafety::command(const std::vector<std::string>& args,World& world,
                                  const std::string& outputDirectory) {
  if(args.size()==3 && args[1]=="perception-shores" && std::filesystem::path(args[2]).is_absolute()) {
    shores(args,world,outputDirectory);return;
  }
  if(args.size()!=3 || args[1]!="perception-hazards" || !std::filesystem::path(args[2]).is_absolute())
    throw std::runtime_error("navdemos perception-hazards ABSOLUTE_FILE");
  std::ifstream input(args[2]);
  if(!input)throw std::runtime_error("perception-hazards cannot read fixtures");
  std::ofstream out(std::filesystem::path(outputDirectory)/"perception-hazards.jsonl");
  if(!out)throw std::runtime_error("perception-hazards cannot write fixtures");
  out<<std::setprecision(9)<<std::boolalpha;
  std::set<std::string> labels;
  std::string line;
  size_t count=0;
  while(std::getline(input,line)) {
    const auto first=line.find_first_not_of(" \t\r");
    if(first==std::string::npos || line[first]=='#')continue;
    std::istringstream row(line);
    std::string label,kind,extra;
    Vec3 start,goal,hazard;
    uint32_t link=0;
    if(!(row>>label>>kind>>start.x>>start.y>>start.z>>goal.x>>goal.y>>goal.z
           >>hazard.x>>hazard.y>>hazard.z>>link) || row>>extra)
      throw std::runtime_error("perception-hazards rows: LABEL cliff|water|ledge SX SY SZ GX GY GZ HX HY HZ LINK_ID (cm)");
    if((kind!="cliff" && kind!="water" && kind!="ledge") || !labels.insert(label).second ||
       label.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")!=std::string::npos)
      throw std::runtime_error("perception-hazards invalid/duplicate label or hazard kind");
    for(float value:{start.x,start.y,start.z,goal.x,goal.y,goal.z,hazard.x,hazard.y,hazard.z})
      if(!std::isfinite(value))throw std::runtime_error("perception-hazards nonfinite coordinate");
    if(++count>256)throw std::runtime_error("perception-hazards fixture budget exceeds 256");
    const auto sourceHazard=hazard,sourceGoal=goal;
    const auto floor=world.physic()->landRay(start+Vec3(0,100,0),600);
    const auto projected=floor.hasCol?floor.v:start;
    // Accepted CV endpoints can end on a smooth ramp just before a real ledge.
    // Qualify the first physical discontinuity on the same approach, rather
    // than treating its total height difference as an edge. This preparation
    // never passes the queried geometry into the navigation teacher.
    bool locatedLedge=false;
    if(kind=="ledge" && floor.hasCol) {
      const auto direction=goal-projected;
      const float length=horizontal(direction);
      if(length>0) {
        const Vec3 forward(direction.x/length,0,direction.z/length);
        float previous=projected.y;
        const float limit=std::min(500.f,length+200.f);
        for(float distance=25;distance<=limit;distance+=25) {
          const auto point=projected+forward*distance;
          const auto ground=world.physic()->landRay(Vec3(point.x,projected.y+100,point.z),3000);
          if(!ground.hasCol || previous-ground.v.y>60) {
            hazard=ground.hasCol?ground.v:Vec3(point.x,projected.y-3000,point.z);
            const float goalDistance=std::max(length,distance+100);
            goal=projected+forward*goalDistance;
            goal.y=ground.hasCol?ground.v.y:sourceGoal.y;
            locatedLedge=true;
            break;
          }
          previous=ground.v.y;
        }
      }
    }
    const auto goalFloor=world.physic()->landRay(goal+Vec3(0,100,0),1200);
    const auto hazardFloor=world.physic()->landRay(Vec3(hazard.x,projected.y+100,hazard.z),3000);
    const auto startWater=waterAbove(world,projected);
    const auto hazardWater=waterAbove(world,hazardFloor.hasCol?hazardFloor.v:hazard);
    const float drop=hazardFloor.hasCol?projected.y-hazardFloor.v.y:3000.f;
    const float startDepth=startWater.hasCol?startWater.wdepth-projected.y:0;
    const float hazardDepth=hazardWater.hasCol && hazardFloor.hasCol?hazardWater.wdepth-hazardFloor.v.y:0;
    const auto delta=goal-projected,offset=hazard-projected;
    const float goalDistance=horizontal(delta),hazardDistance=horizontal(offset);
    const float along=goalDistance>0?(delta.x*offset.x+delta.z*offset.z)/goalDistance:0;
    const float lateral=goalDistance>0?std::abs(delta.x*offset.z-delta.z*offset.x)/goalDistance:0;
    float localDrop=0,previousHeight=projected.y;
    bool missingGround=false;
    const int samples=std::max(1,int(std::ceil(hazardDistance/25.f)));
    for(int i=1;i<=samples;++i) {
      const float fraction=float(i)/float(samples);
      const auto point=projected+offset*fraction;
      const auto ground=world.physic()->landRay(Vec3(point.x,projected.y+100,point.z),3000);
      if(!ground.hasCol) {missingGround=true;break;}
      localDrop=std::max(localDrop,previousHeight-ground.v.y);
      previousHeight=ground.v.y;
    }
    std::string reason="ok";
    if(!floor.hasCol || floor.n.y<.65f || std::abs(projected.y-start.y)>150)reason="unsupported_start";
    else if(startDepth>10)reason="wet_start";
    else if(!standingClear(world,projected))reason="blocked_start";
    else if(goalDistance<150 || goalDistance>2000 || hazardDistance<75 || hazardDistance>500 ||
            along<50 || along>goalDistance+100 || lateral>125)reason="hazard_not_on_near_approach";
    else if(kind=="water" && (!hazardWater.hasCol || hazardDepth<150))reason="no_deep_water";
    else if(kind=="ledge" && !locatedLedge)reason="no_local_height_discontinuity";
    else if(kind!="water" && drop<=60)reason="no_real_height_drop";
    else if(kind!="water" && !missingGround && localDrop<=60)reason="no_local_height_discontinuity";
    else if(kind!="water" && hazardDepth>10)reason="height_fixture_is_water";
    out<<"{\"label\":"<<std::quoted(label)<<",\"kind\":"<<std::quoted(kind)
       <<",\"link_id\":"<<link<<",\"qualified\":"<<(reason=="ok")
       <<",\"reason\":"<<std::quoted(reason)<<",\"start_cm\":";
    vector(out,start);out<<",\"projected_start_cm\":";vector(out,projected);
    out<<",\"source_hazard_cm\":";vector(out,sourceHazard);
    out<<",\"source_goal_cm\":";vector(out,sourceGoal);
    out<<",\"goal_cm\":";vector(out,goal);
    out<<",\"hazard_cm\":";vector(out,hazard);
    out<<",\"hazard_ground_cm\":";
    if(hazardFloor.hasCol)vector(out,hazardFloor.v);else out<<"null";
    out<<",\"goal_ground_cm\":";
    if(goalFloor.hasCol)vector(out,goalFloor.v);else out<<"null";
    out<<",\"drop_cm\":"<<drop<<",\"max_local_drop_cm\":"<<localDrop
       <<",\"missing_ground_on_approach\":"<<missingGround<<",\"start_water_depth_cm\":"<<startDepth
       <<",\"hazard_water_depth_cm\":"<<hazardDepth
       <<",\"start_to_hazard_cm\":"<<hazardDistance<<",\"hazard_lateral_cm\":"<<lateral<<"}\n";
  }
  if(!input.eof() || count==0 || !out)throw std::runtime_error("perception-hazards incomplete fixture input/output");
}
