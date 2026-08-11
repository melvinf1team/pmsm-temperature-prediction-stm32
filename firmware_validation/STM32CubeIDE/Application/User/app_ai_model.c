#include "app_ai_model.h"
#include "app_config.h"

#include <math.h>
#include <string.h>

#if APP_NEAI_MODEL_ENABLED
#include "NanoEdgeAI.h"

_Static_assert(NEAI_INPUT_SIGNAL_LENGTH == 1,
               "Le modele NEAI doit utiliser un echantillon par axe");
_Static_assert(NEAI_INPUT_AXIS_NUMBER == PREPROCESS_EWMA_OUTPUT_COUNT,
               "Le modele NEAI doit accepter les 55 features EWMA");
#endif

static bool app_ai_model_ready = false;

#if APP_NEAI_MODEL_ENABLED
static float app_ai_model_input[PREPROCESS_EWMA_OUTPUT_COUNT];

static bool AppAiModel_IsFinite(float value)
{
  uint32_t bits;

  memcpy(&bits, &value, sizeof(bits));
  return (bits & 0x7F800000UL) != 0x7F800000UL;
}
#endif

void AppAiModel_Init(void)
{
#if APP_NEAI_MODEL_ENABLED
  char *runtime_id = neai_get_id();
  bool dimensions_match =
    (neai_get_input_signal_size() == NEAI_INPUT_SIGNAL_LENGTH) &&
    (neai_get_axis_number() == NEAI_INPUT_AXIS_NUMBER);
  bool identity_matches =
    (runtime_id != NULL) && (strcmp(runtime_id, NEAI_ID) == 0);

  app_ai_model_ready = dimensions_match && identity_matches &&
                       (neai_extrapolation_init() == NEAI_OK);
#else
  app_ai_model_ready = false;
#endif
}

bool AppAiModel_Predict(
  float features[PREPROCESS_EWMA_OUTPUT_COUNT],
  float *predicted_temperature_c)
{
  if ((features == NULL) || (predicted_temperature_c == NULL))
  {
    return false;
  }

  *predicted_temperature_c = NAN;

  if (!app_ai_model_ready)
  {
    return false;
  }

#if APP_NEAI_MODEL_ENABLED
  memcpy(app_ai_model_input, features, sizeof(app_ai_model_input));

  if ((neai_extrapolation(app_ai_model_input, predicted_temperature_c) != NEAI_OK) ||
      !AppAiModel_IsFinite(*predicted_temperature_c))
  {
    *predicted_temperature_c = NAN;
    return false;
  }

  return true;
#else
  (void)features;
  return false;
#endif
}
