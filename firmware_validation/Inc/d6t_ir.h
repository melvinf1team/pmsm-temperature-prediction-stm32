#ifndef D6T_IR_H
#define D6T_IR_H

#include <stdbool.h>
#include <stdint.h>

void D6TIR_Init(void);
void D6TIR_Task(uint32_t now_ms);

bool D6TIR_GetTemperatureC(float *temperature_c);
/* Safety path: reject a missing or stale measurement, including across tick wrap. */
bool D6TIR_GetFreshTemperatureC(float *temperature_c, uint32_t max_age_ms);

#endif /* D6T_IR_H */
