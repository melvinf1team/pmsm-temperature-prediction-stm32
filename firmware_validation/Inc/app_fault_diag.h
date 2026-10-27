#ifndef APP_FAULT_DIAG_H
#define APP_FAULT_DIAG_H

#include <stdbool.h>

/* Hardware OCP threshold (COMP4/6/7 + DAC), WB_UI_OCP_THRESHOLD in the .ioc. */
#define APP_FAULT_DIAG_OCP_THRESHOLD_A       110.0f
/* Trace before the break: 32 FOC periods at 16 kHz, i.e. 2 ms. */
#define APP_FAULT_DIAG_TRACE_LEN             32U

/* Clears peaks, trace and break capture; called at each motor start. */
void AppFaultDiag_Arm(void);

/* End of TSK_HighFrequencyTask: phase-current peaks and trace. */
void AppFaultDiag_OnHighFrequencyTask(void);

/* Start of TIMx_BRK_M1_IRQHandler: freezes the trace and reads the break
 * sources (power-board BKIN pin PB7, COMP4/6/7 outputs). */
void AppFaultDiag_OnBreakIrq(void);

/* Prepares the #FAULT_DIAG and #FAULT_TRACE lines sent after #FAULT. */
void AppFaultDiag_PrepareReport(void);

/* Sends the prepared lines; true when nothing is left to send. */
bool AppFaultDiag_SendPendingReport(void);

#endif /* APP_FAULT_DIAG_H */
