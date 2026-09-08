
/**
  ******************************************************************************
  * @file    mc_parameters.c
  * @author  Motor Control SDK Team, ST Microelectronics
  * @brief   This file provides definitions of HW parameters specific to the
  *          configuration of the subsystem.
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
  */

/* Includes ------------------------------------------------------------------*/
//cstat -MISRAC2012-Rule-21.1
#include "main.h" //cstat !MISRAC2012-Rule-21.1
//cstat +MISRAC2012-Rule-21.1
#include "parameters_conversion.h"
#include "r3_g4xx_pwm_curr_fdbk.h"

/* USER CODE BEGIN Additional include */

/* USER CODE END Additional include */

#define FREQ_RATIO 1                /* Dummy value for single drive */
#define FREQ_RELATION HIGHEST_FREQ  /* Dummy value for single drive */

/**
  * @brief  Internal OPAMP parameters Motor 1 - three shunt - G4xx
  */
R3_3_OPAMPParams_t R3_3_OPAMPParamsM1 =
{
   .OPAMPx_1 = OPAMP3,
   .OPAMPx_2 = OPAMP4,
   .OPAMPx_3 = OPAMP5,
};
/**
  * @brief  Current sensor parameters Motor 1 - three shunt - G4 HSO
  */
const R3_Params_t R3_ParamsM1 =
{

/* Current reading A/D Conversions initialization -----------------------------*/
  .ADCx_1           = ADC3,
  .ADCx_2           = ADC4,
  .ADCx_3           = ADC5,
  /* PWM generation parameters --------------------------------------------------*/
  .RepetitionCounter = REP_COUNTER,
  .TIMx              = TIM8,
  .TIMx_Oversample   = TIM3,
/* Internal OPAMP common settings --------------------------------------------*/
  .OPAMPParams     = &R3_3_OPAMPParamsM1,
/* Internal COMP settings ----------------------------------------------------*/
  .CompOCPASelection     = COMP4,
  .CompOCPAInvInput_MODE = INT_MODE,
  .CompOCPBSelection     = COMP6,
  .CompOCPBInvInput_MODE = INT_MODE,
  .CompOCPCSelection     = COMP7,
  .CompOCPCInvInput_MODE = INT_MODE,
  .DAC_OCP_ASelection    = DAC3,
  .DAC_OCP_BSelection    = DAC4,
  .DAC_OCP_CSelection    = DAC4,
  .DAC_Channel_OCPA      = LL_DAC_CHANNEL_2,
  .DAC_Channel_OCPB      = LL_DAC_CHANNEL_2,
  .DAC_Channel_OCPC      = LL_DAC_CHANNEL_1,
  .CompOVPSelection      = MC_NULL,
  .CompOVPInvInput_MODE  = NONE,
  .DAC_OVP_Selection     = MC_NULL,
  .DAC_Channel_OVP       = (uint32_t)0,
/* DAC settings --------------------------------------------------------------*/
  .DAC_OCP_Threshold     = 4369,
  .DAC_OVP_Threshold     = 23830,
          .DMA_ADCx_1 = DMA2_Channel1,
  .DMA_ADCx_2 = DMA2_Channel2,
  .DMA_ADCx_3 = DMA2_Channel3,
};

const FLASH_Params_t  flashParams =
{
  .motor = {
    .polePairs = POLE_PAIR_NUM,
    .ratedFlux = MOTOR_RATED_FLUX,
    .rs = RS,
    .rsSkinFactor = MOTOR_RS_SKINFACTOR,
    .ls = LS,
    .maxCurrent = NOMINAL_CURRENT_A,
    .mass_copper_kg = MOTOR_MASS_COPPER_KG,
    .cooling_tau_s = MOTOR_COOLINGTAU_S,
    .name = MOTOR_NAME,
  },
  .polPulse =
  {
    .N  = NB_PULSE_PERIODS,
    .Nd = NB_DECAY_PERIODS,
    .PulseCurrentGoal = PULSE_CURRENT_GOAL,
  },
  .zest =
  {
    .zestThresholdFreqHz = 0,
    .zestInjectFreq = 0,
    .zestInjectD = 0,
    .zestGainD = 0,
    .zestGainQ = 0,
  },
  .PIDSpeed =
  {
    .pidSpdKp = PID_SPD_KP,
    .pidSpdKi = PID_SPD_KI,
  },
  .board =
  {
   .limitOverVoltage = BOARD_LIMIT_OVERVOLTAGE,
   .limitRegenHigh = BOARD_LIMIT_REGEN_HIGH,
   .limitRegenLow = BOARD_LIMIT_REGEN_LOW,
   .limitAccelHigh = BOARD_LIMIT_ACCEL_HIGH,
   .limitAccelLow = BOARD_LIMIT_ACCEL_LOW,
   .limitUnderVoltage = BOARD_LIMIT_UNDERVOLTAGE,
   .maxModulationIndex = BOARD_MAX_MODULATION,
   .softOverCurrentTrip = (0.8*M1_MAX_READABLE_CURRENT),
},
  .KSampleDelay = KSAMPLE_DELAY,
  .throttle =
  {
    .offset = THROTTLE_OFFSET, /*TODO: Tobe defined */
	  .gain = THROTTLE_GAIN, /*TODO: Tobe defined */
	  .speedMaxRPM = MOTOR_MAX_SPEED_RPM,
    .direction = 1,
  },
  .scale =
  {
    .voltage = VOLTAGE_SCALE,
    .current = CURRENT_SCALE,
    .frequency = FREQUENCY_SCALE
  },
};

const MotorConfig_reg_t *motorParams = &flashParams.motor;
const zestFlashParams_t *zestParams = &flashParams.zest;
const boardFlashParams_t *boardParams = &flashParams.board;
const scaleFlashParams_t *scaleParams = &flashParams.scale;
const throttleParams_t *throttleParams = &flashParams.throttle;
const float *KSampleDelayParams = &flashParams.KSampleDelay;
const PIDSpeedFlashParams_t *PIDSpeedParams = &flashParams.PIDSpeed;

/* USER CODE BEGIN Additional parameters */

/* USER CODE END Additional parameters */

/******************* (C) COPYRIGHT 2026 STMicroelectronics *****END OF FILE****/

