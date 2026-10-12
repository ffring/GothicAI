#pragma once

#include <Tempest/Painter>

#include <vector>

// The crosshair of the manual aim (game/archery): a small chevron "v", its tip where it aims, and two
// brackets "( )" round it, as far apart as the arrow may go off. Steady, they close in on the chevron;
// shaken, raised, reloading, they open wide. The game draws it, the test bench draws the same lines
namespace Crosshair {
  struct Line final {
    std::vector<Tempest::Vec2> pt;
    float                      alpha = 1;
    };

  // how the lines are stroked, pixels: half their thickness, the dark edge round them, its alpha (of the
  // line's)
  struct Pen final {
    float half    = 1;
    float edge    = 1;
    float outline = 0.5f;
    };

  // how far apart the brackets are, pixels: the spread (radians) as seen with the vertical field of view
  // fovY (degrees) on a screen h pixels high
  float             radius(float spread, float fovY, float h, float scale);
  // the lines round (x,y), the brackets at radius
  std::vector<Line> shape(float x, float y, float radius, float scale);
  Pen               pen(float scale);
  // strokes them: white over a dark outline
  void              paint(Tempest::Painter& p, const std::vector<Line>& lines, float scale);
  }
