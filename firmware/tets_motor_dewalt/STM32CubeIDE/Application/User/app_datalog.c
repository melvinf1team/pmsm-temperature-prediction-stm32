#include "app_datalog.h"
#include "app_motor_control.h"
#include "main.h"
#include "ds18b20.h"
#include "d6t_ir.h"
#include "datalog_module.h"

#include "mc_api.h"
#include "mc_type.h"
#include "mc_interface.h"
#include "mc_config.h"
#include "drive_parameters.h"
#include "pmsm_motor_parameters.h"
#include "fixpmath.h"

#include "stm32g4xx_ll_usart.h"
#include "stm32g4xx_ll_dma.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>

/*
 * ================================
 * CONFIG PAR DEFAUT
 * ================================
 */

#define APP_DATALOG_DEFAULT_PERIOD_MS       100U
#define APP_DS18B20_DEFAULT_PERIOD_MS       1000U

/*
 * Avec le GUI, le header peut partir immédiatement après START.
 */
#define APP_HEADER_DELAY_MS                 0U

#define APP_DATALOG_TX_BUFFER_SIZE          4096U
#define APP_DATALOG_UART_PUMP_MAX_BYTES     128U
#define APP_DATALOG_LINE_SIZE               512U

/*
 * ================================
 * DS18B20 BACKGROUND
 * ================================
 */

static const DatalogModule_t *ds18b20_module = &DS18B20_Module;

typedef enum
{
  APP_DS18B20_IDLE = 0,
  APP_DS18B20_WAIT_CONVERSION
} AppDs18b20State_t;

static AppDs18b20State_t ds18b20_state = APP_DS18B20_IDLE;

static uint32_t ds18b20_next_start_ms = 0U;
static uint32_t ds18b20_conversion_ready_ms = 0U;

static char ds18b20_cached_value[32] = "nan";
static bool ds18b20_has_value = false;

static bool d6t_last_present = false;

/*
 * ================================
 * ETAT DATALOG
 * ================================
 */

static bool logging_enabled = false;
static bool header_sent = false;

static uint32_t header_due_ms = 0U;
static uint32_t next_data_ms = 0U;

static uint32_t app_datalog_period_ms = APP_DATALOG_DEFAULT_PERIOD_MS;
static uint32_t app_ds18b20_period_ms = APP_DS18B20_DEFAULT_PERIOD_MS;

/*
 * ================================
 * UART TX NON BLOQUANT
 * ================================
 */

static char tx_buffer[APP_DATALOG_TX_BUFFER_SIZE];
static volatile uint16_t tx_head = 0U;
static volatile uint16_t tx_tail = 0U;
static volatile uint32_t tx_overflow_count = 0U;

static void AppDatalog_TakeOverUsart1(void)
{
  /*
   * USART1 doit être libre :
   * - Motor Pilot / ASPEP désactivé
   * - AppMcDebug désactivé
   */

  LL_USART_DisableDMAReq_TX(USART1);
  LL_USART_DisableDMAReq_RX(USART1);

  LL_USART_DisableIT_TC(USART1);
  LL_USART_DisableIT_IDLE(USART1);
  LL_USART_DisableIT_ERROR(USART1);

  LL_DMA_DisableChannel(DMA1, LL_DMA_CHANNEL_1); /* USART1_RX */
  LL_DMA_DisableChannel(DMA1, LL_DMA_CHANNEL_2); /* USART1_TX */

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

static void AppDatalog_ResetTxQueue(void)
{
  tx_head = 0U;
  tx_tail = 0U;
  tx_overflow_count = 0U;
}

static bool AppDatalog_TxQueueChar(char c)
{
  uint16_t next = (uint16_t)((tx_head + 1U) % APP_DATALOG_TX_BUFFER_SIZE);

  if (next == tx_tail)
  {
    tx_overflow_count++;
    return false;
  }

  tx_buffer[tx_head] = c;
  tx_head = next;

  return true;
}

static bool AppDatalog_UartQueueText(const char *text)
{
  bool ok = true;

  if (text == NULL)
  {
    return false;
  }

  while (*text != '\0')
  {
    if (!AppDatalog_TxQueueChar(*text))
    {
      ok = false;
      break;
    }

    text++;
  }

  return ok;
}

static void AppDatalog_UartPump(void)
{
  uint32_t sent = 0U;

  while ((tx_tail != tx_head) &&
         (sent < APP_DATALOG_UART_PUMP_MAX_BYTES) &&
         LL_USART_IsActiveFlag_TXE_TXFNF(USART1))
  {
    LL_USART_TransmitData8(USART1, (uint8_t)tx_buffer[tx_tail]);

    tx_tail = (uint16_t)((tx_tail + 1U) % APP_DATALOG_TX_BUFFER_SIZE);
    sent++;
  }
}

bool AppDatalog_SendText(const char *text)
{
  bool ok = AppDatalog_UartQueueText(text);
  AppDatalog_UartPump();
  return ok;
}

/*
 * ================================
 * HELPERS CSV
 * ================================
 */

static void AppDatalog_LineAppend(char *line,
                                  size_t line_size,
                                  size_t *used,
                                  const char *fmt,
                                  ...)
{
  va_list args;
  int written;

  if ((line == NULL) || (used == NULL) || (fmt == NULL))
  {
    return;
  }

  if (*used >= line_size)
  {
    return;
  }

  va_start(args, fmt);
  written = vsnprintf(&line[*used], line_size - *used, fmt, args);
  va_end(args);

  if (written < 0)
  {
    return;
  }

  if ((size_t)written >= (line_size - *used))
  {
    *used = line_size - 1U;
  }
  else
  {
    *used += (size_t)written;
  }
}

static int32_t AppDatalog_FloatToMilli(float x)
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

static void AppDatalog_LineAppendMilliCsv(char *line,
                                          size_t line_size,
                                          size_t *used,
                                          int32_t milli)
{
  const char *sign = "";
  int32_t value = milli;

  if (value < 0)
  {
    sign = "-";
    value = -value;
  }

  AppDatalog_LineAppend(line,
                        line_size,
                        used,
                        ",%s%ld.%03ld",
                        sign,
                        (long)(value / 1000),
                        (long)(value % 1000));
}

/*
 * ================================
 * PERIODES RUNTIME
 * ================================
 */

static uint32_t AppDatalog_ClampDs18b20Period(uint32_t requested_ms)
{
  uint32_t min_ms = ds18b20_module->conversion_time_ms;

  if (requested_ms < min_ms)
  {
    return min_ms;
  }

  return requested_ms;
}

void AppDatalog_SetRuntimePeriods(uint32_t datalog_ms,
                                  uint32_t ds18b20_ms)
{
  if (datalog_ms == 0U)
  {
    datalog_ms = 1U;
  }

  app_datalog_period_ms = datalog_ms;
  app_ds18b20_period_ms = AppDatalog_ClampDs18b20Period(ds18b20_ms);
}

/*
 * ================================
 * HEADER / DATA
 * ================================
 */

static void AppDatalog_SendHeader(void)
{
  AppDatalog_UartQueueText("#LOG_START\r\n");

  AppDatalog_UartQueueText(
    "#CSV_HEADER,"
    "stm32_time_ms,"
    "d6t_temp_c,"
    "ds18b20_temp_c,"
    "motor_ud_v,"
    "motor_uq_v,"
    "motor_speed_mech_rpm,"
    "motor_id_a,"
    "motor_iq_a\r\n"
  );
}

static void AppDatalog_SendDataLine(uint32_t now)
{
  char line[APP_DATALOG_LINE_SIZE];
  size_t used = 0U;
  float ud_v = 0.0f;
  float uq_v = 0.0f;
  float speed_mech_rpm = 0.0f;
  dq_float_t idq = { .D = 0.0f, .Q = 0.0f };

  /*
   * Ddq_out_pu contient la sortie réelle du régulateur courant sous forme
   * de duty-cycle d/q, exprimée dans l'échelle PWM [-1 ; +1].
   *
   * CurrCtrl_M1.Udq_in_pu existe dans la structure MCSDK, mais dans ce projet
   * HSO elle n'est pas alimentée par MC_currentController(), ce qui donne 0.0 V.
   * On reconstruit donc la tension d/q appliquée à partir du duty d/q et de Vbus.
   *
   * D'après l'échelle PWM du SDK :
   *   +1.0 = 100 % high, -1.0 = 100 % low, 0.0 = point milieu.
   * La tension phase-neutre équivalente vaut donc environ duty * Vbus / 2.
   */
  /* En STOP/IDLE, les structures MCSDK conservent parfois le dernier échantillon
   * calculé. Ces valeurs seraient fausses dans un dataset de refroidissement.
   * On publie donc explicitement 0 pour les grandeurs électriques tant que le
   * moteur n'est pas réellement en RUN. */
  if (AppMotorControl_IsRunning())
  {
    Duty_Ddq_t ddq_pu = CurrCtrl_M1.Ddq_out_pu;
    float vbus_v = FIXP30_toF(VBus_M1.Udcbus_in_pu) * VOLTAGE_SCALE;
    float speed_elec_hz = MC_GetSpeedMotor1_F();

    ud_v = FIXP30_toF(ddq_pu.D) * vbus_v * 0.5f;
    uq_v = FIXP30_toF(ddq_pu.Q) * vbus_v * 0.5f;
    speed_mech_rpm = (speed_elec_hz * 60.0f) / (float)POLE_PAIR_NUM;
    idq = MC_GetCurrentMotor1_F();
  }

  AppDatalog_LineAppend(line,
                        sizeof(line),
                        &used,
                        "DATA,%lu,%s,%s",
                        (unsigned long)now,
                        D6TIR_GetCsvValue(),
                        ds18b20_cached_value);

  AppDatalog_LineAppendMilliCsv(line, sizeof(line), &used, AppDatalog_FloatToMilli(ud_v));
  AppDatalog_LineAppendMilliCsv(line, sizeof(line), &used, AppDatalog_FloatToMilli(uq_v));
  AppDatalog_LineAppendMilliCsv(line, sizeof(line), &used, AppDatalog_FloatToMilli(speed_mech_rpm));
  AppDatalog_LineAppendMilliCsv(line, sizeof(line), &used, AppDatalog_FloatToMilli(idq.D));
  AppDatalog_LineAppendMilliCsv(line, sizeof(line), &used, AppDatalog_FloatToMilli(idq.Q));

  AppDatalog_LineAppend(line, sizeof(line), &used, "\r\n");

  AppDatalog_UartQueueText(line);
}

static void AppDatalog_D6tIrStatusTask(void)
{
  bool present = D6TIR_IsPresent();

  if (present == d6t_last_present)
  {
    return;
  }

  if (present)
  {
    AppDatalog_UartQueueText("#INFO,D6T_IR_DETECTED,d6t_temp_c_active\r\n");
  }
  else
  {
    AppDatalog_UartQueueText("#WARN,D6T_IR_NOT_DETECTED,d6t_temp_c=NaN\r\n");
  }

  d6t_last_present = present;
}

/*
 * ================================
 * DS18B20 TASK
 * ================================
 */

static void AppDatalog_Ds18b20Task(uint32_t now)
{
  char value[32];

  switch (ds18b20_state)
  {
    case APP_DS18B20_IDLE:
      if ((int32_t)(now - ds18b20_next_start_ms) >= 0)
      {
        bool ok = ds18b20_module->start_measurement();

        if (ok)
        {
          ds18b20_conversion_ready_ms = now + ds18b20_module->conversion_time_ms;

          /*
           * On planifie déjà le prochain démarrage pour avoir une vraie période
           * start-to-start.
           */
          ds18b20_next_start_ms = now + app_ds18b20_period_ms;

          ds18b20_state = APP_DS18B20_WAIT_CONVERSION;
        }
        else
        {
          ds18b20_next_start_ms = now + app_ds18b20_period_ms;
        }
      }
      break;

    case APP_DS18B20_WAIT_CONVERSION:
      if ((int32_t)(now - ds18b20_conversion_ready_ms) >= 0)
      {
        bool ok = ds18b20_module->read_csv_values(value, sizeof(value));

        if (ok)
        {
          snprintf(ds18b20_cached_value,
                   sizeof(ds18b20_cached_value),
                   "%s",
                   value);

          ds18b20_has_value = true;
        }
        else
        {
          if (!ds18b20_has_value)
          {
            snprintf(ds18b20_cached_value,
                     sizeof(ds18b20_cached_value),
                     "nan");
          }
        }

        ds18b20_state = APP_DS18B20_IDLE;
      }
      break;

    default:
      ds18b20_state = APP_DS18B20_IDLE;
      break;
  }
}

/*
 * ================================
 * START / STOP LOGGING
 * ================================
 */

void AppDatalog_StartLogging(void)
{
  uint32_t now = HAL_GetTick();

  AppDatalog_ResetTxQueue();

  logging_enabled = true;
  header_sent = false;

  header_due_ms = now + APP_HEADER_DELAY_MS;
  next_data_ms = header_due_ms;

  AppDatalog_UartQueueText("#LOG_ARMED\r\n");
  AppDatalog_UartPump();
}

void AppDatalog_StopLogging(void)
{
  logging_enabled = false;
  header_sent = false;

  AppDatalog_ResetTxQueue();

  AppDatalog_UartQueueText("#LOG_STOP\r\n");
  AppDatalog_UartPump();
}

bool AppDatalog_IsLogging(void)
{
  return logging_enabled;
}

/*
 * ================================
 * INIT / TASK
 * ================================
 */

void AppDatalog_Init(void)
{
  uint32_t now;

  AppDatalog_TakeOverUsart1();
  AppDatalog_ResetTxQueue();

  D6TIR_Init();
  ds18b20_module->init();

  AppDatalog_SetRuntimePeriods(APP_DATALOG_DEFAULT_PERIOD_MS,
                               APP_DS18B20_DEFAULT_PERIOD_MS);

  now = HAL_GetTick();

  logging_enabled = false;
  header_sent = false;

  ds18b20_state = APP_DS18B20_IDLE;
  ds18b20_next_start_ms = now;
  ds18b20_conversion_ready_ms = 0U;

  snprintf(ds18b20_cached_value, sizeof(ds18b20_cached_value), "nan");
  ds18b20_has_value = false;
  d6t_last_present = D6TIR_IsPresent();

  AppDatalog_UartQueueText("#BOOT_APP_DATALOG\r\n");
  AppDatalog_UartQueueText("#USART1_TAKEN_OVER_BY_DATALOG\r\n");
  AppDatalog_UartQueueText("#D6T_IR_I2C_SOFT,PB6=SCL,PB9=SDA,ADDR=0x0A\r\n");
  if (!d6t_last_present)
  {
    AppDatalog_UartQueueText("#WARN,D6T_IR_NOT_DETECTED,d6t_temp_c=NaN\r\n");
  }
  AppDatalog_UartQueueText("#WAITING_FOR_GUI_COMMANDS\r\n");

  AppDatalog_UartPump();
}

void AppDatalog_Task(void)
{
  uint32_t now = HAL_GetTick();

  AppDatalog_UartPump();

  /*
   * Le DS18B20 continue en arrière-plan pour garder une valeur fraîche
   * dès que possible, même avant START.
   */
  D6TIR_Task(now);
  AppDatalog_D6tIrStatusTask();
  AppDatalog_Ds18b20Task(now);

  if (!logging_enabled)
  {
    return;
  }

  if (!header_sent)
  {
    if ((int32_t)(now - header_due_ms) >= 0)
    {
      AppDatalog_SendHeader();

      header_sent = true;
      next_data_ms = now;

      AppDatalog_UartPump();
    }

    return;
  }

  if ((int32_t)(now - next_data_ms) >= 0)
  {
    AppDatalog_SendDataLine(now);

    next_data_ms += app_datalog_period_ms;

    if ((int32_t)(now - next_data_ms) >= 0)
    {
      next_data_ms = now + app_datalog_period_ms;
    }

    AppDatalog_UartPump();
  }
}
