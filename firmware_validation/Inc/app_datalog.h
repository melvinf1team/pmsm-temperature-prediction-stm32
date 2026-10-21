#ifndef APP_DATALOG_H
#define APP_DATALOG_H

#include <stdbool.h>

void AppDatalog_Init(void);
void AppDatalog_Task(void);
void AppDatalog_OnUsart1Irq(void);
bool AppDatalog_SendText(const char *text);

#endif /* APP_DATALOG_H */
