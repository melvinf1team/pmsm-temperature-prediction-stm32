/**
  ******************************************************************************
  * @file    hf_registers.c
  * @author  Motor Control SDK Team, ST Microelectronics
  * @brief   This file provides firmware functions that implement the handling
  * of registers access for the DAC and ASYNC protocol used in the High Frequency
  * Task.
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

#include "stdint.h"
#include "register_interface.h"
#include "mc_config.h"

uint8_t HF_GetIDSize (uint16_t dataID)
{
  uint8_t typeID = dataID & TYPE_MASK;
  uint8_t result;
  switch (typeID)
  {
    case TYPE_DATA_8BIT:
      result = 1;
      break;
    case TYPE_DATA_16BIT:
      result = 2;
      break;
    case TYPE_DATA_32BIT:
      result = 4;
      break;
    default:
      result=0;
      break;
  }
  return result;
}

__weak uint8_t HF_GetPtrReg (uint16_t dataID, void ** dataPtr)
{
  uint8_t typeID = dataID & TYPE_MASK;
  uint8_t motorID = (dataID & MOTOR_MASK)-1;
  uint16_t regID = dataID & REG_MASK;
  uint8_t retVal = HF_CMD_OK;

  MCI_Handle_t * pMCI = &Mci[motorID];

  switch (typeID)
  {
  case TYPE_DATA_32BIT:
    {
      switch (regID)
      {
      case MC_REG_ADC_I_U:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Irst_in_pu.R)) + 2UL);
        break;
      case MC_REG_ADC_I_V:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Irst_in_pu.S)) + 2UL);
        break;
      case MC_REG_ADC_I_W:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Irst_in_pu.T)) + 2UL);
        break;
      case MC_REG_ADC_U_U:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Urst_in_pu.R)) + 2UL);
        break;
      case MC_REG_ADC_U_V:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Urst_in_pu.S)) + 2UL);
        break;
      case MC_REG_ADC_U_W:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Urst_in_pu.T)) + 2UL);
        break;
      case MC_REG_I_ALPHA:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Iab_in_pu.A)) + 2UL);
        break;
      case MC_REG_I_BETA:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Iab_in_pu.B)) + 2UL);
        break;
      case MC_REG_U_ALPHA:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Uab_in_pu.A)) + 2UL);
        break;
      case MC_REG_U_BETA:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Uab_in_pu.B)) + 2UL);
        break;
      case MC_REG_I_D:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pCurrCtrl->Idq_in_pu.D)) + 2UL);
        break;
      case MC_REG_I_Q:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pCurrCtrl->Idq_in_pu.Q)) + 2UL);
        break;
      case MC_REG_U_D:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pCurrCtrl->Udq_in_pu.D)) + 2UL);
        break;
      case MC_REG_U_Q:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pCurrCtrl->Udq_in_pu.Q)) + 2UL);
        break;
      case MC_REG_ADC_I_RAW_U:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Irst_in_raw_pu.R)) + 2UL);
        break;
      case MC_REG_ADC_I_RAW_V:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Irst_in_raw_pu.S)) + 2UL);
        break;
      case MC_REG_ADC_I_RAW_W:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Irst_in_raw_pu.T)) + 2UL);
        break;
      case MC_REG_FE_HSO:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pSPD->Fe_hso_pu)) + 2UL);
        break;
      case MC_REG_HSO_FLUX_A:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pSPD->Flux_ab.A)) + 2UL);
        break;
      case MC_REG_HSO_FLUX_B:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pSPD->Flux_ab.B)) + 2UL);
        break;
      case MC_REG_FOC_ANGLE_PU:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pCurrCtrl->angle_park_pu)) + 2UL);
        break;
      case MC_REG_FOC_DUTYCYCLE_R:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Drst_out_pu.R)) + 2UL);
        break;
      case MC_REG_FOC_DUTYCYCLE_S:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Drst_out_pu.S)) + 2UL);
        break;
      case MC_REG_FOC_DUTYCYCLE_T:
        *dataPtr = (void *)((uint32_t)(&(pMCI->pPWM->Drst_out_pu.T)) + 2UL);
        break;
      default:
        break;
      }
    }
      break;
    default:

      break;
  }

  return retVal;
}

/************************ (C) COPYRIGHT 2026 STMicroelectronics *****END OF FILE****/
