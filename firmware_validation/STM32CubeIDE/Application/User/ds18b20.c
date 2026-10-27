#include "ds18b20.h"
#include "main.h"
#include "app_foc_timing.h"

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

#define DS18B20_CMD_SKIP_ROM       0xCCU
#define DS18B20_CMD_CONVERT_T      0x44U
#define DS18B20_CMD_READ_SCRATCH   0xBEU
#define DS18B20_CMD_WRITE_SCRATCH  0x4EU

/*
 * Number of scratchpad read attempts before considering a fresh
 * measurement unsuccessful.
 */
#define DS18B20_READ_RETRY_COUNT   3U

/*
 * ================================
 * LAST VALID VALUE CACHE
 * ================================
 */

static bool ds18b20_has_last_valid = false;
static int16_t ds18b20_last_temp_centi = 0;

/*
 * ================================
 * SHORT CRITICAL SECTIONS
 * ================================
 *
 * Never block IRQs for an entire transaction.
 * Protect only the windows of a few microseconds where 1-Wire timing
 * is critical.
 */

/*
 * These windows are placed between two FOC runs (AppFocTiming): masking IRQs
 * just before the FOC IRQ delays it and raises MC_DURATION (0x0001). Without
 * a free window, the bit is dropped and the transaction fails (retry, then
 * cached value) rather than delaying the FOC.
 */
#define DS18B20_WRITE1_WINDOW_US   8U
#define DS18B20_READ_WINDOW_US     15U

static bool ds18b20_bus_aborted = false;

static bool DS18B20_EnterTinyCritical(uint32_t window_us, uint32_t *primask)
{
  if (ds18b20_bus_aborted)
  {
    return false;
  }
  if (!AppFocTiming_EnterQuietWindow(window_us, primask))
  {
    ds18b20_bus_aborted = true;
    return false;
  }
  return true;
}

static void DS18B20_ExitTinyCritical(uint32_t primask)
{
  AppFocTiming_ExitQuietWindow(primask);
}

/*
 * ================================
 * DWT TIMING
 * ================================
 */

static void DS18B20_DwtInit(void)
{
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
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

/*
 * ================================
 * 1-WIRE GPIO
 * ================================
 *
 * HARDWARE REQUIREMENTS:
 * The DS18B20_DQ pin must use open-drain mode with a pull-up.
 * Prefer an external 4.7 kOhm pull-up to 3.3 V.
 */

static void DS18B20_LineLow(void)
{
  HAL_GPIO_WritePin(DS18B20_DQ_GPIO_Port, DS18B20_DQ_Pin, GPIO_PIN_RESET);
}

static void DS18B20_LineRelease(void)
{
  /*
   * If the GPIO is correctly configured as open-drain, writing SET releases the line.
   */
  HAL_GPIO_WritePin(DS18B20_DQ_GPIO_Port, DS18B20_DQ_Pin, GPIO_PIN_SET);
}

static GPIO_PinState DS18B20_ReadLine(void)
{
  return HAL_GPIO_ReadPin(DS18B20_DQ_GPIO_Port, DS18B20_DQ_Pin);
}

/*
 * ================================
 * 1-WIRE PRIMITIVES
 * ================================
 */

static bool DS18B20_Reset(void)
{
  bool presence;

  /* New transaction: a previous abort no longer applies. */
  ds18b20_bus_aborted = false;

  /*
   * No long critical section is needed here.
   * A 1-Wire reset tolerates a low pulse longer than 480 us.
   */
  DS18B20_LineLow();
  DS18B20_DelayUs(500U);

  DS18B20_LineRelease();
  DS18B20_DelayUs(70U);

  presence = (DS18B20_ReadLine() == GPIO_PIN_RESET);

  DS18B20_DelayUs(410U);

  return presence;
}

static bool DS18B20_ResetRetry(void)
{
  for (uint8_t i = 0U; i < 3U; i++)
  {
    if (DS18B20_Reset())
    {
      return true;
    }

    DS18B20_DelayUs(200U);
  }

  return false;
}

static void DS18B20_WriteBit(uint8_t bit)
{
  if (ds18b20_bus_aborted)
  {
    return;
  }

  if (bit != 0U)
  {
    /*
     * Write 1:
     * Short critical timing: low for about 6 us, then release.
     * Protect only this short window.
     */
    uint32_t primask;

    if (!DS18B20_EnterTinyCritical(DS18B20_WRITE1_WINDOW_US, &primask))
    {
      return;
    }

    DS18B20_LineLow();
    DS18B20_DelayUs(6U);
    DS18B20_LineRelease();

    DS18B20_ExitTinyCritical(primask);

    DS18B20_DelayUs(64U);
  }
  else
  {
    /*
     * Write 0:
     * Long low pulse of about 60 us.
     * Do NOT disable IRQs for 60 us.
     * If a FOC IRQ occurs, the slot may be slightly extended,
     * which is preferable to disrupting motor control.
     */
    DS18B20_LineLow();
    DS18B20_DelayUs(62U);
    DS18B20_LineRelease();
    DS18B20_DelayUs(8U);
  }
}

static uint8_t DS18B20_ReadBit(void)
{
  uint8_t bit;
  uint32_t primask;

  /*
   * Read bit:
   * Short critical window:
   * - brief low pulse;
   * - release;
   * - sample at about 12-15 us.
   *
   * Keep IRQs disabled for about 13 us, not several ms.
   */
  if (!DS18B20_EnterTinyCritical(DS18B20_READ_WINDOW_US, &primask))
  {
    return 1U;
  }

  DS18B20_LineLow();
  DS18B20_DelayUs(3U);

  DS18B20_LineRelease();
  DS18B20_DelayUs(10U);

  bit = (DS18B20_ReadLine() == GPIO_PIN_SET) ? 1U : 0U;

  DS18B20_ExitTinyCritical(primask);

  DS18B20_DelayUs(55U);

  return bit;
}

static void DS18B20_WriteByte(uint8_t data)
{
  for (uint8_t i = 0U; i < 8U; i++)
  {
    DS18B20_WriteBit(data & 0x01U);
    data >>= 1U;
  }
}

static uint8_t DS18B20_ReadByte(void)
{
  uint8_t value = 0U;

  for (uint8_t i = 0U; i < 8U; i++)
  {
    value >>= 1U;

    if (DS18B20_ReadBit() != 0U)
    {
      value |= 0x80U;
    }
  }

  return value;
}

/*
 * ================================
 * CRC
 * ================================
 */

static uint8_t DS18B20_Crc8(const uint8_t *data, uint8_t len)
{
  uint8_t crc = 0U;

  for (uint8_t i = 0U; i < len; i++)
  {
    uint8_t inbyte = data[i];

    for (uint8_t j = 0U; j < 8U; j++)
    {
      uint8_t mix = (crc ^ inbyte) & 0x01U;
      crc >>= 1U;

      if (mix != 0U)
      {
        crc ^= 0x8CU;
      }

      inbyte >>= 1U;
    }
  }

  return crc;
}

/*
 * ================================
 * HIGH-LEVEL COMMANDS
 * ================================
 */

static bool DS18B20_ConfigureSensor(void)
{
  /*
   * Optional but useful.
   * Write the default 12-bit scratchpad configuration:
   * TH=0x4B, TL=0x46, CONFIG=0x7F.
   *
   * This uses standard 1-Wire commands.
   * It is not essential, but it restores the sensor to a known state.
   */
  if (!DS18B20_ResetRetry())
  {
    return false;
  }

  DS18B20_WriteByte(DS18B20_CMD_SKIP_ROM);
  DS18B20_WriteByte(DS18B20_CMD_WRITE_SCRATCH);
  DS18B20_WriteByte(0x4BU);
  DS18B20_WriteByte(0x46U);
  DS18B20_WriteByte(0x7FU);

  return !ds18b20_bus_aborted;
}

void DS18B20_Init(void)
{
  DS18B20_DwtInit();
  DS18B20_LineRelease();

  ds18b20_has_last_valid = false;
  ds18b20_last_temp_centi = 0;

  /*
   * A failure here is not fatal:
   * subsequent reads will still be attempted.
   */
  (void)DS18B20_ConfigureSensor();
}

bool DS18B20_StartMeasurement(void)
{
  bool ok;

  /*
   * Do NOT place this entire transaction in a critical section.
   */
  ok = DS18B20_ResetRetry();

  if (ok)
  {
    DS18B20_WriteByte(DS18B20_CMD_SKIP_ROM);
    DS18B20_WriteByte(DS18B20_CMD_CONVERT_T);
  }

  return ok && !ds18b20_bus_aborted;
}

static bool DS18B20_ReadScratchpad(uint8_t scratchpad[9])
{
  if (scratchpad == NULL)
  {
    return false;
  }

  if (!DS18B20_ResetRetry())
  {
    return false;
  }

  DS18B20_WriteByte(DS18B20_CMD_SKIP_ROM);
  DS18B20_WriteByte(DS18B20_CMD_READ_SCRATCH);

  for (uint8_t i = 0U; i < 9U; i++)
  {
    scratchpad[i] = DS18B20_ReadByte();
  }

  if (ds18b20_bus_aborted)
  {
    return false;
  }

  if (DS18B20_Crc8(scratchpad, 8U) != scratchpad[8])
  {
    return false;
  }

  return true;
}

static bool DS18B20_ReadTemperatureCentiFresh(int16_t *temperature_centi)
{
  uint8_t scratchpad[9];

  if (temperature_centi == NULL)
  {
    return false;
  }

  for (uint8_t attempt = 0U; attempt < DS18B20_READ_RETRY_COUNT; attempt++)
  {
    if (DS18B20_ReadScratchpad(scratchpad))
    {
      int16_t raw = (int16_t)(((uint16_t)scratchpad[1] << 8U) | scratchpad[0]);

      *temperature_centi = (int16_t)(((int32_t)raw * 100) / 16);

      ds18b20_last_temp_centi = *temperature_centi;
      ds18b20_has_last_valid = true;

      return true;
    }

    DS18B20_DelayUs(200U);
  }

  return false;
}

bool DS18B20_ReadMeasurement(void)
{
  int16_t temp_centi;

  if (DS18B20_ReadTemperatureCentiFresh(&temp_centi))
  {
    return true;
  }

  return false;
}

bool DS18B20_GetLastTemperatureC(float *temperature_c)
{
  if ((temperature_c == NULL) || !ds18b20_has_last_valid)
  {
    return false;
  }

  *temperature_c = (float)ds18b20_last_temp_centi / 100.0f;
  return true;
}
