#pragma once

#include <Tempest/Painter>
#include <Tempest/UiOverlay>

#include <cstdint>
#include <deque>
#include <functional>
#include <string>
#include <string_view>
#include <vector>

// Talking to the world, drawn over the game (protocol v7). Nothing here is a mode:
// - T held past a short delay records the player's voice until released, anywhere: every other key
//   still goes to the game, so the hero keeps running or fighting while he speaks. Recording itself
//   happens on the AI server: the overlay only reports the key via onPttStart/onPttStop/onPttCancel.
// - a short tap on T opens a one-line input at the bottom. The keyboard is taken only while it is
//   open: Enter sends the line, Esc closes it.
// - it shows what is said around the player as subtitles (npc's and the hero's own lines), the words
//   recognized while he speaks and a status line.
// - an npc's offer of a deal (v10, docs/npc-tools.md) in a window at the side: what each gives. It takes
//   nothing but Y, which accepts the version shown once the hero has all he gives and the terms have not
//   changed for a moment. A new version replaces the shown one and highlights what changed.
// All strings are in the game codepage.
class ChatOverlay final : public Tempest::UiOverlay {
  public:
    ChatOverlay();

    struct DealLine final {
      std::string item;             // instance symbol
      int32_t     count   = 0;
      std::string text;             // as the window shows it
      bool        fresh   = false;  // new or changed in this version
      bool        missing = false;  // the hero's side: he has less than this
      };
    struct Deal final {
      std::string           id;     // empty: no window
      int32_t               version   = 0;
      std::string           npc;    // its name
      std::vector<DealLine> npcGives, heroGives;
      uint64_t              changedAt = 0;
      bool                  sent      = false;  // accepted, the server has not answered yet
      };

    std::function<bool()>                   canSpeak;
    std::function<void(const std::string&)> onSubmit;
    // T went down: recording may start (the server begins capturing right away so no word is lost);
    // then either onPttStop (held long enough: recognize) or onPttCancel (a short tap: the input opens)
    std::function<void()>                   onPttStart;
    std::function<void()>                   onPttStop;
    std::function<void()>                   onPttCancel;
    std::function<bool()>                   canDeal;
    std::function<void(const std::string& deal, int32_t version)> onDealAccept;

    void openInput();
    void closeInput();
    bool isInputOpen() const { return active; }

    void addLine(std::string speaker, std::string text, uint32_t showMs);
    // an entry that grows while the npc's reply is generated: added once, then updated in place
    void upsertLine(std::string_view key, std::string speaker, std::string text, uint32_t showMs);
    // keep an entry on screen for at least `showMs` more (its lines are being spoken)
    void extend(std::string_view key, uint32_t showMs);
    // the npc was interrupted: the entry keeps only what it said (`said`, empty: nothing) and fades
    void cut(std::string_view key, std::string said);
    void setStatus(std::string text);
    // what the player is saying, recognized so far (push-to-talk); empty clears it
    void setPartial(std::string text);
    void tick();

    // a new version replaces the shown one; the same version again only lets Y be pressed again
    void showDeal(std::string id, int32_t version, std::string npc,
                  std::vector<DealLine> npcGives, std::vector<DealLine> heroGives);
    void hideDeal(std::string_view id);  // empty: whatever is shown
    const Deal& shownDeal() const { return deal; }
    // what the hero lacks of what he gives, checked by the caller (every tick)
    void markMissing(const std::function<bool(const DealLine&)>& lacks);
    // Y: "sent", or why not: "none", "waiting" (sent already), "missing", "changed" (a moment ago)
    std::string pressDeal();

  protected:
    void paintEvent    (Tempest::PaintEvent& e) override;
    void keyDownEvent  (Tempest::KeyEvent&   e) override;
    void keyRepeatEvent(Tempest::KeyEvent&   e) override;
    void keyUpEvent    (Tempest::KeyEvent&   e) override;
    void mouseDownEvent(Tempest::MouseEvent& e) override;
    void mouseUpEvent  (Tempest::MouseEvent& e) override;
    void mouseWheelEvent(Tempest::MouseEvent& e) override;

  private:
    struct Entry final {
      std::string key;
      std::string speaker;
      std::string text;
      uint64_t    shownUntil = 0;
      };

    void edit(Tempest::KeyEvent& e);
    void paintChat(Tempest::Painter& p);
    void paintDeal(Tempest::Painter& p);
    bool hasVisibleContent() const;
    void pttPress(char pendingChar);
    void pttRelease();
    void pttReset();

    bool               active      = false;  // the input line is open
    bool               wasVisible  = false;
    // push-to-talk on T
    bool               tDown       = false;  // T is held (and was taken by the overlay)
    bool               recording   = false;  // held past the delay: the player is speaking
    uint64_t           tDownAt     = 0;
    char               tPendingChar = '\0';  // typed if it turns out to be a short tap
    std::string        input;
    size_t             cursor = 0;
    std::string        status;
    std::string        partial;
    std::deque<Entry>  log;
    Deal               deal;
    bool               yDown = false;  // Y went to the deal window
  };
