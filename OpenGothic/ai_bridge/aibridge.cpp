#include "aibridge.h"

#include <Tempest/Application>
#include <Tempest/Log>
#include <Tempest/Timer>

#include <ixwebsocket/IXNetSystem.h>
#include <ixwebsocket/IXWebSocket.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <ctime>
#include <memory>
#include <mutex>
#include <random>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#include "game/gamescript.h"
#include "game/serialize.h"
#include "commandline.h"
#include "world/objects/npc.h"
#include "world/world.h"
#include "world/worldsound.h"
#include "gothic.h"

#include "chatoverlay.h"
#include "dialoggraph.h"
#include "storyworld.h"
#include "jsonread.h"
#include "jsonwriter.h"
#include "linelibrary.h"
#include "npccontroller.h"
#include "npcobserver.h"
#include "textcodec.h"
#include "voicebuffer.h"

using namespace Tempest;

namespace {

constexpr int         ProtocolVersion = 9;
constexpr const char* SaveEntry       = "ai_bridge/session";  // own entry in save games
constexpr const char* DefaultUrl      = "ws://127.0.0.1:47817";

constexpr uint32_t    MoodHoldMs      = 2000;  // the face of a line stays this long after it, then fades (a second)

// the face of the game's heads for a mood of a line (v9)
std::string_view moodFace(std::string_view mood) {
  if(mood=="neutral")  return "S_NEUTRAL";
  if(mood=="friendly") return "S_FRIENDLY";
  if(mood=="angry")    return "S_ANGRY";
  if(mood=="hostile")  return "S_HOSTILE";
  if(mood=="afraid")   return "S_FRIGHTENED";
  if(mood=="pain")     return "T_HURT";
  return "";
  }

std::string lower(std::string_view s) {
  std::string r(s);
  for(auto& c:r)
    c = char(std::tolower(static_cast<unsigned char>(c)));
  return r;
  }

std::string npcSymbol(World& world, Npc& npc) {
  if(auto sym = world.script().findSymbol(npc.instanceSymbol()))
    return sym->name();
  return {};
  }

// random id of a line of play (see Session below)
std::string newBranchId() {
  static std::mt19937_64 rng{std::random_device{}() ^ uint64_t(std::time(nullptr))};
  static const char*     hex = "0123456789abcdef";
  std::string id;
  for(int i=0; i<2; ++i) {
    uint64_t v = rng();
    for(int k=0; k<16; ++k, v>>=4)
      id += hex[v&0xF];
    }
  return id;
  }

std::vector<uint8_t> fromBase64(std::string_view in) {
  static const auto table = [] {
    std::array<int8_t,256> t{};
    t.fill(-1);
    const char* abc = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    for(int i=0; i<64; ++i)
      t[uint8_t(abc[i])] = int8_t(i);
    return t;
    }();
  std::vector<uint8_t> out;
  out.reserve(in.size()*3/4);
  uint32_t acc  = 0;
  int      bits = 0;
  for(char c:in) {
    const int8_t v = table[uint8_t(c)];
    if(v<0)
      continue;  // padding, line breaks
    acc   = (acc<<6) | uint32_t(v);
    bits += 6;
    if(bits>=8) {
      bits -= 8;
      out.push_back(uint8_t(acc>>bits));
      }
    }
  return out;
  }

// any npc of the world by script symbol (attached or not)
Npc* npcBySymbol(World& world, std::string_view symbol) {
  const size_t id = world.script().findSymbolIndex(symbol);
  return id==size_t(-1) ? nullptr : world.findNpcByInstance(id);
  }

std::vector<std::string> split(std::string_view s) {
  std::vector<std::string> ret;
  std::istringstream       in{std::string(s)};
  std::string              tok;
  while(in >> tok)
    ret.push_back(tok);
  return ret;
  }

}

struct Voice final {
  std::string                  line;
  std::string                  npc;
  std::shared_ptr<VoiceBuffer> buf;
  Tempest::SoundEffect         effect;
  uint64_t                     startedAt = 0;  // world ticks
  uint64_t                     drainedAt = 0;  // app ticks
  bool                         sized     = false;  // the line got its real length
  bool                         overlay   = false;  // said on the move: no output barrier to correct
  };

struct AiBridge::Impl final {
  struct Inbound final {
    enum Kind : uint8_t { Open, Close, Error, Message };
    Kind        kind = Message;
    std::string text;
    };

  Impl();
  ~Impl();

  // A session is one line of play: a new game or a loaded save starts a new branch. The server
  // keeps npc memories per branch; the memory head (`seq`) it reports goes into every save, and a
  // load branches off what the save remembers. World changes keep the branch.
  struct Session final {
    std::string branch;
    std::string parent;
    int64_t     parentSeq = 0;
    int64_t     seq       = 0;
    std::string reason;
    };

  void onTick(uint64_t dt);
  void onStartLoading();
  void onWorldLoaded();
  void takePendingSession();
  void startBranch(std::string parent, int64_t parentSeq, std::string_view reason);
  void sendSession();
  void updateSaveRecord();
  void onDebug(const rapidjson::Value& msg);
  void runDebug();
  void runWorldDebug(const rapidjson::Value& msg);
  void onPerception(Npc& self, Npc& other, Npc* victim, PercType perc);
  void onObservedPerception(Npc& self, Npc& other, Npc* victim, PercType perc);
  // who drives the npc instead of its scripts: "attached" or nobody
  std::string_view npcOwner(std::string_view symbol) const;
  bool onConsoleCommand(std::string_view cmd, std::string& reply);

  void handle(World* world, const std::string& text);
  void onSay(World& world, const rapidjson::Value& msg);
  void onSayAudio(World& world, const rapidjson::Value& msg);
  // streamed voices: playback starts with the line, the line is shortened to the real length
  void startVoice(const LineLibrary::Line& line);
  void tickVoices(World& world);
  void correctBarrier(World& world, struct Voice& v);
  void onChatStatus(World& world, const rapidjson::Value& msg);
  void onTranscript(const rapidjson::Value& msg);
  // v7: the npc is interrupted: its voice stops, its lines not started yet speak nothing
  void onHush(World& world, const rapidjson::Value& msg);

  // talking to the world (v7): T held is voice, a short tap opens the input line; nothing is modal,
  // the server decides whom the hero speaks to
  void ensureChat();
  bool canSpeak();
  // the npc the hero looks at (his focus), empty if none
  std::string focusSymbol(World& world);
  void submitChat(const std::string& text);
  void onLineStarted(const LineLibrary::Line& line);
  // an original line sent with `say` starts (the engine asks for its text)
  void onOriginalLine(std::string_view id);
  void sendPtt(std::string_view type);

  void send(std::string&& text);
  void sendResult(std::string_view req, bool ok, std::string_view err, std::string_view value = {});
  // debug commands about dialogs (-devmode): they run right away, in the game tick
  void onDialogDebug(World* world, const rapidjson::Value& msg);
  void onStoryEval(World& world, const rapidjson::Value& msg);
  void sendWorld(std::string_view type, World* world);

  std::string          url;
  ix::WebSocket        ws;

  std::mutex           sync;
  std::vector<Inbound> inbound;

  bool                 connected = false;
  std::string          lastError;
  NpcController        ctrl{[this](std::string&& s){ send(std::move(s)); }};
  NpcObserver          observer{[this](std::string&& s){ send(std::move(s)); },
                                [this](std::string_view symbol){ return npcOwner(symbol); }};

  Session              session;
  bool                 saving = false;  // a save is in progress: the same world comes back
  // shared with the loading thread (save-game hooks)
  std::mutex           saveSync;
  std::string          saveRecord;      // written into the next save
  bool                 pendingSession  = false;
  bool                 pendingFromSave = false;
  std::string          pendingRecord;   // read from the loaded save

  // debug save/load must not run inside the game tick (it clears the world mid-frame):
  // it waits for the application's event loop
  Tempest::Timer       debugTimer;
  std::string          debugReq, debugCmd, debugSlot;

  LineLibrary                  lines;
  std::vector<struct Voice>    voices;
  std::unordered_map<std::string,std::string> lastLine;  // npc -> line it speaks (or spoke) last
  std::unordered_map<std::string,std::string> originalLines;  // original line sent with `say` -> npc, until it starts
  std::unique_ptr<ChatOverlay> chat;
  // dialogs of the story graph: the lines of the director's dialogs are runtime lines too
  uint64_t                     graphLines = 0;
  DialogGraph                  graph{[this](std::string&& s){ send(std::move(s)); },
                                     [this](const std::string& npc, const std::string& text) {
                                       auto id = "AIB_G" + std::to_string(++graphLines);
                                       lines.add(id,npc,text);
                                       return id;
                                       }};
  // the director's hands in the world: tagged creatures, deaths, effects on npc's (v8)
  StoryWorld                   story{[this](std::string&& s){ send(std::move(s)); },
                                     [this](World& w, Npc& npc) {
                                       std::string err;
                                       const auto  symbol = npcSymbol(w,npc);
                                       if(ctrl.isAttached(symbol))
                                         ctrl.detach(w,symbol,"story",err);
                                       }};
  };

AiBridge::Impl::Impl() {
  const char* envUrl = std::getenv("GOTHIC_AI_URL");
  url = (envUrl!=nullptr && envUrl[0]!='\0') ? envUrl : DefaultUrl;

  auto& gothic = Gothic::inst();
  gothic.onTick              .bind(this,&Impl::onTick);
  gothic.onStartLoading      .bind(this,&Impl::onStartLoading);
  gothic.onWorldLoaded       .bind(this,&Impl::onWorldLoaded);
  gothic.onExternalPerception.bind(this,&Impl::onPerception);
  gothic.onObservedPerception.bind(this,&Impl::onObservedPerception);
  gothic.interceptPerception = [this](Npc& self, Npc& other, Npc* victim, PercType perc) {
    World* world = Gothic::inst().world();
    return connected && world!=nullptr && observer.intercept(*world,self,other,victim,perc);
    };
  gothic.consoleCommandFn = [this](std::string_view cmd, std::string& reply) {
    return onConsoleCommand(cmd,reply);
    };
  gothic.externalMessageFn = [this](std::string_view id, std::string_view& text, uint32_t& time) {
    if(lines.lookup(id,text,time))
      return true;
    if(!originalLines.empty())
      onOriginalLine(id);
    return false;
    };
  gothic.externalSoundFn = [this](std::string_view file, std::vector<uint8_t>& wav) {
    return lines.sound(file,wav);
    };
  // both run on the loading thread
  gothic.saveExtFn = [this](Serialize& fout) {
    std::lock_guard<std::mutex> guard(saveSync);
    fout.setEntry(SaveEntry);
    fout.write(saveRecord);
    };
  gothic.sessionStartFn = [this](Serialize* fin) {
    std::string rec;
    if(fin!=nullptr && fin->setEntry(SaveEntry))
      fin->read(rec);
    std::lock_guard<std::mutex> guard(saveSync);
    pendingSession  = true;
    pendingFromSave = fin!=nullptr;
    pendingRecord   = std::move(rec);
    };
  debugTimer.timeout.bind(this,&Impl::runDebug);
  lines.onStart   = [this](const LineLibrary::Line& l) { onLineStarted(l); };
  ctrl.onFollowEnded = [this](const std::string& npc, std::string_view) {
    // following on its own ended: back to its routine (the server takes it again if it wants)
    std::string err;
    if(auto world = Gothic::inst().world())
      ctrl.detach(*world,npc,"follow_ended",err);
    };

  ix::initNetSystem();
  ws.setUrl(url);
  ws.setMinWaitBetweenReconnectionRetries(500);
  ws.setMaxWaitBetweenReconnectionRetries(3000);
  ws.setOnMessageCallback([this](const ix::WebSocketMessagePtr& msg) {
    // websocket thread: hand over to the main thread
    Inbound in;
    switch(msg->type) {
      case ix::WebSocketMessageType::Open:
        in.kind = Inbound::Open;
        break;
      case ix::WebSocketMessageType::Close:
        in.kind = Inbound::Close;
        in.text = msg->closeInfo.reason;
        break;
      case ix::WebSocketMessageType::Error:
        in.kind = Inbound::Error;
        in.text = msg->errorInfo.reason;
        break;
      case ix::WebSocketMessageType::Message:
        in.kind = Inbound::Message;
        in.text = msg->str;
        break;
      default:
        return;
      }
    std::lock_guard<std::mutex> guard(sync);
    inbound.push_back(std::move(in));
    });
  ws.start();
  Log::i("[ai] bridge started, server: ",url);
  }

AiBridge::Impl::~Impl() {
  auto& gothic = Gothic::inst();
  gothic.onTick              .ubind(this,&Impl::onTick);
  gothic.onStartLoading      .ubind(this,&Impl::onStartLoading);
  gothic.onWorldLoaded       .ubind(this,&Impl::onWorldLoaded);
  gothic.onExternalPerception.ubind(this,&Impl::onPerception);
  gothic.onObservedPerception.ubind(this,&Impl::onObservedPerception);
  gothic.interceptPerception = nullptr;
  gothic.consoleCommandFn  = nullptr;
  gothic.externalMessageFn = nullptr;
  gothic.externalSoundFn   = nullptr;
  gothic.saveExtFn         = nullptr;
  gothic.sessionStartFn    = nullptr;
  voices.clear();  // before the game's sound device goes
  chat.reset();
  ws.stop();
  ix::uninitNetSystem();
  }

void AiBridge::Impl::onTick(uint64_t /*dt*/) {
  std::vector<Inbound> queue;
  {
    std::lock_guard<std::mutex> guard(sync);
    queue.swap(inbound);
  }

  World* world = Gothic::inst().world();
  for(auto& in:queue) {
    switch(in.kind) {
      case Inbound::Open:
        connected = true;
        lastError.clear();
        Log::i("[ai] connected to ",url);
        send(JsonWriter("hello")
               .integer("protocol",ProtocolVersion)
               .str("client","opengothic-ai-bridge")
               .str("codepage","cp1251")
               .finish());
        session.reason = "connect";
        sendSession();
        if(world!=nullptr)
          sendWorld("world_loaded",world);
        break;
      case Inbound::Close:
      case Inbound::Error:
        if(connected) {
          Log::i("[ai] disconnected: ",in.text);
          Gothic::inst().onPrint(TextCodec::fromUtf8("Связь с ИИ-сервером потеряна"));
          // nobody drives the npc's anymore: hand them back to their routines, threats to their scripts
          if(world!=nullptr) {
            ctrl.detachAll(*world,"server_disconnected");
            observer.releaseAll(*world);
            }
          observer.setHoldThreats(false);
          }
        else if(in.kind==Inbound::Error && in.text!=lastError) {
          Log::i("[ai] connection failed: ",in.text);
          }
        connected = false;
        if(in.kind==Inbound::Error)
          lastError = in.text;
        break;
      case Inbound::Message:
        handle(world,in.text);
        break;
      }
    }

  if(world!=nullptr) {
    ctrl.tick(*world);
    if(connected) {
      observer.tick(*world);
      story.tick(*world);
      }
    tickVoices(*world);
    }
  ensureChat();
  chat->tick();
  }

void AiBridge::Impl::onStartLoading() {
  if(Gothic::inst().checkLoading()==Gothic::LoadState::Saving) {
    // the same session continues after saving: npc's stay attached
    saving = true;
    return;
    }
  saving = false;
  if(chat!=nullptr)
    chat->closeInput();
  voices.clear();  // the world and its sound device are about to go
  lastLine.clear();
  originalLines.clear();
  lines.clear();
  observer.clear();
  Log::i("[story] ",graph.status());
  graph.clear();  // the server sends the patches of the new branch
  story.clear();
  if(auto world = Gothic::inst().world())
    ctrl.detachAll(*world,"loading"); else
    ctrl.dropAll("loading");
  send(JsonWriter("loading").finish());
  }

void AiBridge::Impl::onWorldLoaded() {
  if(saving) {
    saving = false;
    send(JsonWriter("saved").str("branch",session.branch).integer("seq",session.seq).finish());
    return;
    }
  takePendingSession();
  sendWorld("world_loaded",Gothic::inst().world());
  }

void AiBridge::Impl::takePendingSession() {
  bool        pending  = false;
  bool        fromSave = false;
  std::string rec;
  {
    std::lock_guard<std::mutex> guard(saveSync);
    std::swap(pending,pendingSession);
    fromSave = pendingFromSave;
    rec      = std::move(pendingRecord);
  }
  if(!pending) {
    // a world change: same line of play
    if(session.branch.empty())
      startBranch("",0,"new_game");
    return;
    }
  std::string parent;
  int64_t     parentSeq = 0;
  if(fromSave && !rec.empty()) {
    rapidjson::Document doc;
    doc.Parse(rec.c_str(),rec.size());
    if(!doc.HasParseError() && doc.IsObject()) {
      parent    = std::string(JsonRead::str(doc,"branch"));
      parentSeq = JsonRead::integer(doc,"seq",0);
      }
    }
  startBranch(std::move(parent),parentSeq,fromSave ? "load" : "new_game");
  }

void AiBridge::Impl::startBranch(std::string parent, int64_t parentSeq, std::string_view reason) {
  session.branch    = newBranchId();
  session.parent    = std::move(parent);
  session.parentSeq = session.parent.empty() ? 0 : parentSeq;
  session.seq       = session.parentSeq;
  session.reason    = std::string(reason);
  updateSaveRecord();
  if(session.parent.empty())
    Log::i("[ai] session ",session.branch," (",reason,")"); else
    Log::i("[ai] session ",session.branch," (",reason," from ",session.parent,"@",session.parentSeq,")");
  sendSession();
  }

void AiBridge::Impl::sendSession() {
  if(session.branch.empty())
    return;
  JsonWriter j("session");
  j.str("branch",session.branch);
  if(!session.parent.empty())
    j.str("parent",session.parent).integer("parent_seq",session.parentSeq);
  j.str("reason",session.reason);
  send(j.finish());
  }

void AiBridge::Impl::updateSaveRecord() {
  auto rec = JsonWriter("ai_session").str("branch",session.branch).integer("seq",session.seq).finish();
  std::lock_guard<std::mutex> guard(saveSync);
  saveRecord = std::move(rec);
  }

void AiBridge::Impl::onPerception(Npc& self, Npc& other, Npc* victim, PercType perc) {
  if(auto world = Gothic::inst().world())
    ctrl.perception(*world,self,other,victim,perc);
  }

void AiBridge::Impl::onObservedPerception(Npc& self, Npc& other, Npc* victim, PercType perc) {
  if(!connected)
    return;
  if(auto world = Gothic::inst().world())
    observer.perception(*world,self,other,victim,perc);
  }

std::string_view AiBridge::Impl::npcOwner(std::string_view symbol) const {
  if(ctrl.isAttached(symbol))
    return "attached";
  return {};
  }

void AiBridge::Impl::handle(World* world, const std::string& text) {
  rapidjson::Document doc;
  doc.Parse(text.c_str(),text.size());
  if(doc.HasParseError() || !doc.IsObject()) {
    Log::e("[ai] malformed message: ",text);
    return;
    }

  const auto type = JsonRead::str(doc,"type");
  const auto req  = JsonRead::str(doc,"req");

  if(type=="ping") {
    send(JsonWriter("pong").str("req",req).finish());
    return;
    }
  if(type=="memory") {
    if(JsonRead::str(doc,"branch")==session.branch) {
      session.seq = JsonRead::integer(doc,"seq",session.seq);
      updateSaveRecord();
      }
    return;
    }
  if(type=="debug") {
    const auto cmd = JsonRead::str(doc,"cmd");
    if(cmd=="dialogs" || cmd=="choose" || cmd=="eval" || cmd=="check")
      onDialogDebug(world,doc); else
      onDebug(doc);
    return;
    }
  if(type=="graph_patch") {
    // patches don't need a world: names are checked when there is one
    auto errors = graph.apply(doc);
    std::string all;
    for(auto& e : errors)
      all += (all.empty() ? "" : "; ") + e;
    if(!req.empty())
      sendResult(req,errors.empty(),all);
    return;
    }
  if(type=="threat_control") {
    // the server's behavioral model decides threats to free npc's first (held from the scripts)
    observer.setHoldThreats(JsonRead::boolean(doc,"hold",false));
    return;
    }

  if(world==nullptr) {
    if(type=="action")
      send(JsonWriter("action_result")
             .str("id",JsonRead::str(doc,"id"))
             .str("npc",JsonRead::str(doc,"npc"))
             .str("status","rejected")
             .str("detail","no world loaded")
             .finish()); else
      sendResult(req,false,"no world loaded");
    return;
    }

  if(type=="attach") {
    std::string err;
    const bool  ok = ctrl.attach(*world,JsonRead::str(doc,"npc"),err);
    sendResult(req,ok,err);
    }
  else if(type=="detach") {
    std::string err;
    const bool  ok = ctrl.detach(*world,JsonRead::str(doc,"npc"),"server",err);
    sendResult(req,ok,err);
    }
  else if(type=="action") {
    ctrl.action(*world,doc);
    }
  else if(type=="snapshot") {
    // any npc since v7: attached ones on the next tick, free ones at once
    if(!ctrl.requestSnapshot(*world,JsonRead::str(doc,"npc")))
      Log::e("[ai] snapshot of unknown npc: ",JsonRead::str(doc,"npc"));
    }
  else if(type=="story_spawn") {
    std::string err;
    const int   made = story.spawn(*world,doc,err);
    sendResult(req,made>0,err,std::to_string(made));
    }
  else if(type=="story_watch") {
    story.watch(doc);
    }
  else if(type=="story_nearby") {
    auto list = story.nearby(*world,doc);
    sendResult(req,!list.empty(),"no npc " + std::string(JsonRead::str(doc,"npc")),list);
    }
  else if(type=="story_state") {
    sendResult(req,true,"",story.state(*world,doc));
    }
  else if(type=="story_tag") {
    const int found = story.tag(*world,doc);
    sendResult(req,true,"",std::to_string(found));
    }
  else if(type=="story_eval") {
    onStoryEval(*world,doc);
    }
  else if(type=="story_effect") {
    std::string err;
    const bool  ok = story.effects(*world,doc,err);
    sendResult(req,ok,err);
    }
  else if(type=="npc_act") {
    observer.act(*world,doc);
    }
  else if(type=="routine") {
    observer.routine(*world,doc);
    }
  else if(type=="hold_verdict") {
    observer.verdict(*world,doc);
    }
  else if(type=="say") {
    onSay(*world,doc);
    }
  else if(type=="chat_text") {
    // the reply as it is generated: shown right away, its lines voice it later
    ensureChat();
    const auto symbol = JsonRead::str(doc,"npc");
    const auto text   = TextCodec::fromUtf8(JsonRead::str(doc,"text"));
    Npc*       npc    = npcBySymbol(*world,symbol);
    chat->upsertLine(JsonRead::str(doc,"reply"),
                     npc!=nullptr ? std::string(npc->displayName()) : std::string(symbol),
                     text,LineLibrary::durationFor(text));
    }
  else if(type=="say_audio" || type=="say_end") {
    onSayAudio(*world,doc);
    }
  else if(type=="chat_status") {
    onChatStatus(*world,doc);
    }
  else if(type=="transcript") {
    onTranscript(doc);
    }
  else if(type=="hush") {
    onHush(*world,doc);
    }
  else {
    sendResult(req,false,"unknown message type: " + std::string(type));
    }
  }

void AiBridge::Impl::onDebug(const rapidjson::Value& msg) {
  const auto req = JsonRead::str(msg,"req");
  if(!CommandLine::inst().isDevMode()) {
    sendResult(req,false,"debug commands need -devmode");
    return;
    }
  const auto cmd = JsonRead::str(msg,"cmd");
  if(cmd=="spawn" || cmd=="player_weapon" || cmd=="player_goto" || cmd=="ptt" || cmd=="type" || cmd=="kill" ||
     cmd=="time") {
    // world changes that are safe within the tick
    runWorldDebug(msg);
    return;
    }
  if(!debugReq.empty()) {
    sendResult(req,false,"another debug command is pending");
    return;
    }
  debugReq  = std::string(req);
  debugCmd  = std::string(JsonRead::str(msg,"cmd"));
  debugSlot = "save_slot_" + std::to_string(JsonRead::integer(msg,"slot",0)) + ".sav";
  debugTimer.start(1);
  }

void AiBridge::Impl::runDebug() {
  debugTimer.stop();
  const auto req  = std::move(debugReq);
  const auto cmd  = std::move(debugCmd);
  const auto slot = std::move(debugSlot);
  debugReq.clear();
  auto& gothic = Gothic::inst();
  if(gothic.checkLoading()!=Gothic::LoadState::Idle) {
    sendResult(req,false,"busy loading or saving");
    return;
    }
  if(cmd=="save") {
    // the game refuses to save during dialogs and cutscenes
    if(gothic.isInDialog() || (gothic.world()!=nullptr && gothic.world()->currentCs()!=nullptr)) {
      sendResult(req,false,"cannot save now (dialog or cutscene)");
      return;
      }
    gothic.save(slot,"AI test");
    sendResult(req,gothic.checkLoading()==Gothic::LoadState::Saving,"save did not start");
    }
  else if(cmd=="load") {
    gothic.load(slot);
    sendResult(req,true,"");
    }
  else {
    sendResult(req,false,"unknown debug command: " + cmd);
    }
  }

void AiBridge::Impl::runWorldDebug(const rapidjson::Value& msg) {
  const auto req   = JsonRead::str(msg,"req");
  const auto cmd   = JsonRead::str(msg,"cmd");
  World*     world = Gothic::inst().world();
  Npc*       pl    = world!=nullptr ? world->player() : nullptr;
  if(world==nullptr || pl==nullptr) {
    sendResult(req,false,"no world loaded");
    return;
    }
  if(cmd=="spawn") {
    // a creature next to an npc (or the player): monsters for tests of threats
    std::string inst(JsonRead::str(msg,"instance"));
    for(auto& c:inst)
      c = char(std::toupper(static_cast<unsigned char>(c)));
    const size_t id   = world->script().findSymbolIndex(inst);
    const auto   near = JsonRead::str(msg,"near");
    Npc*         at   = near.empty() ? pl : npcBySymbol(*world,near);
    if(id==size_t(-1) || at==nullptr) {
      sendResult(req,false,id==size_t(-1) ? "unknown instance" : "unknown npc to spawn near");
      return;
      }
    // towards the player (open ground both have walked), else where the npc faces
    const float dist = float(JsonRead::integer(msg,"distance",600));
    auto        pos  = at->position();
    float       dx   = std::cos(at->rotationRad()), dz = std::sin(at->rotationRad());
    if(at!=pl) {
      const auto  d   = pl->position()-pos;
      const float len = std::sqrt(d.x*d.x+d.z*d.z);
      if(len>1) {
        dx = d.x/len;
        dz = d.z/len;
        }
      }
    pos.x += dist*dx;
    pos.z += dist*dz;
    sendResult(req,world->addNpc(id,pos)!=nullptr,"could not spawn");
    return;
    }
  if(cmd=="time") {
    // the clock goes to `hour`:`minute`, the next day if that is earlier than now (like sleeping)
    world->setDayTime(int32_t(JsonRead::integer(msg,"hour",8)),int32_t(JsonRead::integer(msg,"minute",0)));
    sendResult(req,true,"");
    return;
    }
  if(cmd=="kill") {
    // an npc dies, killed by no one (a quest giver for the director's repairs)
    Npc* npc = npcBySymbol(*world,JsonRead::str(msg,"npc"));
    if(npc==nullptr || npc->isDead() || npc->isPlayer()) {
      sendResult(req,false,npc==nullptr ? "no such npc" : "it can't die now");
      return;
      }
    npc->changeAttribute(ATR_HITPOINTS,-npc->attribute(ATR_HITPOINTSMAX)-1,false);
    sendResult(req,npc->isDead(),"it did not die (immortal?)");
    return;
    }
  if(cmd=="player_goto") {
    // the hero steps to a waypoint (walking away from a conversation): a named one, the nearest one at
    // least `distance` cm away from an npc (`near`), or one next to an npc (`to`, `distance` cm off)
    const WayPoint* wp = nullptr;
    if(auto name = JsonRead::str(msg,"waypoint"); !name.empty()) {
      wp = world->findPoint(name,false);
      }
    else if(Npc* to = npcBySymbol(*world,JsonRead::str(msg,"to"))) {
      const float off = float(JsonRead::integer(msg,"distance",300));
      const auto  at  = to->position();
      wp = world->findWayPoint(at,[&](const WayPoint& w) {
        return (w.position()-at).quadLength() >= off*off;
        });
      }
    else if(Npc* from = npcBySymbol(*world,JsonRead::str(msg,"near"))) {
      const float min  = float(JsonRead::integer(msg,"distance",1500));
      const auto  at   = from->position();
      wp = world->findWayPoint(pl->position(),[&](const WayPoint& w) {
        return (w.position()-at).quadLength() >= min*min;
        });
      }
    if(wp==nullptr) {
      sendResult(req,false,"unknown waypoint");
      return;
      }
    pl->setPosition(wp->position());
    sendResult(req,true,"");
    return;
    }
  if(cmd=="ptt") {
    // the T key as the player presses it (start), holds and lets go (stop) or taps it (cancel): the
    // focus is the hero's own
    const auto state = JsonRead::str(msg,"state");
    if(state!="start" && state!="stop" && state!="cancel") {
      sendResult(req,false,"ptt state: start, stop or cancel");
      return;
      }
    sendResult(req,true,"");
    sendPtt(state=="start" ? "ptt_start" : (state=="stop" ? "ptt_stop" : "ptt_cancel"));
    return;
    }
  if(cmd=="type") {
    // a line typed into the input line and sent
    sendResult(req,true,"");
    submitChat(TextCodec::fromUtf8(JsonRead::str(msg,"text")));
    return;
    }
  // player_weapon: the player draws ("melee") or puts away ("none") a weapon
  const auto state = JsonRead::str(msg,"state");
  const bool ok    = state=="none" ? pl->closeWeapon(false) : pl->drawWeaponMelee();
  sendResult(req,ok,"the player cannot switch weapons now");
  }

void AiBridge::Impl::send(std::string&& text) {
  if(!connected)
    return;
  ws.sendText(text);
  }

void AiBridge::Impl::sendResult(std::string_view req, bool ok, std::string_view err, std::string_view value) {
  JsonWriter j("result");
  j.str("req",req).boolean("ok",ok);
  if(!ok)
    j.str("error",err);
  if(!value.empty())
    j.raw("value",value);
  send(j.finish());
  }

void AiBridge::Impl::onDialogDebug(World* world, const rapidjson::Value& msg) {
  const auto req = JsonRead::str(msg,"req");
  const auto cmd = JsonRead::str(msg,"cmd");
  if(!CommandLine::inst().isDevMode()) {
    sendResult(req,false,"debug commands need -devmode");
    return;
    }
  if(world==nullptr) {
    sendResult(req,false,"no world loaded");
    return;
    }
  if(cmd=="check") {
    sendResult(req,true,"",graph.checkWorld(*world));
    return;
    }
  const auto symbol = JsonRead::str(msg,"npc");
  Npc*       npc    = symbol.empty() ? nullptr : npcBySymbol(*world,symbol);
  if(npc==nullptr && (cmd!="eval" || !symbol.empty())) {
    sendResult(req,false,"no npc " + std::string(symbol));
    return;
    }
  std::string err;
  if(cmd=="dialogs") {
    rapidjson::StringBuffer                    buf;
    rapidjson::Writer<rapidjson::StringBuffer> w(buf);
    w.StartArray();
    for(auto& id : graph.offered(*world,*npc))
      w.String(id.data(),rapidjson::SizeType(id.size()));
    w.EndArray();
    sendResult(req,true,"",std::string_view(buf.GetString(),buf.GetSize()));
    }
  else if(cmd=="choose") {
    const bool ok = graph.choose(*world,*npc,JsonRead::str(msg,"node"),err);
    sendResult(req,ok,err);
    }
  else if(cmd=="eval") {
    auto it = msg.FindMember("expr");
    std::string value;
    if(it==msg.MemberEnd() || !graph.evaluate(*world,npc,it->value,value,err))
      sendResult(req,false,err.empty() ? "no expr" : err); else
      sendResult(req,true,"",value);
    }
  }

void AiBridge::Impl::onStoryEval(World& world, const rapidjson::Value& msg) {
  // story graph expressions on the live VM, self = `npc`, other = the hero: what the director's rules ask
  const auto req = JsonRead::str(msg,"req");
  Npc*       npc = npcBySymbol(world,JsonRead::str(msg,"npc"));
  auto       it  = msg.FindMember("exprs");
  if(npc==nullptr || it==msg.MemberEnd() || !it->value.IsArray()) {
    sendResult(req,false,npc==nullptr ? "no npc " + std::string(JsonRead::str(msg,"npc")) : "no exprs");
    return;
    }
  std::string out = "[";
  for(auto& e : it->value.GetArray()) {
    std::string value, err;
    if(!graph.evaluate(world,npc,e,value,err))
      value = "null";
    out += (out.size()>1 ? "," : "") + value;
    }
  sendResult(req,true,"",out + "]");
  }

void AiBridge::Impl::sendWorld(std::string_view type, World* world) {
  if(world==nullptr)
    return;
  send(JsonWriter(type).str("world",world->name()).finish());
  }

bool AiBridge::Impl::onConsoleCommand(std::string_view cmd, std::string& reply) {
  auto args = split(cmd);
  if(args.empty() || lower(args[0])!="ai")
    return false;

  World*     world = Gothic::inst().world();
  const auto sub   = args.size()>1 ? lower(args[1]) : std::string("status");

  if(sub=="status") {
    reply  = std::string(connected ? "connected to " : "NOT connected to ") + url;
    reply += "; " + ctrl.status(world);
    reply += "; " + graph.status();
    return true;
    }

  if(world==nullptr) {
    reply = "no world loaded";
    return true;
    }

  std::string err;
  if(sub=="attach" && args.size()>2) {
    if(ctrl.attach(*world,args[2],err))
      reply = "attached " + args[2] + (connected ? "" : " (server is not connected!)"); else
      reply = err;
    return true;
    }
  if(sub=="detach") {
    if(args.size()>2) {
      reply = ctrl.detach(*world,args[2],"console",err) ? "detached " + args[2] : err;
      } else {
      ctrl.detachAll(*world,"console");
      reply = "detached all";
      }
    return true;
    }

  if(sub=="dialogs" && args.size()>2) {
    auto symbol = args[2];
    for(auto& c : symbol)
      c = char(std::toupper(static_cast<unsigned char>(c)));
    Npc* npc = npcBySymbol(*world,symbol);
    if(npc==nullptr) {
      reply = "no npc " + args[2];
      return true;
      }
    reply = args[2] + " offers:";
    for(auto& id : graph.offered(*world,*npc))
      reply += " " + id;
    return true;
    }

  reply = "usage: ai status | ai attach <npc> | ai detach [npc] | ai dialogs <npc>";
  return true;
  }

AiBridge::AiBridge() {
  const char* env = std::getenv("GOTHIC_AI");
  if(env!=nullptr && std::string_view(env)=="0") {
    Log::i("[ai] bridge disabled (GOTHIC_AI=0)");
    return;
    }
  impl = std::make_unique<Impl>();
  }

AiBridge::~AiBridge() = default;

void AiBridge::Impl::onSay(World& world, const rapidjson::Value& msg) {
  const auto symbol = std::string(JsonRead::str(msg,"npc"));
  const auto line   = std::string(JsonRead::str(msg,"line"));
  Npc*       pl     = world.player();
  Npc*       npc    = ctrl.find(world,symbol);
  if(npc==nullptr) {
    // a free npc (v6) may speak too, unless it is in a dialog or out of action
    npc = npcBySymbol(world,symbol);
    if(npc!=nullptr && (npc->isPlayer() || npc->isDead() || npc->isDown() || npc->isTalk() ||
                        Gothic::inst().isNpcInDialog(*npc)))
      npc = nullptr;
    }
  if(npc==nullptr || pl==nullptr) {
    Log::e("[ai] say: npc cannot speak now: ",symbol," ",line);
    return;
    }
  if(!LineLibrary::isExternal(line)) {
    // an original line of the game (v8): subtitle and voice come from the game's own files
    if(Gothic::inst().messageByName(line).empty()) {
      Log::e("[ai] say: no such line: ",line);
      return;
      }
    originalLines[line] = symbol;
    npc->aiPush(AiQueue::aiOutput(*pl,line,0));
    return;
    }
  auto wav   = fromBase64(JsonRead::str(msg,"audio"));
  auto voice = std::shared_ptr<VoiceBuffer>();
  if(JsonRead::boolean(msg,"stream",false))
    voice = std::make_shared<VoiceBuffer>(uint32_t(JsonRead::integer(msg,"rate",24000)));
  lines.add(line,symbol,TextCodec::fromUtf8(JsonRead::str(msg,"text")),std::move(wav),std::move(voice),
            std::string(JsonRead::str(msg,"reply")));
  lines.find(line)->mood = std::string(JsonRead::str(msg,"mood"));
  if(!JsonRead::boolean(msg,"overlay",false)) {
    npc->aiPush(AiQueue::aiOutput(*pl,line,0));
    return;
    }
  // on the move (v6): not queued, so the npc keeps doing what it does (running, fighting)
  auto* l = lines.find(line);
  l->overlay = true;
  std::string_view text;
  uint32_t         time = 0;
  lines.lookup(line,text,time);  // starts it: voice stream, subtitle, line_started
  if(!l->wav.empty()) {
    uint64_t len = 0;
    std::shared_ptr<LipSync::Track> lips;
    world.addDlgSound(line + ".WAV",npc->mapHeadBone(),WorldSound::talkRange,len,&lips);
    npc->startSpeech(std::move(lips));
    }
  npc->startFaceAnim("VISEME",1,time);
  // it looks at the hero while it speaks, and the hero at it (a line in the queue does that itself)
  npc->setTalkPartner(pl,time+1000);
  pl->setTalkPartner(npc,time+1000);
  }

void AiBridge::Impl::onSayAudio(World& world, const rapidjson::Value& msg) {
  auto* line = lines.find(JsonRead::str(msg,"line"));
  if(line==nullptr || line->voice==nullptr)
    return;
  if(JsonRead::str(msg,"type")=="say_audio") {
    const auto pcm = fromBase64(JsonRead::str(msg,"data"));
    line->voice->append(pcm.data(),pcm.size());
    return;
    }
  line->voice->finish();
  for(auto& v:voices)
    if(v.line==line->id)
      correctBarrier(world,v);
  }

void AiBridge::Impl::startVoice(const LineLibrary::Line& line) {
  World*       world   = Gothic::inst().world();
  GameSession* session = Gothic::inst().gameSession();
  Npc*         npc     = world!=nullptr ? npcBySymbol(*world,line.npc) : nullptr;
  if(npc==nullptr || session==nullptr)
    return;
  Voice v;
  v.line      = line.id;
  v.npc       = line.npc;
  v.buf       = line.voice;
  v.startedAt = world->tickCount();
  v.sized     = line.voice->isFinished() || line.overlay;  // nothing to correct then
  v.overlay   = line.overlay;
  v.effect    = session->loadSound(VoiceBuffer::producer(line.voice));
  if(!v.effect.isEmpty()) {
    v.effect.setPosition(npc->mapHeadBone());
    v.effect.setMaxDistance(WorldSound::talkRange);
    v.effect.setVolume(2.f);  // as WorldSound does for dialog speech
    v.effect.play();
    npc->startSpeech(line.voice->lipTrack());  // the mouth follows what the audio thread plays
    }
  Log::i("[ai] voice ",line.id," starts with ",line.voice->durationMs()," ms of audio",
         line.voice->isFinished() ? " (complete)" : " (streaming)");
  voices.push_back(std::move(v));
  }

void AiBridge::Impl::correctBarrier(World& world, Voice& v) {
  if(v.sized)
    return;
  v.sized = true;
  Npc* npc = npcBySymbol(world,v.npc);
  if(npc==nullptr || lastLine[v.npc]!=v.line)
    return;  // the npc has moved on
  const uint64_t end = v.startedAt + v.buf->durationMs() + LineLibrary::VoicePauseMs;
  const uint64_t now = world.tickCount();
  npc->setAiOutputBarrier(end>now ? end-now : 0, false);
  }

void AiBridge::Impl::tickVoices(World& world) {
  const uint64_t now = Application::tickCount();
  for(auto& v:voices) {
    if(v.buf->isFinished() && !v.sized)
      correctBarrier(world,v);
    if(!v.effect.isEmpty())
      if(auto npc = npcBySymbol(world,v.npc))
        v.effect.setPosition(npc->mapHeadBone());
    if(v.drainedAt==0 && v.buf->isDrained()) {
      v.drainedAt = now;
      Log::i("[ai] voice ",v.line," played, ",v.buf->durationMs()," ms");
      }
    }
  // keep the source a little after the last sample: OpenAL still holds a buffer of it
  voices.erase(std::remove_if(voices.begin(),voices.end(),[now](const Voice& v) {
    return v.drainedAt!=0 && now>v.drainedAt+500;
    }),voices.end());
  }

void AiBridge::Impl::onChatStatus(World& world, const rapidjson::Value& msg) {
  if(chat==nullptr)
    return;
  const auto status = JsonRead::str(msg,"status");
  if(status=="thinking") {
    Npc* npc = npcBySymbol(world,JsonRead::str(msg,"npc"));
    if(npc!=nullptr)
      chat->setStatus(std::string(npc->displayName()) + TextCodec::fromUtf8(" думает..."));
    }
  else if(status=="recognizing")
    chat->setStatus(TextCodec::fromUtf8("Распознаю речь..."));
  else if(status=="notice" || status=="error") {
    chat->setPartial("");
    const auto detail = TextCodec::fromUtf8(JsonRead::str(msg,"detail"));
    chat->setStatus(status=="error" ? TextCodec::fromUtf8("Ошибка: ") + detail : detail);
    }
  else
    chat->setStatus("");
  }

void AiBridge::Impl::onTranscript(const rapidjson::Value& msg) {
  // what the player says: partial while he speaks, the final one as his line, to whom the server decided
  ensureChat();
  const auto text = TextCodec::fromUtf8(JsonRead::str(msg,"text"));
  if(!JsonRead::boolean(msg,"final",true)) {
    chat->setPartial(text);
    return;
    }
  chat->setPartial("");
  chat->setStatus("");
  if(text.empty())
    return;
  std::string who = TextCodec::fromUtf8("Ты");
  const auto  to  = JsonRead::str(msg,"to_name");
  if(!to.empty())
    who += " -> " + TextCodec::fromUtf8(to);
  const auto time = LineLibrary::durationFor(text);
  chat->addLine(std::move(who),text,time);

  // the hero and the one he speaks to look at each other
  World* world = Gothic::inst().world();
  Npc*   pl    = world!=nullptr ? world->player() : nullptr;
  Npc*   npc   = world!=nullptr ? npcBySymbol(*world,std::string(JsonRead::str(msg,"to"))) : nullptr;
  if(pl!=nullptr && npc!=nullptr && npc!=pl) {
    pl->setTalkPartner(npc,time+1000);
    npc->setTalkPartner(pl,time+1000);
    }
  }

void AiBridge::Impl::onHush(World& world, const rapidjson::Value& msg) {
  const auto symbol = std::string(JsonRead::str(msg,"npc"));
  lines.hush(symbol);
  // the voice stops with its source
  voices.erase(std::remove_if(voices.begin(),voices.end(),[&](const Voice& v) { return v.npc==symbol; }),
               voices.end());
  if(Npc* npc = npcBySymbol(world,symbol)) {
    npc->setAiOutputBarrier(0,false);  // the line it was saying is over: its queue goes on
    npc->stopDlgAnim();                // gestures and lips
    }
  if(chat!=nullptr) {
    const auto reply = JsonRead::str(msg,"reply");
    if(!reply.empty())
      chat->cut(reply,TextCodec::fromUtf8(JsonRead::str(msg,"said")));
    }
  }

void AiBridge::Impl::ensureChat() {
  if(chat!=nullptr)
    return;
  chat = std::make_unique<ChatOverlay>();
  chat->canSpeak    = [this]() { return canSpeak(); };
  chat->onSubmit    = [this](const std::string& text) { submitChat(text); };
  chat->onPttStart  = [this]() { sendPtt("ptt_start"); };
  chat->onPttStop   = [this]() {
    sendPtt("ptt_stop");
    chat->setStatus(TextCodec::fromUtf8("Распознаю речь..."));
    };
  chat->onPttCancel = [this]() { sendPtt("ptt_cancel"); };
  SystemApi::addOverlay(chat.get());
  }

bool AiBridge::Impl::canSpeak() {
  auto&  gothic = Gothic::inst();
  World* world  = gothic.world();
  Npc*   pl     = world!=nullptr ? world->player() : nullptr;
  if(pl==nullptr || gothic.isPause() || gothic.isInDialog() || pl->isDown())
    return false;
  if(!connected) {
    gothic.onPrint(TextCodec::fromUtf8("ИИ-сервер не подключён"));
    return false;
    }
  return true;
  }

std::string AiBridge::Impl::focusSymbol(World& world) {
  Npc* pl  = world.player();
  Npc* npc = pl!=nullptr ? pl->target() : nullptr;
  if(npc==nullptr || npc->isPlayer() || npc->isDead())
    return {};
  return npcSymbol(world,*npc);
  }

void AiBridge::Impl::submitChat(const std::string& text) {
  World* world = Gothic::inst().world();
  if(world==nullptr || world->player()==nullptr || text.empty())
    return;
  // shown once the server has decided whom it is said to (transcript)
  JsonWriter j("player_text");
  j.str("text",TextCodec::toUtf8(text));
  if(auto focus = focusSymbol(*world); !focus.empty())
    j.str("focus",focus);
  send(j.finish());
  }

void AiBridge::Impl::sendPtt(std::string_view type) {
  JsonWriter j(type);
  if(World* world = Gothic::inst().world())
    if(auto focus = focusSymbol(*world); !focus.empty())
      j.str("focus",focus);
  send(j.finish());
  }

void AiBridge::Impl::onLineStarted(const LineLibrary::Line& line) {
  const bool voiced = !line.wav.empty() || line.voice!=nullptr;
  send(JsonWriter("line_started").str("npc",line.npc).str("line",line.id).boolean("voiced",voiced).finish());
  lastLine[line.npc] = line.id;
  if(line.voice!=nullptr)
    startVoice(line);
  // the face while the line is said, and a moment after (v8)
  if(auto face = moodFace(line.mood); !face.empty())
    if(auto world = Gothic::inst().world())
      if(auto npc = npcBySymbol(*world,line.npc)) {
        npc->startFaceAnim(face,1,line.time+MoodHoldMs);
        Log::i("[ai] face ",line.npc," ",face," for ",line.time+MoodHoldMs," ms");
        }
  ensureChat();
  if(!line.reply.empty()) {
    chat->extend(line.reply,line.time);  // its text is on screen already
    return;
    }
  std::string name = line.npc;
  if(auto world = Gothic::inst().world())
    if(auto npc = npcBySymbol(*world,line.npc))
      name = std::string(npc->displayName());
  chat->addLine(std::move(name),line.text,line.time);
  }

void AiBridge::Impl::onOriginalLine(std::string_view id) {
  auto it = originalLines.find(std::string(id));
  if(it==originalLines.end())
    return;
  const auto npc = it->second;
  const auto key = it->first;
  originalLines.erase(it);
  send(JsonWriter("line_started").str("npc",npc).str("line",key).boolean("voiced",true).finish());
  lastLine[npc] = key;
  std::string name = npc;
  if(auto world = Gothic::inst().world())
    if(auto n = npcBySymbol(*world,npc))
      name = std::string(n->displayName());
  const auto text = std::string(Gothic::inst().messageByName(key));
  ensureChat();
  chat->addLine(std::move(name),text,LineLibrary::durationFor(text));
  }

