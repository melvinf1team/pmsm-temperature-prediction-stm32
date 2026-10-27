#ifndef APP_FOC_TIMING_H
#define APP_FOC_TIMING_H

#include <stdbool.h>
#include <stdint.h>

/*
 * Timing de la tâche FOC (TSK_HighFrequencyTask, 16 kHz) mesuré avec le
 * compteur DWT, et fenêtres sans IRQ placées entre deux FOC.
 *
 * MC_DURATION (0x0001) signifie que les rapports cycliques ont été écrits
 * après l'UPDATE suivant de TIM8 : la FOC a démarré trop tard (IRQ masquées
 * par __disable_irq) ou a calculé trop longtemps. Le retard est mesuré par
 * rapport à l'échéancier idéal des démarrages, qui est strictement périodique.
 */

/* Avance minimale entre la fin d'une fenêtre sans IRQ et le prochain
 * démarrage FOC attendu (prologue de l'IRQ DMA inclus). */
#define APP_FOC_TIMING_GUARD_US              3U

typedef struct
{
  uint32_t period_ns;          /* Période FOC nominale */
  uint32_t duration_max_ns;    /* Durée max de TSK_HighFrequencyTask */
  uint32_t late_max_ns;        /* Retard max d'un démarrage FOC */
  uint32_t overrun_count;      /* Périodes terminées en MC_DURATION */
  bool overrun_captured;       /* true si les deux champs suivants sont valides */
  uint32_t overrun_duration_ns;/* Durée de la première période en MC_DURATION */
  uint32_t overrun_late_ns;    /* Retard de démarrage de cette période */
  uint32_t quiet_timeouts;     /* Fenêtres sans IRQ refusées faute de temps libre */
} AppFocTimingReport_t;

/* Active le compteur DWT ; appelé une fois au démarrage du firmware. */
void AppFocTiming_Init(void);

/* Remet à zéro maxima et capture ; appelé à chaque démarrage moteur. */
void AppFocTiming_Arm(void);

/* Début et fin de TSK_HighFrequencyTask (sections USER CODE de mc_tasks.c). */
void AppFocTiming_OnHighFrequencyStart(void);
void AppFocTiming_OnHighFrequencyEnd(bool foc_overrun);

/*
 * Masque les IRQ seulement si le prochain démarrage FOC est à plus de
 * window_us (+ garde). Retourne true avec les IRQ masquées et l'ancien
 * PRIMASK dans *primask ; à rendre avec AppFocTiming_ExitQuietWindow().
 * Retourne false, IRQ actives, si aucune fenêtre n'est trouvée en quatre
 * périodes FOC. Sans FOC active, la fenêtre est accordée tout de suite.
 */
bool AppFocTiming_EnterQuietWindow(uint32_t window_us, uint32_t *primask);
void AppFocTiming_ExitQuietWindow(uint32_t primask);

void AppFocTiming_GetReport(AppFocTimingReport_t *report);

#endif /* APP_FOC_TIMING_H */
