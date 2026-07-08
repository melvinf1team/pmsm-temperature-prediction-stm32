#ifndef APP_MOTOR_CONTROL_H
#define APP_MOTOR_CONTROL_H

#include <stdbool.h>

void AppMotorControl_Init(void);
void AppMotorControl_Task(void);

void AppMotorControl_SetRuntimeConfig(float target_rpm,
                                      float iq_limit_a,
                                      float hard_limit_a,
                                      float accel_elec_hz_s);

bool AppMotorControl_Start(void);
void AppMotorControl_Stop(void);
bool AppMotorControl_IsRunning(void);

#endif /* APP_MOTOR_CONTROL_H */
