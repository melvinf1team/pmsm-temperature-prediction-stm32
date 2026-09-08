#include "app_datalog.h"
#include "main.h"
#include "ds18b20.h"
#include "datalog_module.h"

#include "stm32g4xx_ll_usart.h"
#include "stm32g4xx_ll_dma.h"

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#define APP_DATALOG_PERIOD_MS             1000U
#define APP_HEADER_DELAY_MS               1000U
#define APP_UART_TIMEOUT_MS               20U

static const DatalogModule_t *modules[] =
{
  &DS18B20_Module
};

#define MODULE_COUNT  (sizeof(modules) / sizeof(modules[0]))

typedef enum
{
  APP_DATALOG_IDLE = 0,
  APP_DATALOG_WAIT_CONVERSION
} AppDatalogState_t;

static AppDatalogState_t datalog_state = APP_DATALOG_IDLE;

static uint32_t next_sample_ms = 0;
static uint32_t conversion_ready_ms = 0;
static bool header_sent = false;
static uint32_t header_due_ms = 0;

static void AppDatalog_TakeOverUsart1(void)
{
  /*
   * Motor Control / ASPEP utilise USART1 avec DMA et interruptions.
   * Pour notre logger ASCII, on libère explicitement USART1.
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

static void AppDatalog_UartSend(const char *text)
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

static void AppDatalog_SendHeader(void)
{
  char line[256];
  size_t used = 0;

  AppDatalog_UartSend("#LOG_START\r\n");

  used += snprintf(&line[used], sizeof(line) - used, "#CSV_HEADER,stm32_time_ms");

  for (size_t i = 0; i < MODULE_COUNT; i++)
  {
    used += snprintf(&line[used], sizeof(line) - used, ",%s", modules[i]->csv_columns);
  }

  snprintf(&line[used], sizeof(line) - used, "\r\n");

  AppDatalog_UartSend(line);
}

static void AppDatalog_SendError(const char *msg)
{
  char line[128];

  snprintf(line, sizeof(line), "ERR,%s\r\n", msg);
  AppDatalog_UartSend(line);
}

static void AppDatalog_StartAllMeasurements(uint32_t now)
{
  uint32_t max_conversion_time_ms = 0;

  for (size_t i = 0; i < MODULE_COUNT; i++)
  {
    if (!modules[i]->start_measurement())
    {
      AppDatalog_SendError("module_start_failed");
    }

    if (modules[i]->conversion_time_ms > max_conversion_time_ms)
    {
      max_conversion_time_ms = modules[i]->conversion_time_ms;
    }
  }

  conversion_ready_ms = now + max_conversion_time_ms;
  datalog_state = APP_DATALOG_WAIT_CONVERSION;
}

static void AppDatalog_ReadAndSendAll(uint32_t now)
{
  char line[256];
  char value[48];
  size_t used = 0;

  used += snprintf(&line[used], sizeof(line) - used, "DATA,%lu", (unsigned long)now);

  for (size_t i = 0; i < MODULE_COUNT; i++)
  {
    bool ok = modules[i]->read_csv_values(value, sizeof(value));

    if (!ok)
    {
      AppDatalog_SendError("module_read_failed");
    }

    used += snprintf(&line[used], sizeof(line) - used, ",%s", value);
  }

  snprintf(&line[used], sizeof(line) - used, "\r\n");

  AppDatalog_UartSend(line);

  next_sample_ms += APP_DATALOG_PERIOD_MS;

  if ((int32_t)(now - next_sample_ms) > 0)
  {
    next_sample_ms = now + APP_DATALOG_PERIOD_MS;
  }

  datalog_state = APP_DATALOG_IDLE;
}

void AppDatalog_Init(void)
{
  AppDatalog_TakeOverUsart1();

  for (size_t i = 0; i < MODULE_COUNT; i++)
  {
    modules[i]->init();
  }

  uint32_t now = HAL_GetTick();

  datalog_state = APP_DATALOG_IDLE;

  header_sent = false;
  header_due_ms = now + APP_HEADER_DELAY_MS;

  next_sample_ms = header_due_ms;

  AppDatalog_UartSend("#BOOT_APP_DATALOG\r\n");
  AppDatalog_UartSend("#USART1_TAKEN_OVER_BY_DATALOG\r\n");
}

void AppDatalog_Task(void)
{
  uint32_t now = HAL_GetTick();

  /*
   * Header envoyé une seule fois, avec délai.
   * Aucune DATA ne peut sortir avant ce header.
   */
  if (!header_sent)
  {
    if ((int32_t)(now - header_due_ms) >= 0)
    {
      AppDatalog_SendHeader();

      header_sent = true;
      next_sample_ms = now;
    }

    return;
  }

  switch (datalog_state)
  {
    case APP_DATALOG_IDLE:
      if ((int32_t)(now - next_sample_ms) >= 0)
      {
        AppDatalog_StartAllMeasurements(now);
      }
      break;

    case APP_DATALOG_WAIT_CONVERSION:
      if ((int32_t)(now - conversion_ready_ms) >= 0)
      {
        AppDatalog_ReadAndSendAll(now);
      }
      break;

    default:
      datalog_state = APP_DATALOG_IDLE;
      break;
  }
}
