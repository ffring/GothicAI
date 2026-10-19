#include "game/deedwitness.h"

#include <array>
#include <cstdio>
#include <cstdlib>

namespace {
int checks = 0;
void require(bool condition, const char* message) {
  ++checks;
  if(condition) return;
  std::fprintf(stderr,"FAIL: %s\n",message);
  std::exit(EXIT_FAILURE);
  }
}

int main() {
  DeedWitness witness;
  witness.range = 2000;
  require(witness.admitted(),"ready nearby witness is admitted");
  witness.distanceSquared = 2000*2000;
  require(witness.admitted(),"perception range includes its exact boundary");
  witness.distanceSquared += 1;
  require(!witness.admitted(),"one centimetre squared beyond native range is rejected");
  witness.distanceSquared = 0;
  for(bool DeedWitness::* flag:std::array{
      &DeedWitness::culprit,&DeedWitness::victim,&DeedWitness::player,
      &DeedWitness::down,&DeedWitness::lying,&DeedWitness::sleeping,&DeedWitness::foreignFight}) {
    auto candidate = witness;
    candidate.*flag = true;
    require(!candidate.admitted(),"excluded participants, sleeping/down NPCs and foreign combat cannot witness");
    candidate.*flag = false;
    require(candidate.admitted(),"the same otherwise ready witness is admitted after its gate clears");
    }
  witness.simulated = false;
  require(!witness.admitted(),"far simulation cannot witness an engine deed");
  witness.simulated = true;
  witness.stateStarted = false;
  require(!witness.admitted(),"a pending native state cannot receive a perception yet");
  std::printf("NPC deed witness contract: %d checks passed\n",checks);
  }
