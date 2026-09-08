#ifndef APP_MOTOR_CONTROL_H
#define APP_MOTOR_CONTROL_H

#include <stdbool.h>

/* Bornes applicatives communes au contrôle moteur et au protocole série. */
#define APP_MOTOR_MAX_TARGET_SPEED_RPM       2500.0f
#define APP_MOTOR_MAX_IQ_LIMIT_A             12.0f
#define APP_MOTOR_MAX_TOTAL_CURRENT_A        14.0f

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
