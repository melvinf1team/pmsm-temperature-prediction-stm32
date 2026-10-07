#ifndef D6T_IR_H
#define D6T_IR_H

#include <stdbool.h>
#include <stdint.h>

#define D6TIR_PIXEL_COUNT  16U

void D6TIR_Init(void);
void D6TIR_Task(uint32_t now_ms);

bool D6TIR_IsPresent(void);
const char *D6TIR_GetCsvValue(void);

/* Dernière trame valide, en dixièmes de degré : PTAT (référence interne) et
 * 16 pixels dans l'ordre du capteur. Faux si le capteur ne répond plus. */
bool D6TIR_GetFrameTenths(int16_t *ptat_tenth, int16_t pixels_tenth[D6TIR_PIXEL_COUNT]);
uint8_t D6TIR_GetSelectedPixel(void);

#endif /* D6T_IR_H */