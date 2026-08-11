/**
 ******************************************************************************
 * @file    speed_pos_fdbk_hso.c
 * @author  Motor Control SDK Team, ST Microelectronics
 * @brief   This file provides firmware functions that implement the  features
 *          of the Speed & Position Feedback HSO component of the Motor Control
 *          SDK.
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
  * @ingroup speed_pos_fdbk_hso
  */

/* Includes ------------------------------------------------------------------*/
#include "speed_pos_fdbk_hso.h"
#include "mc_config.h"
#include "mc_parameters.h"
#include "parameters_conversion.h"

/** @addtogroup MCSDK
  * @{
  */

/** @addtogroup HSO
  * @{
  */

/** @addtogroup MCLLHSOAPI
  * @{
  */

/** @defgroup speed_pos_fdbk_hso Speed & position feedback for HSO
  *
  * @brief Speed & Position feedback HSO component of the Motor Control SDK
  *
  * This component provides the speed and the angular position of the rotor based
  * on a sensorless approach.
  *
  * By considering a perfect measurements of three phase currents and voltages with knowledge
  * of stator resistance Rs and inductance Ls the back-emf can be estimated.
  * The electrical angle is then provided by the High Sensitivity Observer after an estimation of
  * the flux based on the back-emf.
  *
  * ![Speed and position feeback with only HSO](speed_position_fdbk.svg)
  *
  *
  * @{
  */

/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* USER CODE BEGIN Private define */
/* Private define ------------------------------------------------------------*/

/* USER CODE END Private define */

/* Private variables----------------------------------------------------------*/

/* USER CODE BEGIN Private Variables */

/* USER CODE END Private Variables */

/* Private functions ---------------------------------------------------------*/
/* USER CODE BEGIN Private Functions */

/* USER CODE END Private Functions */

/**
  * @brief  Initializes speed & position control component.
  *         It should be called during Motor control middleware initialization
  * @param  pHandle: speed & position feedback control handler
  * @param  flashParams: flash parameters
  */
void SPD_Init(SPD_Handle_t *pHandle, FLASH_Params_t const *flashParams)
{

  /* Initialize the objects which return a Handle here */
  pHandle->pImpedCorr = IMPEDCORR_init(&ImpedCorr_M1, sizeof(ImpedCorr_M1));
  pHandle->pHSO = HSO_init(&HSO_M1, sizeof(HSO_M1));
  pHandle->pRsTemp = RSTEMP_init(&RsTemp_M1, sizeof(RsTemp_M1));

  if (pHandle->pImpedCorr == 0 ||
      pHandle->pRsTemp == 0 ||
      pHandle->pHSO == 0)
  {
    /* Handle initialization failed, better to spin here than to crash later */
    for(;;);
  }

  /* Impedance correction */
  pHandle->flagDisableImpedanceCorrection = false;

  ImpedCorr_params_M1.fullScaleFreq_Hz = flashParams->scale.frequency;
  ImpedCorr_params_M1.KsampleDelay = flashParams->KSampleDelay;
  ImpedCorr_params_M1.fullScaleCurrent_A = flashParams->scale.current;
  ImpedCorr_params_M1.fullScaleVoltage_V = flashParams->scale.voltage;

  IMPEDCORR_setParams(pHandle->pImpedCorr, &ImpedCorr_params_M1);
  IMPEDCORR_setRs_si(pHandle->pImpedCorr, flashParams->motor.rs);
  IMPEDCORR_setLs_si(pHandle->pImpedCorr, flashParams->motor.ls);

  /* High Sensitivity Observer */
  Hso_params_M1.Flux_Wb = (double)flashParams->motor.ratedFlux / M_TWOPI;
  Hso_params_M1.FullScaleVoltage_V = flashParams->scale.voltage;
  Hso_params_M1.FullScaleFreq_Hz = flashParams->scale.frequency;
  HSO_setParams(pHandle->pHSO, &Hso_params_M1);

  pHandle->DirCheck_threshold_pu = FIXP30(4.0f / Hso_params_M1.FullScaleFreq_Hz);

 /* RsTemp parameters initialization */
  RsTemp_params_M1.FullScaleCurrent_A = flashParams->scale.current;
  RsTemp_params_M1.FullScaleVoltage_V = flashParams->scale.voltage;
  RsTemp_params_M1.FullScaleFreq_Hz   = flashParams->scale.frequency;
  RsTemp_params_M1.RsRatedOhm         = flashParams->motor.rs;
  RsTemp_params_M1.LsHenry            = flashParams->motor.ls;
  RsTemp_params_M1.MotorMaxCurrent_A  = flashParams->motor.maxCurrent;
  RSTEMP_setParams(pHandle->pRsTemp, &RsTemp_params_M1);

  RSTEMP_setIref_A(pHandle->pRsTemp, 0.1f);           // 0.1A injection
  RSTEMP_setFreqXY_Hz(pHandle->pRsTemp, 3.0f);        // 3Hz injection
  RSTEMP_setFilterStates(pHandle->pRsTemp, false);
  RSTEMP_setBandwidth(pHandle->pRsTemp, 4.0f);
  pHandle->Rs_update_select = FOC_Rs_update_None;
  pHandle->flagRsTemp = false;

  pHandle->AngleCorrection_pu = FIXP30(0.0f);

  pHandle->Hz_pu_to_step_per_isr = FIXP30(flashParams->scale.frequency / TF_REGULATION_RATE);

  pHandle->OpenLoopAngle = FIXP30(0.0f);

  float_t fs_l = (flashParams->scale.voltage / flashParams->scale.current / (float_t)VOLTAGE_FILTER_POLE_RPS);
  float_t ls_pu_flt = flashParams->motor.ls / fs_l;
  FIXPSCALED_floatToFIXPscaled(ls_pu_flt, &pHandle->Ls_Active_pu_fps);
}

#if defined (CCMRAM)
#if defined (__ICCARM__)
#pragma location = ".ccmram"
#elif defined (__CC_ARM) || defined(__GNUC__)
__attribute__((section (".ccmram")))
#endif
#endif
/**
  * @brief  Runs speed & position control component.
  *         It should be called during Motor control middleware initialization
  * @param  pHandle: speed & position feedback control handler
  * @param  In: input data structure
  * @param  Out: output data structure
  */
void SPD_Run(SPD_Handle_t *pHandle, Sensorless_Input_t *In, Sensorless_Output_t *Out)
{

  /* Use the previous cycle's estimated electrical frequency to perform the impedance correction */
  fixp30_t w_corr_pu = 0;
  if (true == pHandle->closedLoopAngleEnabled)
  {
    w_corr_pu = HSO_getEmfSpeed_pu(pHandle->pHSO);
  }

  Out->Uab_emf_pu = IMPEDCORR_run(pHandle->pImpedCorr, &In->Iab_pu, &In->Uab_pu, w_corr_pu);

  /* Store EMF's magnitude */
  pHandle->EmfMag_pu = FIXP_mag(Out->Uab_emf_pu.A, Out->Uab_emf_pu.B);

  Currents_Iab_t Iab_filt_pu;
  IMPEDCORR_getIab_filt_pu(pHandle->pImpedCorr, &Iab_filt_pu);

  /* High Sensitivity Observer (HSO) **********************************************************************/
  HSO_Handle handleHso = pHandle->pHSO;

  fixp30_t correction_angle_pu = FIXP30(0.0f);

  if ( (pHandle->flagHsoAngleEqualsOpenLoopAngle == true) || (pHandle->flagFreezeHsoAngle == true) )
  {
    HSO_setFlag_TrackAngle(handleHso, false);
    if (pHandle->flagHsoAngleEqualsOpenLoopAngle == true)
    {
      /* copy the OPENLOOP angle for ZEST identification procedure */
      HSO_setAngle_pu(handleHso, pHandle->OpenLoopAngle);
    }
  }
  else
  {
    HSO_setFlag_TrackAngle(handleHso, true);
  }

  HSO_run(handleHso, &Out->Uab_emf_pu, correction_angle_pu);

  /* Estimated angle in cosine/sine */
  FIXP_CosSin_t park_cossin;
  HSO_getCosSinTh_ab(pHandle->pHSO, &park_cossin);

  /* Resistance Temperature estimation */
  RSTEMP_run(pHandle->pRsTemp, &In->Uab_pu, &Iab_filt_pu, &park_cossin, HSO_getEmfSpeed_pu(handleHso));

  /* store flux, angle and frequency for monitoring */
  pHandle->Flux_ab = HSO_getFlux_ab_Wb(handleHso);
  pHandle->Flux_Active_Wb = HSO_getFluxAmpl_Wb(handleHso);
  pHandle->theta_pu = HSO_getAngle_pu(handleHso);
  pHandle->Fe_hso_pu = HSO_getEmfSpeed_pu(handleHso);

  Out->angSpeed = pHandle->theta_pu;
  Out->speed = pHandle->Fe_hso_pu;

} /* end of SPD_Run() */

#if defined (CCMRAM)
#if defined (__ICCARM__)
#pragma location = ".ccmram"
#elif defined (__CC_ARM) || defined(__GNUC__)
__attribute__((section (".ccmram")))
#endif
#endif
/**
  * @brief  Exports the estimated electrical speed.
  *
  * @param  pHandle: speed & position feedback control handler
  * @param  emfSpeed: Estimated speed from delta angle and taking into account ZeST correction signal
  * @param  speedLP: Estimated speed from delta angle
  */
void SPD_GetSpeed(SPD_Handle_t *pHandle, fixp30_t *emfSpeed, fixp30_t *speedLP)
{

  if (true == pHandle->closedLoopAngleEnabled)
  {
    *emfSpeed = HSO_getEmfSpeed_pu(pHandle->pHSO);
    *speedLP = HSO_getSpeedLP_pu(pHandle->pHSO);
  }
  else
  {
    *emfSpeed = 0;
    *speedLP	= 0;
  }
}

#if defined (CCMRAM)
#if defined (__ICCARM__)
#pragma location = ".ccmram"
#elif defined (__CC_ARM) || defined(__GNUC__)
__attribute__((section (".ccmram")))
#endif
#endif
/**
  * @brief  Exports the estimated electrical angle.
  *         If the application is in closed loop then the angle is the one estimated
  *         by HSO. While in open loop case, the angle is the one forced during the open loop.
  *
  * @param  pHandle: speed & position feedback control handler
  * @param  angle: Estimated electrical angle from HSO or from open loop update routine
  */
void SPD_GetAngle(SPD_Handle_t *pHandle, fixp30_t *angle)
{

  if (true == pHandle->closedLoopAngleEnabled)
  {
    *angle = HSO_getAngle_pu(pHandle->pHSO);
  }
  else
  {
    *angle = pHandle->OpenLoopAngle;
  }
}

#if defined (CCMRAM)
#if defined (__ICCARM__)
#pragma location = ".ccmram"
#elif defined (__CC_ARM) || defined(__GNUC__)
__attribute__((section (".ccmram")))
#endif
#endif
/**
  * @brief  Updates the open loop electrical angle
  *         this routines is necessary when open loop mode is selected
  * @param  pHandle: speed & position feedback control handler
  * @param  speedRef: electrical speed reference
  */
void SPD_UpdtOpenLoopAngle(SPD_Handle_t *pHandle, fixp30_t speedRef)
{
  fixp30_t angle_step_pu = FIXP30_mpy(speedRef, pHandle->Hz_pu_to_step_per_isr);
  fixp30_t angle_pu = pHandle->OpenLoopAngle;
  angle_pu += angle_step_pu;
  angle_pu &= (FIXP30(1.0f) - 1);
  pHandle->OpenLoopAngle = angle_pu;
}

/**
  * @brief  Performs the background process of the Rs estimators.
  * @param  pHandle: speed & position feedback control handler
  */
void SPD_RsBackground(SPD_Handle_t *pHandle)
{
  switch (pHandle->Rs_update_select)
  {
    case FOC_Rs_update_Auto:
    {
      if ((RUN == Mci[M1].State) && CurrCtrl_M1.currentControlEnabled)
      {
        /* Motor is running closed loop, we can estimate the resistance */
        RSTEMP_setFlagEnable(pHandle->pRsTemp, true);
      }
      else
      {
        /* Motor is not running closed loop and open loop current, go back to rated resistance */
        RSTEMP_setFlagEnable(pHandle->pRsTemp, false);
      }
    }
    break;
    case FOC_Rs_update_None:
    {
      RSTEMP_setFlagEnable(pHandle->pRsTemp, false);
    }
    break;

    default:
      break;
  }

  /* Update Rs & Ls for impedance correction */
  if (!pHandle->flagDisableImpedanceCorrection)
  {
    float_t Actual_RsOhm = IMPEDCORR_getRs_si(pHandle->pImpedCorr);
    RSTEMP_runBackground(pHandle->pRsTemp, Actual_RsOhm, pHandle->flagRsTemp);
    if (RSTEMP_getFlagUpdateEnable(pHandle->pRsTemp) == true)
    {
      Actual_RsOhm = RSTEMP_getRsOhm(pHandle->pRsTemp);
    }

    IMPEDCORR_setRs_si(pHandle->pImpedCorr, Actual_RsOhm);
    IMPEDCORR_setLs(pHandle->pImpedCorr, pHandle->Ls_Active_pu_fps.value, pHandle->Ls_Active_pu_fps.fixpFmt);
  }

}

/**
  * @}
  */

/**
  * @}
  */

/**
  * @}
  */

/************************ (C) COPYRIGHT 2026 STMicroelectronics *****END OF FILE****/
