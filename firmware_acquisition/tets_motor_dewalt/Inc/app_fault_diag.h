#ifndef APP_FAULT_DIAG_H
#define APP_FAULT_DIAG_H

#include <stdbool.h>

/* Seuil OCP matériel (COMP4/6/7 + DAC), WB_UI_OCP_THRESHOLD du .ioc. */
#define APP_FAULT_DIAG_OCP_THRESHOLD_A       110.0f
/* Trace avant coupure : 32 périodes FOC à 16 kHz, soit 2 ms. */
#define APP_FAULT_DIAG_TRACE_LEN             32U

/* Efface pics, trace et capture break ; appelé à chaque démarrage moteur. */
void AppFaultDiag_Arm(void);

/* Fin de TSK_HighFrequencyTask : pics et trace des courants de phase. */
void AppFaultDiag_OnHighFrequencyTask(void);

/* Début de TIMx_BRK_M1_IRQHandler : fige la trace et lit les sources du
 * break (broche BKIN PB7 de la carte de puissance, sorties COMP4/6/7). */
void AppFaultDiag_OnBreakIrq(void);

/* Prépare les lignes #FAULT_DIAG et #FAULT_TRACE à envoyer après #FAULT. */
void AppFaultDiag_PrepareReport(void);

/* Envoie les lignes préparées ; true quand il ne reste rien à envoyer. */
bool AppFaultDiag_SendPendingReport(void);

#endif /* APP_FAULT_DIAG_H */
