#ifndef APP_CONFIG_H
#define APP_CONFIG_H

/* 1 : inference embarquee, sortie D6T;prediction;consigne TB-200S.
 * 0 : Serial Emulator, sortie des 55 features. */
#ifndef APP_NEAI_MODEL_ENABLED
#define APP_NEAI_MODEL_ENABLED  1U
#endif

#if ((APP_NEAI_MODEL_ENABLED != 0U) && (APP_NEAI_MODEL_ENABLED != 1U))
#error "APP_NEAI_MODEL_ENABLED doit valoir 0 ou 1"
#endif

#endif /* APP_CONFIG_H */
