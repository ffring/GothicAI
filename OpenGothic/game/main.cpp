#include <Tempest/Window>
#include <Tempest/Application>
#include <Tempest/Log>
#include <Tempest/Painter>
#include <Tempest/Except>

#include <zenkit/Logger.hh>

#include <Tempest/VulkanApi>

#if defined(_MSC_VER)
#include <Tempest/DirectX12Api>
#endif

#if defined(__APPLE__)
#include <Tempest/MetalApi>
#endif

#if defined(__IOS__) || defined(__ANDROID__)
#include "utils/installdetect.h"
#include <filesystem>
#endif

#include "utils/crashlog.h"
#include "mainwindow.h"
#include "gothic.h"
#include "build.h"
#include "commandline.h"
#include "resources.h"
#include "aibridge.h"

#include <dmusic.h>

std::string_view selectDevice(const Tempest::AbstractGraphicsApi& api) {
  auto d = api.devices();

  static Tempest::Device::Props p;
  for(auto& i:d)
    // if(i.type==Tempest::DeviceType::Integrated) {
    if(i.type==Tempest::DeviceType::Discrete) {
      p = i;
      return p.name;
      }
  if(d.size()>0) {
    p = d[0];
    return p.name;
    }
  return "";
  }

std::unique_ptr<Tempest::AbstractGraphicsApi> mkApi(const CommandLine& g) {
  Tempest::ApiFlags flg = g.isValidationMode() ? Tempest::ApiFlags::Validation : Tempest::ApiFlags::NoFlags;
  switch(g.graphicsApi()) {
    case CommandLine::DirectX12:
#if defined(_MSC_VER)
      return std::make_unique<Tempest::DirectX12Api>(flg);
#else
      break;
#endif
    case CommandLine::Vulkan:
#if !defined(__APPLE__)
      return std::make_unique<Tempest::VulkanApi>(flg);
#else
      break;
#endif
    }

#if defined(__APPLE__)
  return std::make_unique<Tempest::MetalApi>(flg);
#else
  return std::make_unique<Tempest::VulkanApi>(flg);
#endif
  }

// Startup cannot use game textures or fonts: the base may still be importing.
class BaseLoadingWindow final : public Tempest::Window {
  public:
    explicit BaseLoadingWindow(Tempest::Device& device)
      : device(device), swapchain(device,hwnd()), atlas(device) {
      setWindowTitle("Gothic II - Preparing game data");
      }
    ~BaseLoadingWindow() override { device.waitIdle(); }
    void progress(std::string_view message) {
      text=message;
      update();
      Tempest::Application::processEvents();
      render();
      }
    void failure(std::string_view message) {
      failed=true;
      setWindowTitle("Gothic II - Cannot load game data");
      progress(std::string(message)+"\n\nPress Enter or close this window to exit.");
      }
  private:
    void paintEvent(Tempest::PaintEvent& event) override {
      Tempest::Painter painter(event);
      painter.setFont(Tempest::Application::defaultFont());
      painter.setPen(Tempest::Color(1.f));
      painter.drawText(20,20,w()-40,h()-40,text);
      }
    void closeEvent(Tempest::CloseEvent& event) override {
      event.ignore();
      if(failed) Tempest::SystemApi::exit();
      }
    void keyDownEvent(Tempest::KeyEvent& event) override {
      if(failed && (event.key==Tempest::Event::K_Return || event.key==Tempest::Event::K_ESCAPE))
        Tempest::SystemApi::exit();
      }
    void render() override {
      if(w()<=0 || h()<=0) return;
      try {
        device.waitIdle();
        dispatchPaintEvent(image,atlas);
        mesh.update(device,image);
        {
        auto encoder=commands.startEncoding(device);
        encoder.setFramebuffer({{swapchain[swapchain.currentImage()],Tempest::Vec4(0,0,0,1),Tempest::Preserve}});
        mesh.draw(encoder);
        }
        auto fence=device.submit(commands);
        fence.wait();
        device.present(swapchain);
        }
      catch(const Tempest::SwapchainSuboptimal&) { device.waitIdle(); swapchain.reset(); }
      }
    Tempest::Device& device;
    Tempest::Swapchain swapchain;
    Tempest::TextureAtlas atlas;
    Tempest::VectorImage image;
    Tempest::VectorImage::Mesh mesh;
    Tempest::CommandBuffer commands;
    std::string text;
    bool failed=false;
  };

int main(int argc,const char** argv) {
#if defined(__ANDROID__)
  {
    auto appdir = InstallDetect::androidInternalDataPath();
    std::filesystem::current_path(appdir);
  }
#elif defined(__IOS__)
  {
    auto appdir = InstallDetect::applicationSupportDirectory();
    std::filesystem::current_path(appdir);
  }
#endif

  try {
    static Tempest::WFile logFile("log.txt");
    Tempest::Log::setOutputCallback([](Tempest::Log::Mode mode, const char* text) {
      logFile.write(text,std::strlen(text));
      logFile.write("\n",1);
      if(mode==Tempest::Log::Error)
        logFile.flush();
      });
    }
  catch(...) {
    Tempest::Log::e("unable to setup logfile - fallback to console log");
    }
  CrashLog::setup();

  zenkit::Logger::set(zenkit::LogLevel::INFO, Resources::zenkitLog);
  Dm_setLogger(DmLogLevel_INFO, [](void* ctx, DmLogLevel lvl, char const* msg) {
    switch (lvl) {
      case DmLogLevel_FATAL:
      case DmLogLevel_ERROR:
      case DmLogLevel_WARN:
        Tempest::Log::e("[dmusic] ", msg);
        break;
      case DmLogLevel_INFO:
        Tempest::Log::i("[dmusic] ", msg);
        break;
      case DmLogLevel_DEBUG:
      case DmLogLevel_TRACE:
        Tempest::Log::d("[dmusic] ", msg);
        break;
      }
    }, nullptr);

  Tempest::Log::i(appBuild);
  Workers::setThreadName("Main thread");

  CommandLine          cmd{argc,argv};
  auto                 api     = mkApi(cmd);
  const auto           gpuName = selectDevice(*api);
  CrashLog::setGpu(gpuName);

  Tempest::Device      device{*api,gpuName};
  CrashLog::setGpu(device.properties().name);

  Tempest::Application app;
  std::unique_ptr<BaseLoadingWindow> loading;
  const auto startup=Tempest::Application::tickCount();
  const auto progress=[&](std::string_view message) {
    if(!loading && message!="base: importing installation") return;
    if(!loading) loading=std::make_unique<BaseLoadingWindow>(device);
    loading->progress(message);
    };
  try {
  Resources            resources{device,progress};
  Gothic               gothic;
  GameMusic            music;
  gothic.setupGlobalScripts();
  AiBridge             aiBridge;

  MainWindow           wx(device);
  loading.reset();
  Tempest::Log::i("base: game window ready (",Tempest::Application::tickCount()-startup," ms since base preparation)");
  return app.exec();
  }
  catch(const BaseStore::Error& error) {
    Tempest::Log::e(error.what());
    if(!loading) loading=std::make_unique<BaseLoadingWindow>(device);
    loading->failure(error.what());
    app.exec();
    return EXIT_FAILURE;
    }
  }
