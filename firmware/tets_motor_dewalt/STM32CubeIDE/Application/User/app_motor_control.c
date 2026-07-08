#include "app_motor_control.h"

#include "main.h"
#include "motorcontrol.h"
#include "mc_api.h"
#include "mc_interface.h"
#include "mc_type.h"
#include "pmsm_motor_parameters.h"
#include "fixpmath.h"

#include <stdbool.h>

/*
 * ================================
 * CONFIG PAR DEFAUT
 * ================================
 */

/*
 * IMPORTANT :
 * Avec le GUI Python, l'autostart doit être désactivé.
 * Le moteur démarre seulement quand le PC envoie START.
 */
#define APP_MOTOR_AUTOSTART_ENABLE          0

#define APP_MOTOR_START_DELAY_MS            3000U

#define APP_DEFAULT_TARGET_SPEED_RPM        600.0f
#define APP_DEFAULT_IQ_LIMIT_A              2.0f
#define APP_DEFAULT_HARD_STOP_CURRENT_A     6.0f
#define APP_DEFAULT_ACCEL_ELEC_HZ_S         5.0f

#define APP_CURRENT_CHECK_AFTER_RUN_MS      500U
#define APP_HARD_STOP_DEBOUNCE_MS           50U
#define APP_STARTUP_TIMEOUT_MS              5000U

/* Sécurité anti-emballement : si la vitesse estimée dépasse largement
 * la consigne pendant RUN, on coupe le moteur. */
#define APP_OVERSPEED_CHECK_AFTER_RUN_MS    800U
#define APP_OVERSPEED_DEBOUNCE_MS           80U
#define APP_OVERSPEED_RATIO                 1.35f
#define APP_OVERSPEED_MARGIN_RPM            150.0f

#define APP_CFG_MIN_TARGET_SPEED_RPM        100.0f
#define APP_CFG_MAX_TARGET_SPEED_RPM        1600.0f
#define APP_CFG_MAX_IQ_LIMIT_A              20f
#define APP_CFG_MAX_HARD_STOP_CURRENT_A     20f
#define APP_CFG_MAX_ACCEL_ELEC_HZ_S         200.0f

extern MCI_Handle_t *pMCI[NBR_OF_MOTORS];

typedef enum
{
  APP_MC_IDLE = 0,
  APP_MC_WAIT_AUTOSTART,
  APP_MC_STARTING,
  APP_MC_RUNNING,
  APP_MC_FAULT
} AppMotorControlState_t;

static AppMotorControlState_t app_mc_state = APP_MC_IDLE;

static uint32_t app_mc_boot_ms = 0U;
static uint32_t app_mc_start_ms = 0U;
static uint32_t app_mc_run_enter_ms = 0U;
static uint32_t app_mc_overcurrent_since_ms = 0U;
static uint32_t app_mc_overspeed_since_ms = 0U;

static float app_target_speed_rpm = APP_DEFAULT_TARGET_SPEED_RPM;
static float app_iq_limit_a = APP_DEFAULT_IQ_LIMIT_A;
static float app_hard_stop_current_a = APP_DEFAULT_HARD_STOP_CURRENT_A;
static float app_accel_elec_hz_s = APP_DEFAULT_ACCEL_ELEC_HZ_S;

static float AppMotorControl_MechRpmToElecHz(float rpm)
{
  return (rpm * (float)POLE_PAIR_NUM) / 60.0f;
}

static void AppMotorControl_ApplyConfig(void)
{
  float target_elec_hz = AppMotorControl_MechRpmToElecHz(app_target_speed_rpm);

  /*
   * Limite de courant utilisée par le contrôleur.
   * Attention : plafonnée par NOMINAL_CURRENT_A côté paramètres moteur.
   */
  MCI_SetMaxCurrent(pMCI[M1], FIXP16(app_iq_limit_a));

  MC_SetAccelerationMotor1_F(app_accel_elec_hz_s);

  MC_SetControlModeMotor1(MCM_SPEED_MODE);
  MC_SetSpeedReferenceMotor1_F(target_elec_hz);
}

void AppMotorControl_SetRuntimeConfig(float target_rpm,
                                      float iq_limit_a,
                                      float hard_limit_a,
                                      float accel_elec_hz_s)
{
  if (target_rpm < APP_CFG_MIN_TARGET_SPEED_RPM)
  {
    target_rpm = APP_CFG_MIN_TARGET_SPEED_RPM;
  }
  else if (target_rpm > APP_CFG_MAX_TARGET_SPEED_RPM)
  {
    target_rpm = APP_CFG_MAX_TARGET_SPEED_RPM;
  }
  app_target_speed_rpm = target_rpm;

  if (iq_limit_a <= 0.0f)
  {
    iq_limit_a = APP_DEFAULT_IQ_LIMIT_A;
  }
  app_iq_limit_a = iq_limit_a;

  if (hard_limit_a <= 0.0f)
  {
    hard_limit_a = APP_DEFAULT_HARD_STOP_CURRENT_A;
  }
  app_hard_stop_current_a = hard_limit_a;

  if (accel_elec_hz_s <= 0.0f)
  {
    accel_elec_hz_s = APP_DEFAULT_ACCEL_ELEC_HZ_S;
  }
  else if (accel_elec_hz_s > APP_CFG_MAX_ACCEL_ELEC_HZ_S)
  {
    accel_elec_hz_s = APP_CFG_MAX_ACCEL_ELEC_HZ_S;
  }
  app_accel_elec_hz_s = accel_elec_hz_s;

  /*
   * Si plus tard tu veux changer les paramètres pendant RUN,
   * cette ligne applique directement la nouvelle consigne.
   */
  if (app_mc_state == APP_MC_RUNNING)
  {
    AppMotorControl_ApplyConfig();
  }
}

void AppMotorControl_Init(void)
{
  app_mc_boot_ms = HAL_GetTick();

#if APP_MOTOR_AUTOSTART_ENABLE
  app_mc_state = APP_MC_WAIT_AUTOSTART;
#else
  app_mc_state = APP_MC_IDLE;
#endif
}

bool AppMotorControl_Start(void)
{
  MCI_State_t state = MC_GetSTMStateMotor1();

  if (state == FAULT_OVER)
  {
    MC_AcknowledgeFaultsMotor1();
  }

  if (MC_GetCurrentFaultsMotor1() != MC_NO_FAULTS)
  {
    app_mc_state = APP_MC_FAULT;
    return false;
  }

  state = MC_GetSTMStateMotor1();

  if ((state != IDLE) && (state != STOP))
  {
    return false;
  }

  AppMotorControl_ApplyConfig();

  if (MC_StartWithPolarizationMotor1() == MC_SUCCESS)
  {
    app_mc_state = APP_MC_STARTING;
    app_mc_start_ms = HAL_GetTick();
    app_mc_overcurrent_since_ms = 0U;
    app_mc_overspeed_since_ms = 0U;
    return true;
  }

  app_mc_state = APP_MC_FAULT;
  return false;
}

void AppMotorControl_Stop(void)
{
  MC_StopMotor1();

  app_mc_state = APP_MC_IDLE;
  app_mc_start_ms = 0U;
  app_mc_run_enter_ms = 0U;
  app_mc_overcurrent_since_ms = 0U;
  app_mc_overspeed_since_ms = 0U;
}

bool AppMotorControl_IsRunning(void)
{
  return (app_mc_state == APP_MC_RUNNING) || (MC_GetSTMStateMotor1() == RUN);
}

void AppMotorControl_Task(void)
{
  uint32_t now = HAL_GetTick();
  uint32_t current_faults = MC_GetCurrentFaultsMotor1();
  MCI_State_t mc_state = MC_GetSTMStateMotor1();

  if (current_faults != MC_NO_FAULTS)
  {
    MC_StopMotor1();
    app_mc_state = APP_MC_FAULT;
    return;
  }

  switch (app_mc_state)
  {
    case APP_MC_WAIT_AUTOSTART:
      if ((now - app_mc_boot_ms) >= APP_MOTOR_START_DELAY_MS)
      {
        (void)AppMotorControl_Start();
      }
      break;

    case APP_MC_STARTING:
      if (mc_state == RUN)
      {
        app_mc_state = APP_MC_RUNNING;
        app_mc_run_enter_ms = now;
        app_mc_overcurrent_since_ms = 0U;
        app_mc_overspeed_since_ms = 0U;
      }
      else if ((mc_state == FAULT_NOW) || (mc_state == FAULT_OVER))
      {
        app_mc_state = APP_MC_FAULT;
      }
      else if ((now - app_mc_start_ms) > APP_STARTUP_TIMEOUT_MS)
      {
        MC_StopMotor1();
        app_mc_state = APP_MC_FAULT;
      }
      break;

    case APP_MC_RUNNING:
    {
      dq_float_t idq = MC_GetCurrentMotor1_F();

      float i2 = (idq.D * idq.D) + (idq.Q * idq.Q);
      float i_max2 = app_hard_stop_current_a * app_hard_stop_current_a;

      if ((now - app_mc_run_enter_ms) >= APP_CURRENT_CHECK_AFTER_RUN_MS)
      {
        if (i2 > i_max2)
        {
          if (app_mc_overcurrent_since_ms == 0U)
          {
            app_mc_overcurrent_since_ms = now;
          }

          if ((now - app_mc_overcurrent_since_ms) >= APP_HARD_STOP_DEBOUNCE_MS)
          {
            MC_StopMotor1();
            app_mc_state = APP_MC_FAULT;
          }
        }
        else
        {
          app_mc_overcurrent_since_ms = 0U;
        }
      }

      if ((now - app_mc_run_enter_ms) >= APP_OVERSPEED_CHECK_AFTER_RUN_MS)
      {
        float speed_elec_hz = MC_GetSpeedMotor1_F();
        float speed_rpm = (speed_elec_hz * 60.0f) / (float)POLE_PAIR_NUM;
        float speed_limit_rpm = (app_target_speed_rpm * APP_OVERSPEED_RATIO) + APP_OVERSPEED_MARGIN_RPM;

        if (speed_rpm < 0.0f)
        {
          speed_rpm = -speed_rpm;
        }

        if (speed_rpm > speed_limit_rpm)
        {
          if (app_mc_overspeed_since_ms == 0U)
          {
            app_mc_overspeed_since_ms = now;
          }

          if ((now - app_mc_overspeed_since_ms) >= APP_OVERSPEED_DEBOUNCE_MS)
          {
            MC_StopMotor1();
            app_mc_state = APP_MC_FAULT;
          }
        }
        else
        {
          app_mc_overspeed_since_ms = 0U;
        }
      }

      break;
    }

    case APP_MC_FAULT:
      break;

    case APP_MC_IDLE:
    default:
      break;
  }
}
