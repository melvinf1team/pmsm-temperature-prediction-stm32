#include "test_platform.h"
#include FIRMWARE_SOURCE

static uint32_t mock_tick;
uint32_t HAL_GetTick(void) { return mock_tick; }
#define CHECK(x) do { if (!(x)) return __LINE__; } while (0)

int main(void)
{
  float temperature = 0.0f;
  CHECK(!D6TIR_GetFreshTemperatureC(&temperature, 1000U));
  d6tir_has_value = true;
  d6tir_last_raw_tenth = 701;
  d6tir_last_valid_ms = 100U;
  mock_tick = 100U;
  CHECK(D6TIR_GetFreshTemperatureC(&temperature, 1000U));
  CHECK(temperature > 70.0f && temperature < 70.2f);
  CHECK(!D6TIR_GetFreshTemperatureC(NULL, 1000U));
  mock_tick = 1100U;
  CHECK(D6TIR_GetFreshTemperatureC(&temperature, 1000U));
  mock_tick = 1101U;
  CHECK(!D6TIR_GetFreshTemperatureC(&temperature, 1000U));
  d6tir_last_valid_ms = UINT32_MAX - 500U;
  mock_tick = 499U;
  CHECK(D6TIR_GetFreshTemperatureC(&temperature, 1000U));
  mock_tick = 500U;
  CHECK(!D6TIR_GetFreshTemperatureC(&temperature, 1000U));
  return 0;
}
