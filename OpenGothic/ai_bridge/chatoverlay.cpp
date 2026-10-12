#include "chatoverlay.h"

#include <Tempest/Application>
#include <Tempest/Painter>

#include <algorithm>
#include <cstdlib>
#include <vector>

#include "utils/gthfont.h"
#include "gothic.h"
#include "resources.h"

#include "textcodec.h"

using namespace Tempest;

namespace {

constexpr size_t   MaxInput       = 300;
constexpr size_t   MaxLog         = 32;
constexpr size_t   OpenRows       = 6;   // entries shown while the input line is open
constexpr size_t   SubtitleRows   = 3;   // entries shown as subtitles otherwise (the hero's line and replies)
constexpr uint64_t LingerMs       = 2500;
constexpr uint64_t PttHoldMs      = 300;    // T held longer than this is push-to-talk
constexpr uint64_t PttMaxMs       = 30000;  // the key-up may never come (focus lost)
constexpr auto     FontName       = "font_old_20_white.tga"; // the large game font, has cyrillic in RU
constexpr uint64_t DealSettleMs   = 1000;   // Y waits this long after the terms appear or change
constexpr uint64_t DealFreshMs    = 4000;   // what changed stays highlighted this long

// GOTHIC_AI_CHAT_SCALE: extra size multiplier for the chat, on top of the game's interface scale
// the end of `text` that fits into `width` (live transcripts grow at the end)
std::string fitTail(const GthFont& font, std::string text, int width) {
  if(font.textSize(text).w<=width)
    return text;
  while(!text.empty() && font.textSize("..."+text).w>width) {
    const size_t sp = text.find(' ',1);
    text.erase(0,sp==std::string::npos ? 1 : sp);
    }
  return "..."+text;
  }

float chatScale() {
  static const float v = [] {
    const char* env = std::getenv("GOTHIC_AI_CHAT_SCALE");
    const float f   = env!=nullptr ? std::strtof(env,nullptr) : 0.f;
    return f>0.f ? f : 1.f;
    }();
  return v;
  }

}

ChatOverlay::ChatOverlay() {
  }

void ChatOverlay::openInput() {
  active = true;
  input.clear();
  cursor = 0;
  update();
  }

void ChatOverlay::closeInput() {
  active = false;
  input.clear();
  cursor = 0;
  update();
  }

void ChatOverlay::setPartial(std::string text) {
  partial = std::move(text);
  update();
  }

void ChatOverlay::pttPress(char pendingChar) {
  partial.clear();
  tDown        = true;
  tDownAt      = Application::tickCount();
  tPendingChar = pendingChar;
  if(onPttStart)
    onPttStart();
  }

void ChatOverlay::pttRelease() {
  if(!tDown)
    return;
  const bool spoke = recording;
  const char typed = tPendingChar;
  pttReset();
  if(spoke) {
    if(onPttStop)
      onPttStop();
    update();
    return;
    }
  if(onPttCancel)
    onPttCancel();
  if(!active) {
    openInput();  // a short tap: type instead of speaking
    return;
    }
  if(typed!='\0' && input.size()<MaxInput) {
    input.insert(cursor,1,typed);
    ++cursor;
    }
  update();
  }

void ChatOverlay::pttReset() {
  tDown        = false;
  recording    = false;
  tPendingChar = '\0';
  }

void ChatOverlay::addLine(std::string speaker, std::string text, uint32_t showMs) {
  Entry e;
  e.speaker    = std::move(speaker);
  e.text       = std::move(text);
  e.shownUntil = Application::tickCount() + showMs + LingerMs;
  log.push_back(std::move(e));
  while(log.size()>MaxLog)
    log.pop_front();
  update();
  }

void ChatOverlay::upsertLine(std::string_view key, std::string speaker, std::string text, uint32_t showMs) {
  for(auto it=log.rbegin(); it!=log.rend(); ++it) {
    if(it->key!=key)
      continue;
    it->text       = std::move(text);
    it->shownUntil = std::max(it->shownUntil, Application::tickCount() + showMs + LingerMs);
    update();
    return;
    }
  addLine(std::move(speaker),std::move(text),showMs);
  log.back().key = std::string(key);
  }

void ChatOverlay::extend(std::string_view key, uint32_t showMs) {
  for(auto it=log.rbegin(); it!=log.rend(); ++it)
    if(it->key==key) {
      it->shownUntil = std::max(it->shownUntil, Application::tickCount() + showMs + LingerMs);
      update();
      return;
      }
  }

void ChatOverlay::cut(std::string_view key, std::string said) {
  const uint64_t now = Application::tickCount();
  for(auto it=log.rbegin(); it!=log.rend(); ++it) {
    if(it->key!=key)
      continue;
    if(said.empty()) {
      it->shownUntil = now;
      } else {
      it->text       = std::move(said) + " -";
      it->shownUntil = now + LingerMs;
      }
    update();
    return;
    }
  }

void ChatOverlay::setStatus(std::string text) {
  status = std::move(text);
  update();
  }

bool ChatOverlay::hasVisibleContent() const {
  return !log.empty() && log.back().shownUntil>Application::tickCount();
  }

void ChatOverlay::tick() {
  if(tDown) {
    const uint64_t held = Application::tickCount() - tDownAt;
    if(!recording && held>=PttHoldMs)
      recording = true;
    if(recording && held>=PttMaxMs)
      pttRelease();
    }
  // repaint while something is on screen (cursor blink, subtitles expiring), and once after it is gone
  const bool visible = active || recording || !partial.empty() || !status.empty() || hasVisibleContent() ||
                       !deal.id.empty();
  if(visible || wasVisible)
    update();
  wasVisible = visible;
  }

void ChatOverlay::showDeal(std::string id, int32_t version, std::string npc,
                           std::vector<DealLine> npcGives, std::vector<DealLine> heroGives) {
  if(deal.id==id && deal.version==version) {
    deal.sent = false;  // the server answered: Y may be pressed again
    update();
    return;
    }
  // what is not in the shown version is new
  auto mark = [&](std::vector<DealLine>& now, const std::vector<DealLine>& before) {
    for(auto& l:now)
      l.fresh = deal.id==id && std::none_of(before.begin(),before.end(),[&](const DealLine& b){
        return b.text==l.text;
        });
    };
  mark(npcGives, deal.npcGives);
  mark(heroGives,deal.heroGives);
  deal.id        = std::move(id);
  deal.version   = version;
  deal.npc       = std::move(npc);
  deal.npcGives  = std::move(npcGives);
  deal.heroGives = std::move(heroGives);
  deal.changedAt = Application::tickCount();
  deal.sent      = false;
  update();
  }

void ChatOverlay::hideDeal(std::string_view id) {
  if(deal.id.empty() || (!id.empty() && deal.id!=id))
    return;
  deal = Deal();
  update();
  }

void ChatOverlay::markMissing(const std::function<bool(const DealLine&)>& lacks) {
  for(auto& l:deal.heroGives)
    l.missing = lacks(l);
  }

std::string ChatOverlay::pressDeal() {
  if(deal.id.empty())
    return "none";
  if(deal.sent)
    return "waiting";
  for(auto& l:deal.heroGives)
    if(l.missing)
      return "missing";
  if(Application::tickCount() < deal.changedAt + DealSettleMs)
    return "changed";
  deal.sent = true;
  if(onDealAccept)
    onDealAccept(deal.id,deal.version);
  update();
  return "sent";
  }

void ChatOverlay::paintEvent(PaintEvent& e) {
  Painter p(e);
  if(!deal.id.empty())
    paintDeal(p);
  paintChat(p);
  }

void ChatOverlay::paintDeal(Painter& p) {
  const uint64_t now   = Application::tickCount();
  const float    scale = Gothic::interfaceScale(this)*chatScale();
  auto&          text  = Resources::font(FontName,Resources::FontType::Normal,  scale);
  auto&          name  = Resources::font(FontName,Resources::FontType::Yellow,  scale);
  auto&          hint  = Resources::font(FontName,Resources::FontType::Disabled,scale);
  auto&          red   = Resources::font(FontName,Resources::FontType::Red,     scale);
  const int      lineH = text.pixelSize();
  const int      pad   = std::max(8,lineH/2);
  const int      boxW  = std::min(w()-2*pad, std::max(int(520*scale), w()/3));
  const int      textW = boxW-2*pad;
  const int      x0    = w()-boxW-pad;

  static const std::string title   = TextCodec::fromUtf8("Сделка: ");
  static const std::string gives   = TextCodec::fromUtf8(" даёт:");
  static const std::string youGive = TextCodec::fromUtf8("Ты даёшь:");
  static const std::string nothing = TextCodec::fromUtf8("ничего");
  // the game's font has no square brackets
  static const std::string accept  = TextCodec::fromUtf8("Y — принять");
  static const std::string lacking = TextCodec::fromUtf8("Y — принять, но не хватает отмеченного");
  static const std::string waiting = TextCodec::fromUtf8("Ждём ответа...");

  struct Row final {
    const GthFont* font;
    std::string    str;
    int            indent;
    };
  std::vector<Row> rows;
  const bool fresh = now < deal.changedAt + DealFreshMs;
  auto side = [&](const std::string& head, const std::vector<DealLine>& lines) {
    rows.push_back({&hint,head,0});
    if(lines.empty())
      rows.push_back({&hint,nothing,pad});
    for(auto& l:lines) {
      const GthFont* f = l.missing ? &red : (fresh && l.fresh ? &name : &text);
      rows.push_back({f,l.text,pad});
      }
    };
  rows.push_back({&name,title+deal.npc,0});
  side(deal.npc+gives,deal.npcGives);
  side(youGive,deal.heroGives);
  const bool missing = std::any_of(deal.heroGives.begin(),deal.heroGives.end(),[](const DealLine& l){
    return l.missing;
    });
  if(deal.sent)
    rows.push_back({&hint,waiting,0});
  else if(missing)
    rows.push_back({&hint,lacking,0});
  else
    rows.push_back({now < deal.changedAt + DealSettleMs ? &hint : &name,accept,0});

  int total = pad;
  for(auto& r:rows)
    total += r.font->textSize(textW-r.indent,r.str).h + pad/4;
  total += pad;

  int y = std::max(pad,h()/5);
  p.setBrush(Color(0.f,0.f,0.f,0.6f));
  p.drawRect(x0,y,boxW,total);
  y += pad;
  for(auto& r:rows) {
    r.font->drawText(p,x0+pad+r.indent,y+lineH,textW-r.indent,0,r.str,AlignLeft);
    y += r.font->textSize(textW-r.indent,r.str).h + pad/4;
    }
  }

void ChatOverlay::paintChat(Painter& p) {
  const uint64_t now = Application::tickCount();
  std::vector<const Entry*> rows;
  for(auto it=log.rbegin(); it!=log.rend(); ++it) {
    if(active ? rows.size()>=OpenRows : (rows.size()>=SubtitleRows || it->shownUntil<=now))
      break;
    rows.insert(rows.begin(),&*it);
    }
  // one line under the subtitles: the player speaking, his words being recognized, or a status
  const bool extra = recording || !partial.empty() || !status.empty();
  if(!active && !extra && rows.empty())
    return;

  const float scale = Gothic::interfaceScale(this)*chatScale();
  auto&       text  = Resources::font(FontName,Resources::FontType::Normal,  scale);
  auto&       name  = Resources::font(FontName,Resources::FontType::Yellow,  scale);
  auto&       hint  = Resources::font(FontName,Resources::FontType::Disabled,scale);
  auto&       rec   = Resources::font(FontName,Resources::FontType::Red,     scale);
  const int   lineH = text.pixelSize();
  const int   pad   = std::max(8,lineH/2);
  // ~75 characters per line of the large font, at least 60% of the window
  const int   boxW  = std::min(w()-2*pad, std::max(int(1000*scale), w()*3/5));
  const int   textW = boxW-2*pad;
  const int   x0    = (w()-boxW)/2;

  int total = pad;
  for(auto r:rows)
    total += lineH + text.textSize(textW,r->text).h + pad/2;
  if(extra)
    total += lineH;
  if(active)
    total += lineH;
  total += pad/2;

  int y = h() - 2*lineH - total;
  p.setBrush(Color(0.f,0.f,0.f,0.6f));
  p.drawRect(x0,y,boxW,total);

  y += pad;
  for(auto r:rows) {
    name.drawText(p,x0+pad,y+lineH,r->speaker);
    y += lineH;
    text.drawText(p,x0+pad,y+lineH,textW,0,r->text,AlignLeft);
    y += text.textSize(textW,r->text).h + pad/2;
    }

  if(recording) {
    static const std::string label = TextCodec::fromUtf8("Запись: ");
    static const std::string speak = TextCodec::fromUtf8("говорите... отпустите T, чтобы сказать");
    rec.drawText(p,x0+pad,y+lineH,label+fitTail(rec,partial.empty() ? speak : partial,textW-rec.textSize(label).w));
    y += lineH;
    }
  else if(!partial.empty()) {
    static const std::string label = TextCodec::fromUtf8("Распознаю: ");
    hint.drawText(p,x0+pad,y+lineH,label+fitTail(hint,partial,textW-hint.textSize(label).w));
    y += lineH;
    }
  else if(!status.empty()) {
    hint.drawText(p,x0+pad,y+lineH,fitTail(hint,status,textW));
    y += lineH;
    }

  if(!active)
    return;
  const std::string prompt = "> ";
  text.drawText(p,x0+pad,y+lineH,prompt+input);
  // blinking cursor
  const int   cx = x0+pad + text.textSize(prompt+input.substr(0,cursor)).w;
  const float a  = float(now%1000)/1000.f;
  p.setBrush(Color(1.f,1.f,1.f,a<0.5f ? 1.f : 0.f));
  p.drawRect(cx,y+2,std::max(1,int(scale)),lineH-4);
  }

void ChatOverlay::keyDownEvent(KeyEvent& e) {
  const bool plainT = e.key==Event::K_T && e.modifier==Event::M_NoModifier;
  if(!active) {
    // the game keeps every key but T: the hero moves and fights while the player speaks
    if(plainT && (tDown || (canSpeak && canSpeak()))) {
      e.accept();
      if(!tDown)
        pttPress('\0');  // held, it is push-to-talk right away; a short tap opens the input line
      return;
      }
    // Y accepts the deal in the window; while the input line is open it is a letter
    if(e.key==Event::K_Y && e.modifier==Event::M_NoModifier && !deal.id.empty() && (!canDeal || canDeal())) {
      e.accept();
      yDown = true;
      pressDeal();
      return;
      }
    e.ignore();
    return;
    }
  if(Event::K_F1<=e.key && e.key<=Event::K_F12) {
    e.ignore();
    return;
    }
  e.accept();
  if(tDown && e.key!=Event::K_ESCAPE)
    return;
  if(plainT && input.empty()) {
    // a word may start with this letter: decided on release (short tap) or after the hold delay
    pttPress(TextCodec::encode(e.code));
    return;
    }
  edit(e);
  }

void ChatOverlay::keyRepeatEvent(KeyEvent& e) {
  if(!active) {
    if((e.key==Event::K_T && tDown) || (e.key==Event::K_Y && yDown))
      e.accept(); else
      e.ignore();
    return;
    }
  e.accept();
  if(!tDown)
    edit(e);
  }

void ChatOverlay::keyUpEvent(KeyEvent& e) {
  const bool ours = active || (e.key==Event::K_T && tDown) || (e.key==Event::K_Y && yDown);
  if(e.key==Event::K_Y)
    yDown = false;
  if(e.key==Event::K_T)
    pttRelease();
  if(ours)
    e.accept(); else
    e.ignore();
  }

void ChatOverlay::edit(KeyEvent& e) {
  switch(e.key) {
    case Event::K_ESCAPE:
      if(tDown) {
        pttReset();
        if(onPttCancel)
          onPttCancel();
        }
      closeInput();
      return;
    case Event::K_Return: {
      const std::string txt = input;
      closeInput();
      if(!txt.empty() && onSubmit)
        onSubmit(txt);
      return;
      }
    case Event::K_Back:
      if(cursor>0)
        input.erase(--cursor,1);
      break;
    case Event::K_Delete:
      if(cursor<input.size())
        input.erase(cursor,1);
      break;
    case Event::K_Left:
      if(cursor>0)
        --cursor;
      break;
    case Event::K_Right:
      if(cursor<input.size())
        ++cursor;
      break;
    case Event::K_Home:
      cursor = 0;
      break;
    case Event::K_End:
      cursor = input.size();
      break;
    default: {
      if(e.code<32 || (e.modifier & (Event::M_Ctrl|Event::M_Command))!=0)
        break;
      const char c = TextCodec::encode(e.code);
      if(c!='\0' && input.size()<MaxInput) {
        input.insert(cursor,1,c);
        ++cursor;
        }
      break;
      }
    }
  update();
  }

// while the line is open, clicks and the wheel stay here (no swings at the air); the mouse still turns the hero
void ChatOverlay::mouseDownEvent(MouseEvent& e) {
  if(active) e.accept(); else e.ignore();
  }

void ChatOverlay::mouseUpEvent(MouseEvent& e) {
  if(active) e.accept(); else e.ignore();
  }

void ChatOverlay::mouseWheelEvent(MouseEvent& e) {
  if(active) e.accept(); else e.ignore();
  }
