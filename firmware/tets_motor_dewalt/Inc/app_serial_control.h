#ifndef APP_SERIAL_CONTROL_H
#define APP_SERIAL_CONTROL_H

void AppSerialControl_Init(void);
void AppSerialControl_Task(void);

void AppSerialControl_OnUsart1Irq(void);

#endif /* APP_SERIAL_CONTROL_H */
