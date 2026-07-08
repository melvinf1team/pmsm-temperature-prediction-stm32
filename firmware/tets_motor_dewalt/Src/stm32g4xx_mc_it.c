
/**
  ******************************************************************************
  * @file    stm32g4xx_mc_it.c
  * @author  Motor Control SDK Team, ST Microelectronics
  * @brief   Main Interrupt Service Routines.
  *          This file provides exceptions handler and peripherals interrupt
  *          service routine related to Motor Control for the STM32G4 Family.
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
  * @ingroup STM32G4xx_IRQ_Handlers
  */

/* Includes ------------------------------------------------------------------*/
#include "mc_type.h"
#include "mc_config.h"
#include "mc_tasks.h"
#include "parameters_conversion.h"
#include "motorcontrol.h"
#include "stm32g4xx_ll_exti.h"
#include "stm32g4xx_hal.h"
#include "stm32g4xx.h"

/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/** @addtogroup MCSDK
  * @{
  */

/** @addtogroup STM32G4xx_IRQ_Handlers STM32G4xx IRQ Handlers
  * @{
  */

/* USER CODE BEGIN PRIVATE */

/* Private typedef -----------------------------------------------------------*/
/* Private define ------------------------------------------------------------*/
#define SYSTICK_DIVIDER (SYS_TICK_FREQUENCY/1000)
/* Private macro -------------------------------------------------------------*/
/* Private variables ---------------------------------------------------------*/
/* Private function prototypes -----------------------------------------------*/
/* Private functions ---------------------------------------------------------*/

/* USER CODE END PRIVATE */

/* Public prototypes of IRQ handlers called from assembly code ---------------*/
void TIMx_UP_M1_IRQHandler(void);
void TIMx_BRK_M1_IRQHandler(void);
void DMA1_Channel1_IRQHandler(void);
void DMA1_Channel2_IRQHandler(void);

/**
  * @brief  This function handles first motor TIMx Update interrupt request.
  */
void TIMx_UP_M1_IRQHandler(void)
{
 /* USER CODE BEGIN TIMx_UP_M1_IRQn 0 */

 /* USER CODE END  TIMx_UP_M1_IRQn 0 */

    LL_TIM_ClearFlag_UPDATE(TIM8);
#if (OVS_COUNT ==1)
    TSK_HighFrequencyTask();
#endif
    R3_TIMx_UP_IRQHandler(&PWM_Handle_M1);

 /* USER CODE BEGIN TIMx_UP_M1_IRQn 1 */

 /* USER CODE END  TIMx_UP_M1_IRQn 1 */
}

/**
  * @brief  This function handles motor TIMx CC interrupt request.
  */
void TIM8_CC_IRQHandler(void)
{
  if (LL_TIM_IsActiveFlag_CC4(TIM8))
  {
    LL_TIM_ClearFlag_CC4(TIM8);
    PulseCountDown(&MC_PolPulse_M1);
  }
  else
  {
    /* nothing to do */
  }
}

void TIMx_BRK_M1_IRQHandler(void)
{
  /* USER CODE BEGIN TIMx_BRK_M1_IRQn 0 */

  /* USER CODE END TIMx_BRK_M1_IRQn 0 */
  if (LL_TIM_IsActiveFlag_BRK(TIM8))
  {
    LL_TIM_ClearFlag_BRK(TIM8);
    PWMC_OCP_Handler(&PWM_Handle_M1._Super);
  }
  if (LL_TIM_IsActiveFlag_BRK2(TIM8))
  {
    LL_TIM_ClearFlag_BRK2(TIM8);
    PWMC_OVP_Handler(&PWM_Handle_M1._Super, TIM8);
  }
  /* Systick is not executed due low priority so is necessary to call MC_Scheduler here.*/
  MC_Scheduler();

  /* USER CODE BEGIN TIMx_BRK_M1_IRQn 1 */

  /* USER CODE END TIMx_BRK_M1_IRQn 1 */
}

#if defined (CCMRAM)
#if defined (__ICCARM__)
#pragma location = ".ccmram"
#elif defined (__CC_ARM) || defined(__GNUC__)
__attribute__((section (".ccmram")))
#endif
#endif
void DMA2_Channel1_IRQHandler (void)
{
	/* ADCxBuff shall be full of Data*/
	if (LL_DMA_IsActiveFlag_TC1(DMA2))
	{
      LL_DMA_ClearFlag_TC1(DMA2);
	}

	/* Swap DMA buffers */
	R3_SwapDmaBuffer(&PWM_Handle_M1);

	/* Restart conversions */
	LL_ADC_REG_StartConversion(ADC3);
	LL_ADC_REG_StartConversion(ADC4);
	LL_ADC_REG_StartConversion(ADC5);

#if (OVS_COUNT !=1)
	/* Run motor control in high frequency task */
	TSK_HighFrequencyTask();
#endif

} /* end of DMA1_Channel1_IRQHandler() */

/* USER CODE BEGIN 1 */

/* USER CODE END 1 */

/**
  * @}
  */

/**
  * @}
  */

/******************* (C) COPYRIGHT 2026 STMicroelectronics *****END OF FILE****/
