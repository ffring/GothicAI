#include "../stuckprogress.h"

#include <cstdio>
#include <cstdlib>

namespace {
int checks = 0;
void require(bool condition, const char* message) {
  ++checks;
  if(condition)
    return;
  std::fprintf(stderr,"FAIL: %s\n",message);
  std::exit(EXIT_FAILURE);
  }
}

int main() {
  using P = StuckProgress;
  P still;
  require(still.update(0,{10,20,30},10000)==P::None,"first position is not stuck");
  require(still.update(2999,{10,20,30},39990)==P::None,"less than three seconds is not stuck");
  require(still.update(3000,{10,20,30},40000)==P::Detected,"three stationary seconds detect once");
  require(still.start()==0 && still.gameTime()==10000,"event starts at beginning of the stationary window");
  require(still.update(5000,{110,20,30},60000)==P::None,"exactly one metre does not end an episode");
  require(still.update(5001,{110.1f,20,30},60010)==P::Recovered,"more than one metre ends an episode");
  require(!still.isStuck() && still.start()==0,"recovery preserves event metadata for output");
  require(still.update(8001,{110.1f,20,30},90010)==P::Detected,"a later stationary window creates a new episode");

  P boundary;
  boundary.update(0,{0,0,0},0);
  require(boundary.update(3000,{50,0,0},30000)==P::None,"exactly fifty centimetres is progress");
  require(boundary.update(6000,{99.9f,0,0},60000)==P::Detected,"under fifty centimetres detects");

  P rolling;
  rolling.update(0,{0,0,0},0);
  rolling.update(2000,{100,0,0},20000);
  require(rolling.update(4000,{100,0,0},40000)==P::None,"three-second window interpolates cutoff at fifty centimetres");
  require(rolling.update(4500,{100,0,0},45000)==P::Detected,"sliding window catches a stop after earlier walking");
  require(rolling.start()==1500 && rolling.gameTime()==15000,"interpolated window preserves game clock");

  P walk;
  for(uint64_t tick=0; tick<=12000; tick+=250)
    require(walk.update(tick,{float(tick)*0.1f,0,0},int64_t(tick)*10)==P::None,"ordinary walking never detects");
  std::printf("stuck progress: %d checks passed\n",checks);
  }
