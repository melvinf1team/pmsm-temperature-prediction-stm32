#ifndef APP_CONFIG_H
#define APP_CONFIG_H

/* 1: embedded inference, output D6T;prediction;TB-200S setpoint.
 * 0: Serial Emulator, output all 55 features. */
#ifndef APP_NEAI_MODEL_ENABLED
#define APP_NEAI_MODEL_ENABLED  1U
#endif

#if ((APP_NEAI_MODEL_ENABLED != 0U) && (APP_NEAI_MODEL_ENABLED != 1U))
#error "APP_NEAI_MODEL_ENABLED must be 0 or 1"
#endif

#endif /* APP_CONFIG_H */
