#pragma once

#include "expr.h"

#include <rapidjson/document.h>

#include <string>

namespace storygraph {

// An expression in the JSON form the converter writes (server/src/gothic_ai/gamedata/storygraph.py describes it):
// ["&&", ["==", ["var", "MIS_X"], ["int", 1]], ["call", "NPC_KNOWSINFO", ["inst", "OTHER"], ["sym", "DIA_X"]]].
// Names may be in any case; whether they exist is found out when the expression is evaluated.
// nullptr and `error` if the form is wrong.
ExprP readExpr(const rapidjson::Value& v, std::string& error);

// UTF-8 symbol name (as ident() writes it) -> the DAT's Windows-1252, uppercase; empty if it can't be one
std::string symbolName(std::string_view utf8);

}
