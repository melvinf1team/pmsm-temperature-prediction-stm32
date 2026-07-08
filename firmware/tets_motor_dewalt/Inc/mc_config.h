/**
  ******************************************************************************
  * @file    mc_config.h
  * @author  Motor Control SDK Team, ST Microelectronics
  * @brief   Motor Control Subsystem components configuration and handler
  *          structures declarations.
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

#ifndef __MC_CONFIG_H
#define __MC_CONFIG_H

#include "pwm_curr_fdbk.h"
#include "r3_g4xx_pwm_curr_fdbk.h"
#include "oversampling.h"
#include "mc_interface.h"
#include "speed_torq_ctrl_hso.h"
#include "mc_curr_ctrl.h"
#include "speed_pos_fdbk_hso.h"
#include "mc_polpulse.h"
#include "profiler.h"
#include "rsdc_est.h"
#include "bus_voltage.h"
#include "ntc_temperature_sensor.h"
#include "regular_conversion_manager.h"

/* USER CODE BEGIN Additional include */

/* USER CODE END Additional include */

extern PWMC_R3_Handle_t PWM_Handle_M1;
extern Oversampling_t oversampling;
extern IMPEDCORR_Obj ImpedCorr_M1;
extern HSO_Obj       HSO_M1;
extern SPD_Handle_t SPD_M1;
extern STC_Handle_t STC_M1;
extern IMPEDCORR_Params ImpedCorr_params_M1;
extern HSO_Params Hso_params_M1;
extern RSTEMP_Obj RsTemp_M1;
extern RSTEMP_Params RsTemp_params_M1;
extern NTC_Handle_t TempSensor_M1;
extern CurrCtrl_Handle_t CurrCtrl_M1;
extern RsDCEstimation_Handle_t RsDCEst_M1;
extern POLPULSE_Params PolPulse_params_M1;
extern MC_PolPulse_Handle_t MC_PolPulse_M1;
extern PROFILER_Obj	profiler_M1;
extern PROFILER_Params profilerParams_M1;
extern ProfilerMotor_Handle_t profilerMotor_M1;
extern BusVoltageSensor_Handle_t VBus_M1;
extern RegConv_t PotRegConv_M1;
#define NBR_OF_MOTORS 1
extern MCI_Handle_t Mci[NBR_OF_MOTORS];
/* USER CODE BEGIN Additional extern */

/* USER CODE END Additional extern */
#define NBR_OF_MOTORS 1
#endif /* __MC_CONFIG_H */
/******************* (C) COPYRIGHT 2026 STMicroelectronics *****END OF FILE****/
