# storygraph: lifts the story layer of a compiled GOTHIC.DAT (dialogs, quest variables) into a graph.
# See docs/story-graph.md in the monorepo.
#
# Standalone build (only ZenKit, no engine):
#   cmake -S OpenGothic/ai_bridge/storygraph -B OpenGothic/ai_bridge/storygraph/build -G Ninja
#   cmake --build OpenGothic/ai_bridge/storygraph/build

cmake_minimum_required(VERSION 3.21)
project(StoryGraph CXX)

if(PROJECT_IS_TOP_LEVEL)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE RelWithDebInfo)
  endif()
  set(BUILD_SQUISH_WITH_SSE2 OFF CACHE INTERNAL "")
  set(ZK_ENABLE_ASAN         OFF CACHE INTERNAL "")
  set(ZK_ENABLE_DEPRECATION  OFF CACHE INTERNAL "")
  add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../../lib/ZenKit ${CMAKE_CURRENT_BINARY_DIR}/zenkit)
endif()

add_library(StoryGraph STATIC
  bytecode.cpp bytecode.h
  eval.cpp     eval.h
  expr.cpp     expr.h
  lifter.cpp   lifter.h
  model.cpp    model.h
  vocab.h
  ../textcodec.cpp ../textcodec.h)
target_include_directories(StoryGraph PUBLIC ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/..)
# rapidjson 1.1 trips modern clang warnings; treat it as a system header
target_include_directories(StoryGraph SYSTEM PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../../lib/rapidjson/include)
target_link_libraries(StoryGraph PUBLIC zenkit)

add_executable(storygraph main.cpp scan.cpp check.cpp check.h fuzz.cpp saveworld.cpp saveworld.h)
target_link_libraries(storygraph PRIVATE StoryGraph)

if(NOT MSVC)
  target_compile_options(StoryGraph PRIVATE -Wall -Wconversion -Werror)
  target_compile_options(storygraph PRIVATE -Wall -Wconversion -Werror)
endif()
