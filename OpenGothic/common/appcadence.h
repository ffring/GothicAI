#pragma once
#ifdef OPENGOTHIC_ENABLE_APP_CADENCE
#include <cstdint>
#include <string>
namespace AppCadence {
void enable(std::string output);
bool normalClock();
void beginAttempt();
bool arm(bool ragdoll);
void appFrame();
bool done();
void counts(bool before,uint32_t models,uint32_t dead,uint32_t parts,uint32_t asleep,uint32_t down);
void flush();
}
#endif
