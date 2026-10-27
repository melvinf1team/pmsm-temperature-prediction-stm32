#include "app_foc_timing.h"

#include "main.h"
#include "parameters_conversion.h"

#include "stm32g4xx_ll_tim.h"

#include <stddef.h>

/* Sans démarrage FOC depuis 4 périodes, la FOC est considérée arrêtée. */
#define APP_FOC_TIMING_IDLE_PERIODS          4U
#define APP_FOC_TIMING_QUIET_TIMEOUT_PERIODS 4U

static uint32_t foc_period_cycles = 0U;
/* 0 tant que AppFocTiming_Init() n'a pas tourné : aucune mesure convertie. */
static uint32_t foc_cycles_per_us = 0U;

/* Démarrage idéal de la période en cours : jamais après le démarrage mesuré. */
static volatile uint32_t foc_ideal_start = 0U;
static volatile uint32_t foc_last_start = 0U;
static volatile uint32_t foc_late_cycles = 0U;
static volatile bool foc_has_reference = false;

static volatile uint32_t foc_duration_max_cycles = 0U;
static volatile uint32_t foc_late_max_cycles = 0U;
static volatile uint32_t foc_overrun_count = 0U;
static volatile bool foc_overrun_captured = false;
static volatile uint32_t foc_overrun_duration_cycles = 0U;
static volatile uint32_t foc_overrun_late_cycles = 0U;
static volatile uint32_t foc_quiet_timeouts = 0U;

static uint32_t AppFocTiming_CyclesToNs(uint32_t cycles)
{
  if (foc_cycles_per_us == 0U)
  {
    return 0U;
  }
  return (uint32_t)(((uint64_t)cycles * 1000U) / foc_cycles_per_us);
}

static uint32_t AppFocTiming_PeriodCycles(void)
{
  /* TIM8 en centré : une période PWM = 2 x ARR ticks à ADV_TIM_CLK_MHz. */
  if ((foc_period_cycles == 0U) && (foc_cycles_per_us != 0U))
  {
    uint32_t arr = LL_TIM_GetAutoReload(TIM8);

    if (arr != 0U)
    {
      foc_period_cycles = (2U * arr * (uint32_t)REGULATION_EXECUTION_RATE * foc_cycles_per_us) /
                          (uint32_t)ADV_TIM_CLK_MHz;
    }
  }
  return foc_period_cycles;
}

void AppFocTiming_Init(void)
{
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  foc_cycles_per_us = SystemCoreClock / 1000000U;
}

void AppFocTiming_Arm(void)
{
  uint32_t primask = __get_PRIMASK();

  __disable_irq();
  foc_duration_max_cycles = 0U;
  foc_late_max_cycles = 0U;
  foc_overrun_count = 0U;
  foc_overrun_captured = false;
  foc_overrun_duration_cycles = 0U;
  foc_overrun_late_cycles = 0U;
  foc_quiet_timeouts = 0U;
  if (primask == 0U)
  {
    __enable_irq();
  }
}

void AppFocTiming_OnHighFrequencyStart(void)
{
  uint32_t now = DWT->CYCCNT;
  uint32_t period = AppFocTiming_PeriodCycles();
  uint32_t late = 0U;

  if (foc_has_reference && (period != 0U))
  {
    uint32_t expected = foc_ideal_start + period;
    int32_t delta = (int32_t)(now - expected);

    if ((delta > 0) && ((uint32_t)delta < period))
    {
      late = (uint32_t)delta;
      now = expected;
    }
    /* Démarrage en avance, ou trou de plus d'une période (FOC suspendue,
     * CYCCNT remis à zéro) : la mesure devient la nouvelle référence. */
  }
  foc_ideal_start = now;
  foc_last_start = now + late;
  foc_late_cycles = late;
  foc_has_reference = true;
}

void AppFocTiming_OnHighFrequencyEnd(bool foc_overrun)
{
  uint32_t duration = DWT->CYCCNT - foc_last_start;
  uint32_t late = foc_late_cycles;

  if (duration > foc_duration_max_cycles)
  {
    foc_duration_max_cycles = duration;
  }
  if (late > foc_late_max_cycles)
  {
    foc_late_max_cycles = late;
  }
  if (foc_overrun)
  {
    foc_overrun_count++;
    if (!foc_overrun_captured)
    {
      foc_overrun_duration_cycles = duration;
      foc_overrun_late_cycles = late;
      foc_overrun_captured = true;
    }
  }
}

bool AppFocTiming_EnterQuietWindow(uint32_t window_us, uint32_t *primask)
{
  uint32_t period = AppFocTiming_PeriodCycles();
  uint32_t needed = (window_us + APP_FOC_TIMING_GUARD_US) * foc_cycles_per_us;
  uint32_t start = DWT->CYCCNT;

  for (;;)
  {
    uint32_t saved = __get_PRIMASK();
    uint32_t now;

    __disable_irq();
    now = DWT->CYCCNT;
    if (!foc_has_reference || (period == 0U) ||
        ((now - foc_last_start) > (APP_FOC_TIMING_IDLE_PERIODS * period)))
    {
      *primask = saved;
      return true;
    }
    /* Prochain démarrage au plus tôt : échéancier idéal + une période. */
    if ((int32_t)(foc_ideal_start + period - now) >= (int32_t)needed)
    {
      *primask = saved;
      return true;
    }
    if (saved == 0U)
    {
      __enable_irq();
    }
    if ((now - start) > (APP_FOC_TIMING_QUIET_TIMEOUT_PERIODS * period))
    {
      foc_quiet_timeouts++;
      return false;
    }
  }
}

void AppFocTiming_ExitQuietWindow(uint32_t primask)
{
  if (primask == 0U)
  {
    __enable_irq();
  }
}

void AppFocTiming_GetReport(AppFocTimingReport_t *report)
{
  if (report == NULL)
  {
    return;
  }
  report->period_ns = AppFocTiming_CyclesToNs(AppFocTiming_PeriodCycles());
  report->duration_max_ns = AppFocTiming_CyclesToNs(foc_duration_max_cycles);
  report->late_max_ns = AppFocTiming_CyclesToNs(foc_late_max_cycles);
  report->overrun_count = foc_overrun_count;
  report->overrun_captured = foc_overrun_captured;
  report->overrun_duration_ns = AppFocTiming_CyclesToNs(foc_overrun_duration_cycles);
  report->overrun_late_ns = AppFocTiming_CyclesToNs(foc_overrun_late_cycles);
  report->quiet_timeouts = foc_quiet_timeouts;
}
