#pragma once
#include "Resources.h"
#include "hypothesis.h"

void updateColors(Resources& r, const std::list<std::unique_ptr<BotHypothesis>>& bestBotModels, const std::list<std::unique_ptr<BallHypothesis>>& ballCandidates);

// Writes learned and reference colors as JSON (atomically via tmp + rename) for the wrapper UI
void writeColorsJson(const Resources& r, const std::string& path);
