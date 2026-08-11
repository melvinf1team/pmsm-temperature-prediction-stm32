#ifndef APP_AI_MODEL_H
#define APP_AI_MODEL_H

#include "preprocess_ewma.h"

#include <stdbool.h>

void AppAiModel_Init(void);

bool AppAiModel_Predict(
  float features[PREPROCESS_EWMA_OUTPUT_COUNT],
  float *predicted_temperature_c);

#endif /* APP_AI_MODEL_H */