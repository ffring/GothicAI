#include "crosshair.h"

#include <algorithm>
#include <cmath>

using namespace Tempest;

namespace {

// pixels at the interface scale 1
constexpr float ChevronW    = 6.f;   // half its width
constexpr float ChevronH    = 5.f;
constexpr float RadiusMin   = 12.f;  // the brackets close in that near, clear of the chevron
constexpr float RadiusWide  = 70.f;  // that far apart they fade to BracketWide
constexpr float RadiusMax   = 0.2f;  // of the screen's height
constexpr float ArcLen      = 16.f;  // half a bracket's length...
constexpr float ArcMin      = 0.3f;  // ...radians of its arc each way, wide apart...
constexpr float ArcMax      = 0.85f; // ...and close in
constexpr float ArcStep     = 3.f;   // a bracket is drawn in pieces that long
constexpr float Width       = 1.6f;
constexpr float Outline     = 1.f;   // the dark edge round the lines
constexpr float Bright      = 0.95f;
constexpr float BracketWide = 0.55f;
constexpr float OutlineK    = 0.5f;  // the outline's alpha, of the line's

Vec2 normalOf(const Vec2& a, const Vec2& b) {
  const Vec2  d = b-a;
  const float l = d.length();
  return l>0.f ? Vec2(-d.y/l,d.x/l) : Vec2(0,1);
  }

void tri(Painter& p, const Vec2& a, const Vec2& b, const Vec2& c) {
  p.drawTriangle(a.x,a.y,0,0, b.x,b.y,0,0, c.x,c.y,0,0);
  }

// a polyline that thick, mitered joins (the pieces do not overlap: no darker spots where they meet);
// ends pushed out by cap
void stroke(Painter& p, const std::vector<Vec2>& pt, float half, float cap) {
  const size_t n = pt.size();
  if(n<2)
    return;
  std::vector<Vec2> pos(pt), off(n);
  for(size_t i=0; i<n; ++i) {
    if(i==0) {
      off[i] = normalOf(pt[0],pt[1])*half;
      } else if(i+1==n) {
      off[i] = normalOf(pt[n-2],pt[n-1])*half;
      } else {
      const Vec2  n0 = normalOf(pt[i-1],pt[i]);
      const Vec2  n1 = normalOf(pt[i],pt[i+1]);
      Vec2        m  = n0+n1;
      const float l  = m.length();
      m = l>1e-3f ? m/l : n0;
      const float c  = std::max(0.5f,m.x*n0.x+m.y*n0.y);
      off[i] = m*(half/c);
      }
    }
  const Vec2 d0 = Vec2(off[0].y,-off[0].x)/half;
  const Vec2 d1 = Vec2(off[n-1].y,-off[n-1].x)/half;
  pos[0]   = pos[0]   - d0*cap;
  pos[n-1] = pos[n-1] + d1*cap;
  for(size_t i=0; i+1<n; ++i) {
    const Vec2 a0 = pos[i]+off[i],     a1 = pos[i]-off[i];
    const Vec2 b0 = pos[i+1]+off[i+1], b1 = pos[i+1]-off[i+1];
    tri(p,a0,a1,b0);
    tri(p,a1,b1,b0);
    }
  }

}

float Crosshair::radius(float spread, float fovY, float h, float scale) {
  const float perRad = 0.5f*h/std::tan(0.5f*fovY*float(M_PI)/180.f);
  return std::clamp(std::tan(spread)*perRad, RadiusMin*scale, std::max(RadiusMin*scale,RadiusMax*h));
  }

std::vector<Crosshair::Line> Crosshair::shape(float x, float y, float radius, float scale) {
  std::vector<Line> ret;
  // the chevron: its tip on the point
  Line ch;
  ch.alpha = Bright;
  ch.pt    = {Vec2(x-ChevronW*scale,y-ChevronH*scale), Vec2(x,y), Vec2(x+ChevronW*scale,y-ChevronH*scale)};
  ret.push_back(std::move(ch));

  // the brackets: arcs of the circle the arrow may go off in; the same length near and far, fainter far
  const float r    = std::max(radius,RadiusMin*scale);
  const float wide = std::clamp((r-RadiusMin*scale)/((RadiusWide-RadiusMin)*scale),0.f,1.f);
  const float arc  = std::clamp(ArcLen*scale/r,ArcMin,ArcMax);
  const int   n    = std::clamp(int(std::ceil(2.f*arc*r/(ArcStep*scale))),4,32);
  for(int side=0; side<2; ++side) {
    Line br;
    br.alpha = Bright + (BracketWide-Bright)*wide;
    const float mid = side==0 ? float(M_PI) : 0.f;
    for(int i=0; i<=n; ++i) {
      const float a = mid - arc + 2.f*arc*float(i)/float(n);
      br.pt.emplace_back(x+r*std::cos(a),y+r*std::sin(a));
      }
    ret.push_back(std::move(br));
    }
  return ret;
  }

Crosshair::Pen Crosshair::pen(float scale) {
  Pen pn;
  pn.half    = 0.5f*std::max(1.5f,Width*scale);
  pn.edge    = std::max(1.f,Outline*scale);
  pn.outline = OutlineK;
  return pn;
  }

void Crosshair::paint(Painter& p, const std::vector<Line>& lines, float scale) {
  const Pen pn = pen(scale);
  p.pushState();
  for(auto& l:lines) {
    p.setBrush(Brush(Color(0,0,0,pn.outline*l.alpha),Painter::Alpha));
    stroke(p,l.pt,pn.half+pn.edge,pn.edge);
    }
  for(auto& l:lines) {
    p.setBrush(Brush(Color(1,1,1,l.alpha),Painter::Alpha));
    stroke(p,l.pt,pn.half,0);
    }
  p.popState();
  }
