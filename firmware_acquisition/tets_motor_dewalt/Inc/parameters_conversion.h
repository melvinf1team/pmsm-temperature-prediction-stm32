
/**
  ******************************************************************************
  * @file    parameters_conversion.h
  * @author  Motor Control SDK Team, ST Microelectronics
  * @brief   This file includes the proper Parameter conversion on the base
  *          of stdlib for the first drive
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

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef PARAMETERS_CONVERSION_H
#define PARAMETERS_CONVERSION_H

#include "mc_math.h"
#include "parameters_conversion_g4xx.h"
#include "pmsm_motor_parameters.h"
#include "drive_parameters.h"
#include "power_stage_parameters.h"

/* Current conversion from Ampere unit to 16Bit Digit */
#define CURRENT_CONV_FACTOR                 (uint16_t)((65536.0 * RSHUNT * AMPLIFICATION_GAIN)/ADC_REFERENCE_VOLTAGE)
#define CURRENT_CONV_FACTOR_INV             (1.0 / ((65536.0 * RSHUNT * AMPLIFICATION_GAIN) / ADC_REFERENCE_VOLTAGE))

/* Current conversion from Ampere unit to 16Bit Digit */

/**
  * @brief  definition of ADC used for oversampling
  */
enum _OVS_ADC_e_
{
    OVS_ADC3,
    OVS_ADC4,
    OVS_ADC5
};

/**
  * @brief  definition of ADC channel rank used for oversampling
  */
enum _OVS_RANK_e_
{
  OVS_RANK1 = 0,
  OVS_RANK2,
  OVS_RANK3,
  OVS_RANK4,
  OVS_RANK5,
  OVS_RANK6,
  OVS_RANK7,
  OVS_RANK8,
  // etc...
};
#define ADC_REFERENCE_VOLTAGE               3.3
#define M1_MAX_READABLE_CURRENT  (ADC_REFERENCE_VOLTAGE / (2 * RSHUNT * AMPLIFICATION_GAIN))

/************************* CONTROL FREQUENCIES & DELAIES **********************/
#define TF_REGULATION_RATE                  (uint32_t)((uint32_t)(PWM_FREQUENCY) / (REGULATION_EXECUTION_RATE))

/* TF_REGULATION_RATE_SCALED is TF_REGULATION_RATE divided by PWM_FREQ_SCALING to allow more dynamic */
#define TF_REGULATION_RATE_SCALED           (uint16_t)((uint32_t)(PWM_FREQUENCY) / (REGULATION_EXECUTION_RATE\
                                            * PWM_FREQ_SCALING))

/* DPP_CONV_FACTOR is introduce to compute the right DPP with TF_REGULATOR_SCALED  */
#define DPP_CONV_FACTOR                     (65536 / PWM_FREQ_SCALING)

/* Current conversion from Ampere unit to 16Bit Digit */
#define ID_DEMAG                            (ID_DEMAG_A * CURRENT_CONV_FACTOR)
#define IQMAX                               (IQMAX_A * CURRENT_CONV_FACTOR)

#define DEFAULT_TORQUE_COMPONENT            (DEFAULT_TORQUE_COMPONENT_A * CURRENT_CONV_FACTOR)
#define DEFAULT_FLUX_COMPONENT              (DEFAULT_FLUX_COMPONENT_A * CURRENT_CONV_FACTOR)
#define REP_COUNTER                         (uint16_t)((REGULATION_EXECUTION_RATE * 2u) - 1u)
#define SYS_TICK_FREQUENCY                  (uint16_t)2000
#define UI_TASK_FREQUENCY_HZ                10U
#define PHASE1_FINAL_CURRENT                (PHASE1_FINAL_CURRENT_A * CURRENT_CONV_FACTOR)
#define PHASE2_FINAL_CURRENT                (PHASE2_FINAL_CURRENT_A * CURRENT_CONV_FACTOR)
#define PHASE3_FINAL_CURRENT                (PHASE3_FINAL_CURRENT_A * CURRENT_CONV_FACTOR)
#define PHASE4_FINAL_CURRENT                (PHASE4_FINAL_CURRENT_A * CURRENT_CONV_FACTOR)
#define PHASE5_FINAL_CURRENT                (PHASE5_FINAL_CURRENT_A * CURRENT_CONV_FACTOR)
#define MEDIUM_FREQUENCY_TASK_RATE          (uint16_t)SPEED_LOOP_FREQUENCY_HZ
#define MF_TASK_OCCURENCE_TICKS             (SYS_TICK_FREQUENCY / SPEED_LOOP_FREQUENCY_HZ) - 1u
#define UI_TASK_OCCURENCE_TICKS             (SYS_TICK_FREQUENCY / UI_TASK_FREQUENCY_HZ) - 1u
#define SERIALCOM_TIMEOUT_OCCURENCE_TICKS   (SYS_TICK_FREQUENCY / SERIAL_COM_TIMEOUT_INVERSE) - 1u
#define SERIALCOM_ATR_TIME_TICKS            (uint16_t)(((SYS_TICK_FREQUENCY * SERIAL_COM_ATR_TIME_MS) / 1000u) - 1u)

#define MAX_APPLICATION_SPEED_UNIT          ((MAX_APPLICATION_SPEED_RPM * SPEED_UNIT) / U_RPM)
#define MIN_APPLICATION_SPEED_UNIT          ((MIN_APPLICATION_SPEED_RPM * SPEED_UNIT) / U_RPM)

#define MAX_APPLICATION_SPEED_UNIT2         ((MAX_APPLICATION_SPEED_RPM2 * SPEED_UNIT) / U_RPM)
#define MIN_APPLICATION_SPEED_UNIT2         ((MIN_APPLICATION_SPEED_RPM2 * SPEED_UNIT) / U_RPM)

/**************************   VOLTAGE CONVERSIONS  Motor 1 *************************/
#define OVERVOLTAGE_THRESHOLD_d             (uint16_t)(OV_VOLTAGE_THRESHOLD_V * 65535 /\
                                            (ADC_REFERENCE_VOLTAGE / VBUS_PARTITIONING_FACTOR))
#define OVERVOLTAGE_THRESHOLD_LOW_d         (uint16_t)(OV_VOLTAGE_THRESHOLD_V * 65535 /\
                                            (ADC_REFERENCE_VOLTAGE / VBUS_PARTITIONING_FACTOR))
#define UNDERVOLTAGE_THRESHOLD_d            (uint16_t)((UD_VOLTAGE_THRESHOLD_V * 65535) /\
                                            ((uint16_t)(ADC_REFERENCE_VOLTAGE / VBUS_PARTITIONING_FACTOR)))
#define INT_SUPPLY_VOLTAGE                  (uint16_t)(65536 / ADC_REFERENCE_VOLTAGE)
#define DELTA_TEMP_THRESHOLD                (OV_TEMPERATURE_THRESHOLD_C - T0_C)
#define DELTA_V_THRESHOLD                   (dV_dT * DELTA_TEMP_THRESHOLD)
#define OV_TEMPERATURE_THRESHOLD_d          ((V0_V + DELTA_V_THRESHOLD) * INT_SUPPLY_VOLTAGE)
#define DELTA_TEMP_HYSTERESIS               (OV_TEMPERATURE_HYSTERESIS_C)
#define DELTA_V_HYSTERESIS                  (dV_dT * DELTA_TEMP_HYSTERESIS)
#define OV_TEMPERATURE_HYSTERESIS_d         (DELTA_V_HYSTERESIS * INT_SUPPLY_VOLTAGE)

/*************** Timer for PWM generation & currenst sensing parameters  ******/
#define PWM_PERIOD_CYCLES                   (uint16_t)(((uint32_t)ADV_TIM_CLK_MHz * (uint32_t)1000000u\
                                            / ((uint32_t)(PWM_FREQUENCY))) & (uint16_t)0xFFFE)

#define M1_AUX_TIM_PERIOD_CYCLES            (uint16_t)((((uint32_t)ADV_TIM_CLK_MHz * (uint32_t)1000000u\
                                            / ((uint32_t)(PWM_FREQUENCY)*OVS_COUNT)))-1)

#define DEADTIME_NS                         SW_DEADTIME_NS
#define DEAD_TIME_ADV_TIM_CLK_MHz           (ADV_TIM_CLK_MHz * TIM_CLOCK_DIVIDER)
#define DEAD_TIME_COUNTS_1                  (DEAD_TIME_ADV_TIM_CLK_MHz * DEADTIME_NS / 1000uL)
#if (DEAD_TIME_COUNTS_1 <= 255)
#define DEAD_TIME_COUNTS                    (uint16_t)DEAD_TIME_COUNTS_1
#elif (DEAD_TIME_COUNTS_1 <= 508)
#define DEAD_TIME_COUNTS                    (uint16_t)(((DEAD_TIME_ADV_TIM_CLK_MHz * DEADTIME_NS/2) /1000uL) + 128)
#elif (DEAD_TIME_COUNTS_1 <= 1008)
#define DEAD_TIME_COUNTS                    (uint16_t)(((DEAD_TIME_ADV_TIM_CLK_MHz * DEADTIME_NS/8) /1000uL) + 320)
#elif (DEAD_TIME_COUNTS_1 <= 2015)
#define DEAD_TIME_COUNTS                    (uint16_t)(((DEAD_TIME_ADV_TIM_CLK_MHz * DEADTIME_NS/16) /1000uL) + 384)
#else
#define DEAD_TIME_COUNTS 510
#endif
#define DTCOMPCNT                           (uint16_t)((DEADTIME_NS * ADV_TIM_CLK_MHz) / 2000)
#define TON_NS                              500
#define TOFF_NS                             500
#define TON                                 (uint16_t)((TON_NS * ADV_TIM_CLK_MHz)  / 2000)
#define TOFF                                (uint16_t)((TOFF_NS * ADV_TIM_CLK_MHz) / 2000)

#define M1_CHARGE_BOOT_CAP_TICKS          (((uint16_t)SYS_TICK_FREQUENCY * (uint16_t)10) / 1000U)
#define M1_CHARGE_BOOT_CAP_DUTY_CYCLES ((uint32_t)0.000\
                                      * ((uint32_t)PWM_PERIOD_CYCLES / 2U))
#define M2_CHARGE_BOOT_CAP_TICKS         (((uint16_t)SYS_TICK_FREQUENCY * (uint16_t)10) / 1000U)
#define M2_CHARGE_BOOT_CAP_DUTY_CYCLES ((uint32_t)0\
                                      * ((uint32_t)PWM_PERIOD_CYCLES2 / 2U))

/**********************/
/* MOTOR 1 ADC Timing */
/**********************/
/* In ADV_TIMER CLK cycles*/
#define SAMPLING_TIME                       ((ADC_SAMPLING_CYCLES * ADV_TIM_CLK_MHz) / ADC_CLK_MHz)

#define TRISE                               ((TRISE_NS * ADV_TIM_CLK_MHz)/1000uL)
#define TDEAD                               ((uint16_t)((DEADTIME_NS * ADV_TIM_CLK_MHz)/1000))
#define TNOISE                              ((uint16_t)((TNOISE_NS*ADV_TIM_CLK_MHz)/1000))
#define HTMIN 1 /* Required for main.c compilation only, CCR4 is overwritten at runtime */
#define TW_AFTER                            ((uint16_t)(((DEADTIME_NS+MAX_TNTR_NS)*ADV_TIM_CLK_MHz)/1000UL))
#define MAX_TWAIT                           ((uint16_t)((TW_AFTER - SAMPLING_TIME)/2))
#define TW_BEFORE                           ((uint16_t)((ADC_TRIG_CONV_LATENCY_CYCLES + ADC_SAMPLING_CYCLES)\
                                            * ADV_TIM_CLK_MHz) / ADC_CLK_MHz  + 1u)

/* Number of Timer ticks above which low a side current shunt sample is disqualified
 * and needs to be reconstructed.
 * Duty cycle fraction converted into ticks in the same scale as PWM duty */
#define MAX_TICKS_CURRENT_RECONSTRUCTION_THRESHOLD ((((CURRENT_RECONSTRUCTION_THRESHOLD + 1.0) / 2.0))\
                                                    * PWM_PERIOD_CYCLES / 2)

#define ADC_CURRENT_SCALE                    (-(float_t)ADC_REFERENCE_VOLTAGE / (float_t)AMPLIFICATION_GAIN\
                                             / (float_t)RSHUNT)

#define ADC_VOLTAGE_SCALE                    ((float_t)ADC_REFERENCE_VOLTAGE\
                                             / (float_t)M1_PHASE_VOLTAGE_PARTITIONING_FACTOR)
#define ADC_BUSVOLTAGE_SCALE                 ((float_t)ADC_REFERENCE_VOLTAGE / (float_t)VBUS_PARTITIONING_FACTOR)
#define CURRENT_FACTOR(CurrentScale)         (ADC_CURRENT_SCALE/(CurrentScale))
#define VOLTAGE_FACTOR(VoltageScale)         (ADC_VOLTAGE_SCALE/(VoltageScale))
#define BUSVOLTAGE_FACTOR(VoltageScale)      (ADC_BUSVOLTAGE_SCALE/(VoltageScale))
#define OVS_COUNT                            (4) /*!< @brief  Oversampling level. default value is 4 */
#define OVS_NUM_ADCS                         (3) /*!< @brief number of ADCs used for Motor Control */
#define OVS_LONGEST_TASK                     (3) /*!< @brief Longest ADC sequence length */
#define OVS_ADC1_TASKLENGTH                  (0) /*!< @brief ADC1 sequence length */
#define OVS_ADC2_TASKLENGTH                  (0) /*!< @brief ADC2 sequence length */
#define OVS_ADC3_TASKLENGTH                  (3) /*!< @brief ADC3 sequence length */
#define OVS_ADC4_TASKLENGTH                  (3) /*!< @brief ADC4 sequence length */
#define OVS_ADC5_TASKLENGTH                  (3) /*!< @brief ADC5 sequence length */
#define OVS_ADC_I_R                          OVS_ADC3 /*!< @brief ADC used to sample phase current R */
#define OVS_RANK_I_R                         OVS_RANK1 /*!< @brief rank of the ADC channel used to sample phase current R */
#define OVS_ADC_I_S                          OVS_ADC4 /*!< @brief ADC used to sample phase current S */
#define OVS_RANK_I_S                         OVS_RANK1 /*!< @brief rank of the ADC channel used to sample phase current S */
#define OVS_ADC_I_T                          OVS_ADC5 /*!< @brief ADC used to sample phase current T */
#define OVS_RANK_I_T                         OVS_RANK1 /*!< @brief rank of the ADC channel used to sample phase current T */
#define OVS_ADC_U_R                          OVS_ADC3 /*!< @brief ADC used to sample voltage R */
#define OVS_RANK_U_R                         OVS_RANK2 /*!< @brief rank of the ADC channel used to sample voltage R */
#define OVS_ADC_U_S                          OVS_ADC4 /*!< @brief ADC used to sample voltage S */
#define OVS_RANK_U_S                         OVS_RANK2 /*!< @brief rank of the ADC channel used to sample voltage S */
#define OVS_ADC_U_T                          OVS_ADC5 /*!< @brief ADC used to sample voltage T */
#define OVS_RANK_U_T                         OVS_RANK2 /*!< @brief rank of the ADC channel used to sample voltage T */

#define OVS_ADC_U_DC                         OVS_ADC3 /*!< @brief ADC used to sample DC Bus */
#define OVS_RANK_U_DC                        OVS_RANK3 /*!< @brief rank of the ADC channel used to sample DC Bus */
#define OVS_ADC_TEMP                         OVS_ADC4 /*!< @brief ADC used to sample temperature */
#define OVS_RANK_TEMP                        OVS_RANK3 /*!< @brief rank of the ADC channel used to sample temperature */
/* Factor for one sample per period for shunt */
#define OVERSAMPLING_FIXP_CURRENT_FACTOR(Currentscale) (FIXP30(CURRENT_FACTOR(Currentscale)\
                                                       / REGULATION_EXECUTION_RATE))
#define OVERSAMPLING_FIXP_VOLTAGE_FACTOR(VoltageScale) (FIXP30(VOLTAGE_FACTOR(VoltageScale)\
                                                       / (OVS_COUNT * REGULATION_EXECUTION_RATE)))
#define OVERSAMPLING_FIXP_BUSVOLTAGE_FACTOR(VoltageScale) (FIXP30(BUSVOLTAGE_FACTOR(VoltageScale)\
                                                       / (OVS_COUNT * REGULATION_EXECUTION_RATE)))

/* Index of the sample used for single-sampled channels, the low side shunt measurements  */
#if (OVS_COUNT == 1)
#define BOARD_SHUNT_SAMPLE_SELECT (0)
#else
#define BOARD_SHUNT_SAMPLE_SELECT ((OVS_COUNT/2) - 1)
#endif

/* USER CODE BEGIN temperature */
#define M1_VIRTUAL_HEAT_SINK_TEMPERATURE_VALUE 25u
#define M1_TEMP_SW_FILTER_BW_FACTOR         250u
/* USER CODE END temperature */

#define PQD_CONVERSION_FACTOR               (float_t)(((1.732 * ADC_REFERENCE_VOLTAGE) /\
                                            (RSHUNT * AMPLIFICATION_GAIN)) / 65536.0f)

/****** Prepares the UI configurations according the MCconfxx settings ********/
#define DAC_ENABLE
#define DAC_OP_ENABLE

/* Motor 1 settings */
#define FW_ENABLE

#define DIFFTERM_ENABLE

/* Sensors setting */
#define AUX_SCFG                               0x0
#define PLLTUNING_ENABLE

#define UI_CFGOPT_PFC_ENABLE

/*******************************************************************************
  * UI configurations settings. It can be manually overwritten if special
  * configuartion is required.
*******************************************************************************/
/* Specific options of UI */
#define UI_CONFIG_M1                        (UI_CFGOPT_NONE DAC_OP_ENABLE FW_ENABLE DIFFTERM_ENABLE\
                                            | (MAIN_SCFG << MAIN_SCFG_POS)\
                                            | (AUX_SCFG << AUX_SCFG_POS)\
                                            | UI_CFGOPT_SETIDINSPDMODE PLLTUNING_ENABLE UI_CFGOPT_PFC_ENABLE\
                                            | UI_CFGOPT_PLLTUNING)
#define UI_CONFIG_M2
#define DIN_ACTIVE_LOW                      Bit_RESET
#define DIN_ACTIVE_HIGH                     Bit_SET
#define DOUT_ACTIVE_HIGH                    DOutputActiveHigh
#define DOUT_ACTIVE_LOW                     DOutputActiveLow

#define LPF_FILT_CONST                      ((int16_t)(32767 * 0.5))

/* MMI Table Motor 1 MAX_MODULATION_100_PER_CENT */
#define MAX_MODULE                          (uint16_t)((100* 32767)/100)

#define SAMPLING_CYCLE_CORRECTION           0.5 /* Add half cycle required by STM32G473QETx ADC */
#define LL_ADC_SAMPLINGTIME_1CYCLES_5       LL_ADC_SAMPLINGTIME_1CYCLE_5

//cstat !MISRAC2012-Rule-20.10 !DEFINE-hash-multiple
#define LL_ADC_SAMPLING_CYCLE(CYCLE)        LL_ADC_SAMPLINGTIME_ ## CYCLE ## CYCLES_5

#endif /*PARAMETERS_CONVERSION_H*/

/******************* (C) COPYRIGHT 2026 STMicroelectronics *****END OF FILE****/
