#include "particlesdefinitions.h"

#include <Tempest/Log>

#include "graphics/pfx/particlefx.h"
#include "utils/fileext.h"
#include "gothic.h"

using namespace Tempest;

ParticlesDefinitions::ParticlesDefinitions() {
  vm = Gothic::inst().createPhoenixVm("ParticleFx.dat");
  }

ParticlesDefinitions::~ParticlesDefinitions() {
  }

const ParticleFx* ParticlesDefinitions::get(std::string_view name, bool relaxed) {
  if(name.empty())
    return nullptr;

  while(FileExt::hasExt(name,"PFX"))
    name = name.substr(0,name.size()-4);

  std::lock_guard<std::recursive_mutex> guard(sync);
  return implGet(name,relaxed);
  }

const ParticleFx* ParticlesDefinitions::get(const ParticleFx* base, const VisualFx::Key* key) {
  if(base==nullptr || key==nullptr)
    return base;
  std::lock_guard<std::recursive_mutex> guard(sync);
  return implGet(*base,*key);
  }

const ParticleFx* ParticlesDefinitions::implGet(std::string_view name, bool relaxed) {
  auto cname = std::string(name);
  auto it    = pfx.find(cname);
  if(it!=pfx.end())
    return it->second.get();
  auto decl=implGetDirect(name, relaxed);
  if(!decl)
    return nullptr;
  std::unique_ptr<ParticleFx> p{new ParticleFx(*decl,name)};
  auto elt = pfx.insert(std::make_pair(std::move(cname),std::move(p)));

  return elt.first->second.get();
  }

const ParticleFx* ParticlesDefinitions::implGet(const ParticleFx& base, const VisualFx::Key& key) {
  auto it = pfxKey.find(&key);
  if(it!=pfxKey.end())
    return it->second.get();

  std::unique_ptr<ParticleFx> p{new ParticleFx(base,key)};
  auto elt = pfxKey.insert(std::make_pair(&key,std::move(p)));

  return elt.first->second.get();
  }

std::shared_ptr<zenkit::IParticleEffect> ParticlesDefinitions::implGetDirect(std::string_view name, bool relaxed) {
  if(!vm || name.empty())
    return nullptr;

  if(name=="PFX_GAI_PEE")
    return peeStream();
  if(name.substr(0,13)=="PFX_GAI_AXIS_") {
    // debug (GAI_PEE_AXES=1): a glowing stream along one axis of the pelvis bone, to find its forward
    static const char* col[4] = {"255 0 0","0 255 0","0 80 255","255 255 255"};
    static const float hd[4]  = {-90, 90, 0, 0};
    static const float el[4]  = {0, 0, 90, -90};
    const int i = name.back()-'0';
    auto r = peeStream();
    r->dir_angle_head = hd[i]; r->dir_angle_elev = el[i]; r->fly_gravity_s = "0 0 0";
    r->vis_tex_color_start_s = r->vis_tex_color_end_s = col[i];
    r->vis_size_start_s = "25 25"; r->vis_alpha_func_s = "BLEND"; r->vis_name_s = "WHITECLOUD.TGA"; r->vel_avg = 0.2f;
    return r;
    }

  auto id = vm->find_symbol_by_name(name);
  if(id==nullptr) {
    if(!relaxed)
      Log::e("invalid particle system: \"",name,"\"");
    return nullptr;
    }

  auto ret = std::make_shared<zenkit::IParticleEffect>();
  ret->vis_tex_is_quadpoly = 1; // seem to be default
  vm->init_instance(ret, id);
  return ret;
  }

// A man relieving himself (S_PEE, mercy.py's humiliation): a thin yellow stream from the pelvis, built on the
// game's own water outlet (its texture and blending), arcing down a metre ahead of him.
std::shared_ptr<zenkit::IParticleEffect> ParticlesDefinitions::peeStream() {
  auto ret = std::make_shared<zenkit::IParticleEffect>();
  ret->vis_tex_is_quadpoly = 1;
  if(auto id = vm->find_symbol_by_name("WATEROUTLET"); id!=nullptr) {
    vm->init_instance(ret, id);
    Log::i("pee stream base WATEROUTLET: pps ",ret->pps_value," dir ",ret->dir_mode_s," for ",ret->dir_for_s,
           " head ",ret->dir_angle_head," elev ",ret->dir_angle_elev," vel ",ret->vel_avg,"/",ret->vel_var,
           " life ",ret->lsp_part_avg," gravity \"",ret->fly_gravity_s,"\" vis ",ret->vis_name_s," ",ret->vis_orientation_s,
           " size \"",ret->vis_size_start_s,"\"x",ret->vis_size_end_scale," color \"",ret->vis_tex_color_start_s,"\"->\"",
           ret->vis_tex_color_end_s,"\" alpha ",ret->vis_alpha_func_s," ",ret->vis_alpha_start,"->",ret->vis_alpha_end,
           " shape ",ret->shp_type_s," ",ret->shp_dim_s);
    }
  ret->pps_value              = PeeStream::pps;
  ret->pps_is_looping         = 1;
  ret->pps_scale_keys_s       = "";
  ret->pps_create_em_s        = "";
  ret->shp_type_s             = "POINT";
  ret->shp_for_s              = "OBJECT";
  ret->shp_offset_vec_s       = PeeStream::offset;
  ret->dir_mode_s             = "DIR";
  ret->dir_for_s              = "OBJECT";
  ret->dir_angle_head         = PeeStream::head;
  ret->dir_angle_head_var     = PeeStream::spread;
  ret->dir_angle_elev         = PeeStream::elev;
  ret->dir_angle_elev_var     = PeeStream::spread;
  ret->vel_avg                = PeeStream::vel;
  ret->vel_var                = PeeStream::vel*0.06f;
  ret->lsp_part_avg           = PeeStream::lifeMs;
  ret->lsp_part_var           = PeeStream::lifeMs*0.1f;
  ret->fly_gravity_s          = PeeStream::gravity;
  ret->fly_colldet_b          = 0;
  ret->vis_orientation_s      = "VELO";
  ret->vis_name_s             = "SIMPLEGLOW.TGA";  // debug: see the way
  ret->vis_alpha_func_s       = "ADD";
  ret->vis_tex_color_start_s  = PeeStream::colorStart;
  ret->vis_tex_color_end_s    = PeeStream::colorEnd;
  ret->vis_size_start_s       = PeeStream::size;
  ret->vis_size_end_scale     = 1.4f;
  ret->vis_alpha_start        = 255;
  ret->vis_alpha_end          = 180;
  ret->trl_texture_s          = "";
  ret->mrkt_exture_s          = "";
  return ret;
  }
