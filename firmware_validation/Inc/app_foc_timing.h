#ifndef APP_FOC_TIMING_H
#define APP_FOC_TIMING_H

#include <stdbool.h>
#include <stdint.h>

/*
 * Timing of the FOC task (TSK_HighFrequencyTask, 16 kHz) measured with the
 * DWT cycle counter, and IRQ-free windows placed between two FOC runs.
 *
 * MC_DURATION (0x0001) means the duty cycles were written after the next
 * TIM8 UPDATE: the FOC started too late (IRQs masked by __disable_irq) or
 * computed for too long. The delay is measured against the ideal start
 * schedule, which is strictly periodic.
 */

/* Minimum lead between the end of an IRQ-free window and the next
 * expected FOC start (DMA IRQ prologue included). */
#define APP_FOC_TIMING_GUARD_US              3U

typedef struct
{
  uint32_t period_ns;          /* Nominal FOC period */
  uint32_t duration_max_ns;    /* Max TSK_HighFrequencyTask duration */
  uint32_t late_max_ns;        /* Max delay of a FOC start */
  uint32_t overrun_count;      /* Periods that ended in MC_DURATION */
  bool overrun_captured;       /* true when the next two fields are valid */
  uint32_t overrun_duration_ns;/* Duration of the first MC_DURATION period */
  uint32_t overrun_late_ns;    /* Start delay of that period */
  uint32_t quiet_timeouts;     /* IRQ-free windows refused for lack of free time */
} AppFocTimingReport_t;

/* Enables the DWT counter; called once at firmware start. */
void AppFocTiming_Init(void);

/* Clears maxima and capture; called at each motor start. */
void AppFocTiming_Arm(void);

/* Start and end of TSK_HighFrequencyTask (USER CODE sections of mc_tasks.c). */
void AppFocTiming_OnHighFrequencyStart(void);
void AppFocTiming_OnHighFrequencyEnd(bool foc_overrun);

/*
 * Masks IRQs only if the next FOC start is more than window_us (+ guard)
 * away. Returns true with IRQs masked and the previous PRIMASK in *primask;
 * release it with AppFocTiming_ExitQuietWindow(). Returns false, IRQs
 * enabled, if no window is found within four FOC periods. Without an active
 * FOC, the window is granted at once.
 */
bool AppFocTiming_EnterQuietWindow(uint32_t window_us, uint32_t *primask);
void AppFocTiming_ExitQuietWindow(uint32_t primask);

void AppFocTiming_GetReport(AppFocTimingReport_t *report);

#endif /* APP_FOC_TIMING_H */
