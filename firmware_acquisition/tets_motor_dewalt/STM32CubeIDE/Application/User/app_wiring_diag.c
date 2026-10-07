#include "app_wiring_diag.h"

#include "main.h"
#include "app_datalog.h"
#include "app_motor_control.h"
#include "d6t_ir.h"

#include "mc_api.h"
#include "mc_config.h"
#include "drive_parameters.h"
#include "pmsm_motor_parameters.h"
#include "fixpmath.h"

#include "stm32g4xx_ll_adc.h"

#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#define DIAG_PULL_SETTLE_US          2000U
#define DIAG_SHORT_SETTLE_US         500U
#define DIAG_I2C_HALF_PERIOD_US      20U
#define DIAG_I2C_SCL_TIMEOUT_US      1000U
#define DIAG_D6T_ADDR_7BIT           0x0AU
#define DIAG_I2C_SCAN_FIRST          0x08U
#define DIAG_I2C_SCAN_LAST           0x77U
#define DIAG_MAX_CANDIDATES          6U
#define DIAG_OW_CONVERT_TIMEOUT_MS   1000U
#define DIAG_OW_PARASITE_CONVERT_MS  750U
#define DIAG_DAC_SETTLE_MS           100U
#define DIAG_DAC_HOLD_US             2000U
#define DIAG_RC_DISCHARGE_MS         300U
#define DIAG_RC_TIMEOUT_US           600000U
#define DIAG_RISE_TIMEOUT            0xFFFFFFFFUL
#define DIAG_ADC_VREF_MV             3300U
#define DIAG_ADC_FULL_SCALE          4095U
#define DIAG_ADC_SAMPLES             8U
#define DIAG_ADC_TIMEOUT_MS          10U
#define DIAG_ADC_CONVERSION_TIMEOUT_US 1000U

extern DAC_HandleTypeDef hdac1;

typedef struct
{
  const char *name;
  GPIO_TypeDef *port;
  uint16_t pin;
} DiagPin_t;

static const DiagPin_t diag_scl = { "PB6", GPIOB, GPIO_PIN_6 };
static const DiagPin_t diag_sda = { "PB9", GPIOB, GPIO_PIN_9 };
static const DiagPin_t diag_dq = { "PG6", GPIOG, GPIO_PIN_6 };
static const DiagPin_t diag_dac = { "PA5", GPIOA, GPIO_PIN_5 };

/* Broches Morpho libres voisines des broches attendues : un fil décalé d'un pas
 * y aboutit. Ni le firmware ni la STDES-LVHP01 ne les utilisent. */
static const DiagPin_t diag_neighbors[] =
{
  { "PD0", GPIOD, GPIO_PIN_0 },     /* CN7-2 */
  { "PD3", GPIOD, GPIO_PIN_3 },     /* CN7-17 */
  { "PA4", GPIOA, GPIO_PIN_4 },     /* CN7-31 */
  { "PE10", GPIOE, GPIO_PIN_10 },   /* CN7-34 */
  { "PD15", GPIOD, GPIO_PIN_15 },   /* CN10-22 */
  { "PE9", GPIOE, GPIO_PIN_9 },     /* CN10-23 */
  { "PD7", GPIOD, GPIO_PIN_7 },     /* CN10-25 */
  { "PA12", GPIOA, GPIO_PIN_12 },   /* CN10-28 */
  { "PA6", GPIOA, GPIO_PIN_6 },     /* CN10-29 */
  { "PA11", GPIOA, GPIO_PIN_11 },   /* CN10-30 */
};

#define DIAG_NEIGHBOR_COUNT  (sizeof(diag_neighbors) / sizeof(diag_neighbors[0]))

/* Voisines de CN7-32 où le réseau R1/C1 du TB-200S peut aboutir. */
static const DiagPin_t *const diag_rc_neighbors[] =
{
  &diag_neighbors[2],
  &diag_neighbors[3],
};

static char diag_line[192];

/*
 * ================================
 * OUTILS
 * ================================
 */

static void Diag_Send(const char *fmt, ...)
{
  va_list args;

  va_start(args, fmt);
  (void)vsnprintf(diag_line, sizeof(diag_line), fmt, args);
  va_end(args);

  (void)AppDatalog_SendText(diag_line);
}

static void Diag_EnableCycleCounter(void)
{
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

static uint32_t Diag_CyclesPerUs(void)
{
  return SystemCoreClock / 1000000U;
}

static uint32_t Diag_ElapsedUs(uint32_t start_cycles)
{
  return (DWT->CYCCNT - start_cycles) / Diag_CyclesPerUs();
}

static void Diag_DelayUs(uint32_t us)
{
  uint32_t start = DWT->CYCCNT;
  uint32_t cycles = us * Diag_CyclesPerUs();

  while ((DWT->CYCCNT - start) < cycles)
  {
    __NOP();
  }
}

static uint32_t Diag_EnterCritical(void)
{
  uint32_t primask = __get_PRIMASK();
  __disable_irq();
  return primask;
}

static void Diag_ExitCritical(uint32_t primask)
{
  if (primask == 0U)
  {
    __enable_irq();
  }
}

static void Diag_Configure(const DiagPin_t *p, uint32_t mode, uint32_t pull)
{
  GPIO_InitTypeDef init = {0};

  init.Pin = p->pin;
  init.Mode = mode;
  init.Pull = pull;
  init.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(p->port, &init);
}

static bool Diag_Read(const DiagPin_t *p)
{
  return HAL_GPIO_ReadPin(p->port, p->pin) == GPIO_PIN_SET;
}

static void Diag_Write(const DiagPin_t *p, bool high)
{
  HAL_GPIO_WritePin(p->port, p->pin, high ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

static void Diag_ReleaseOpenDrain(const DiagPin_t *p)
{
  Diag_Write(p, true);
  Diag_Configure(p, GPIO_MODE_OUTPUT_OD, GPIO_PULLUP);
}

static unsigned Diag_Flag(bool value)
{
  return value ? 1U : 0U;
}

/*
 * ================================
 * ETAT MOTEUR / ALIMENTATION
 * ================================
 */

static uint32_t Diag_VbusMv(void)
{
  float vbus_v = FIXP30_toF(VBus_M1.Udcbus_in_pu) * VOLTAGE_SCALE;

  if (!(vbus_v > 0.0f) || (vbus_v > 1000.0f))
  {
    return 0U;
  }
  return (uint32_t)(vbus_v * 1000.0f);
}

static void Diag_SendMotorState(const char *prefix)
{
  float speed_rpm = (MC_GetSpeedMotor1_F() * 60.0f) / (float)POLE_PAIR_NUM;

  if (!(speed_rpm > -100000.0f) || !(speed_rpm < 100000.0f))
  {
    speed_rpm = 0.0f;
  }

  Diag_Send("%s,app_state=%s,fault_reason=%s,mc_state=%u,faults_now=0x%04lX,"
            "faults_occurred=0x%04lX,vbus_mv=%lu,speed_rpm=%ld,load_ma=%lu\r\n",
            prefix,
            AppMotorControl_GetStateName(),
            AppMotorControl_GetFaultReason(),
            (unsigned)MC_GetSTMStateMotor1(),
            (unsigned long)MC_GetCurrentFaultsMotor1(),
            (unsigned long)MC_GetOccurredFaultsMotor1(),
            (unsigned long)Diag_VbusMv(),
            (long)speed_rpm,
            (unsigned long)((AppMotorControl_GetLoadSetpointA() * 1000.0f) + 0.5f));
}

/*
 * ================================
 * NIVEAUX STATIQUES ET COURTS-CIRCUITS
 * ================================
 */

static bool Diag_ReadWithPull(const DiagPin_t *p, uint32_t pull)
{
  Diag_Configure(p, GPIO_MODE_INPUT, pull);
  Diag_DelayUs(DIAG_PULL_SETTLE_US);
  return Diag_Read(p);
}

/* pd=1 : une pull-up externe domine la pull-down interne ; pu=0 : ligne tirée à la masse. */
static bool Diag_ReportPin(const DiagPin_t *p, const char *role)
{
  bool pd = Diag_ReadWithPull(p, GPIO_PULLDOWN);
  bool pu = Diag_ReadWithPull(p, GPIO_PULLUP);

  Diag_Send("DIAG,PIN,name=%s,role=%s,pd=%u,pu=%u\r\n",
            p->name, role, Diag_Flag(pd), Diag_Flag(pu));
  return pd && pu;
}

static void Diag_ReportShort(const DiagPin_t *driven, const DiagPin_t *sensed)
{
  bool shorted;

  Diag_Configure(sensed, GPIO_MODE_INPUT, GPIO_PULLUP);
  Diag_Write(driven, false);
  Diag_Configure(driven, GPIO_MODE_OUTPUT_OD, GPIO_NOPULL);
  Diag_DelayUs(DIAG_SHORT_SETTLE_US);
  shorted = !Diag_Read(sensed);

  Diag_Write(driven, true);
  Diag_Configure(driven, GPIO_MODE_INPUT, GPIO_PULLUP);
  Diag_DelayUs(DIAG_SHORT_SETTLE_US);

  Diag_Send("DIAG,SHORT,a=%s,b=%s,shorted=%u\r\n",
            driven->name, sensed->name, Diag_Flag(shorted));
}

/*
 * ================================
 * I2C (D6T)
 * ================================
 */

static bool Diag_I2cSclHigh(const DiagPin_t *scl)
{
  uint32_t start = DWT->CYCCNT;

  Diag_Write(scl, true);
  while (!Diag_Read(scl))
  {
    if (Diag_ElapsedUs(start) > DIAG_I2C_SCL_TIMEOUT_US)
    {
      return false;
    }
  }
  Diag_DelayUs(DIAG_I2C_HALF_PERIOD_US);
  return true;
}

static void Diag_I2cStop(const DiagPin_t *scl, const DiagPin_t *sda)
{
  Diag_Write(sda, false);
  Diag_DelayUs(DIAG_I2C_HALF_PERIOD_US);
  (void)Diag_I2cSclHigh(scl);
  Diag_Write(sda, true);
  Diag_DelayUs(DIAG_I2C_HALF_PERIOD_US);
}

/* Adresse seule en écriture, horloge lente et pull-ups internes : un ACK
 * prouve SCL, SDA, alimentation et masse du capteur. */
static bool Diag_I2cProbe(const DiagPin_t *scl, const DiagPin_t *sda, uint8_t addr7)
{
  uint8_t value = (uint8_t)(addr7 << 1);
  bool ack = false;

  Diag_ReleaseOpenDrain(scl);
  Diag_ReleaseOpenDrain(sda);
  Diag_DelayUs(DIAG_I2C_HALF_PERIOD_US * 5U);

  if (!Diag_Read(scl) || !Diag_Read(sda))
  {
    return false;
  }

  Diag_Write(sda, false);
  Diag_DelayUs(DIAG_I2C_HALF_PERIOD_US);
  Diag_Write(scl, false);
  Diag_DelayUs(DIAG_I2C_HALF_PERIOD_US);

  for (uint8_t bit = 0U; bit < 8U; bit++)
  {
    Diag_Write(sda, (value & 0x80U) != 0U);
    Diag_DelayUs(DIAG_I2C_HALF_PERIOD_US);
    if (!Diag_I2cSclHigh(scl))
    {
      Diag_I2cStop(scl, sda);
      return false;
    }
    Diag_Write(scl, false);
    value = (uint8_t)(value << 1);
  }

  Diag_Write(sda, true);
  Diag_DelayUs(DIAG_I2C_HALF_PERIOD_US);
  if (Diag_I2cSclHigh(scl))
  {
    ack = !Diag_Read(sda);
  }
  Diag_Write(scl, false);
  Diag_DelayUs(DIAG_I2C_HALF_PERIOD_US);
  Diag_I2cStop(scl, sda);

  return ack;
}

static void Diag_ReportI2cScan(void)
{
  char devices[80];
  size_t used = 0U;

  devices[0] = '\0';
  for (uint8_t addr = DIAG_I2C_SCAN_FIRST; addr <= DIAG_I2C_SCAN_LAST; addr++)
  {
    if (Diag_I2cProbe(&diag_scl, &diag_sda, addr) && ((used + 4U) < sizeof(devices)))
    {
      int written = snprintf(&devices[used],
                             sizeof(devices) - used,
                             "%s%02X",
                             (used > 0U) ? ":" : "",
                             (unsigned)addr);
      if (written > 0)
      {
        used += (size_t)written;
      }
    }
  }

  Diag_Send("DIAG,I2C_SCAN,devices=%s\r\n", devices);
}

/*
 * ================================
 * 1-WIRE (DS18B20)
 * ================================
 */

static bool Diag_OwReset(const DiagPin_t *p)
{
  bool presence;
  uint32_t primask;

  Diag_ReleaseOpenDrain(p);
  Diag_DelayUs(100U);
  if (!Diag_Read(p))
  {
    return false;
  }

  Diag_Write(p, false);
  Diag_DelayUs(500U);

  primask = Diag_EnterCritical();
  Diag_Write(p, true);
  Diag_DelayUs(70U);
  presence = !Diag_Read(p);
  Diag_ExitCritical(primask);

  Diag_DelayUs(430U);
  return presence;
}

static void Diag_OwWriteBit(const DiagPin_t *p, bool bit)
{
  uint32_t primask = Diag_EnterCritical();

  Diag_Write(p, false);
  Diag_DelayUs(bit ? 6U : 62U);
  Diag_Write(p, true);
  Diag_ExitCritical(primask);

  Diag_DelayUs(bit ? 64U : 8U);
}

static bool Diag_OwReadBit(const DiagPin_t *p)
{
  bool bit;
  uint32_t primask = Diag_EnterCritical();

  Diag_Write(p, false);
  Diag_DelayUs(3U);
  Diag_Write(p, true);
  Diag_DelayUs(10U);
  bit = Diag_Read(p);
  Diag_ExitCritical(primask);

  Diag_DelayUs(55U);
  return bit;
}

static void Diag_OwWriteByte(const DiagPin_t *p, uint8_t value)
{
  for (uint8_t i = 0U; i < 8U; i++)
  {
    Diag_OwWriteBit(p, (value & 0x01U) != 0U);
    value = (uint8_t)(value >> 1);
  }
}

static uint8_t Diag_OwReadByte(const DiagPin_t *p)
{
  uint8_t value = 0U;

  for (uint8_t i = 0U; i < 8U; i++)
  {
    value = (uint8_t)(value >> 1);
    if (Diag_OwReadBit(p))
    {
      value |= 0x80U;
    }
  }
  return value;
}

static uint8_t Diag_Crc8Maxim(const uint8_t *data, uint8_t len)
{
  uint8_t crc = 0U;

  for (uint8_t i = 0U; i < len; i++)
  {
    uint8_t inbyte = data[i];

    for (uint8_t j = 0U; j < 8U; j++)
    {
      uint8_t mix = (uint8_t)((crc ^ inbyte) & 0x01U);
      crc = (uint8_t)(crc >> 1);
      if (mix != 0U)
      {
        crc ^= 0x8CU;
      }
      inbyte = (uint8_t)(inbyte >> 1);
    }
  }
  return crc;
}

static void Diag_TestDs18b20(void)
{
  uint8_t scratchpad[9] = {0};
  bool external_power;
  bool data_ok = false;
  uint32_t start_ms;
  uint32_t conversion_ms;
  int16_t raw = 0;

  if (!Diag_OwReset(&diag_dq))
  {
    return;
  }
  Diag_OwWriteByte(&diag_dq, 0xCCU);
  Diag_OwWriteByte(&diag_dq, 0xB4U);
  external_power = Diag_OwReadBit(&diag_dq);

  if (!Diag_OwReset(&diag_dq))
  {
    return;
  }
  Diag_OwWriteByte(&diag_dq, 0xCCU);
  Diag_OwWriteByte(&diag_dq, 0x44U);
  start_ms = HAL_GetTick();
  if (external_power)
  {
    while (!Diag_OwReadBit(&diag_dq) &&
           ((HAL_GetTick() - start_ms) < DIAG_OW_CONVERT_TIMEOUT_MS))
    {
      HAL_Delay(5U);
    }
  }
  else
  {
    HAL_Delay(DIAG_OW_PARASITE_CONVERT_MS);
  }
  conversion_ms = HAL_GetTick() - start_ms;

  if (Diag_OwReset(&diag_dq))
  {
    Diag_OwWriteByte(&diag_dq, 0xCCU);
    Diag_OwWriteByte(&diag_dq, 0xBEU);
    for (uint8_t i = 0U; i < 9U; i++)
    {
      scratchpad[i] = Diag_OwReadByte(&diag_dq);
    }
    /* Les 5 bits bas du registre de configuration valent toujours 1 sur un DS18B20. */
    data_ok = (Diag_Crc8Maxim(scratchpad, 8U) == scratchpad[8]) &&
              ((scratchpad[4] & 0x1FU) == 0x1FU);
    raw = (int16_t)(((uint16_t)scratchpad[1] << 8) | scratchpad[0]);
  }

  Diag_Send("DIAG,DS18B20,power=%s,conv_ms=%lu,crc=%u,temp_centi=%ld\r\n",
            external_power ? "external" : "parasite",
            (unsigned long)conversion_ms,
            Diag_Flag(data_ok),
            (long)(((int32_t)raw * 100) / 16));
}

/*
 * ================================
 * DAC / TB-200S
 * ================================
 */

static bool Diag_AdcInit(void)
{
  uint32_t start;

  __HAL_RCC_ADC12_CLK_ENABLE();
  if (LL_ADC_IsEnabled(ADC2) != 0UL)
  {
    return false;
  }

  LL_ADC_SetCommonClock(__LL_ADC_COMMON_INSTANCE(ADC2), LL_ADC_CLOCK_SYNC_PCLK_DIV4);
  LL_ADC_DisableDeepPowerDown(ADC2);
  LL_ADC_EnableInternalRegulator(ADC2);
  Diag_DelayUs(LL_ADC_DELAY_INTERNAL_REGUL_STAB_US * 2U);

  LL_ADC_StartCalibration(ADC2, LL_ADC_SINGLE_ENDED);
  start = HAL_GetTick();
  while (LL_ADC_IsCalibrationOnGoing(ADC2) != 0UL)
  {
    if ((HAL_GetTick() - start) > DIAG_ADC_TIMEOUT_MS)
    {
      return false;
    }
  }
  Diag_DelayUs(10U);

  /* PA5 = ADC2_IN13 ; échantillonnage long pour un noeud à haute impédance. */
  LL_ADC_SetChannelSamplingTime(ADC2, LL_ADC_CHANNEL_13, LL_ADC_SAMPLINGTIME_640CYCLES_5);
  LL_ADC_REG_SetSequencerRanks(ADC2, LL_ADC_REG_RANK_1, LL_ADC_CHANNEL_13);

  LL_ADC_ClearFlag_ADRDY(ADC2);
  LL_ADC_Enable(ADC2);
  start = HAL_GetTick();
  while (LL_ADC_IsActiveFlag_ADRDY(ADC2) == 0UL)
  {
    if ((HAL_GetTick() - start) > DIAG_ADC_TIMEOUT_MS)
    {
      return false;
    }
  }
  return true;
}

static void Diag_AdcDeinit(void)
{
  uint32_t start = HAL_GetTick();

  if (LL_ADC_IsEnabled(ADC2) != 0UL)
  {
    LL_ADC_Disable(ADC2);
    while ((LL_ADC_IsEnabled(ADC2) != 0UL) &&
           ((HAL_GetTick() - start) <= DIAG_ADC_TIMEOUT_MS))
    {
    }
  }
  LL_ADC_DisableInternalRegulator(ADC2);
}

static bool Diag_AdcReadMv(uint32_t *millivolts)
{
  uint32_t sum = 0U;

  for (uint32_t i = 0U; i < DIAG_ADC_SAMPLES; i++)
  {
    uint32_t start = DWT->CYCCNT;

    LL_ADC_ClearFlag_EOC(ADC2);
    LL_ADC_REG_StartConversion(ADC2);
    while (LL_ADC_IsActiveFlag_EOC(ADC2) == 0UL)
    {
      if (Diag_ElapsedUs(start) > DIAG_ADC_CONVERSION_TIMEOUT_US)
      {
        return false;
      }
    }
    sum += LL_ADC_REG_ReadConversionData12(ADC2);
  }

  *millivolts = ((sum / DIAG_ADC_SAMPLES) * DIAG_ADC_VREF_MV) / DIAG_ADC_FULL_SCALE;
  return true;
}

static uint32_t Diag_MeasureRiseUs(const DiagPin_t *p, uint32_t timeout_us)
{
  uint32_t start = DWT->CYCCNT;

  while (!Diag_Read(p))
  {
    if (Diag_ElapsedUs(start) >= timeout_us)
    {
      return DIAG_RISE_TIMEOUT;
    }
  }
  return Diag_ElapsedUs(start);
}

static long Diag_RiseField(uint32_t rise_us)
{
  return (rise_us == DIAG_RISE_TIMEOUT) ? -1L : (long)rise_us;
}

/* Mesures sur PA5 : sortie DAC chargée, maintien de C1, temps de montée avec
 * la pull-up interne (~40 kOhm) et tension résiduelle imposée par l'extérieur. */
static void Diag_TestDac(void)
{
  uint32_t drive_code = AppMotorControl_LoadToDacCode(APP_TB200S_MAX_LOAD_A);
  uint32_t v_drive_mv = 0U;
  uint32_t v_hold_mv = 0U;
  uint32_t v_zero_mv = 0U;
  uint32_t v_pu_end_mv = 0U;
  uint32_t v_pd_end_mv = 0U;
  uint32_t rise_us;
  bool adc_ok = Diag_AdcInit();

  (void)HAL_DAC_SetValue(&hdac1, DAC_CHANNEL_2, DAC_ALIGN_12B_R, drive_code);
  HAL_Delay(DIAG_DAC_SETTLE_MS);
  adc_ok = adc_ok && Diag_AdcReadMv(&v_drive_mv);

  (void)HAL_DAC_Stop(&hdac1, DAC_CHANNEL_2);
  Diag_DelayUs(DIAG_DAC_HOLD_US);
  adc_ok = adc_ok && Diag_AdcReadMv(&v_hold_mv);

  (void)HAL_DAC_Start(&hdac1, DAC_CHANNEL_2);
  (void)HAL_DAC_SetValue(&hdac1, DAC_CHANNEL_2, DAC_ALIGN_12B_R, 0U);
  HAL_Delay(DIAG_DAC_SETTLE_MS);
  adc_ok = adc_ok && Diag_AdcReadMv(&v_zero_mv);

  (void)HAL_DAC_Stop(&hdac1, DAC_CHANNEL_2);
  Diag_Configure(&diag_dac, GPIO_MODE_INPUT, GPIO_PULLUP);
  rise_us = Diag_MeasureRiseUs(&diag_dac, DIAG_RC_TIMEOUT_US);
  Diag_Configure(&diag_dac, GPIO_MODE_ANALOG, GPIO_NOPULL);
  adc_ok = adc_ok && Diag_AdcReadMv(&v_pu_end_mv);

  Diag_Configure(&diag_dac, GPIO_MODE_INPUT, GPIO_PULLDOWN);
  HAL_Delay(DIAG_RC_DISCHARGE_MS);
  Diag_Configure(&diag_dac, GPIO_MODE_ANALOG, GPIO_NOPULL);
  adc_ok = adc_ok && Diag_AdcReadMv(&v_pd_end_mv);

  (void)HAL_DAC_Start(&hdac1, DAC_CHANNEL_2);
  (void)AppMotorControl_RestoreLoadOutput();
  Diag_AdcDeinit();

  Diag_Send("DIAG,DAC,adc=%u,drive_code=%lu,v_drive_mv=%lu,v_hold_mv=%lu,v_zero_mv=%lu,"
            "rise_us=%ld,v_pu_end_mv=%lu,v_pd_end_mv=%lu\r\n",
            Diag_Flag(adc_ok),
            (unsigned long)drive_code,
            (unsigned long)v_drive_mv,
            (unsigned long)v_hold_mv,
            (unsigned long)v_zero_mv,
            Diag_RiseField(rise_us),
            (unsigned long)v_pu_end_mv,
            (unsigned long)v_pd_end_mv);
}

static void Diag_TestRcNeighbor(const DiagPin_t *p)
{
  uint32_t rise_us;

  Diag_Configure(p, GPIO_MODE_INPUT, GPIO_PULLDOWN);
  HAL_Delay(DIAG_RC_DISCHARGE_MS);
  Diag_Configure(p, GPIO_MODE_INPUT, GPIO_PULLUP);
  rise_us = Diag_MeasureRiseUs(p, DIAG_RC_TIMEOUT_US);
  Diag_Configure(p, GPIO_MODE_ANALOG, GPIO_NOPULL);

  Diag_Send("DIAG,RC,pin=%s,rise_us=%ld\r\n", p->name, Diag_RiseField(rise_us));
}

/*
 * ================================
 * API
 * ================================
 */

void AppWiringDiag_SendStatus(void)
{
  Diag_SendMotorState("STATUS");
}

void AppWiringDiag_Run(void)
{
  const DiagPin_t *candidates[DIAG_MAX_CANDIDATES];
  uint32_t candidate_count = 0U;
  bool dq_presence = false;

  Diag_EnableCycleCounter();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOE_CLK_ENABLE();
  __HAL_RCC_GPIOG_CLK_ENABLE();

  Diag_Send("DIAG,BEGIN,version=1\r\n");
  Diag_SendMotorState("DIAG,POWER");

  (void)Diag_ReportPin(&diag_scl, "SCL");
  (void)Diag_ReportPin(&diag_sda, "SDA");
  (void)Diag_ReportPin(&diag_dq, "DQ");

  candidates[candidate_count++] = &diag_scl;
  candidates[candidate_count++] = &diag_sda;
  candidates[candidate_count++] = &diag_dq;

  for (uint32_t i = 0U; i < DIAG_NEIGHBOR_COUNT; i++)
  {
    bool pulled_up = Diag_ReportPin(&diag_neighbors[i], "NEIGHBOR");

    if (pulled_up && (candidate_count < DIAG_MAX_CANDIDATES))
    {
      candidates[candidate_count++] = &diag_neighbors[i];
    }
    else
    {
      Diag_Configure(&diag_neighbors[i], GPIO_MODE_ANALOG, GPIO_NOPULL);
    }
  }

  Diag_ReportShort(&diag_scl, &diag_sda);
  Diag_ReportShort(&diag_scl, &diag_dq);
  Diag_ReportShort(&diag_sda, &diag_dq);

  /* Toutes les paires (SCL, SDA) possibles parmi les lignes tirées à 3,3 V :
   * repère un D6T inversé ou branché sur une broche voisine. */
  for (uint32_t i = 0U; i < candidate_count; i++)
  {
    for (uint32_t j = 0U; j < candidate_count; j++)
    {
      if (i != j)
      {
        bool ack = Diag_I2cProbe(candidates[i], candidates[j], DIAG_D6T_ADDR_7BIT);

        Diag_Send("DIAG,I2C,scl=%s,sda=%s,ack=%u\r\n",
                  candidates[i]->name, candidates[j]->name, Diag_Flag(ack));
      }
    }
  }
  Diag_ReportI2cScan();

  for (uint32_t i = 0U; i < candidate_count; i++)
  {
    bool presence = Diag_OwReset(candidates[i]);

    if (candidates[i] == &diag_dq)
    {
      dq_presence = presence;
    }
    Diag_Send("DIAG,OW,pin=%s,presence=%u\r\n", candidates[i]->name, Diag_Flag(presence));
  }
  if (dq_presence)
  {
    Diag_TestDs18b20();
  }

  Diag_TestDac();
  for (uint32_t i = 0U; i < (sizeof(diag_rc_neighbors) / sizeof(diag_rc_neighbors[0])); i++)
  {
    Diag_TestRcNeighbor(diag_rc_neighbors[i]);
  }

  for (uint32_t i = 3U; i < candidate_count; i++)
  {
    Diag_Configure(candidates[i], GPIO_MODE_ANALOG, GPIO_NOPULL);
  }
  Diag_Write(&diag_dq, true);
  Diag_Configure(&diag_dq, GPIO_MODE_OUTPUT_OD, GPIO_NOPULL);
  D6TIR_Init();

  Diag_Send("DIAG,D6T,read=%u,temp_c=%s\r\n",
            Diag_Flag(D6TIR_IsPresent()),
            D6TIR_GetCsvValue());
  Diag_Send("DIAG,END\r\n");
}
