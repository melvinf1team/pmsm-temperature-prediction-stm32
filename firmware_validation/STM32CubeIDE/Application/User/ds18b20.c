#include "ds18b20.h"
#include "main.h"

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

#define DS18B20_CMD_SKIP_ROM       0xCCU
#define DS18B20_CMD_CONVERT_T      0x44U
#define DS18B20_CMD_READ_SCRATCH   0xBEU
#define DS18B20_CMD_WRITE_SCRATCH  0x4EU

/*
 * Nombre de tentatives de lecture scratchpad avant de considérer
 * qu'une mesure fraîche a échoué.
 */
#define DS18B20_READ_RETRY_COUNT   3U

/*
 * ================================
 * CACHE DERNIERE VALEUR VALIDE
 * ================================
 */

static bool ds18b20_has_last_valid = false;
static int16_t ds18b20_last_temp_centi = 0;

/*
 * ================================
 * CRITICAL SECTIONS COURTES
 * ================================
 *
 * On ne bloque plus jamais les IRQ pendant toute une transaction.
 * On protège uniquement les fenêtres de quelques microsecondes où le timing
 * du 1-Wire est vraiment critique.
 */

static uint32_t DS18B20_EnterTinyCritical(void)
{
  uint32_t primask = __get_PRIMASK();
  __disable_irq();
  return primask;
}

static void DS18B20_ExitTinyCritical(uint32_t primask)
{
  if (primask == 0U)
  {
    __enable_irq();
  }
}

/*
 * ================================
 * TIMING DWT
 * ================================
 */

static void DS18B20_DwtInit(void)
{
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0U;
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
 * GPIO 1-WIRE
 * ================================
 *
 * IMPORTANT HARDWARE :
 * La pin DS18B20_DQ doit être en Open-Drain avec pull-up.
 * Idéalement pull-up externe 4.7 kΩ vers 3.3 V.
 */

static void DS18B20_LineLow(void)
{
  HAL_GPIO_WritePin(DS18B20_DQ_GPIO_Port, DS18B20_DQ_Pin, GPIO_PIN_RESET);
}

static void DS18B20_LineRelease(void)
{
  /*
   * Si la GPIO est bien en open-drain, écrire SET relâche la ligne.
   */
  HAL_GPIO_WritePin(DS18B20_DQ_GPIO_Port, DS18B20_DQ_Pin, GPIO_PIN_SET);
}

static GPIO_PinState DS18B20_ReadLine(void)
{
  return HAL_GPIO_ReadPin(DS18B20_DQ_GPIO_Port, DS18B20_DQ_Pin);
}

/*
 * ================================
 * PRIMITIVES 1-WIRE
 * ================================
 */

static bool DS18B20_Reset(void)
{
  bool presence;

  /*
   * Pas de critical section longue ici.
   * Un reset 1-Wire tolère bien un pulse low plus long que 480 us.
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
  if (bit != 0U)
  {
    /*
     * Write 1 :
     * Timing critique court : low environ 6 us puis release.
     * On protège seulement cette petite fenêtre.
     */
    uint32_t primask = DS18B20_EnterTinyCritical();

    DS18B20_LineLow();
    DS18B20_DelayUs(6U);
    DS18B20_LineRelease();

    DS18B20_ExitTinyCritical(primask);

    DS18B20_DelayUs(64U);
  }
  else
  {
    /*
     * Write 0 :
     * Low long environ 60 us.
     * On ne coupe PAS les IRQ pendant 60 us.
     * Si une IRQ FOC arrive, le slot peut être un peu allongé,
     * ce qui est préférable à casser le contrôle moteur.
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
   * Read bit :
   * Fenêtre critique courte :
   * - low bref ;
   * - release ;
   * - échantillonnage vers 12-15 us.
   *
   * On garde les IRQ désactivées environ 13 us, pas plusieurs ms.
   */
  primask = DS18B20_EnterTinyCritical();

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
 * COMMANDES HAUT NIVEAU
 * ================================
 */

static bool DS18B20_ConfigureSensor(void)
{
  /*
   * Optionnel mais utile.
   * On écrit la config scratchpad en 12 bits par défaut :
   * TH=0x4B, TL=0x46, CONFIG=0x7F.
   *
   * Ça reste du 1-Wire standard.
   * Ce n'est pas indispensable, mais ça remet le capteur dans un état connu.
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

  return true;
}

void DS18B20_Init(void)
{
  DS18B20_DwtInit();
  DS18B20_LineRelease();

  ds18b20_has_last_valid = false;
  ds18b20_last_temp_centi = 0;

  /*
   * Si ça échoue ici, ce n'est pas grave :
   * les lectures suivantes tenteront quand même.
   */
  (void)DS18B20_ConfigureSensor();
}

bool DS18B20_StartMeasurement(void)
{
  bool ok;

  /*
   * On ne met PAS toute cette transaction en critical section.
   */
  ok = DS18B20_ResetRetry();

  if (ok)
  {
    DS18B20_WriteByte(DS18B20_CMD_SKIP_ROM);
    DS18B20_WriteByte(DS18B20_CMD_CONVERT_T);
  }

  return ok;
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
