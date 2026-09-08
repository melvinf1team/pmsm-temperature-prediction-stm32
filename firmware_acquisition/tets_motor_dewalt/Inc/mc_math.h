/**
  ******************************************************************************
  * @file    mc_math.h
  * @author  Motor Control SDK Team, ST Microelectronics
  * @brief   This file provides mathematics functions useful for and specific to
  *          Motor Control.
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
  * @ingroup MC_Math_HSO
  */
/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef MC_MATH_H
#define MC_MATH_H

/* Includes ------------------------------------------------------------------*/
#include "mc_type.h"

/** @addtogroup MCSDK
  * @{
  */

/** @addtogroup HSO
  * @{
  */

/**
  * @brief FOC_ModulationMode_e Space Vector PWM common type modes
  * Please refer for more details to [Space Vector Pulse Width Modulation](space_vector_PWM_implementation.md)
  */
typedef enum _FOC_ModulationMode_e_
{
  FOC_MODULATIONMODE_Sine,          /**< This method corresponds to a pure sinewave modulation without any common mode(Ucom=0).  */
  FOC_MODULATIONMODE_Centered,      /**< Classical Space Vector Modulation (SVM), keeps the output voltage balanced equally between the top and bottom.*/
  FOC_MODULATIONMODE_ShiftedCenter, /**< This method keeps the pulses on the low side shunts as wide as possible to enlarge the space for current reconstruction.*/
  FOC_MODULATIONMODE_MinLow,        /**< This method keeps one phase stuck the output voltages to the negative DC rail, hence only two of the three bridges are switching at any instant.*/
  FOC_MODULATIONMODE_MaxHigh,       /**< This method keeps one phase stuck the output voltages to the positive DC rail, hence only two of the three bridges are switching at any instant. Beware that this method is only applicable using ICS topology.*/
  FOC_MODULATIONMODE_UpDown,        /**< This Method keeps at least one phase from switching (less switching losses).  Beware that this method is only applicable using ICS topology. */
  numFOC_MODULATIONMODE
} FOC_ModulationMode_e;

Currents_Iab_t MCM_Clarke_Current( const Currents_Irst_t Irst );
Voltages_Uab_t MCM_Clarke_Voltage( const Voltages_Urst_t Urst );
Currents_Idq_t MCM_Park_Current( Currents_Iab_t Iab, FIXP_CosSin_t* cosSin );
Voltages_Udq_t MCM_Park_Voltage( Voltages_Uab_t Uab, FIXP_CosSin_t* cosSin );
Duty_Dab_t MCM_Inv_Park_Duty( Duty_Ddq_t Ddq, FIXP_CosSin_t* cosSin );
Duty_Drst_t MCM_Inv_Clarke_Duty(Duty_Dab_t* Dab_pu);
Duty_Drst_t MCM_Modulate(const Duty_Drst_t *pDrst_pu, const FOC_ModulationMode_e mode);

/**
  * @}
  */

/**
  * @}
  */
#endif /* MC_MATH_H*/
/******************* (C) COPYRIGHT 2026 STMicroelectronics *****END OF FILE****/
