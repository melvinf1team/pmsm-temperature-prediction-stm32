#ifndef APP_MC_DEBUG_H
#define APP_MC_DEBUG_H

#include <stdint.h>

void AppMcDebug_Init(void);
void AppMcDebug_PrintConfig(void);
void AppMcDebug_Snapshot(const char *tag);
void AppMcDebug_PrintStartRequest(float target_elec_hz,
                                  float accel_elec_hz_s,
                                  float current_limit_a);
void AppMcDebug_PrintStartResult(uint32_t ret);
void AppMcDebug_Task(void);

#endif /* APP_MC_DEBUG_H */