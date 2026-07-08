#include "app_mc_debug.h"

#include "main.h"
#include "mc_api.h"
#include "mc_type.h"
#include "mc_interface.h"
#include "mc_config.h"
#include "drive_parameters.h"
#include "pmsm_motor_parameters.h"
#include "fixpmath.h"

#include "stm32g4xx_ll_usart.h"
#include "stm32g4xx_ll_dma.h"

#include <stdio.h>
#include <string.h>

#define DBG_PERIOD_BOOT_MS      250U
#define DBG_PERIOD_RUN_MS       1000U
#define DBG_BOOT_VERBOSE_MS     8000U

static uint32_t dbg_boot_ms = 0;
static uint32_t dbg_next_ms = 0;
static MCI_State_t dbg_last_state = (MCI_State_t)255;
static uint32_t dbg_last_fault_now = 0xFFFFFFFFu;
static uint32_t dbg_last_fault_occ = 0xFFFFFFFFu;

static int32_t f_to_milli(float x)
{
  if (x >= 0.0f)
  {
    return (int32_t)((x * 1000.0f) + 0.5f);
  }
  else
  {
    return (int32_t)((x * 1000.0f) - 0.5f);
  }
}

static const char *state_to_str(MCI_State_t s)
{
  switch (s)
  {
    case IDLE:            return "IDLE";
    case ICLWAIT:         return "ICLWAIT";
    case CHARGE_BOOT_CAP: return "CHARGE_BOOT_CAP";
    case OFFSET_CALIB:    return "OFFSET_CALIB";
    case START:           return "START";
    case RUN:             return "RUN";
    case STOP:            return "STOP";
    case FAULT_NOW:       return "FAULT_NOW";
    case FAULT_OVER:      return "FAULT_OVER";
    case STATE_RSDCESTIMATE: return "RSDC_ESTIMATE";
    default:              return "UNKNOWN";
  }
}

static void append_fault(char *buf, size_t size, size_t *used, uint32_t f, uint32_t bit, const char *name)
{
  if ((f & bit) != 0U)
  {
    *used += snprintf(&buf[*used], size - *used, "%s|", name);
  }
}

static void faults_to_str(uint32_t f, char *buf, size_t size)
{
  size_t used = 0;

  if (f == MC_NO_FAULTS)
  {
    snprintf(buf, size, "NONE");
    return;
  }

  append_fault(buf, size, &used, f, MC_DURATION,    "MC_DURATION");
  append_fault(buf, size, &used, f, MC_OVER_VOLT,   "MC_OVER_VOLT");
  append_fault(buf, size, &used, f, MC_UNDER_VOLT,  "MC_UNDER_VOLT");
  append_fault(buf, size, &used, f, MC_OVER_TEMP,   "MC_OVER_TEMP");
  append_fault(buf, size, &used, f, MC_START_UP,    "MC_START_UP");
  append_fault(buf, size, &used, f, MC_SPEED_FDBK,  "MC_SPEED_FDBK");
  append_fault(buf, size, &used, f, MC_OVER_CURR,   "MC_OVER_CURR");
  append_fault(buf, size, &used, f, MC_SW_ERROR,    "MC_SW_ERROR");
  append_fault(buf, size, &used, f, MC_SAMPLEFAULT, "MC_SAMPLEFAULT");
  append_fault(buf, size, &used, f, MC_OVERCURR_SW, "MC_OVERCURR_SW");
  append_fault(buf, size, &used, f, MC_DP_FAULT,    "MC_DP_FAULT");

  if (used > 0U && buf[used - 1U] == '|')
  {
    buf[used - 1U] = '\0';
  }
}

static void uart_takeover_usart1(void)
{
  LL_USART_DisableDMAReq_TX(USART1);
  LL_USART_DisableDMAReq_RX(USART1);

  LL_USART_DisableIT_TC(USART1);
  LL_USART_DisableIT_IDLE(USART1);
  LL_USART_DisableIT_ERROR(USART1);

  LL_DMA_DisableChannel(DMA1, LL_DMA_CHANNEL_1);
  LL_DMA_DisableChannel(DMA1, LL_DMA_CHANNEL_2);

  LL_DMA_ClearFlag_TC1(DMA1);
  LL_DMA_ClearFlag_TC2(DMA1);
  LL_DMA_ClearFlag_TE1(DMA1);
  LL_DMA_ClearFlag_TE2(DMA1);

  LL_USART_ClearFlag_TC(USART1);
  LL_USART_ClearFlag_IDLE(USART1);
  LL_USART_ClearFlag_ORE(USART1);
  LL_USART_ClearFlag_FE(USART1);
  LL_USART_ClearFlag_NE(USART1);
}

static void dbg_send(const char *text)
{
  while (*text)
  {
    uint32_t t0 = HAL_GetTick();

    while (!LL_USART_IsActiveFlag_TXE_TXFNF(USART1))
    {
      if ((HAL_GetTick() - t0) > 10U)
      {
        return;
      }
    }

    LL_USART_TransmitData8(USART1, (uint8_t)(*text));
    text++;
  }

  uint32_t t0 = HAL_GetTick();

  while (!LL_USART_IsActiveFlag_TC(USART1))
  {
    if ((HAL_GetTick() - t0) > 10U)
    {
      return;
    }
  }
}

void AppMcDebug_Init(void)
{
  dbg_boot_ms = HAL_GetTick();
  dbg_next_ms = dbg_boot_ms;

  uart_takeover_usart1();

  dbg_send("#MCDBG_BOOT\r\n");
}

void AppMcDebug_PrintConfig(void)
{
  char line[256];

  snprintf(line, sizeof(line),
           "#MCDBG_CONFIG,"
           "pole_pairs=%d,"
           "default_target_rpm=%d,"
           "nominal_current_A=%d,"
           "iqmax_A=%d,"
           "ov_mV=%ld,"
           "uv_mV=%ld,"
           "speed_loop_hz=%d\r\n",
           POLE_PAIR_NUM,
           DEFAULT_TARGET_SPEED_RPM,
           NOMINAL_CURRENT_A,
           IQMAX_A,
           (long)f_to_milli(OV_VOLTAGE_THRESHOLD_V),
           (long)f_to_milli(UD_VOLTAGE_THRESHOLD_V),
           SPEED_LOOP_FREQUENCY_HZ);

  dbg_send(line);
}

void AppMcDebug_Snapshot(const char *tag)
{
  char line[384];
  char fnow_txt[160];
  char focc_txt[160];

  uint32_t now = HAL_GetTick();
  MCI_State_t state = MC_GetSTMStateMotor1();
  uint32_t fnow = MC_GetCurrentFaultsMotor1();
  uint32_t focc = MC_GetOccurredFaultsMotor1();

  float vbus_v = FIXP30_toF(VBus_M1.Udcbus_in_pu) * VOLTAGE_SCALE;
  float speed_ref_hz = MC_GetSpeedReferenceMotor1_F();
  float speed_hz = MC_GetSpeedMotor1_F();
  float acc_hz_s = MC_GetAccelerationMotor1_F();

  dq_float_t idq = MC_GetCurrentMotor1_F();
  dq_float_t idq_ref = MC_GetCurrentReferenceMotor1_F();

  faults_to_str(fnow, fnow_txt, sizeof(fnow_txt));
  faults_to_str(focc, focc_txt, sizeof(focc_txt));

  snprintf(line, sizeof(line),
           "#MCDBG_SNAPSHOT,"
           "t=%lu,"
           "tag=%s,"
           "state=%s(%d),"
           "fault_now=0x%04lX[%s],"
           "fault_occ=0x%04lX[%s],"
           "vbus_mV=%ld,"
           "speed_ref_mHz=%ld,"
           "speed_mHz=%ld,"
           "acc_mHz_s=%ld,"
           "id_mA=%ld,"
           "iq_mA=%ld,"
           "idref_mA=%ld,"
           "iqref_mA=%ld,"
           "mode=%d\r\n",
           (unsigned long)now,
           tag,
           state_to_str(state),
           (int)state,
           (unsigned long)fnow,
           fnow_txt,
           (unsigned long)focc,
           focc_txt,
           (long)f_to_milli(vbus_v),
           (long)f_to_milli(speed_ref_hz),
           (long)f_to_milli(speed_hz),
           (long)f_to_milli(acc_hz_s),
           (long)f_to_milli(idq.D),
           (long)f_to_milli(idq.Q),
           (long)f_to_milli(idq_ref.D),
           (long)f_to_milli(idq_ref.Q),
           (int)MC_GetControlModeMotor1());

  dbg_send(line);
}

void AppMcDebug_PrintStartRequest(float target_elec_hz,
                                  float accel_elec_hz_s,
                                  float current_limit_a)
{
  char line[192];

  snprintf(line, sizeof(line),
           "#MCDBG_START_REQUEST,"
           "t=%lu,"
           "target_elec_mHz=%ld,"
           "accel_mHz_s=%ld,"
           "current_limit_mA=%ld\r\n",
           (unsigned long)HAL_GetTick(),
           (long)f_to_milli(target_elec_hz),
           (long)f_to_milli(accel_elec_hz_s),
           (long)f_to_milli(current_limit_a));

  dbg_send(line);
}

void AppMcDebug_PrintStartResult(uint32_t ret)
{
  char line[128];

  snprintf(line, sizeof(line),
           "#MCDBG_START_RESULT,t=%lu,ret=%lu\r\n",
           (unsigned long)HAL_GetTick(),
           (unsigned long)ret);

  dbg_send(line);
}

void AppMcDebug_Task(void)
{
  uint32_t now = HAL_GetTick();
  MCI_State_t state = MC_GetSTMStateMotor1();
  uint32_t fnow = MC_GetCurrentFaultsMotor1();
  uint32_t focc = MC_GetOccurredFaultsMotor1();

  if (state != dbg_last_state)
  {
    dbg_last_state = state;
    AppMcDebug_Snapshot("STATE_CHANGE");
  }

  if ((fnow != dbg_last_fault_now) || (focc != dbg_last_fault_occ))
  {
    dbg_last_fault_now = fnow;
    dbg_last_fault_occ = focc;
    AppMcDebug_Snapshot("FAULT_CHANGE");
  }

  uint32_t period = ((now - dbg_boot_ms) < DBG_BOOT_VERBOSE_MS) ? DBG_PERIOD_BOOT_MS : DBG_PERIOD_RUN_MS;

  if ((int32_t)(now - dbg_next_ms) >= 0)
  {
    dbg_next_ms = now + period;
    AppMcDebug_Snapshot("PERIODIC");
  }
}