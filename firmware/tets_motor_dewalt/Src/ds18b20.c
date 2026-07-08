#include "ds18b20.h"
#include "main.h"

#include <stdio.h>
#include <string.h>

#define DS18B20_CMD_SKIP_ROM       0xCC
#define DS18B20_CMD_CONVERT_T      0x44
#define DS18B20_CMD_READ_SCRATCH   0xBE


static uint32_t DS18B20_EnterCritical(void)
{
  uint32_t primask = __get_PRIMASK();
  __disable_irq();
  return primask;
}

static void DS18B20_ExitCritical(uint32_t primask)
{
  if (primask == 0U)
  {
    __enable_irq();
  }
}

static void DS18B20_DelayUs(uint32_t us)
{
  uint32_t cycles_per_us = SystemCoreClock / 1000000U;
  uint32_t start = DWT->CYCCNT;
  uint32_t delay_cycles = us * cycles_per_us;

  while ((DWT->CYCCNT - start) < delay_cycles)
  {
    __NOP();
  }
}

static void DS18B20_DwtInit(void)
{
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

static void DS18B20_LineLow(void)
{
  HAL_GPIO_WritePin(DS18B20_DQ_GPIO_Port, DS18B20_DQ_Pin, GPIO_PIN_RESET);
}

static void DS18B20_LineRelease(void)
{
  HAL_GPIO_WritePin(DS18B20_DQ_GPIO_Port, DS18B20_DQ_Pin, GPIO_PIN_SET);
}

static GPIO_PinState DS18B20_ReadLine(void)
{
  return HAL_GPIO_ReadPin(DS18B20_DQ_GPIO_Port, DS18B20_DQ_Pin);
}

static bool DS18B20_Reset(void)
{
  bool presence;

  DS18B20_LineLow();
  DS18B20_DelayUs(480);

  DS18B20_LineRelease();
  DS18B20_DelayUs(70);

  presence = (DS18B20_ReadLine() == GPIO_PIN_RESET);

  DS18B20_DelayUs(410);

  return presence;
}

static void DS18B20_WriteBit(uint8_t bit)
{
  if (bit)
  {
    DS18B20_LineLow();
    DS18B20_DelayUs(6);
    DS18B20_LineRelease();
    DS18B20_DelayUs(64);
  }
  else
  {
    DS18B20_LineLow();
    DS18B20_DelayUs(60);
    DS18B20_LineRelease();
    DS18B20_DelayUs(10);
  }
}

static uint8_t DS18B20_ReadBit(void)
{
  uint8_t bit;

  DS18B20_LineLow();
  DS18B20_DelayUs(6);

  DS18B20_LineRelease();
  DS18B20_DelayUs(9);

  bit = (DS18B20_ReadLine() == GPIO_PIN_SET) ? 1U : 0U;

  DS18B20_DelayUs(55);

  return bit;
}

static void DS18B20_WriteByte(uint8_t data)
{
  for (uint8_t i = 0; i < 8; i++)
  {
    DS18B20_WriteBit(data & 0x01U);
    data >>= 1;
  }
}

static uint8_t DS18B20_ReadByte(void)
{
  uint8_t value = 0;

  for (uint8_t i = 0; i < 8; i++)
  {
    value >>= 1;

    if (DS18B20_ReadBit())
    {
      value |= 0x80U;
    }
  }

  return value;
}

static uint8_t DS18B20_Crc8(const uint8_t *data, uint8_t len)
{
  uint8_t crc = 0;

  for (uint8_t i = 0; i < len; i++)
  {
    uint8_t inbyte = data[i];

    for (uint8_t j = 0; j < 8; j++)
    {
      uint8_t mix = (crc ^ inbyte) & 0x01U;
      crc >>= 1;

      if (mix)
      {
        crc ^= 0x8CU;
      }

      inbyte >>= 1;
    }
  }

  return crc;
}

static void DS18B20_InitModule(void)
{
  DS18B20_DwtInit();
  DS18B20_LineRelease();
}

static bool DS18B20_StartMeasurement(void)
{
  bool ok;
  uint32_t primask = DS18B20_EnterCritical();

  ok = DS18B20_Reset();

  if (ok)
  {
    DS18B20_WriteByte(DS18B20_CMD_SKIP_ROM);
    DS18B20_WriteByte(DS18B20_CMD_CONVERT_T);
  }

  DS18B20_ExitCritical(primask);

  return ok;
}

static bool DS18B20_ReadTemperatureCenti(int16_t *temperature_centi)
{
  uint8_t scratchpad[9];
  bool ok = true;

  uint32_t primask = DS18B20_EnterCritical();

  if (!DS18B20_Reset())
  {
    ok = false;
  }

  if (ok)
  {
    DS18B20_WriteByte(DS18B20_CMD_SKIP_ROM);
    DS18B20_WriteByte(DS18B20_CMD_READ_SCRATCH);

    for (uint8_t i = 0; i < 9; i++)
    {
      scratchpad[i] = DS18B20_ReadByte();
    }
  }

  DS18B20_ExitCritical(primask);

  if (!ok)
  {
    return false;
  }

  if (DS18B20_Crc8(scratchpad, 8) != scratchpad[8])
  {
    return false;
  }

  int16_t raw = (int16_t)((scratchpad[1] << 8) | scratchpad[0]);

  *temperature_centi = (int16_t)(((int32_t)raw * 100) / 16);

  return true;
}

static bool DS18B20_ReadCsvValues(char *dst, size_t dst_len)
{
  int16_t temp_centi;

  if (!DS18B20_ReadTemperatureCenti(&temp_centi))
  {
    snprintf(dst, dst_len, "nan");
    return false;
  }

  int32_t value = temp_centi;
  const char *sign = "";

  if (value < 0)
  {
    sign = "-";
    value = -value;
  }

  snprintf(dst, dst_len, "%s%ld.%02ld",
           sign,
           (long)(value / 100),
           (long)(value % 100));

  return true;
}

const DatalogModule_t DS18B20_Module =
{
  .csv_columns = "ds18b20_temp_c",
  .init = DS18B20_InitModule,
  .start_measurement = DS18B20_StartMeasurement,
  .read_csv_values = DS18B20_ReadCsvValues,
  .conversion_time_ms = 750U
};
