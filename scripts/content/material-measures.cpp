#include "material-measures.h"

// Fixed scalar ABI for ctypes; pixel arrays are inputs only. No file I/O.
extern "C" int material_measure_rgba(const uint8_t* rgba,int width,int height,double* out) {
  try {
    const auto result=material_measures::measure(rgba,width,height);
    out[0]=result.periodU; out[1]=result.periodV;
    out[2]=result.periodConfidenceU; out[3]=result.periodConfidenceV;
    out[4]=result.direction; out[5]=result.directionConfidence; out[6]=result.angle;
    for(int c=0;c<3;++c) { out[7+c]=result.oklab[c]; out[10+c]=result.meanRgb[c]; }
    return 0;
  } catch(...) { return 1; }
}
