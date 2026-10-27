#include "app_fault_diag.h"

#include "main.h"
#include "app_datalog.h"
#include "app_foc_timing.h"
#include "mc_config.h"
#include "drive_parameters.h"
#include "fixpmath.h"

#include "stm32g4xx_ll_comp.h"
#include "stm32g4xx_ll_gpio.h"

#include <stdint.h>
#include <stdio.h>

/*
 * The TIM8 break (MC_OVER_CURR, 0x0040) merges two sources:
 *  - external BKIN PB7, active low: fault line of the STDES-LVHP01 board /
 *    L6491 drivers;
 *  - internal BKIN COMP4/6/7 (phases U/V/W) against the DAC threshold (~110 A).
 * The MCSDK does not say which one fired: their state is read in the break
 * IRQ, and the preceding 2 ms of phase currents are kept.
 */
#define APP_FAULT_DIAG_BKIN_PORT             GPIOB
#define APP_FAULT_DIAG_BKIN_PIN              LL_GPIO_PIN_7
#define APP_FAULT_DIAG_COMP_U                0x01U
#define APP_FAULT_DIAG_COMP_V                0x02U
#define APP_FAULT_DIAG_COMP_W                0x04U
/* Recent peak: two alternating 10 ms windows (160 FOC periods). */
#define APP_FAULT_DIAG_PEAK_BUCKET_CYCLES    160U
#define APP_FAULT_DIAG_TRACE_PER_LINE        8U
#define APP_FAULT_DIAG_TRACE_LINES           (APP_FAULT_DIAG_TRACE_LEN / APP_FAULT_DIAG_TRACE_PER_LINE)
#define APP_FAULT_DIAG_LINE_COUNT            (1U + APP_FAULT_DIAG_TRACE_LINES)
#define APP_FAULT_DIAG_LINE_SIZE             448U

typedef struct
{
  fixp30_t u;
  fixp30_t v;
  fixp30_t w;
  fixp30_t vbus;
} AppFaultDiagSample_t;

static volatile AppFaultDiagSample_t diag_trace[APP_FAULT_DIAG_TRACE_LEN];
static volatile uint32_t diag_trace_head = 0U;
static volatile uint32_t diag_trace_count = 0U;

static volatile fixp30_t diag_peak_run_pu = 0;
static volatile fixp30_t diag_peak_bucket_pu[2] = { 0, 0 };
static volatile uint32_t diag_peak_bucket_cycles = 0U;
static volatile uint32_t diag_peak_bucket_index = 0U;

static volatile bool diag_break_latched = false;
static volatile uint32_t diag_break_tick_ms = 0U;
static volatile uint32_t diag_break_irq_count = 0U;
static volatile uint32_t diag_break_tim_sr = 0U;
static volatile bool diag_break_bkin_low = false;
static volatile uint32_t diag_break_comp_mask = 0U;
static volatile fixp30_t diag_break_peak_recent_pu = 0;

static char diag_lines[APP_FAULT_DIAG_LINE_COUNT][APP_FAULT_DIAG_LINE_SIZE];
static uint32_t diag_line_count = 0U;
static uint32_t diag_line_next = 0U;

static fixp30_t AppFaultDiag_AbsPu(fixp30_t value)
{
  return (value < 0) ? -value : value;
}

static long AppFaultDiag_CurrentDeciAmps(fixp30_t value_pu)
{
  return (long)(FIXP30_toF(value_pu) * CURRENT_SCALE * 10.0f);
}

static long AppFaultDiag_VoltageDeciVolts(fixp30_t value_pu)
{
  return (long)(FIXP30_toF(value_pu) * VOLTAGE_SCALE * 10.0f);
}

static bool AppFaultDiag_BkinIsLow(void)
{
  return LL_GPIO_IsInputPinSet(APP_FAULT_DIAG_BKIN_PORT, APP_FAULT_DIAG_BKIN_PIN) == 0U;
}

static uint32_t AppFaultDiag_ReadCompMask(void)
{
  uint32_t mask = 0U;

  /* Non-inverted polarity: output high = current above the threshold. */
  if (LL_COMP_ReadOutputLevel(COMP4) == LL_COMP_OUTPUT_LEVEL_HIGH)
  {
    mask |= APP_FAULT_DIAG_COMP_U;
  }
  if (LL_COMP_ReadOutputLevel(COMP6) == LL_COMP_OUTPUT_LEVEL_HIGH)
  {
    mask |= APP_FAULT_DIAG_COMP_V;
  }
  if (LL_COMP_ReadOutputLevel(COMP7) == LL_COMP_OUTPUT_LEVEL_HIGH)
  {
    mask |= APP_FAULT_DIAG_COMP_W;
  }
  return mask;
}

void AppFaultDiag_Arm(void)
{
  uint32_t primask = __get_PRIMASK();

  __disable_irq();
  diag_trace_head = 0U;
  diag_trace_count = 0U;
  diag_peak_run_pu = 0;
  diag_peak_bucket_pu[0] = 0;
  diag_peak_bucket_pu[1] = 0;
  diag_peak_bucket_cycles = 0U;
  diag_break_latched = false;
  diag_break_irq_count = 0U;
  diag_break_tim_sr = 0U;
  diag_break_bkin_low = false;
  diag_break_comp_mask = 0U;
  diag_break_peak_recent_pu = 0;
  if (primask == 0U)
  {
    __enable_irq();
  }
  diag_line_count = 0U;
  diag_line_next = 0U;
}

void AppFaultDiag_OnHighFrequencyTask(void)
{
  const Currents_Irst_t *current = &PWM_Handle_M1._Super.Irst_in_pu;
  uint32_t head;
  uint32_t bucket;
  fixp30_t peak;
  fixp30_t abs_v;
  fixp30_t abs_w;

  /* Trace frozen at the break instant. */
  if (diag_break_latched)
  {
    return;
  }

  head = diag_trace_head;
  diag_trace[head].u = current->R;
  diag_trace[head].v = current->S;
  diag_trace[head].w = current->T;
  diag_trace[head].vbus = VBus_M1.Udcbus_in_pu;
  diag_trace_head = (head + 1U) % APP_FAULT_DIAG_TRACE_LEN;
  if (diag_trace_count < APP_FAULT_DIAG_TRACE_LEN)
  {
    diag_trace_count++;
  }

  peak = AppFaultDiag_AbsPu(current->R);
  abs_v = AppFaultDiag_AbsPu(current->S);
  abs_w = AppFaultDiag_AbsPu(current->T);
  if (abs_v > peak)
  {
    peak = abs_v;
  }
  if (abs_w > peak)
  {
    peak = abs_w;
  }
  if (peak > diag_peak_run_pu)
  {
    diag_peak_run_pu = peak;
  }

  if (++diag_peak_bucket_cycles >= APP_FAULT_DIAG_PEAK_BUCKET_CYCLES)
  {
    diag_peak_bucket_cycles = 0U;
    diag_peak_bucket_index ^= 1U;
    diag_peak_bucket_pu[diag_peak_bucket_index] = 0;
  }
  bucket = diag_peak_bucket_index;
  if (peak > diag_peak_bucket_pu[bucket])
  {
    diag_peak_bucket_pu[bucket] = peak;
  }
}

void AppFaultDiag_OnBreakIrq(void)
{
  fixp30_t recent;

  diag_break_irq_count++;
  if (diag_break_latched)
  {
    return;
  }
  /* Read immediately: a short pulse may already be released. */
  diag_break_tim_sr = TIM8->SR;
  diag_break_bkin_low = AppFaultDiag_BkinIsLow();
  diag_break_comp_mask = AppFaultDiag_ReadCompMask();
  diag_break_tick_ms = HAL_GetTick();
  recent = diag_peak_bucket_pu[0];
  if (diag_peak_bucket_pu[1] > recent)
  {
    recent = diag_peak_bucket_pu[1];
  }
  diag_break_peak_recent_pu = recent;
  diag_break_latched = true;
}

void AppFaultDiag_PrepareReport(void)
{
  bool latched = diag_break_latched;
  uint32_t count = diag_trace_count;
  uint32_t oldest = (diag_trace_head + APP_FAULT_DIAG_TRACE_LEN - count) % APP_FAULT_DIAG_TRACE_LEN;
  fixp30_t recent = latched ? diag_break_peak_recent_pu
                            : ((diag_peak_bucket_pu[0] > diag_peak_bucket_pu[1])
                               ? diag_peak_bucket_pu[0] : diag_peak_bucket_pu[1]);
  uint32_t line;
  AppFocTimingReport_t timing;

  AppFocTiming_GetReport(&timing);

  /* A trace that is not frozen keeps moving: send it only if the break froze it. */
  if (!latched)
  {
    count = 0U;
  }

  (void)snprintf(diag_lines[0], APP_FAULT_DIAG_LINE_SIZE,
    "#FAULT_DIAG,break=%u,t_break_ms=%lu,t_now_ms=%lu,irq_count=%lu,tim_sr=0x%04lX,"
    "bkin_low=%u,comp=0x%lX,bkin_now_low=%u,comp_now=0x%lX,"
    "peak_run_da=%ld,peak_recent_da=%ld,ocp_da=%ld,vbus_dv=%ld,"
    "trace_n=%lu,trace_period_us=%lu,"
    "foc_period_ns=%lu,foc_max_ns=%lu,foc_late_max_ns=%lu,foc_overruns=%lu,"
    "foc_overrun_ns=%lu,foc_overrun_late_ns=%lu,quiet_timeouts=%lu\r\n",
    latched ? 1U : 0U,
    (unsigned long)diag_break_tick_ms,
    (unsigned long)HAL_GetTick(),
    (unsigned long)diag_break_irq_count,
    (unsigned long)(diag_break_tim_sr & 0xFFFFU),
    diag_break_bkin_low ? 1U : 0U,
    (unsigned long)diag_break_comp_mask,
    AppFaultDiag_BkinIsLow() ? 1U : 0U,
    (unsigned long)AppFaultDiag_ReadCompMask(),
    AppFaultDiag_CurrentDeciAmps(diag_peak_run_pu),
    AppFaultDiag_CurrentDeciAmps(recent),
    (long)(APP_FAULT_DIAG_OCP_THRESHOLD_A * 10.0f),
    AppFaultDiag_VoltageDeciVolts(VBus_M1.Udcbus_in_pu),
    (unsigned long)count,
    (unsigned long)(1000000UL / ISR_FREQUENCY_HZ),
    (unsigned long)timing.period_ns,
    (unsigned long)timing.duration_max_ns,
    (unsigned long)timing.late_max_ns,
    (unsigned long)timing.overrun_count,
    (unsigned long)(timing.overrun_captured ? timing.overrun_duration_ns : 0U),
    (unsigned long)(timing.overrun_captured ? timing.overrun_late_ns : 0U),
    (unsigned long)timing.quiet_timeouts);

  /* #FAULT_TRACE,<index of the first sample, -1 = last before the break>,
   * then u:v:w:vbus in 0.1 A and 0.1 V, oldest first. */
  diag_line_count = 1U;
  for (line = 0U; (line * APP_FAULT_DIAG_TRACE_PER_LINE) < count; line++)
  {
    char *text = diag_lines[diag_line_count];
    size_t used;
    uint32_t first = line * APP_FAULT_DIAG_TRACE_PER_LINE;
    uint32_t k;
    int written = snprintf(text, APP_FAULT_DIAG_LINE_SIZE, "#FAULT_TRACE,%ld",
                           (long)first - (long)count);

    used = (written > 0) ? (size_t)written : 0U;
    for (k = first; (k < count) && (k < first + APP_FAULT_DIAG_TRACE_PER_LINE); k++)
    {
      uint32_t index = (oldest + k) % APP_FAULT_DIAG_TRACE_LEN;

      written = snprintf(text + used, APP_FAULT_DIAG_LINE_SIZE - used, ",%ld:%ld:%ld:%ld",
                         AppFaultDiag_CurrentDeciAmps(diag_trace[index].u),
                         AppFaultDiag_CurrentDeciAmps(diag_trace[index].v),
                         AppFaultDiag_CurrentDeciAmps(diag_trace[index].w),
                         AppFaultDiag_VoltageDeciVolts(diag_trace[index].vbus));
      if ((written <= 0) || ((size_t)written >= APP_FAULT_DIAG_LINE_SIZE - used))
      {
        break;
      }
      used += (size_t)written;
    }
    (void)snprintf(text + used, APP_FAULT_DIAG_LINE_SIZE - used, "\r\n");
    diag_line_count++;
  }
  diag_line_next = 0U;
}

bool AppFaultDiag_SendPendingReport(void)
{
  while (diag_line_next < diag_line_count)
  {
    if (!AppDatalog_SendText(diag_lines[diag_line_next]))
    {
      return false;
    }
    diag_line_next++;
  }
  return true;
}
