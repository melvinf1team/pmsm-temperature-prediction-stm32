#ifndef APP_MOTOR_CONTROL_H
#define APP_MOTOR_CONTROL_H

#include <stdbool.h>
#include <stdint.h>

/* Bornes applicatives communes au contrôle moteur et au protocole série. */
#define APP_MOTOR_MAX_TARGET_SPEED_RPM       4500.0f
#define APP_MOTOR_MAX_IQ_LIMIT_A             30.0f
#define APP_MOTOR_MAX_TOTAL_CURRENT_A        30.0f

/* TB-200S en mode 0-10 V / 0-3 A. Le DAC 0-3,3 V couvre largement la
 * plage de charge volontairement limitée à 0,05-0,25 A. */
#define APP_TB200S_MIN_LOAD_A                 0.05f
#define APP_TB200S_MAX_LOAD_A                 0.25f

void AppMotorControl_Init(void);
void AppMotorControl_Task(void);

void AppMotorControl_SetRuntimeConfig(float target_rpm,
                                      float iq_limit_a,
                                      float hard_limit_a,
                                      float accel_elec_hz_s);

bool AppMotorControl_Start(void);
void AppMotorControl_Stop(void);
bool AppMotorControl_IsRunning(void);

bool AppMotorControl_SetLoadFixed(float load_a);
bool AppMotorControl_SetLoadVariable(void);
float AppMotorControl_GetLoadSetpointA(void);
uint32_t AppMotorControl_LoadToDacCode(float load_a);
bool AppMotorControl_RestoreLoadOutput(void);

const char *AppMotorControl_GetStateName(void);
/* Cause du dernier passage en FAULT ; "NONE" depuis le dernier démarrage réussi. */
const char *AppMotorControl_GetFaultReason(void);

#endif /* APP_MOTOR_CONTROL_H */
