#ifndef D6T_IR_H
#define D6T_IR_H

#include <stdbool.h>
#include <stdint.h>

void D6TIR_Init(void);
void D6TIR_Task(uint32_t now_ms);

bool D6TIR_GetTemperatureC(float *temperature_c);

#endif /* D6T_IR_H */