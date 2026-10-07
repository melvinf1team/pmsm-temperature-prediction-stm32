#ifndef APP_WIRING_DIAG_H
#define APP_WIRING_DIAG_H

/* Auto-test du câblage, moteur à l'arrêt : émet des lignes DIAG,... sur USART1. */
void AppWiringDiag_Run(void);

/* Émet une ligne STATUS,... : état moteur, défauts MCSDK et tension bus. */
void AppWiringDiag_SendStatus(void);

#endif /* APP_WIRING_DIAG_H */
