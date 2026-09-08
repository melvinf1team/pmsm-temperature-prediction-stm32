#ifndef APP_DATALOG_H
#define APP_DATALOG_H

#include <stdbool.h>
#include <stdint.h>

void AppDatalog_Init(void);
void AppDatalog_Task(void);

void AppDatalog_SetRuntimePeriods(uint32_t datalog_ms,
                                  uint32_t ds18b20_ms);

void AppDatalog_StartLogging(void);
void AppDatalog_StopLogging(void);
bool AppDatalog_IsLogging(void);

/*
 * Utilisé par app_serial_control.c pour envoyer ACK / ERR sur le même UART.
 */
bool AppDatalog_SendText(const char *text);

#endif /* APP_DATALOG_H */
