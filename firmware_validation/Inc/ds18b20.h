#ifndef DS18B20_H
#define DS18B20_H

#include <stdbool.h>
#include <stdint.h>

#define DS18B20_CONVERSION_TIME_MS  750U

void DS18B20_Init(void);
bool DS18B20_StartMeasurement(void);
bool DS18B20_ReadMeasurement(void);
bool DS18B20_GetLastTemperatureC(float *temperature_c);

#endif /* DS18B20_H */