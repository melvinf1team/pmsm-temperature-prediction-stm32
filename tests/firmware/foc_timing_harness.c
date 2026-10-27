#include "test_platform.h"

/* Every DWT->CYCCNT read advances the counter by mock_dwt_step cycles, so
 * the quiet-window wait loop sees time pass. */
static TestDwt mock_dwt_state;
static uint32_t mock_dwt_step;
static TestDwt *mock_dwt(void)
{
  mock_dwt_state.CYCCNT += mock_dwt_step;
  return &mock_dwt_state;
}
#define DWT mock_dwt()

#include FIRMWARE_SOURCE

static TestCoreDebug mock_core_debug;
static TestTim mock_tim8 = { 5312U };
TestCoreDebug *CoreDebug = &mock_core_debug;
TestTim *TIM8 = &mock_tim8;
uint32_t SystemCoreClock = 170000000U;

#define CHECK(x) do { if (!(x)) return __LINE__; } while (0)
#define PERIOD 10624U  /* 2 x ARR at 170 MHz: 62.5 us */

static void run_foc(uint32_t start, uint32_t duration, bool overrun)
{
  mock_dwt_state.CYCCNT = start;
  AppFocTiming_OnHighFrequencyStart();
  mock_dwt_state.CYCCNT = start + duration;
  AppFocTiming_OnHighFrequencyEnd(overrun);
}

int main(void)
{
  AppFocTimingReport_t report;
  uint32_t primask = 1U;
  uint32_t base = UINT32_MAX - (2U * PERIOD);  /* Crosses the CYCCNT wrap. */

  /* No FOC yet: the window is granted at once. */
  AppFocTiming_Init();
  CHECK(AppFocTiming_EnterQuietWindow(15U, &primask));
  CHECK(primask == 0U);
  AppFocTiming_ExitQuietWindow(primask);
  AppFocTiming_Arm();

  run_foc(base, 3000U, false);
  run_foc(base + PERIOD, 3000U, false);
  /* Start delayed by 1700 cycles (10 us), then MC_DURATION. */
  run_foc(base + 2U * PERIOD + 1700U, 3000U, true);
  /* The schedule stays on the ideal grid: an on-time start is not late. */
  run_foc(base + 3U * PERIOD, 2000U, false);
  AppFocTiming_GetReport(&report);
  CHECK(report.period_ns == 62494U);
  CHECK(report.late_max_ns == 10000U);
  CHECK(report.duration_max_ns == 17647U);
  CHECK(report.overrun_count == 1U);
  CHECK(report.overrun_captured);
  CHECK(report.overrun_late_ns == 10000U);
  CHECK(report.overrun_duration_ns == 17647U);
  CHECK(foc_late_cycles == 0U);

  /* An early start becomes the new reference, then a gap resynchronizes. */
  run_foc(base + 4U * PERIOD - 50U, 2000U, false);
  CHECK(foc_late_cycles == 0U);
  run_foc(base + 14U * PERIOD, 2000U, false);
  CHECK(foc_late_cycles == 0U);
  run_foc(base + 15U * PERIOD + 340U, 2000U, false);
  CHECK(foc_late_cycles == 340U);

  /* 3 us after a FOC end: about 50 us remain before the next start. */
  mock_dwt_state.CYCCNT = base + 15U * PERIOD + 2000U + 510U;
  CHECK(AppFocTiming_EnterQuietWindow(15U, &primask));
  AppFocTiming_ExitQuietWindow(primask);

  /* Too close to the next start: refused until the FOC goes silent, which
   * the wait loop treats as a stopped FOC and grants. */
  mock_dwt_step = 50U;
  mock_dwt_state.CYCCNT = base + 16U * PERIOD - 2000U;
  CHECK(AppFocTiming_EnterQuietWindow(15U, &primask));
  CHECK((mock_dwt_state.CYCCNT - (base + 15U * PERIOD)) > 4U * PERIOD);
  mock_dwt_step = 0U;

  AppFocTiming_Arm();
  AppFocTiming_GetReport(&report);
  CHECK(report.late_max_ns == 0U);
  CHECK(report.overrun_count == 0U);
  CHECK(!report.overrun_captured);
  CHECK(report.quiet_timeouts == 0U);
  return 0;
}
