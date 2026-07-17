#include "d6t_ir.h"
#include "main.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#define D6TIR_SCL_GPIO_Port         GPIOB
#define D6TIR_SCL_Pin               GPIO_PIN_6
#define D6TIR_SDA_GPIO_Port         GPIOB
#define D6TIR_SDA_Pin               GPIO_PIN_9

#define D6TIR_I2C_ADDR_7BIT         0x0AU
#define D6TIR_READ_COMMAND          0x4CU
#define D6TIR_FRAME_SIZE            35U
#define D6TIR_OBJECT_COUNT          16U

/* Pixel instantane logge dans d6t_temp_c : 5 = ligne 2, colonne 2 sur la matrice 4x4. */
#define D6TIR_SELECTED_PIXEL_INDEX  5U

#define D6TIR_PERIOD_PRESENT_MS     250U
#define D6TIR_PERIOD_MISSING_MS     2000U
#define D6TIR_HALF_PERIOD_US        5U
#define D6TIR_SCL_WAIT_US           100U

static bool d6tir_present = false;
static bool d6tir_has_value = false;
static uint32_t d6tir_next_read_ms = 0U;
static char d6tir_csv_value[16] = "NaN";

static void D6TIR_DwtInit(void)
{
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0U;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

static void D6TIR_DelayUs(uint32_t us)
{
  uint32_t cycles_per_us = SystemCoreClock / 1000000U;
  uint32_t start = DWT->CYCCNT;
  uint32_t delay_cycles = us * cycles_per_us;

  while ((DWT->CYCCNT - start) < delay_cycles)
  {
    __NOP();
  }
}

static void D6TIR_SclRelease(void)
{
  HAL_GPIO_WritePin(D6TIR_SCL_GPIO_Port, D6TIR_SCL_Pin, GPIO_PIN_SET);
}

static void D6TIR_SclLow(void)
{
  HAL_GPIO_WritePin(D6TIR_SCL_GPIO_Port, D6TIR_SCL_Pin, GPIO_PIN_RESET);
}

static void D6TIR_SdaRelease(void)
{
  HAL_GPIO_WritePin(D6TIR_SDA_GPIO_Port, D6TIR_SDA_Pin, GPIO_PIN_SET);
}

static void D6TIR_SdaLow(void)
{
  HAL_GPIO_WritePin(D6TIR_SDA_GPIO_Port, D6TIR_SDA_Pin, GPIO_PIN_RESET);
}

static bool D6TIR_ReadSda(void)
{
  return HAL_GPIO_ReadPin(D6TIR_SDA_GPIO_Port, D6TIR_SDA_Pin) == GPIO_PIN_SET;
}

static bool D6TIR_WaitSclHigh(void)
{
  for (uint32_t elapsed = 0U; elapsed < D6TIR_SCL_WAIT_US; elapsed++)
  {
    if (HAL_GPIO_ReadPin(D6TIR_SCL_GPIO_Port, D6TIR_SCL_Pin) == GPIO_PIN_SET)
    {
      return true;
    }
    D6TIR_DelayUs(1U);
  }

  return false;
}

static bool D6TIR_ClockHigh(void)
{
  D6TIR_SclRelease();
  if (!D6TIR_WaitSclHigh())
  {
    return false;
  }
  D6TIR_DelayUs(D6TIR_HALF_PERIOD_US);
  return true;
}

static void D6TIR_Start(void)
{
  D6TIR_SdaRelease();
  D6TIR_SclRelease();
  (void)D6TIR_WaitSclHigh();
  D6TIR_DelayUs(D6TIR_HALF_PERIOD_US);
  D6TIR_SdaLow();
  D6TIR_DelayUs(D6TIR_HALF_PERIOD_US);
  D6TIR_SclLow();
}

static void D6TIR_Stop(void)
{
  D6TIR_SdaLow();
  D6TIR_DelayUs(D6TIR_HALF_PERIOD_US);
  D6TIR_SclRelease();
  (void)D6TIR_WaitSclHigh();
  D6TIR_DelayUs(D6TIR_HALF_PERIOD_US);
  D6TIR_SdaRelease();
  D6TIR_DelayUs(D6TIR_HALF_PERIOD_US);
}

static bool D6TIR_WriteByte(uint8_t value)
{
  for (uint8_t bit = 0U; bit < 8U; bit++)
  {
    if ((value & 0x80U) != 0U)
    {
      D6TIR_SdaRelease();
    }
    else
    {
      D6TIR_SdaLow();
    }

    if (!D6TIR_ClockHigh())
    {
      return false;
    }

    D6TIR_SclLow();
    D6TIR_DelayUs(D6TIR_HALF_PERIOD_US);
    value <<= 1;
  }

  D6TIR_SdaRelease();
  if (!D6TIR_ClockHigh())
  {
    return false;
  }

  bool ack = !D6TIR_ReadSda();
  D6TIR_SclLow();
  D6TIR_DelayUs(D6TIR_HALF_PERIOD_US);

  return ack;
}

static uint8_t D6TIR_ReadByte(bool ack)
{
  uint8_t value = 0U;

  D6TIR_SdaRelease();

  for (uint8_t bit = 0U; bit < 8U; bit++)
  {
    value <<= 1;
    if (D6TIR_ClockHigh())
    {
      if (D6TIR_ReadSda())
      {
        value |= 1U;
      }
    }
    D6TIR_SclLow();
    D6TIR_DelayUs(D6TIR_HALF_PERIOD_US);
  }

  if (ack)
  {
    D6TIR_SdaLow();
  }
  else
  {
    D6TIR_SdaRelease();
  }

  (void)D6TIR_ClockHigh();
  D6TIR_SclLow();
  D6TIR_SdaRelease();
  D6TIR_DelayUs(D6TIR_HALF_PERIOD_US);

  return value;
}

static void D6TIR_BusRecover(void)
{
  D6TIR_SdaRelease();
  for (uint8_t i = 0U; i < 9U; i++)
  {
    D6TIR_SclRelease();
    (void)D6TIR_WaitSclHigh();
    D6TIR_DelayUs(D6TIR_HALF_PERIOD_US);
    D6TIR_SclLow();
    D6TIR_DelayUs(D6TIR_HALF_PERIOD_US);
  }
  D6TIR_Stop();
}

static uint8_t D6TIR_PecUpdate(uint8_t crc, uint8_t value)
{
  crc ^= value;

  for (uint8_t bit = 0U; bit < 8U; bit++)
  {
    if ((crc & 0x80U) != 0U)
    {
      crc = (uint8_t)((crc << 1) ^ 0x07U);
    }
    else
    {
      crc <<= 1;
    }
  }

  return crc;
}

static bool D6TIR_CheckPec(const uint8_t *frame)
{
  uint8_t crc = 0U;

  crc = D6TIR_PecUpdate(crc, (uint8_t)(D6TIR_I2C_ADDR_7BIT << 1));
  crc = D6TIR_PecUpdate(crc, D6TIR_READ_COMMAND);
  crc = D6TIR_PecUpdate(crc, (uint8_t)((D6TIR_I2C_ADDR_7BIT << 1) | 1U));

  for (uint8_t i = 0U; i < (D6TIR_FRAME_SIZE - 1U); i++)
  {
    crc = D6TIR_PecUpdate(crc, frame[i]);
  }

  return crc == frame[D6TIR_FRAME_SIZE - 1U];
}

static bool D6TIR_ReadFrame(uint8_t *frame)
{
  bool ok = false;
  const uint8_t addr_write = (uint8_t)(D6TIR_I2C_ADDR_7BIT << 1);
  const uint8_t addr_read = (uint8_t)(addr_write | 1U);

  D6TIR_Start();

  if (!D6TIR_WriteByte(addr_write))
  {
    goto done;
  }
  if (!D6TIR_WriteByte(D6TIR_READ_COMMAND))
  {
    goto done;
  }

  D6TIR_Start();

  if (!D6TIR_WriteByte(addr_read))
  {
    goto done;
  }

  for (uint8_t i = 0U; i < D6TIR_FRAME_SIZE; i++)
  {
    frame[i] = D6TIR_ReadByte(i < (D6TIR_FRAME_SIZE - 1U));
  }

  ok = D6TIR_CheckPec(frame);

done:
  D6TIR_Stop();
  return ok;
}

static void D6TIR_FormatRawTenth(int16_t raw_tenth)
{
  const char *sign = "";
  int16_t value = raw_tenth;

  if (value < 0)
  {
    sign = "-";
    value = (int16_t)-value;
  }

  snprintf(d6tir_csv_value,
           sizeof(d6tir_csv_value),
           "%s%ld.%01ld",
           sign,
           (long)(value / 10),
           (long)(value % 10));
}

static bool D6TIR_ReadTemperature(void)
{
  uint8_t frame[D6TIR_FRAME_SIZE];
  uint8_t offset;
  int16_t raw_tenth;

  if (D6TIR_SELECTED_PIXEL_INDEX >= D6TIR_OBJECT_COUNT)
  {
    return false;
  }

  if (!D6TIR_ReadFrame(frame))
  {
    return false;
  }

  offset = (uint8_t)(2U + (D6TIR_SELECTED_PIXEL_INDEX * 2U));
  raw_tenth = (int16_t)((uint16_t)frame[offset] | ((uint16_t)frame[offset + 1U] << 8));

  if ((raw_tenth < -400) || (raw_tenth > 2000))
  {
    return false;
  }

  D6TIR_FormatRawTenth(raw_tenth);
  d6tir_has_value = true;

  return true;
}

void D6TIR_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  __HAL_RCC_GPIOB_CLK_ENABLE();
  D6TIR_DwtInit();

  HAL_GPIO_WritePin(D6TIR_SCL_GPIO_Port, D6TIR_SCL_Pin, GPIO_PIN_SET);
  HAL_GPIO_WritePin(D6TIR_SDA_GPIO_Port, D6TIR_SDA_Pin, GPIO_PIN_SET);

  GPIO_InitStruct.Pin = D6TIR_SCL_Pin | D6TIR_SDA_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_OD;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  D6TIR_BusRecover();

  d6tir_present = D6TIR_ReadTemperature();
  d6tir_next_read_ms = HAL_GetTick() + (d6tir_present ? D6TIR_PERIOD_PRESENT_MS : D6TIR_PERIOD_MISSING_MS);
}

void D6TIR_Task(uint32_t now_ms)
{
  bool ok;

  if ((int32_t)(now_ms - d6tir_next_read_ms) < 0)
  {
    return;
  }

  ok = D6TIR_ReadTemperature();
  d6tir_present = ok;

  if (!ok && !d6tir_has_value)
  {
    snprintf(d6tir_csv_value, sizeof(d6tir_csv_value), "NaN");
  }

  d6tir_next_read_ms = now_ms + (ok ? D6TIR_PERIOD_PRESENT_MS : D6TIR_PERIOD_MISSING_MS);
}

bool D6TIR_IsPresent(void)
{
  return d6tir_present;
}

const char *D6TIR_GetCsvValue(void)
{
  if (!d6tir_has_value)
  {
    return "NaN";
  }

  return d6tir_csv_value;
}