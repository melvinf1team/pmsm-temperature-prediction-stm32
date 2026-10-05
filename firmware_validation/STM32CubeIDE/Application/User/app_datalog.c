#include "app_datalog.h"
#include "app_ai_model.h"
#include "app_config.h"
#include "app_motor_control.h"
#include "d6t_ir.h"
#include "ds18b20.h"
#include "main.h"
#include "preprocess_ewma.h"

#include "drive_parameters.h"
#include "fixpmath.h"
#include "mc_api.h"
#include "mc_config.h"
#include "mc_interface.h"
#include "mc_type.h"
#include "pmsm_motor_parameters.h"

#include "stm32g4xx_ll_dma.h"
#include "stm32g4xx_ll_usart.h"

#include <math.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define APP_DS18B20_PERIOD_MS            1000U
#define APP_DATALOG_TX_BUFFER_SIZE       4096U
#define APP_DATALOG_UART_PUMP_MAX_BYTES  256U
#define APP_DATALOG_LINE_SIZE            1024U
#define APP_DATALOG_MAX_FLOAT_WHOLE      4294967040.0f
#define APP_DATALOG_RX_QUEUE_SIZE          256U
#define APP_DATALOG_RX_LINE_SIZE            64U
#define APP_EWMA_SNAPSHOT_MAGIC           0x45574D41UL
#define APP_EWMA_SNAPSHOT_VERSION         2UL
#define APP_EWMA_SNAPSHOT_COUNT           2U

_Static_assert(PREPROCESS_EWMA_OUTPUT_COUNT == 55U,
               "Le pipeline NanoEdge attend exactement 55 features");

typedef enum
{
  APP_DS18B20_IDLE = 0,
  APP_DS18B20_WAIT_CONVERSION
} AppDs18b20State_t;

static AppDs18b20State_t ds18b20_state = APP_DS18B20_IDLE;
static uint32_t ds18b20_next_start_ms = 0U;
static uint32_t ds18b20_conversion_ready_ms = 0U;
static uint32_t next_data_ms = 0U;

static PreprocessEwmaContext_t preprocess_context;
static float preprocess_features[PREPROCESS_EWMA_OUTPUT_COUNT];
static char app_datalog_line[APP_DATALOG_LINE_SIZE];

typedef struct
{
  uint32_t magic;
  uint32_t version;
  uint32_t context_size;
  uint32_t sequence;
  PreprocessEwmaContext_t context;
  uint32_t crc32;
} AppEwmaSnapshot_t;

static volatile AppEwmaSnapshot_t ewma_snapshots[APP_EWMA_SNAPSHOT_COUNT]
  __attribute__((section(".noinit.ewma"), aligned(8), used));
static uint32_t ewma_snapshot_sequence = 0U;

static char tx_buffer[APP_DATALOG_TX_BUFFER_SIZE];
static volatile uint16_t tx_head = 0U;
static volatile uint16_t tx_tail = 0U;

static volatile uint8_t rx_queue[APP_DATALOG_RX_QUEUE_SIZE];
static volatile uint16_t rx_head = 0U;
static volatile uint16_t rx_tail = 0U;
static char rx_line[APP_DATALOG_RX_LINE_SIZE];
static uint16_t rx_index = 0U;

static uint32_t AppDatalog_Crc32Update(uint32_t crc,
                                      const volatile void *data,
                                      size_t size)
{
  const volatile uint8_t *bytes = (const volatile uint8_t *)data;

  for (size_t index = 0U; index < size; index++)
  {
    crc ^= bytes[index];
    for (uint32_t bit = 0U; bit < 8U; bit++)
    {
      crc = ((crc & 1U) != 0U)
              ? ((crc >> 1U) ^ 0xEDB88320UL)
              : (crc >> 1U);
    }
  }

  return crc;
}

static uint32_t AppDatalog_EwmaSnapshotCrc(
  const volatile AppEwmaSnapshot_t *snapshot)
{
  uint32_t crc = 0xFFFFFFFFUL;

  crc = AppDatalog_Crc32Update(crc,
                               &snapshot->sequence,
                               sizeof(snapshot->sequence));
  crc = AppDatalog_Crc32Update(crc,
                               &snapshot->context,
                               sizeof(snapshot->context));
  return crc ^ 0xFFFFFFFFUL;
}

static bool AppDatalog_EwmaSnapshotIsValid(
  const volatile AppEwmaSnapshot_t *snapshot)
{
  return (snapshot->magic == APP_EWMA_SNAPSHOT_MAGIC) &&
         (snapshot->version == APP_EWMA_SNAPSHOT_VERSION) &&
         (snapshot->context_size == sizeof(PreprocessEwmaContext_t)) &&
         (snapshot->crc32 == AppDatalog_EwmaSnapshotCrc(snapshot));
}

static bool AppDatalog_RestoreEwma(void)
{
  bool valid_0 = AppDatalog_EwmaSnapshotIsValid(&ewma_snapshots[0]);
  bool valid_1 = AppDatalog_EwmaSnapshotIsValid(&ewma_snapshots[1]);
  const volatile AppEwmaSnapshot_t *selected;

  if (!valid_0 && !valid_1)
  {
    ewma_snapshots[0].magic = 0U;
    ewma_snapshots[1].magic = 0U;
    ewma_snapshot_sequence = 0U;
    return false;
  }

  if (valid_0 && valid_1)
  {
    selected = ((int32_t)(ewma_snapshots[1].sequence -
                          ewma_snapshots[0].sequence) > 0)
                 ? &ewma_snapshots[1]
                 : &ewma_snapshots[0];
  }
  else
  {
    selected = valid_1 ? &ewma_snapshots[1] : &ewma_snapshots[0];
  }

  preprocess_context = selected->context;
  ewma_snapshot_sequence = selected->sequence;
  return true;
}

static void AppDatalog_CheckpointEwma(void)
{
  uint32_t next_sequence = ewma_snapshot_sequence + 1U;
  volatile AppEwmaSnapshot_t *target =
    &ewma_snapshots[next_sequence % APP_EWMA_SNAPSHOT_COUNT];

  target->magic = 0U;
  __DMB();

  target->version = APP_EWMA_SNAPSHOT_VERSION;
  target->context_size = sizeof(PreprocessEwmaContext_t);
  target->sequence = next_sequence;
  target->context = preprocess_context;
  target->crc32 = AppDatalog_EwmaSnapshotCrc(target);

  __DMB();
  target->magic = APP_EWMA_SNAPSHOT_MAGIC;
  __DMB();

  ewma_snapshot_sequence = next_sequence;
}

static void AppDatalog_TakeOverUsart1(void)
{
  LL_USART_DisableDMAReq_TX(USART1);
  LL_USART_DisableDMAReq_RX(USART1);

  LL_USART_DisableIT_RXNE_RXFNE(USART1);
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

  HAL_NVIC_DisableIRQ(USART1_IRQn);
}

static uint16_t AppDatalog_TxQueueFree(void)
{
  if (tx_head >= tx_tail)
  {
    return (uint16_t)(APP_DATALOG_TX_BUFFER_SIZE -
                      (tx_head - tx_tail) -
                      1U);
  }

  return (uint16_t)(tx_tail - tx_head - 1U);
}

static bool AppDatalog_UartQueueText(const char *text)
{
  size_t text_length;

  if (text == NULL)
  {
    return false;
  }

  text_length = strlen(text);
  if (text_length > AppDatalog_TxQueueFree())
  {
    return false;
  }

  while (*text != '\0')
  {
    tx_buffer[tx_head] = *text;
    tx_head = (uint16_t)((tx_head + 1U) % APP_DATALOG_TX_BUFFER_SIZE);
    text++;
  }

  return true;
}

static void AppDatalog_RxPush(uint8_t value)
{
  uint16_t next = (uint16_t)((rx_head + 1U) % APP_DATALOG_RX_QUEUE_SIZE);

  if (next != rx_tail)
  {
    rx_queue[rx_head] = value;
    rx_head = next;
  }
}

static bool AppDatalog_RxPop(uint8_t *value)
{
  if ((value == NULL) || (rx_tail == rx_head))
  {
    return false;
  }

  *value = rx_queue[rx_tail];
  rx_tail = (uint16_t)((rx_tail + 1U) % APP_DATALOG_RX_QUEUE_SIZE);
  return true;
}

void AppDatalog_OnUsart1Irq(void)
{
  if (LL_USART_IsActiveFlag_ORE(USART1))
  {
    LL_USART_ClearFlag_ORE(USART1);
  }
  if (LL_USART_IsActiveFlag_FE(USART1))
  {
    LL_USART_ClearFlag_FE(USART1);
  }
  if (LL_USART_IsActiveFlag_NE(USART1))
  {
    LL_USART_ClearFlag_NE(USART1);
  }

  while (LL_USART_IsActiveFlag_RXNE_RXFNE(USART1))
  {
    AppDatalog_RxPush(LL_USART_ReceiveData8(USART1));
  }
}

static bool AppDatalog_ParseFloat(const char *text, float *value)
{
  const char *cursor = text;
  uint32_t integer = 0U;
  uint32_t fraction = 0U;
  uint32_t scale = 1U;
  bool has_digit = false;

  if ((text == NULL) || (value == NULL))
  {
    return false;
  }

  while ((*cursor >= '0') && (*cursor <= '9'))
  {
    has_digit = true;
    integer = (integer * 10U) + (uint32_t)(*cursor - '0');
    cursor++;
  }

  if (*cursor == '.')
  {
    cursor++;
    while ((*cursor >= '0') && (*cursor <= '9'))
    {
      has_digit = true;
      if (scale < 1000000U)
      {
        fraction = (fraction * 10U) + (uint32_t)(*cursor - '0');
        scale *= 10U;
      }
      cursor++;
    }
  }

  while ((*cursor == ' ') || (*cursor == '\t'))
  {
    cursor++;
  }

  if (!has_digit || (*cursor != '\0'))
  {
    return false;
  }

  *value = (float)integer + ((float)fraction / (float)scale);
  return true;
}

static bool AppDatalog_ParseProfile(const char *token,
                                    AppMotorProfile_t *profile,
                                    const char **ack_text)
{
  if ((token == NULL) || (profile == NULL) || (ack_text == NULL))
  {
    return false;
  }

  if (strcmp(token, "STABLE") == 0)
  {
    *profile = APP_MOTOR_PROFILE_STABLE;
    *ack_text = "ACK,PROFILE,STABLE\r\n";
    return true;
  }
  if (strcmp(token, "VARIABLE_LOAD") == 0)
  {
    *profile = APP_MOTOR_PROFILE_VARIABLE_LOAD;
    *ack_text = "ACK,PROFILE,VARIABLE_LOAD\r\n";
    return true;
  }
  if (strcmp(token, "VARIABLE_SPEED") == 0)
  {
    *profile = APP_MOTOR_PROFILE_VARIABLE_SPEED;
    *ack_text = "ACK,PROFILE,VARIABLE_SPEED\r\n";
    return true;
  }
  if (strcmp(token, "VARIABLE_ALL") == 0)
  {
    *profile = APP_MOTOR_PROFILE_VARIABLE_ALL;
    *ack_text = "ACK,PROFILE,VARIABLE_ALL\r\n";
    return true;
  }

  return false;
}

static void AppDatalog_HandleCommand(char *line)
{
  float load_a;
  bool ok;
  AppMotorProfile_t profile;
  AppMotorProfileStartResult_t profile_result;
  const char *profile_ack;

  if (strcmp(line, "STOP") == 0)
  {
    AppMotorControl_Stop();
    (void)AppDatalog_UartQueueText("ACK,STOP\r\n");
    return;
  }
  if (strncmp(line, "PROFILE,", 8U) == 0)
  {
    if (!AppDatalog_ParseProfile(&line[8], &profile, &profile_ack))
    {
      (void)AppDatalog_UartQueueText("ERR,BAD_PROFILE\r\n");
      return;
    }

    profile_result = AppMotorControl_StartProfile(profile);
    if (profile_result == APP_MOTOR_PROFILE_START_ACCEPTED)
    {
      (void)AppDatalog_UartQueueText(profile_ack);
    }
    else if (profile_result == APP_MOTOR_PROFILE_START_LOAD_ERROR)
    {
      (void)AppDatalog_UartQueueText("ERR,LOAD_DAC_FAILED\r\n");
    }
    else
    {
      (void)AppDatalog_UartQueueText("ERR,PROFILE_REJECTED\r\n");
    }
    return;
  }
  if (strcmp(line, "PROFILE") == 0)
  {
    (void)AppDatalog_UartQueueText("ERR,BAD_PROFILE\r\n");
    return;
  }
  if (strcmp(line, "LOAD,VARIABLE") == 0)
  {
    ok = AppMotorControl_SetLoadVariable();
  }
  else if (strncmp(line, "LOAD,", 5U) == 0)
  {
    if (!AppDatalog_ParseFloat(&line[5], &load_a))
    {
      (void)AppDatalog_UartQueueText("ERR,BAD_LOAD\r\n");
      return;
    }
    if ((load_a < APP_TB200S_MIN_LOAD_A) ||
        (load_a > APP_TB200S_MAX_LOAD_A))
    {
      (void)AppDatalog_UartQueueText("ERR,LOAD_VALUE_OUT_OF_RANGE\r\n");
      return;
    }
    ok = AppMotorControl_SetLoadFixed(load_a);
  }
  else
  {
    (void)AppDatalog_UartQueueText("ERR,UNKNOWN_COMMAND\r\n");
    return;
  }

  (void)AppDatalog_UartQueueText(ok
    ? "ACK,LOAD\r\n"
    : "ERR,LOAD_DAC_FAILED\r\n");
}

static void AppDatalog_SerialTask(void)
{
  uint8_t value;

  while (AppDatalog_RxPop(&value))
  {
    if (value == '\r')
    {
      continue;
    }
    if (value == '\n')
    {
      rx_line[rx_index] = '\0';
      AppDatalog_HandleCommand(rx_line);
      rx_index = 0U;
      continue;
    }

    if ((value >= 32U) && (value <= 126U))
    {
      if (rx_index < (APP_DATALOG_RX_LINE_SIZE - 1U))
      {
        rx_line[rx_index++] = (char)value;
      }
      else
      {
        rx_index = 0U;
        (void)AppDatalog_UartQueueText("ERR,RX_LINE_TOO_LONG\r\n");
      }
    }
  }
}

static void AppDatalog_UartRecover(void)
{
  if (!LL_USART_IsEnabled(USART1))
  {
    LL_USART_Enable(USART1);
  }

  if (LL_USART_IsActiveFlag_ORE(USART1))
  {
    LL_USART_ClearFlag_ORE(USART1);
  }
  if (LL_USART_IsActiveFlag_FE(USART1))
  {
    LL_USART_ClearFlag_FE(USART1);
  }
  if (LL_USART_IsActiveFlag_NE(USART1))
  {
    LL_USART_ClearFlag_NE(USART1);
  }
}

static void AppDatalog_UartPump(void)
{
  uint32_t sent = 0U;

  AppDatalog_UartRecover();

  while ((tx_tail != tx_head) &&
         (sent < APP_DATALOG_UART_PUMP_MAX_BYTES) &&
         LL_USART_IsActiveFlag_TXE_TXFNF(USART1))
  {
    LL_USART_TransmitData8(USART1, (uint8_t)tx_buffer[tx_tail]);
    tx_tail = (uint16_t)((tx_tail + 1U) % APP_DATALOG_TX_BUFFER_SIZE);
    sent++;
  }
}

static void AppDatalog_LineAppend(char *line,
                                  size_t line_size,
                                  size_t *used,
                                  const char *format,
                                  ...)
{
  va_list args;
  int written;

  if ((line == NULL) || (used == NULL) || (format == NULL) || (*used >= line_size))
  {
    return;
  }

  va_start(args, format);
  written = vsnprintf(&line[*used], line_size - *used, format, args);
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

static bool AppDatalog_IsFinite(float value)
{
  uint32_t bits;

  memcpy(&bits, &value, sizeof(bits));
  return (bits & 0x7F800000UL) != 0x7F800000UL;
}

static void AppDatalog_LineAppendFloat(char *line,
                                       size_t line_size,
                                       size_t *used,
                                       const char *separator,
                                       float value)
{
  const char *sign = "";
  float absolute_value;
  uint32_t whole;
  uint32_t fraction;

  if (!AppDatalog_IsFinite(value))
  {
    value = 0.0f;
  }

  if (value < 0.0f)
  {
    sign = "-";
  }

  absolute_value = fabsf(value);
  if (absolute_value > APP_DATALOG_MAX_FLOAT_WHOLE)
  {
    absolute_value = 0.0f;
    sign = "";
  }

  whole = (uint32_t)absolute_value;
  fraction = (uint32_t)(((absolute_value - (float)whole) * 1000000.0f) + 0.5f);

  if (fraction >= 1000000U)
  {
    whole++;
    fraction = 0U;
  }

  AppDatalog_LineAppend(line,
                        line_size,
                        used,
                        "%s%s%lu.%06lu",
                        separator,
                        sign,
                        (unsigned long)whole,
                        (unsigned long)fraction);
}

static void AppDatalog_Ds18b20Task(uint32_t now)
{
  switch (ds18b20_state)
  {
    case APP_DS18B20_IDLE:
      if ((int32_t)(now - ds18b20_next_start_ms) >= 0)
      {
        if (DS18B20_StartMeasurement())
        {
          ds18b20_conversion_ready_ms = now + DS18B20_CONVERSION_TIME_MS;
          ds18b20_state = APP_DS18B20_WAIT_CONVERSION;
        }

        ds18b20_next_start_ms = now + APP_DS18B20_PERIOD_MS;
      }
      break;

    case APP_DS18B20_WAIT_CONVERSION:
      if ((int32_t)(now - ds18b20_conversion_ready_ms) >= 0)
      {
        (void)DS18B20_ReadMeasurement();
        ds18b20_state = APP_DS18B20_IDLE;
      }
      break;

    default:
      ds18b20_state = APP_DS18B20_IDLE;
      break;
  }
}

static void AppDatalog_ReadMotorSignals(PreprocessEwmaInput_t *input)
{
  input->motor_ud_v = 0.0f;
  input->motor_uq_v = 0.0f;
  input->motor_speed_mech_rpm = 0.0f;
  input->motor_id_a = 0.0f;
  input->motor_iq_a = 0.0f;

  if (AppMotorControl_IsRunning())
  {
    Duty_Ddq_t duty_dq = CurrCtrl_M1.Ddq_out_pu;
    dq_float_t current_dq = MC_GetCurrentMotor1_F();
    float bus_voltage_v = FIXP30_toF(VBus_M1.Udcbus_in_pu) * VOLTAGE_SCALE;
    float electrical_speed_hz = MC_GetSpeedMotor1_F();

    input->motor_ud_v = FIXP30_toF(duty_dq.D) * bus_voltage_v * 0.5f;
    input->motor_uq_v = FIXP30_toF(duty_dq.Q) * bus_voltage_v * 0.5f;
    input->motor_speed_mech_rpm =
      (electrical_speed_hz * 60.0f) / (float)POLE_PAIR_NUM;
    input->motor_id_a = current_dq.D;
    input->motor_iq_a = current_dq.Q;
  }
}

static void AppDatalog_SendDataLine(void)
{
  PreprocessEwmaInput_t input;
  size_t used = 0U;

  if (!DS18B20_GetLastTemperatureC(&input.ds18b20_temp_c))
  {
    input.ds18b20_temp_c = NAN;
  }

  AppDatalog_ReadMotorSignals(&input);

  if (!PreprocessEwma_Process(&preprocess_context, &input, preprocess_features))
  {
    return;
  }

  AppDatalog_CheckpointEwma();

#if APP_NEAI_MODEL_ENABLED
  float actual_temperature_c;
  float predicted_temperature_c;

  if (!D6TIR_GetTemperatureC(&actual_temperature_c) ||
      !AppAiModel_Predict(preprocess_features, &predicted_temperature_c))
  {
    return;
  }

  AppDatalog_LineAppendFloat(app_datalog_line,
                             sizeof(app_datalog_line),
                             &used,
                             "",
                             actual_temperature_c);
  AppDatalog_LineAppendFloat(app_datalog_line,
                             sizeof(app_datalog_line),
                             &used,
                             ";",
                             predicted_temperature_c);
  AppDatalog_LineAppendFloat(app_datalog_line,
                             sizeof(app_datalog_line),
                             &used,
                             ";",
                             AppMotorControl_GetLoadSetpointA());
#else
  for (size_t index = 0U; index < PREPROCESS_EWMA_OUTPUT_COUNT; index++)
  {
    AppDatalog_LineAppendFloat(app_datalog_line,
                               sizeof(app_datalog_line),
                               &used,
                               (index == 0U) ? "" : ";",
                               preprocess_features[index]);
  }
#endif

  AppDatalog_LineAppend(app_datalog_line,
                        sizeof(app_datalog_line),
                        &used,
                        "\r\n");
  (void)AppDatalog_UartQueueText(app_datalog_line);
}

void AppDatalog_Init(void)
{
  uint32_t now;

  AppDatalog_TakeOverUsart1();
  tx_head = 0U;
  tx_tail = 0U;
  rx_head = 0U;
  rx_tail = 0U;
  rx_index = 0U;

  D6TIR_Init();
  DS18B20_Init();
  if (!AppDatalog_RestoreEwma())
  {
    PreprocessEwma_Reset(&preprocess_context);
  }
  AppAiModel_Init();

  now = HAL_GetTick();
  ds18b20_state = APP_DS18B20_IDLE;
  ds18b20_next_start_ms = now;
  ds18b20_conversion_ready_ms = 0U;
  next_data_ms = now;

  LL_USART_ClearFlag_ORE(USART1);
  LL_USART_ClearFlag_FE(USART1);
  LL_USART_ClearFlag_NE(USART1);
  LL_USART_EnableIT_RXNE_RXFNE(USART1);
  LL_USART_EnableIT_ERROR(USART1);
  HAL_NVIC_EnableIRQ(USART1_IRQn);
}

void AppDatalog_Task(void)
{
  uint32_t now = HAL_GetTick();

  AppDatalog_UartPump();
  AppDatalog_SerialTask();
  D6TIR_Task(now);
  AppDatalog_Ds18b20Task(now);

  if ((int32_t)(now - next_data_ms) >= 0)
  {
    AppDatalog_SendDataLine();
    next_data_ms += PREPROCESS_EWMA_SAMPLE_PERIOD_MS;

    if ((int32_t)(now - next_data_ms) >= 0)
    {
      next_data_ms = now + PREPROCESS_EWMA_SAMPLE_PERIOD_MS;
    }

    AppDatalog_UartPump();
  }
}
