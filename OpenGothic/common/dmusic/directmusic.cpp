#include "directmusic.h"
#include "resources.h"
#include "sourcefile.h"

#include <stdexcept>

#include <Tempest/File>

using namespace Dx8;

DirectMusic::DirectMusic() {
  }

DirectMusic::DirectMusic(Native source) : native(std::move(source)) {
  }

PatternList DirectMusic::load(const Segment &s) {
  return PatternList(s,*this);
  }

PatternList DirectMusic::load(const char16_t *fsgt) {
  if(native.segment)
    return load(native.segment(fsgt));

  Tempest::RFile fin = implOpen(fsgt);
  std::vector<uint8_t> v(size_t(fin.size()));
  fin.read(v.data(), v.size());

  auto r   = Dx8::Riff(v.data(), v.size());
  auto sgt = Dx8::Segment(r);
  return load(sgt);
  }

void DirectMusic::addPath(std::u16string p) {
  path.emplace_back(std::move(p));
  }

const Style &DirectMusic::style(const Reference &id) {
  for(auto& i:styles){
    if(i.first==id.file)
      return i.second;
    }

  if(native.style) {
    styles.emplace_back(id.file,native.style(id.file.c_str()));
    return styles.back().second;
    }

  Tempest::RFile fin = implOpen(id.file.c_str());
  const size_t length = fin.size();

  std::vector<uint8_t> data(length);
  fin.read(&data[0],data.size());

  Riff  r{data.data(),data.size()};
  Style stl(r);

  styles.emplace_back(id.file,std::move(stl));
  return styles.back().second;
  }

const DlsCollection &DirectMusic::dlsCollection(const Reference &id) {
  return dlsCollection(id.file);
  }

const DlsCollection &DirectMusic::dlsCollection(const std::u16string &file) {
  for(auto& i:dls){
    if(i->first==file)
      return i->second;
    }
  if(native.dls) {
    DlsCollection value(native.dls(file.c_str()));
    dls.emplace_back(new std::pair<std::u16string,DlsCollection>(file,std::move(value)));
    return dls.back()->second;
    }

  Tempest::RFile fin    = implOpen(file.c_str());
  const size_t   length = fin.size();

  std::vector<uint8_t> data(length);
  fin.read(reinterpret_cast<char*>(&data[0]),data.size());

  Riff          r{data.data(),data.size()};
  DlsCollection stl(r);

  dls.emplace_back(new std::pair<std::u16string,DlsCollection>(file,std::move(stl)));
  return dls.back()->second;
  }

Tempest::RFile DirectMusic::implOpen(const char16_t *file) {
  std::u16string filepath;
  auto fin = openSourceFile(path,file,filepath);
  Resources::censusFile(filepath);
  return fin;
  }
