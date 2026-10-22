#ifndef APP_MOTOR_CONTROL_H
#define APP_MOTOR_CONTROL_H

#include <stdbool.h>
#include <stdint.h>

/* Bornes applicatives communes au contrôle moteur et au protocole série. */
#define APP_MOTOR_MAX_TARGET_SPEED_RPM       4500.0f
#define APP_MOTOR_MAX_IQ_LIMIT_A             30.0f
#define APP_MOTOR_MAX_TOTAL_CURRENT_A        30.0f

/* Custom START: speed first, then brake load; B2/PROFILE keep their presets. */
#define APP_CUSTOM_START_SPEED_RPM           2000.0f
#define APP_CUSTOM_START_ACCEL_ELEC_HZ_S      500.0f
#define APP_CUSTOM_START_STABLE_MS           2000U
#define APP_CUSTOM_SPEED_RAMP_RPM_PER_S       500.0f
#define APP_CUSTOM_TARGET_STABLE_MS          1000U
#define APP_CUSTOM_LOAD_RAMP_A_PER_S          0.04f
#define APP_CUSTOM_SPEED_TOLERANCE_RATIO     0.05f
#define APP_CUSTOM_MIN_SPEED_TOLERANCE_RPM   50.0f
#define APP_CUSTOM_SPEED_TIMEOUT_MS          5000U

/* Positive d-axis heating current, applied only after stable RUN speed. */
#define APP_ID_TARGET_A                       2.0f
#define APP_ID_RAMP_A_PER_S                    0.25f
#define APP_ID_SPEED_STABLE_MS                1000U
#define APP_ID_SPEED_TOLERANCE_RATIO          0.05f
#define APP_ID_MIN_SPEED_TOLERANCE_RPM        50.0f

/* D6T motor protection; restart needs a fresh reading and an explicit start. */
#define APP_MOTOR_OVERTEMP_C                  70.0f
#define APP_MOTOR_RESTART_TEMP_C              65.0f
#define APP_MOTOR_D6T_MAX_AGE_MS               1000U

/* TB-200S en mode 0-10 V / 0-3 A. Le DAC 0-3,3 V couvre largement la
 * plage de charge volontairement limitée à 0,05-0,25 A. */
#define APP_TB200S_MIN_LOAD_A                 0.05f
#define APP_TB200S_MAX_LOAD_A                 0.25f

/* Profils prédéfinis, identiques à ceux du firmware de validation. */
typedef enum
{
  APP_MOTOR_PROFILE_STABLE = 0,
  APP_MOTOR_PROFILE_VARIABLE_LOAD,
  APP_MOTOR_PROFILE_VARIABLE_SPEED,
  APP_MOTOR_PROFILE_VARIABLE_ALL,
  APP_MOTOR_PROFILE_COUNT
} AppMotorProfile_t;

typedef enum
{
  APP_MOTOR_PROFILE_START_ACCEPTED = 0,
  APP_MOTOR_PROFILE_START_INVALID,
  APP_MOTOR_PROFILE_START_LOAD_ERROR,
  APP_MOTOR_PROFILE_START_THERMAL_ERROR
} AppMotorProfileStartResult_t;

void AppMotorControl_Init(void);
void AppMotorControl_Task(void);

void AppMotorControl_SetRuntimeConfig(float target_rpm,
                                      float iq_limit_a,
                                      float hard_limit_a,
                                      float accel_elec_hz_s);

bool AppMotorControl_Start(void);
void AppMotorControl_Stop(void);
bool AppMotorControl_IsRunning(void);

/* Arme puis lance le profil de façon non bloquante. Si le moteur tourne déjà,
 * il est d'abord arrêté et le redémarrage attend que le MCSDK revienne IDLE. */
AppMotorProfileStartResult_t AppMotorControl_StartProfile(
  AppMotorProfile_t profile);

bool AppMotorControl_SetLoadFixed(float load_a);
bool AppMotorControl_SetLoadVariable(void);
float AppMotorControl_GetLoadSetpointA(void);
uint32_t AppMotorControl_LoadToDacCode(float load_a);
bool AppMotorControl_RestoreLoadOutput(void);

const char *AppMotorControl_GetStateName(void);
/* Cause du dernier passage en FAULT ; "NONE" depuis le dernier démarrage réussi. */
const char *AppMotorControl_GetFaultReason(void);

#endif /* APP_MOTOR_CONTROL_H */
