
/**
 ******************************************************************************
 * @file    mc_tasks.c
 * @author  Motor Control SDK Team, ST Microelectronics
 * @brief   This file implements tasks routines
 *
 ******************************************************************************
 * @attention
 *
 * <h2><center>&copy; Copyright (c) 2026 STMicroelectronics.
 * All rights reserved.</center></h2>
 *
 * This software component is licensed by ST under Ultimate Liberty license
 * SLA0044, the "License"; You may not use this file except in compliance with
 * the License. You may obtain a copy of the License at:
 *                             www.st.com/SLA0044
 *
 ******************************************************************************
 * @ingroup MCTasks_hso
 */

/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "mc_type.h"
#include "mc_math.h"
#include "motorcontrol.h"
#include "mc_interface.h"
#include "pwm_common.h"
#include "mc_tasks.h"
#include "parameters_conversion.h"
#include "mc_parameters.h"
#include "register_interface.h"
#include "mcp_config.h"
#include "mc_curr_ctrl.h"
#include "speed_pos_fdbk_hso.h"
#include "speed_torq_ctrl_hso.h"
#include "rsdc_est.h"
#include "mc_polpulse.h"

#include "fixpmath.h"
#include "oversampling.h"

/** @defgroup MCTasks_hso Motor Control Tasks
  *
  * @brief HSO Motor Control subsystem configuration and operation routines.
  *
  * @{
  */

/* USER CODE BEGIN Includes */
#include "app_config.h"
/* USER CODE END Includes */

/* USER CODE BEGIN Private define */
/* Private define ------------------------------------------------------------*/

/* USER CODE END Private define */
/*! @brief  defines the type of error to check in the safety task */
#define VBUS_TEMP_ERR_MASK (MC_OVER_VOLT| MC_UNDER_VOLT| MC_OVER_TEMP)

#define M1_CHARGE_BOOT_CAP_TICKS          (((uint16_t)SYS_TICK_FREQUENCY * (uint16_t)10) / 1000U)
#define M1_CHARGE_BOOT_CAP_DUTY_CYCLES ((uint32_t)0.000\
                                      * ((uint32_t)PWM_PERIOD_CYCLES / 2U))

/* Private variables----------------------------------------------------------*/
FOCVars_t FOCVars[NBR_OF_MOTORS];
MCI_Handle_t * pMCInterface[NBR_OF_MOTORS];
BusVoltageSensor_Handle_t *pVBus[NBR_OF_MOTORS];
PWMC_Handle_t * pwmcHandle[NBR_OF_MOTORS];
STC_Handle_t *pSTC[NBR_OF_MOTORS];
CurrCtrl_Handle_t *pCurrCtrlhandle[NBR_OF_MOTORS];
SPD_Handle_t *pSPD[NBR_OF_MOTORS];
static volatile uint16_t hMFTaskCounterM1 = 0;
static volatile uint16_t hBootCapDelayCounterM1 = 0;
static volatile uint16_t hStopPermanencyCounterM1 = 0;

uint8_t bMCBootCompleted = 0;

/* Performs the CPU load measure of FOC main tasks. */

/* USER CODE BEGIN Private Variables */

/* USER CODE END Private Variables */

/* Private functions ---------------------------------------------------------*/

void TSK_MediumFrequencyTaskM1(void);
void FOC_Clear(uint8_t bMotor);
static uint16_t FOC_CurrControllerM1(void);
void TSK_SetChargeBootCapDelayM1(uint16_t hTickCount);
bool TSK_ChargeBootCapDelayHasElapsedM1(void);
void TSK_SetStopPermanencyTimeM1(uint16_t hTickCount);
bool TSK_StopPermanencyTimeHasElapsedM1(void);
void TSK_SafetyTask_PWMOFF(uint8_t motor);
void FOC_ReferenceSelection(uint8_t bMotor);
void MC_init(void);
uint16_t ErrorDetection(uint8_t bMotor);

/* USER CODE BEGIN Private Functions */

/* USER CODE END Private Functions */

__weak void MCboot_init( MCI_Handle_t* pMCIList[NBR_OF_MOTORS] );

/* Reference to pMCI and pMCT to re-run init without LoadConfiguration */
extern MCI_Handle_t* pMCI[NBR_OF_MOTORS];

/**
  * @brief  Initializes the whole motor control subsystem according to user
  *         configuration and parameters.
  * @param  pMCIList motor control interface data structure that will be
  *         initialized.
  */
__weak void MCboot( MCI_Handle_t* pMCIList[NBR_OF_MOTORS] )
{
  /* USER CODE BEGIN MCboot 0 */

  /* USER CODE END MCboot 0 */

  /**************************************/
  /*    State machine initialization    */
  /**************************************/
  bMCBootCompleted = 0;

  /**********************************************************/
  /*    PWM and current sensing component initialization    */
  /**********************************************************/
  pwmcHandle[M1] = &PWM_Handle_M1._Super;
  R3_Init(&PWM_Handle_M1, scaleParams);
  ASPEP_start (&aspepOverUartA);

  MCP_RegisterCallBack (0,flashWrite_cb);
  MCP_RegisterCallBack (1,flashRead_cb);

  /* USER CODE BEGIN MCboot 1 */

  /* USER CODE END MCboot 1 */

  /******************************************************/
  /*   Main speed sensor component initialization       */
  /******************************************************/
  pMCInterface[M1] = &Mci[M1];
  pMCInterface[M1]->pVBus = &VBus_M1;
  MCI_Init(pMCInterface[M1], &FOCVars[M1], &flashParams);
  pMCIList[M1] = pMCInterface[M1];

  /* USER CODE BEGIN MCboot 2 */

  /* USER CODE END MCboot 2 */

  /*******************************************************/
  /*    speed and torque controller initialization       */
  /*******************************************************/
  STC_Init(&STC_M1, &flashParams);
  pSTC[M1] = &STC_M1;

  /*******************************************************/
  /*   Sensor less controller initialization             */
  /*******************************************************/
  SPD_Init(&SPD_M1,&flashParams);
  pSPD[M1] = &SPD_M1;

  /*******************************************************/
  /*   Current controller initialization                 */
  /*******************************************************/
  MC_currentControllerInit(&CurrCtrl_M1, &flashParams);
  pCurrCtrlhandle[M1] = &CurrCtrl_M1;

  /*******************************************************/
  /*   Temperature measurement component initialization  */
  /*******************************************************/
  NTC_Init(&TempSensor_M1);

  /*******************************************************/
  /*   PolPulse component initialization                 */
  /*******************************************************/
  MC_POLPULSE_init(&MC_PolPulse_M1, &PolPulse_params_M1,&flashParams);

  Mci[M1].pPWM = pwmcHandle[M1];
  Mci[M1].pSPD = &SPD_M1;
  Mci[M1].pSTC = &STC_M1;
  Mci[M1].pCurrCtrl = &CurrCtrl_M1;
  Mci[M1].pRsDCEst = &RsDCEst_M1;
  Mci[M1].pPolPulse = &MC_PolPulse_M1;
  Mci[M1].pProfiler = &profiler_M1;
  /* Perform additional initialization */
  MC_init();

  FOC_Clear(M1);

  LL_TIM_ClearFlag_UPDATE(TIM3);
  /* start timers synchronously */
  startTimers();

#if (OVS_COUNT !=1)
  while(LL_TIM_IsActiveFlag_UPDATE(TIM3) == 0)
  {}
#endif

  /* Start conversions */
  LL_ADC_REG_StartConversion(ADC3);
  LL_ADC_REG_StartConversion(ADC4);
  LL_ADC_REG_StartConversion(ADC5);

  bMCBootCompleted = 1;
}

/**
 * @brief Performs stop process and update the state machine.This function
 *        shall be called only during medium frequency task
 * @param pHandle Motor Control Intercafe data structure
 * @param motor Motor ID
 */
void TSK_MF_StopProcessing( uint8_t motor)
{
  R3_SwitchOffPWM(pwmcHandle[motor]);
  FOC_Clear(motor);
  TSK_SetStopPermanencyTimeM1(STOPPERMANENCY_TICKS);
  Mci[motor].State = STOP;
  return;
}

/**
 * @brief Runs all the Tasks of the Motor Control cockpit
 *
 * This function is called periodically at least at the Medium Frequency task
 * rate. Exact invocation rate is the Speed regulator execution rate.
 *
 * It executes  the Medium Frequency Tasks  processes communication
 * TX/RX packets.
 */
__weak void MC_RunMotorControlTasks(void)
{
  if ( bMCBootCompleted ) {
    /* ** Medium Frequency Tasks ** */
    MC_Scheduler();
  }
}

/**
 * @brief  Schedules background routines.
 *
 * MC_scheduler executes Medium frequency task and [motor control protocol]()
 * communication at a frequency defined by SPEED_LOOP_FREQUENCY_HZ.
 *
 */
__weak void MC_Scheduler(void)
{
  /* USER CODE BEGIN MC_Scheduler 0 */

  /* USER CODE END MC_Scheduler 0 */

  if (bMCBootCompleted == 1)
  {
    if(hMFTaskCounterM1 > 0u)
    {
      hMFTaskCounterM1--;
    }
    else
    {
      TSK_MediumFrequencyTaskM1();

      MCP_Over_UartA.rxBuffer = MCP_Over_UartA.pTransportLayer->fRXPacketProcess ( MCP_Over_UartA.pTransportLayer,  &MCP_Over_UartA.rxLength);
      if (MCP_Over_UartA.rxBuffer)
      {
        /* Synchronous answer */
        if (MCP_Over_UartA.pTransportLayer->fGetBuffer (MCP_Over_UartA.pTransportLayer, (void **) &MCP_Over_UartA.txBuffer, MCTL_SYNC))
        {
          MCP_ReceivedPacket(&MCP_Over_UartA);
          MCP_Over_UartA.pTransportLayer->fSendPacket (MCP_Over_UartA.pTransportLayer, MCP_Over_UartA.txBuffer, MCP_Over_UartA.txLength, MCTL_SYNC);
        }
        else
        {
          /* no buffer available to build the answer ... should not occur */
        }
      }

      /* USER CODE BEGIN MC_Scheduler 1 */

      /* USER CODE END MC_Scheduler 1 */
      hMFTaskCounterM1 = MF_TASK_OCCURENCE_TICKS;
    }
    if(hBootCapDelayCounterM1 > 0u)
    {
      hBootCapDelayCounterM1--;
    }
    if(hStopPermanencyCounterM1 > 0u)
    {
      hStopPermanencyCounterM1--;
    }
  }
  else
  {
  }
  /* USER CODE BEGIN MC_Scheduler 2 */

  /* USER CODE END MC_Scheduler 2 */
}

/**
 * @brief Executes medium frequency tasks
 *
 * This function manages the motor control [state machine](motor_control_subsystemstatemachine_and_commands_handling.md) and executes
 * background routines that requires heavy computation.
 *
 */
__weak void TSK_MediumFrequencyTaskM1(void)
{

  /* USER CODE BEGIN MediumFrequencyTask M1 0 */

  /* USER CODE END MediumFrequencyTask M1 0 */
  FOCVars_t* pFOCVars = &FOCVars[M1];
  fixp30_t emfSpeed;
  fixp30_t speedLP;

  /* Update the resistance estimation */
  SPD_RsBackground(&SPD_M1);

  /* Angle compensation */
  SPD_GetSpeed(&SPD_M1, &emfSpeed, &speedLP);
  AngleCompensation_runBackground(&CurrCtrl_M1, emfSpeed);

  if (MCI_GetCurrentFaults(&Mci[M1]) == MC_NO_FAULTS)
  {
    if (MCI_GetOccurredFaults(&Mci[M1]) == MC_NO_FAULTS)
    {
      switch (Mci[M1].State)
      {
        case IDLE:
        {
          if (MCI_START == Mci[M1].DirectCommand)
          {
            if ((pFOCVars->flagStartWithCalibration == true) || (pFOCVars->PolarizationState == NOT_DONE))
            {
              TSK_SetChargeBootCapDelayM1(M1_CHARGE_BOOT_CAP_TICKS);
              R3_PwmShort(pwmcHandle[M1]);
              Mci[M1].State = OFFSET_CALIB;
            }
            else
            {
              TSK_SetChargeBootCapDelayM1(M1_CHARGE_BOOT_CAP_TICKS);
              R3_PwmShort(pwmcHandle[M1]);
              Mci[M1].State = CHARGE_BOOT_CAP;
            }
          }
          else if (MCI_MEASURE_OFFSETS == Mci[M1].DirectCommand)
          {
            TSK_SetChargeBootCapDelayM1(M1_CHARGE_BOOT_CAP_TICKS);
            R3_PwmShort(pwmcHandle[M1]);
            Mci[M1].State = OFFSET_CALIB;
          }
        break;
        }

        case CHARGE_BOOT_CAP:
        {
          if (MCI_STOP == Mci[M1].DirectCommand)
          {
            TSK_MF_StopProcessing(M1);
          }
          else
          {
            if ( TSK_ChargeBootCapDelayHasElapsedM1() )
            {
                FOC_Clear(M1);

                {
                  Mci[M1].State = RUN;  /* Proceed on RUN  */
                }

        /*   USER CODE BEGIN MediumFrequencyTask M1 Charge BootCap elapsed */

        /*   USER CODE END MediumFrequencyTask M1 Charge BootCap elapsed */
            }
            else
            {
              /* wait for boot capacitor charge */
            }
          }
          break;
        }

      case OFFSET_CALIB:
      {
        if (MCI_STOP == Mci[M1].DirectCommand)
        {
          TSK_MF_StopProcessing(M1);
        }
        else
        {
          if ( TSK_ChargeBootCapDelayHasElapsedM1() )
          {
            if ((pFOCVars->PolarizationState == NOT_DONE))
            {
              PWMC_CurrentReadingCalibr( pwmcHandle[M1], CRC_START );
              pFOCVars->PolarizationState = ONGOING;
            }
            else if ( pFOCVars->PolarizationState == ONGOING )
            {
              if ( PWMC_CurrentReadingCalibr( pwmcHandle[M1], CRC_EXEC ) )
              {
                pFOCVars->PolarizationState = COMPLETED;
                if (MCI_MEASURE_OFFSETS == Mci[M1].DirectCommand)
                {
                  FOC_Clear(M1);
                  Mci[M1].DirectCommand = MCI_NO_COMMAND;
                  Mci[M1].State = IDLE;  /* Back to Idle state after Calibration procedure */
                }
                else if (pFOCVars->flagStartWithCalibration)
                {
                  pFOCVars->flagStartWithCalibration = false;
                  MCI_SetControlMode(&Mci[M1],FOCVars[M1].controlMode);
                  TSK_SetChargeBootCapDelayM1(M1_CHARGE_BOOT_CAP_TICKS);
                  R3_PwmShort(pwmcHandle[M1]);
                  Mci[M1].State = CHARGE_BOOT_CAP;  /* Proceed on RUN  */
                }
                else
                {
                   Mci[M1].State = IDLE;  /* Back to Idle state after Calibration procedure */
                }
              }
              else
              {
                /* wait for offset calibration end */
              }
            }
            else
            {
              Mci[M1].State = IDLE;  /* Back to Idle in cases */
            }
          }
          else
          {
            /* wait for boostrap capacitor to be charged */
          }
        }
        break;
      }

      case RUN:
      {
        /* USER CODE BEGIN MediumFrequencyTask M1 2 */

        /* USER CODE END MediumFrequencyTask M1 2 */
        if (MCI_STOP == Mci[M1].DirectCommand)
        {
          TSK_MF_StopProcessing(M1);
        }
        else
        {
          if ( (RsDCEst_M1.RSDCestimate_Fast && RsDCEst_M1.RSDCestimate_state != RSDC_EST_COMPLETED) &&
               (pFOCVars->controlMode == MCM_SPEED_MODE || pFOCVars->controlMode == MCM_TORQUE_MODE) &&
               (pFOCVars->controlMode_prev != pFOCVars->controlMode) )
          {
              TSK_PulsesRsDC(FAST_RSDC);
          }
          else if ( (RsDCEst_M1.flag_enableRSDCestimate && RsDCEst_M1.RSDCestimate_state != RSDC_EST_COMPLETED) &&
                    (pFOCVars->controlMode == MCM_SPEED_MODE || pFOCVars->controlMode == MCM_TORQUE_MODE) &&
                    (pFOCVars->controlMode_prev != pFOCVars->controlMode) )
          {
              TSK_PulsesRsDC(RSDC_ONLY);
          }
          else if ( (MC_PolPulse_M1.flagPolePulseActivation) && (POLPULSE_getState(&MC_PolPulse_M1.PolpulseObj) != POLPULSE_STATE_PostcalcsComplete) &&
                    (pFOCVars->controlMode == MCM_SPEED_MODE || pFOCVars->controlMode == MCM_TORQUE_MODE) &&
                    (pFOCVars->controlMode_prev != pFOCVars->controlMode) )
          {
              TSK_PulsesRsDC(POL_PULSE_ONLY);
          }
          else
          {
            if (pFOCVars->controlMode_prev != pFOCVars->controlMode)
            {
              pFOCVars->controlMode_prev = pFOCVars->controlMode;
              __disable_irq();  // << Start of critical zone: write global variables read by HighFrequencyTask

              // During RUN we can switch MODE which may enable/disable PWM etc
              switch (pFOCVars->controlMode)
              {
                case MCM_OBSERVING_MODE:
                  /* PWM output is off, HSO is active */
                  STC_M1.SpeedControlEnabled = false;
                  CurrCtrl_M1.currentControlEnabled = true; // NOTE PWM off, current control tracking the duty
                  SPD_M1.closedLoopAngleEnabled = true;
                  SPD_M1.flagHsoAngleEqualsOpenLoopAngle = false;
                  CurrCtrl_M1.forceZeroPwm = false;
                  POLPULSE_resetState(&MC_PolPulse_M1.PolpulseObj);
                  SPD_M1.flagRsTemp = false;
                  // Disable PWM while in Observing mode
                  if (PWMC_IsPwmEnabled(pwmcHandle[M1]))
                  {
                    PWMC_SwitchOffPWM(pwmcHandle[M1]);
                  }
                  break;

                case MCM_SPEED_MODE:
                  /* Closed loop angle and speed PID active */
                  STC_M1.SpeedControlEnabled = true;
                  CurrCtrl_M1.currentControlEnabled = true;
                  SPD_M1.closedLoopAngleEnabled = true;
                  SPD_M1.flagHsoAngleEqualsOpenLoopAngle = false;
                  CurrCtrl_M1.forceZeroPwm = false;
                  PIDREG_SPEED_setUi_pu(&pSTC[M1]->PIDSpeed, FIXP30(0.0f));
                  SPD_M1.flagRsTemp = false;
                  // Enable PWM in these modes
                  PWMC_SwitchOnPWM(pwmcHandle[M1]);
                  HSO_setFlag_EnableOffsetUpdate(&HSO_M1, true);
                  break;

                case MCM_TORQUE_MODE:
                  /* Closed loop angle with speed PID disabled */
                  STC_M1.SpeedControlEnabled = false;
                  CurrCtrl_M1.currentControlEnabled = true;
                  SPD_M1.closedLoopAngleEnabled = true;
                  SPD_M1.flagHsoAngleEqualsOpenLoopAngle = false;
                  CurrCtrl_M1.forceZeroPwm = false;
                  // Enable PWM in these modes
                  PWMC_SwitchOnPWM(pwmcHandle[M1]);
                  HSO_setFlag_EnableOffsetUpdate(&HSO_M1, true);
                  SPD_M1.flagRsTemp = false;
                  break;

                case MCM_OPEN_LOOP_CURRENT_MODE:
                  /* Open loop angle, with current controllers active */
                  STC_M1.SpeedControlEnabled = false;
                  CurrCtrl_M1.currentControlEnabled = true;
                  SPD_M1.closedLoopAngleEnabled = false;
                  SPD_M1.flagHsoAngleEqualsOpenLoopAngle = true;
                  CurrCtrl_M1.forceZeroPwm = false;
                  // Enable PWM in these modes
                  PWMC_SwitchOnPWM(pwmcHandle[M1]);
                  HSO_setFlag_EnableOffsetUpdate(&HSO_M1, true);
                  SPD_M1.flagRsTemp = false;
                  break;

                case MCM_OPEN_LOOP_VOLTAGE_MODE:
                  /* Open loop angle, using duty reference Ddq_ref_pu */
                  STC_M1.SpeedControlEnabled = false;
                  CurrCtrl_M1.currentControlEnabled = false;
                  SPD_M1.closedLoopAngleEnabled = false;
                  SPD_M1.flagHsoAngleEqualsOpenLoopAngle = true;
                  CurrCtrl_M1.forceZeroPwm = false;
                  // Enable PWM in these modes
                  PWMC_SwitchOnPWM(pwmcHandle[M1]);
                  HSO_setFlag_EnableOffsetUpdate(&HSO_M1, true);
                  SPD_M1.flagRsTemp = false;
                  break;

                case MCM_SHORTED_MODE:
                  /* PWM output is on, HSO is active, duty is being forced the same on all phases, shorting the motor */
                  STC_M1.SpeedControlEnabled = false;
                  CurrCtrl_M1.currentControlEnabled = true;
                  SPD_M1.closedLoopAngleEnabled = true;
                  SPD_M1.flagHsoAngleEqualsOpenLoopAngle = false;
                  CurrCtrl_M1.forceZeroPwm = false;
                  POLPULSE_resetState(&MC_PolPulse_M1.PolpulseObj);
                  SPD_M1.flagRsTemp = false;
                  // Short PWM while in Shorted mode
                  PWMC_PWMShort(pwmcHandle[M1]);
                  break;

                default:
                  /* No action */
                  break;
              }
              __enable_irq();  // >> End of critical zone
            }
          }
        }
        /* USER CODE BEGIN MediumFrequencyTask M1 3 */

        /* USER CODE END MediumFrequencyTask M1 3 */
        break;
      }

      case STOP:
      {
        if (TSK_StopPermanencyTimeHasElapsedM1())
        {
          Mci[M1].DirectCommand = MCI_NO_COMMAND;
          Mci[M1].State = IDLE;
          PWMC_SwitchOffPWM(pwmcHandle[M1]);
          HSO_setFlag_EnableOffsetUpdate(&HSO_M1, false);
        }
        else
        {
          /* nothing to do, FW waits for to stop */
        }
        break;
      }

      case FAULT_OVER:
      {
        if (MCI_ACK_FAULTS == Mci[M1].DirectCommand)
        {
          Mci[M1].DirectCommand = MCI_NO_COMMAND;
          Mci[M1].State = IDLE;

        }
        else
        {
          /* nothing to do, FW stays in FAULT_OVER state until acknowledgement */
        }
        break;
      }

      case FAULT_NOW:
      {
        Mci[M1].State = FAULT_OVER;
        break;
      }

      default:
        break;

      }
    }
    else
    {
      Mci[M1].State = FAULT_OVER;
    }
  }
  else
  {
    Mci[M1].State = FAULT_NOW;
  }

  FOC_ReferenceSelection(M1);

  /* PolPulse background handling */
  {
	  fixp_t oneOverUdc_pu = pCurrCtrlhandle[M1]->busVoltageComp;
	  POLPULSE_runBackground(&MC_PolPulse_M1.PolpulseObj, oneOverUdc_pu, VBus_M1.Udcbus_in_pu);
  }

  /* USER CODE BEGIN MediumFrequencyTask M1 6 */

  /* USER CODE END MediumFrequencyTask M1 6 */

}

/**
 * @brief  Clears variables for the motor identified by @p bMotor.
 *         It must be called before each motor restart.
 */
__weak void FOC_Clear(uint8_t bMotor)
{
  /* USER CODE BEGIN FOC_Clear 0 */

  /* USER CODE END FOC_Clear 0 */

  /* Initialize additional FOCVars members */
  FOCVars_t* pFOCVars = &FOCVars[bMotor];
  pFOCVars->controlMode_prev = MCM_OBSERVING_MODE;
  pSTC[bMotor]->SpeedControlEnabled = false;
  pCurrCtrlhandle[bMotor]->currentControlEnabled = true;
  pCurrCtrlhandle[bMotor]->forceZeroPwm = false;
  pSPD[bMotor]->closedLoopAngleEnabled = true;
  pSPD[bMotor]->flagHsoAngleEqualsOpenLoopAngle = false;
  RsDCEst_M1.RSDCestimate_state = RSDC_EST_RESET;

  PIDREG_SPEED_setUi_pu(&pSTC[bMotor]->PIDSpeed, FIXP30(0.0f));
  PIDREGDQX_CURRENT_setUiD_pu(&pCurrCtrlhandle[bMotor]->pid_IdIqX_obj, FIXP30(0.0f));
  PIDREGDQX_CURRENT_setUiQ_pu(&pCurrCtrlhandle[bMotor]->pid_IdIqX_obj, FIXP30(0.0f));
  PWMC_SwitchOffPWM(pwmcHandle[bMotor]);

  POLPULSE_stopPulse(&MC_PolPulse_M1.PolpulseObj);

  /* USER CODE BEGIN FOC_Clear 1 */

  /* USER CODE END FOC_Clear 1 */
}

/**
  * @brief  Initializes @p hTickCount counter for charge boot capacitors of motor 1
  */
__weak void TSK_SetChargeBootCapDelayM1(uint16_t hTickCount)
{
  hBootCapDelayCounterM1 = hTickCount;
}

/**
 * @brief  Monitors the motor1 counter for charge boot
 *         capacitors.
 * @retval bool true if time has elapsed, false otherwise
 */
__weak bool TSK_ChargeBootCapDelayHasElapsedM1(void)
{
  bool retVal = false;
  if (hBootCapDelayCounterM1 == 0)
  {
    retVal = true;
  }
  return (retVal);
}

/**
 * @brief  Initalizes the @p hTickCount counter used for counting the permanency
 *         time in STOP state for motor 1
 */
__weak void TSK_SetStopPermanencyTimeM1(uint16_t hTickCount)
{
  hStopPermanencyCounterM1 = hTickCount;
}

/**
 * @brief  Monitors the counter of the permanency time in STOP state
 *         for motor 1
 * @retval bool true if time is elapsed, false otherwise
 */
__weak bool TSK_StopPermanencyTimeHasElapsedM1(void)
{
  bool retVal = false;
  if (hStopPermanencyCounterM1 == 0)
  {
    retVal = true;
  }
  return (retVal);
}

#if defined (CCMRAM)
#if defined (__ICCARM__)
#pragma location = ".ccmram"
#elif defined (__CC_ARM) || defined(__GNUC__)
__attribute__((section (".ccmram")))
#endif
#endif
/**
 * @brief  Executes High frequency task.
 *
 *  This is mainly the FOC current control loop.
 *
 * @retval Motor ID.
 */
__weak uint8_t TSK_HighFrequencyTask(void)
{
  uint16_t hFOCreturn;

  /* USER CODE BEGIN HighFrequencyTask 0 */

  /* USER CODE END HighFrequencyTask 0 */

  /* Simplified controller */
  if (bMCBootCompleted != 0)
  {
    hFOCreturn = FOC_CurrControllerM1();

    if(hFOCreturn == MC_DURATION)
    {
      MCI_FaultProcessing(&Mci[M1], MC_DURATION, 0);
    }

    TSK_SafetyTask();

  }

  /* USER CODE BEGIN HighFrequencyTask 1 */

  /* USER CODE END HighFrequencyTask 1 */

  return M1;
}

#if defined (CCMRAM)
#if defined (__ICCARM__)
#pragma location = ".ccmram"
#elif defined (__CC_ARM) || defined(__GNUC__)
__attribute__((section (".ccmram")))
#endif
#endif
/**
 * @brief Executes the core of FOC  that is the controllers for Iqd
 *        currents regulation. Reference frame transformations are carried out
 *        accordingly to the active speed sensor.
 * @retval uint16_t: It returns MC_NO_FAULTS if the FOC has been ended before
 *         next PWM Update event, MC_DURATION otherwise
 */
inline uint16_t FOC_CurrControllerM1(void)
{

  uint16_t hCodeError = 0;
  Measurement_Output_t measuredVal;
  Sensorless_Input_t SensorlessInp;
  Sensorless_Output_t SensorlessOut;
  CurrCtrl_Input_t   CurrCtrlInp;
  STC_input_t STCInp;
  Duty_Dab_t dutyAB;
  fixp30_t angle_park_pu;
  bool pwmControlEnable;

  /* schedule regular conversion result */
  RCM_ExecNextConv();

  pwmControlEnable = R3_IsInPwmControl(pwmcHandle[M1]);

  R3_GetVbusMeasurements(pwmcHandle[M1], &VBus_M1.Udcbus_in_pu);
  /* High level motor control */
  SPD_GetSpeed(&SPD_M1,
               &STCInp.emfSpeed,
               &STCInp.SpeedLP);
  STCInp.pwmControlEnable = pwmControlEnable;
  STCInp.Udcbus_in_pu = VBus_M1.Udcbus_in_pu;
  STCInp.closedLoopEnabled = SPD_M1.closedLoopAngleEnabled;
  STC_Run(&STC_M1, &STCInp);

  if (false == RsDCEst_M1.flag_enableRSDCestimate || RSDC_EST_ONGOING != RsDCEst_M1.RSDCestimate_state)
  {
    /* always update open loop angle unless RSDC estimation is on going */
    SPD_UpdtOpenLoopAngle(&SPD_M1, STC_M1.speed_ref_ramped_pu);
  }

  /* - Retrieve measurements from hardware,
     - perform Clarke transformation
     - outputs currents and voltages expressed in alphaBeta coordinates */
  PWMC_GetMeasurements(&PWM_Handle_M1._Super, &measuredVal);

  MC_POLPULSE_run(&MC_PolPulse_M1, 0); // angle optional and currently not used in PolPulse

  /* filters  alpha/beta currents and voltages: used by Rs DC estimator
     can be removed if Rs DC estimation is not used */
  RsDC_estimationFiltering(&RsDCEst_M1, &measuredVal.Iab_pu, &measuredVal.Uab_pu);

   /* Sensorless position/speed estimator */

  SensorlessInp.Iab_pu = measuredVal.Iab_pu;
  SensorlessInp.Uab_pu = measuredVal.Uab_pu;
  SPD_Run(&SPD_M1, &SensorlessInp, &SensorlessOut);

  /* FOC current control
     Prepare park and pwm angles for current control
     get angle from sensorless Use closed loop estimated angle or open loop angle */
  SPD_GetAngle(&SPD_M1, &angle_park_pu);

  /* FOC current control */

  /* get sensorless speed */
  SPD_GetSpeed(&SPD_M1,
               &STCInp.emfSpeed,       /* dummy */
               &CurrCtrlInp.SpeedLP);

  /* Calculate current references */
  CurrCtrlInp.Idq_ref_currentcontrol_pu = STC_M1.Idq_ref_limited_pu;

  if(RSTEMP_getFlagEnable(SPD_M1.pRsTemp))
  {
    CurrCtrlInp.Idq_ref_currentcontrol_pu.D += RSTEMP_getIdqLFref(SPD_M1.pRsTemp).D;
    CurrCtrlInp.Idq_ref_currentcontrol_pu.Q += RSTEMP_getIdqLFref(SPD_M1.pRsTemp).Q;
  }

  CurrCtrlInp.Udcbus_in_pu = VBus_M1.Udcbus_in_pu;
  CurrCtrlInp.Iab_in_pu = measuredVal.Iab_pu;
  CurrCtrlInp.Uab_emf_pu = SensorlessOut.Uab_emf_pu;
  CurrCtrlInp.angle = angle_park_pu;
  CurrCtrlInp.pwmControlEnable = pwmControlEnable;
  MC_currentController(&CurrCtrl_M1,
                       &CurrCtrlInp,
                       &dutyAB);

  if(RSTEMP_getFlagEnable(SPD_M1.pRsTemp))
  {
    dutyAB.A += RSTEMP_getLFabDuty(SPD_M1.pRsTemp).A;
    dutyAB.B += RSTEMP_getLFabDuty(SPD_M1.pRsTemp).B;
  }

  /* generates PWM duty cycles from alpha beta duty values  */
  hCodeError = PWMC_SetPhaseDuty(&PWM_Handle_M1._Super, &dutyAB);

  /* read regular conversion result */
  RCM_ReadOngoingConv();

  /* Data logging */
  GLOBAL_TIMESTAMP++;
  if (MCPA_UART_A.Mark != 0)
  {
    MCPA_dataLog (&MCPA_UART_A);
  }

  return hCodeError;
}

/**
 * @brief  Executes safety checks (e.g. bus voltage and temperature) for all drive instances.
 *
 * Faults flags are updated here.
 */
__weak void TSK_SafetyTask(void)
{
  /* USER CODE BEGIN TSK_SafetyTask 0 */

  /* USER CODE END TSK_SafetyTask 0 */
  if (bMCBootCompleted == 1)
  {
    TSK_SafetyTask_PWMOFF(M1);
    /* User conversion execution */
    /* USER CODE BEGIN TSK_SafetyTask 1 */

    /* USER CODE END TSK_SafetyTask 1 */
  }
}

/**
 * @brief  Safety task implementation, of the motor identified by @p bMotor, if  MC.M1_ON_OVER_VOLTAGE == TURN_OFF_PWM
 *
 */
__weak void TSK_SafetyTask_PWMOFF(uint8_t bMotor)
{
  /* USER CODE BEGIN TSK_SafetyTask_PWMOFF 0 */

  /* USER CODE END TSK_SafetyTask_PWMOFF 0 */

  uint16_t CodeReturn = MC_NO_FAULTS;

  CodeReturn |= PWMC_IsFaultOccurred(pwmcHandle[bMotor]);                    /* check for fault. It return MC_OVER_CURR or MC_NO_FAULTS
                                                                                 (for STM32F30x can return MC_OVER_VOLT in case of HW Overvoltage) */

  fixp30_t rawValueTempM1;
  OVERSAMPLING_getTempMeasurements(&rawValueTempM1);
  CodeReturn |= NTC_CalcAvTemp(&TempSensor_M1, (uint16_t)rawValueTempM1);

  CodeReturn |= ErrorDetection(bMotor);

  MCI_FaultProcessing(&Mci[bMotor], CodeReturn, ~CodeReturn); /* Update the STM according error code */

  if (MCI_GetFaultState(&Mci[bMotor]) != (uint32_t)MC_NO_FAULTS) /* Acts on PWM outputs in case of faults */
  {
      PWMC_SwitchOffPWM(pwmcHandle[bMotor]);
      FOC_Clear(bMotor);
      /* USER CODE BEGIN TSK_SafetyTask_PWMOFF 1 */

      /* USER CODE END TSK_SafetyTask_PWMOFF 1 */
  }
  /* USER CODE BEGIN TSK_SafetyTask_PWMOFF 3 */

  /* USER CODE END TSK_SafetyTask_PWMOFF 3 */
}

/**
 * @brief  Puts the Motor Control subsystem in safety conditions on a Hard Fault
 *
 *  This function is to be executed when a general hardware failure has been detected
 * by the microcontroller and is used to put the system in safety condition.
 */
__weak void TSK_HardwareFaultTask(void)
{
  /* USER CODE BEGIN TSK_HardwareFaultTask 0 */

  /* USER CODE END TSK_HardwareFaultTask 0 */

  R3_SwitchOffPWM(pwmcHandle[M1]);
  MCI_FaultProcessing(&Mci[M1], MC_SW_ERROR, 0);
  /* USER CODE BEGIN TSK_HardwareFaultTask 1 */

  /* USER CODE END TSK_HardwareFaultTask 1 */
}

/**
 * @brief  Executes startup procedudure using combine or single PolPules, RsDC measure
 */
void TSK_PulsesRsDC(StartMode_t Mode)
{
  FOCVars_t* pFOCVars = &FOCVars[M1];

  switch (Mode)
  {
    case FAST_RSDC:
      if (POLPULSE_getState(&MC_PolPulse_M1.PolpulseObj) == POLPULSE_STATE_Idle)
      {
        __disable_irq();  // << Start of critical zone: write global variables read by HighFrequencyTask
        /* Onpen loop, with current controllers disabled during pulses sequence */
        STC_M1.SpeedControlEnabled = false;
        CurrCtrl_M1.currentControlEnabled = false;
        SPD_M1.closedLoopAngleEnabled = false;
        SPD_M1.flagHsoAngleEqualsOpenLoopAngle = false;
        CurrCtrl_M1.forceZeroPwm = false;
        POLPULSE_trigger(&MC_PolPulse_M1.PolpulseObj);
        // Enable PWM in these modes
        PWMC_SwitchOnPWM(pwmcHandle[M1]);
        HSO_setFlag_EnableOffsetUpdate(&HSO_M1, true);

        __enable_irq();  // >> End of critical zone
      }
      else if ( (POLPULSE_getState(&MC_PolPulse_M1.PolpulseObj) == POLPULSE_STATE_PostcalcsComplete) &&
                (RsDCEst_M1.RSDCestimate_state != RSDC_EST_COMPLETED) )
      {
        if (RSDC_EST_RESET == RsDCEst_M1.RSDCestimate_state)
        {
          RsDC_estimationInit(pFOCVars, &RsDCEst_M1);
        }
        else if (RSDC_EST_ONGOING == RsDCEst_M1.RSDCestimate_state)
        {
          RsDC_estimationRun(pFOCVars, &RsDCEst_M1);
        }
        else
        {
          /* nothing to do, Rs DC estimation complete */
        }
      }
      break;
    case RSDC_ONLY:
        if (RSDC_EST_RESET == RsDCEst_M1.RSDCestimate_state)
        {
          RsDC_estimationInit(pFOCVars, &RsDCEst_M1);
        }
        else if (RSDC_EST_ONGOING == RsDCEst_M1.RSDCestimate_state)
        {
          RsDC_estimationRun(pFOCVars, &RsDCEst_M1);
        }
        else
        {
          /* nothing to do, Rs DC estimation complete */
        }
      break;
    case POL_PULSE_ONLY:
      if (POLPULSE_getState(&MC_PolPulse_M1.PolpulseObj) == POLPULSE_STATE_Idle)
      {
        __disable_irq();  // << Start of critical zone: write global variables read by HighFrequencyTask
        /* Onpen loop, with current controllers disabled during pulses sequence */
        STC_M1.SpeedControlEnabled = false;
        CurrCtrl_M1.currentControlEnabled = false;
        SPD_M1.closedLoopAngleEnabled = false;
        SPD_M1.flagHsoAngleEqualsOpenLoopAngle = false;
        CurrCtrl_M1.forceZeroPwm = false;
        POLPULSE_trigger(&MC_PolPulse_M1.PolpulseObj);
        // Enable PWM in these modes
        PWMC_SwitchOnPWM(pwmcHandle[M1]);
        HSO_setFlag_EnableOffsetUpdate(&HSO_M1, true);

        __enable_irq();  // >> End of critical zone
      }
      break;
      default:
        break;
  }
}

/**
 * @brief  Initializes miscellaneous montrol control core variables.
 *
 * Initializes mainly FOCVars data structure.
 */
void MC_init(void)
{

  FOCVars_t* pFOCVars = &FOCVars[M1];

  /* Initialize Calibration status */
  pFOCVars->PolarizationState = NOT_DONE;
  pFOCVars->flagStartWithCalibration = false;

  /* Pre-calculated values */
  fixp24_t ls_henry_fixp24 = FIXP24(motorParams->ls);
  pFOCVars->Ls_Rated_H = ls_henry_fixp24;
  pFOCVars->Ls_Active_H = ls_henry_fixp24;

  float_t fs_l = (scaleParams->voltage / scaleParams->current / (float_t)VOLTAGE_FILTER_POLE_RPS);
  float_t ls_pu_flt = motorParams->ls / fs_l;
  FIXPSCALED_floatToFIXPscaled(ls_pu_flt, &pFOCVars->Ls_Rated_pu_fps);
  FIXPSCALED_floatToFIXPscaled(ls_pu_flt, &pFOCVars->Ls_Active_pu_fps);

  pFOCVars->Rs_Rated_Ohm = FIXP20(motorParams->rs);

  pFOCVars->Flux_Rated_VpHz = FIXP24(motorParams->ratedFlux);

  /* References */
  pCurrCtrlhandle[M1]->Ddq_ref_pu.D = 0;
  pCurrCtrlhandle[M1]->Ddq_ref_pu.Q = 0;

  pFOCVars->controlMode = DEFAULT_CONTROL_MODE;
  pFOCVars->controlMode_prev = MCM_OBSERVING_MODE;
  pFOCVars->polePairs = motorParams->polePairs;
  pFOCVars->Kt = (1.0f / (1.5f * (double)motorParams->polePairs * ((double)motorParams->ratedFlux / M_TWOPI))); /* Pre-computation of cst Kt in Ampere/Nm */

} /* end of MC_init() */

/**
 * @brief  Monitors fault error for motor identified by @p bMotor.
 *
 * This routines needs to be called at least in the safety task
 *
 * @retval uint16_t error status
 *
 */
uint16_t ErrorDetection(uint8_t bMotor)
{
  uint16_t errorCode = 0;
  /* Software overcurrent limit tripped */
  if (RUN == pMCI[bMotor]->State)
  {
    fixp30_t overcurrent_trip_pu = pwmcHandle[bMotor]->softOvercurrentTripLevel_pu;
    if (	FIXP_abs(pwmcHandle[bMotor]->Irst_in_pu.R) > overcurrent_trip_pu ||
        FIXP_abs(pwmcHandle[bMotor]->Irst_in_pu.S) > overcurrent_trip_pu ||
        FIXP_abs(pwmcHandle[bMotor]->Irst_in_pu.T) > overcurrent_trip_pu)
    {
      errorCode |= MC_OVERCURR_SW;
    }
  }

  /* Software over/under voltage limit tripped */
  if (Mci[bMotor].pVBus->Udcbus_in_pu > Mci[bMotor].pVBus->Udcbus_overvoltage_limit_pu)
  {
    errorCode |= MC_OVER_VOLT;
  }
  else if (Mci[bMotor].pVBus->Udcbus_in_pu < Mci[bMotor].pVBus->Udcbus_undervoltage_limit_pu)
  {
    errorCode |= MC_UNDER_VOLT;
  }

  /* Current sampling fault, related to current reconstruction */
  if (pwmcHandle[bMotor]->currentsamplingfault)
  {
    errorCode |= MC_SAMPLEFAULT;
  }

  return errorCode;
}

/**
 * @brief  Selects the reference for speed or torque for the motor identified by @p bMotor.
 *
 * The reference can be define by APIs or throttle/potentiometer position
 *
 */
void FOC_ReferenceSelection(uint8_t bMotor)
{
  /* Q-current reference source *********************************************************************/

  /* Speed source selection */
    /* Reference from user via MCI */
    pSTC[bMotor]->speed_ref_active_pu = pSTC[bMotor]->speed_ref_pu;

  /* Speed reference source *************************************************************************/

  /* For torque/current mode, select the reference based on speedref_source */
    /* Reference from user via MCI */
    pSTC[bMotor]->Iq_ref_active_pu = pSTC[bMotor]->Idq_ref_pu.Q;
}

__weak void UI_HandleStartStopButton_cb (void)
{
/* USER CODE BEGIN START_STOP_BTN */
  if (IDLE == MC_GetSTMStateMotor1())
  {
    /* Ramp parameters should be tuned for the actual motor */
    (void)MC_StartWithPolarizationMotor1();
  }
  else
  {
    (void)MC_StopMotor1();
  }
/* USER CODE END START_STOP_BTN */
}

 /**
  * @brief  Locks GPIO pins used for Motor Control to prevent accidental reconfiguration
  */
__weak void mc_lock_pins (void)
{
LL_GPIO_LockPin(M1_CURR_AMP_U_GPIO_Port, M1_CURR_AMP_U_Pin);
LL_GPIO_LockPin(M1_SHUNT_M_U_GPIO_Port, M1_SHUNT_M_U_Pin);
LL_GPIO_LockPin(M1_SHUNT_M_V_GPIO_Port, M1_SHUNT_M_V_Pin);
LL_GPIO_LockPin(M1_CURR_AMP_V_GPIO_Port, M1_CURR_AMP_V_Pin);
LL_GPIO_LockPin(M1_CURR_AMP_W_GPIO_Port, M1_CURR_AMP_W_Pin);
LL_GPIO_LockPin(M1_SHUNT_M_W_GPIO_Port, M1_SHUNT_M_W_Pin);
LL_GPIO_LockPin(M1_PWM_UL_GPIO_Port, M1_PWM_UL_Pin);
LL_GPIO_LockPin(M1_PWM_VL_GPIO_Port, M1_PWM_VL_Pin);
LL_GPIO_LockPin(M1_PWM_WL_GPIO_Port, M1_PWM_WL_Pin);
LL_GPIO_LockPin(M1_PWM_UH_GPIO_Port, M1_PWM_UH_Pin);
LL_GPIO_LockPin(M1_PWM_VH_GPIO_Port, M1_PWM_VH_Pin);
LL_GPIO_LockPin(M1_DP_GPIO_Port, M1_DP_Pin);
LL_GPIO_LockPin(M1_PWM_WH_GPIO_Port, M1_PWM_WH_Pin);
LL_GPIO_LockPin(M1_TEMPERATURE_GPIO_Port, M1_TEMPERATURE_Pin);
LL_GPIO_LockPin(M1_CURR_V_GPIO_Port, M1_CURR_V_Pin);
LL_GPIO_LockPin(M1_VOLT_V_GPIO_Port, M1_VOLT_V_Pin);
LL_GPIO_LockPin(M1_CURR_W_GPIO_Port, M1_CURR_W_Pin);
LL_GPIO_LockPin(M1_VOLT_W_GPIO_Port, M1_VOLT_W_Pin);
LL_GPIO_LockPin(M1_CURR_U_GPIO_Port, M1_CURR_U_Pin);
LL_GPIO_LockPin(M1_VOLT_U_GPIO_Port, M1_VOLT_U_Pin);
LL_GPIO_LockPin(M1_BUS_VOLTAGE_GPIO_Port, M1_BUS_VOLTAGE_Pin);
LL_GPIO_LockPin(M1_EN_DRIVER_GPIO_Port, M1_EN_DRIVER_Pin);
LL_GPIO_LockPin(M1_CURR_SHUNT_U_AND_SHUNT_P_U_GPIO_Port, M1_CURR_SHUNT_U_AND_SHUNT_P_U_Pin);
LL_GPIO_LockPin(M1_CURR_SHUNT_V_AND_SHUNT_P_V_GPIO_Port, M1_CURR_SHUNT_V_AND_SHUNT_P_V_Pin);
LL_GPIO_LockPin(M1_CURR_SHUNT_W_AND_SHUNT_P_W_GPIO_Port, M1_CURR_SHUNT_W_AND_SHUNT_P_W_Pin);
}

/* USER CODE BEGIN mc_task 0 */

/* USER CODE END mc_task 0 */

/******************* (C) COPYRIGHT 2026 STMicroelectronics *****END OF FILE****/
