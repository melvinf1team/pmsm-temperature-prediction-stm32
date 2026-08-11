
/**
  ******************************************************************************
  * @file    mc_config.c
  * @author  Motor Control SDK Team, ST Microelectronics
  * @brief   Motor Control Subsystem components configuration and handler structures.
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
  */
#include "main.h"
#include "mc_type.h"
#include "mc_parameters.h"
#include "mc_config.h"
#include "parameters_conversion.h"
#include "oversampling.h"

/* USER CODE BEGIN Additional include */

/* USER CODE END Additional include */

#define OFFCALIBRWAIT_MS     100

/* USER CODE BEGIN Additional define */

/* USER CODE END Additional define */

PWMC_R3_Handle_t PWM_Handle_M1 =
{
  {
    .pFctGetMeasurements               = &R3_GetMeasurements,
    .pFctSwitchOffPwm                  = &R3_SwitchOffPWM,
    .pFctSwitchOnPwm                   = &R3_SwitchOnPWM,
    .pFctTurnOnLowSides                = &R3_TurnOnLowSides,
    .pFctOCPSetReferenceVoltage        = MC_NULL,
    .pFctSetADCSampPointSectX          = &R3_SetADCSampPointSectX,
    .pFctGetAuxAdcMeasurement          = &R3_GetAuxAdcMeasurement,
    .pFctGetCurrentReconstructionData  = &R3_GetCurrentReconstructionData,
    .pFctGetOffsets                    = &R3_GetOffsets,
    .pFctSetOffsets                    = &R3_SetOffsets,
    .pFctPreparePWM                    = &R3_PreparePWM,
    .pFctStartOffsetCalibration        = &R3_StartOffsetCalibration,
    .pFctFinishOffsetCalibration       = &R3_FinishOffsetCalibration,
	.pFctPwmShort                      = &R3_PwmShort,
	.pFctIsInPwmControl                = &R3_IsInPwmControl,
	.pFctIsPwmEnabled                  = &R3_IsPwmEnabled,

    .LowSideOutputs    = (LowSideOutputsFunction_t)LOW_SIDE_SIGNALS_ENABLING,
    .pwm_en_u_port     = MC_NULL,
    .pwm_en_u_pin      = (uint16_t)0,
    .pwm_en_v_port     = MC_NULL,
    .pwm_en_v_pin      = (uint16_t)0,
    .pwm_en_w_port     = MC_NULL,
    .pwm_en_w_pin      = (uint16_t)0,
    .CntPhA = PWM_PERIOD_CYCLES >> 2,
    .CntPhB = PWM_PERIOD_CYCLES >> 2,
    .CntPhC = PWM_PERIOD_CYCLES >> 2,
    .SWerror = 0,
    .TurnOnLowSidesAction = false,
    .OffCalibrWaitTimeCounter = 0,
    .Motor = M1,
    .QuarterPeriodCnt   = PWM_PERIOD_CYCLES >> 2,
    .OffCalibrWaitTicks = (uint16_t)((SPEED_LOOP_FREQUENCY_HZ * OFFCALIBRWAIT_MS)/ 1000),

    .flagEnableCurrentReconstruction = true,
    .softOvercurrentTripLevel_pu = FIXP30(BOARD_SOFT_OVERCURRENT_TRIP / CURRENT_SCALE),
    .polpulseHandle = &MC_PolPulse_M1.PolpulseObj,
    .modulationMode = FOC_MODULATIONMODE_Centered,

  },

  .wPhaseOffsetFilterConst = FIXP31(200.0f / PWM_FREQUENCY),  /* Set filter cutoff frequency to 200 rad/s */

  .maxTicksCurrentReconstruction = (uint16_t)(MAX_TICKS_CURRENT_RECONSTRUCTION_THRESHOLD),

  .Half_PWMPeriod = PWM_PERIOD_CYCLES/2u,
  .OverCurrentFlag = false,
  .OverVoltageFlag = false,
  .BrakeActionLock = false,
  .pParams_str = &R3_ParamsM1
};

Oversampling_t oversampling =
{
  .count = OVS_COUNT,
  .divider = REGULATION_EXECUTION_RATE,
  .singleIdx = BOARD_SHUNT_SAMPLE_SELECT,
  .readBuffer = 1,
  .writeBuffer = 0,
  .Channel =
  {
    /* I_R */
    {
      .adc = OVS_ADC_I_R,
      .rank = OVS_RANK_I_R,
      .tasklen = OVS_ADC3_TASKLENGTH,
      .single = true,
    },
    /* I_S */
    {
      .adc = OVS_ADC_I_S,
      .rank = OVS_RANK_I_S,
      .tasklen = OVS_ADC4_TASKLENGTH,
      .single = true,
    },
    /* I_T */
    {
      .adc = OVS_ADC_I_T,
      .rank = OVS_RANK_I_T,
      .tasklen = OVS_ADC5_TASKLENGTH,
      .single = true,
    },
    /* U_R */
    {
       .adc = OVS_ADC_U_R,
       .rank = OVS_RANK_U_R,
       .tasklen = OVS_ADC3_TASKLENGTH,
    },
    /* U_S */
    {
      .adc = OVS_ADC_U_S,
      .rank = OVS_RANK_U_S,
      .tasklen = OVS_ADC4_TASKLENGTH,
    },
    /* U_T */
    {
        .adc = OVS_ADC_U_T,
        .rank = OVS_RANK_U_T,
        .tasklen = OVS_ADC5_TASKLENGTH,
    },
    /* U_DC */
    {
        .adc = OVS_ADC_U_DC,
        .rank = OVS_RANK_U_DC,
        .tasklen = OVS_ADC3_TASKLENGTH,
    },
    /* Temperature*/
    {
      .adc = OVS_ADC_TEMP,
      .rank = OVS_RANK_TEMP,
      .tasklen = OVS_ADC4_TASKLENGTH,
    },
  }
};

NTC_Handle_t TempSensor_M1 =
{
  .bSensorType             = REAL_SENSOR,
  .hOverTempThreshold      = (uint16_t)(OV_TEMPERATURE_THRESHOLD_d),
  .hOverTempDeactThreshold = (uint16_t)(OV_TEMPERATURE_THRESHOLD_d - OV_TEMPERATURE_HYSTERESIS_d),
  .hSensitivity            = (int16_t)(ADC_REFERENCE_VOLTAGE/dV_dT),
  .wV0                     = (uint16_t)((V0_V * 65536) / ADC_REFERENCE_VOLTAGE),
  .hT0                     = T0_C,
};

RsDCEstimation_Handle_t RsDCEst_M1 =
{
  .pSTC = &STC_M1,
  .pCurrCtrl = &CurrCtrl_M1,
  .pSPD = &SPD_M1,
  .pPWM = &PWM_Handle_M1._Super,

  .flag_enableRSDCestimate = false,
  .RSDCestimate_AlignOnly = false,
  .RSDCestimate_FilterShift = 10,
  .RSDCestimate_counterInitialValue = RSDC_TOTAL_MEASURE_TIME_TICKS,
  .RSDCestimate_counterMeasureValue = RSDC_SLOP_TICKS,
  .RSDCestimate_Id_ref_pu = FIXP30(NOMINAL_CURRENT_A * 0.5f / CURRENT_SCALE),
  .RSDCestimate_Fast = false,
  .FastRsDC_measure_duration = (uint32_t) (FAST_RSDC_TOTAL_MEASURE_TIME_MS * SPEED_LOOP_FREQUENCY_HZ /1000),
  .FastRsDC_ramp_duration = (uint32_t) (FAST_RSDC_SLOP_MS * SPEED_LOOP_FREQUENCY_HZ /1000),
};

IMPEDCORR_Obj ImpedCorr_M1;
HSO_Obj       HSO_M1;
RSTEMP_Obj    RsTemp_M1;

SPD_Handle_t SPD_M1 =
{
  .flagDynamicZestConfig = false,
  .closedLoopAngleEnabled = true,
  .pImpedCorr = &ImpedCorr_M1,
  .pHSO = &HSO_M1,
  .pRsTemp_params = &RsTemp_params_M1,
};

STC_Handle_t STC_M1 =
{
  .pCurrCtrl = &CurrCtrl_M1,
  .accel_factor				      = FIXP30(1.0f),
  .regen_factor				      = FIXP30(1.0f),
  .active_limits.u16			  = 0,
  .speedref_source          = FOC_SPEED_SOURCE_Default,
  .SpeedControlEnabled = false,
  .Enable_InjectBoost = false,
};

IMPEDCORR_Params ImpedCorr_params_M1=
{
  .voltageFilterPole_rps = VOLTAGE_FILTER_POLE_RPS,
  .cycleFreq_Hz = (PWM_FREQUENCY / REGULATION_EXECUTION_RATE),
};

HSO_Params Hso_params_M1 =
{
  .isrTime_s = 1.0 / TF_REGULATION_RATE,
  .speedPole_rps = SPEED_POLE_RPS,
  .CrossOver_Hz = 20.0f,            /* depends on motor used */
  .flag_FilterAdaptive = true,
  .Filter_adapt_fmin_Hz = 10.0f, /* lowest FO cross-over Hz */
  .Filter_adapt_fmax_Hz = 500.0f, /* highest FO cross-over Hz */
  .CheckDirBW_Hz = 0.5f,
};

RSTEMP_Params RsTemp_params_M1 =
{
	.FullScaleCurrent_A = CURRENT_SCALE,
	.FullScaleVoltage_V = VOLTAGE_SCALE,
	.FullScaleFreq_Hz = FREQUENCY_SCALE,
	.RatedCelcius = 20.0f,
	.RsRatedOhm = RS,
	.LsHenry = LS,
	.MotorMaxCurrent_A = NOMINAL_CURRENT_A,
	.F_background_Hz = (float_t) SPEED_LOOP_FREQUENCY_HZ, /* Background */
	.F_interrupt_Hz = (float_t) (TF_REGULATION_RATE),	/* Interrupt */
};

POLPULSE_Params PolPulse_params_M1 =
{
  .Ts                   = 1.0f / TF_REGULATION_RATE,
};

MC_PolPulse_Handle_t MC_PolPulse_M1 =
{
  .pPWM = &PWM_Handle_M1._Super,
  .pSPD = &SPD_M1,
  .pSTC = &STC_M1,
  .TIMx = TIM8,              /*!< timer used for PWM generation.*/
  .pulse_countdown = -1,
  .flagPolePulseActivation = true,
};

CurrCtrl_Handle_t CurrCtrl_M1 =
{
  .busVoltageCompEnable  = true,
  .busVoltageComp        = FIXP24(1.0f),
  .busVoltageCompMax     = FIXP24(20.0f),
  .busVoltageCompMin     = FIXP24(1.0f),
  .currentControlEnabled = true,
  .forceZeroPwm = false,
  .pid_IdIqX_obj =
  {
    .KpAlign = MOTOR_CURRENT_KP_ALIGN,
  }

};

PROFILER_Params profilerParams_M1 =
{
  .isrFreq_Hz = (PWM_FREQUENCY / REGULATION_EXECUTION_RATE),
  .background_rate_hz = SPEED_LOOP_FREQUENCY_HZ,
  .fullScaleFreq_Hz = FREQUENCY_SCALE,
  .fullScaleCurrent_A = CURRENT_SCALE,
  .fullScaleVoltage_V = VOLTAGE_SCALE,
  .voltagefilterpole_rps = VOLTAGE_FILTER_POLE_RPS,
  .PolePairs = POLE_PAIR_NUM,
  .dcac_PowerDC_goal_W = 3.0f,
  .dcac_duty_ramping_time_s = 2.0f,
  .dcac_current_ramping_time_s = 0.5f,
  .dcac_dc_measurement_time_s = 2.0f,
  .dcac_ac_measurement_time_s = 5.0f,
  .dcac_duty_maxgoal = 0.2f,
  .dcac_ac_freq_Hz = 80.0f,
  .dcac_DCmax_current_A = M1_MAX_READABLE_CURRENT*0.8f,	/* max current, power_goal should come first */
  .glue_dcac_fluxestim_on = true,
  .fluxestim_measurement_time_s = 1.5f,
  .fluxestim_Idref_A = 0.0f,
  .fluxestim_current_ramping_time_s = 0.2f,
  .fluxestim_speed_Hz = 50.0f,
  .fluxestim_speed_ramping_time_s = 2.0f,
};

PROFILER_Obj	profiler_M1;

ProfilerMotor_Handle_t profilerMotor_M1 =
{
  .pSPD = &SPD_M1,
  .pSTC = &STC_M1,
  .pPolpulse = &MC_PolPulse_M1.PolpulseObj,
  .pCurrCtrl = &CurrCtrl_M1,
  .pPWM = &PWM_Handle_M1._Super,
  .pVBus = &VBus_M1,
};

BusVoltageSensor_Handle_t VBus_M1 =
{
  .Udcbus_convFactor = FIXP30(ADC_BUSVOLTAGE_SCALE/VOLTAGE_SCALE),
  .Udcbus_overvoltage_limit_pu = FIXP30(BOARD_LIMIT_OVERVOLTAGE / VOLTAGE_SCALE),
  .Udcbus_undervoltage_limit_pu = FIXP30(BOARD_LIMIT_UNDERVOLTAGE / VOLTAGE_SCALE),
};

MCI_Handle_t Mci[NBR_OF_MOTORS];

/* USER CODE BEGIN Additional configuration */

/* USER CODE END Additional configuration */

/******************* (C) COPYRIGHT 2026 STMicroelectronics *****END OF FILE****/

