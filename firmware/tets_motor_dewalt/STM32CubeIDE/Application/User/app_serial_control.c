#include "app_serial_control.h"

#include "main.h"
#include "app_motor_control.h"
#include "app_datalog.h"

#include "stm32g4xx_ll_usart.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>

#define APP_SERIAL_RX_QUEUE_SIZE     512U
#define APP_SERIAL_RX_LINE_SIZE      192U

/*
 * Mettre 1 seulement si tu veux voir les commandes inconnues.
 * En mode normal, on ignore les déchets UART.
 */
#define APP_SERIAL_VERBOSE_UNKNOWN   0

/* Sécurités firmware : aucune commande PC corrompue ne doit pouvoir envoyer
 * une consigne dangereuse au moteur. Ajuste ces valeurs seulement après validation. */
#define APP_CFG_MIN_TARGET_RPM       100.0f
#define APP_CFG_MAX_TARGET_RPM       2000.0f
#define APP_CFG_MAX_IQ_LIMIT_A       11.0f
#define APP_CFG_MAX_HARD_LIMIT_A     11.0f
#define APP_CFG_MAX_ACCEL_HZ_S       2000.0f
#define APP_CFG_MIN_DATALOG_MS       1U
#define APP_CFG_MAX_DATALOG_MS       10000U
#define APP_CFG_MIN_DS18B20_MS       750U
#define APP_CFG_MAX_DS18B20_MS       10000U

static volatile uint8_t rx_queue[APP_SERIAL_RX_QUEUE_SIZE];
static volatile uint16_t rx_head = 0U;
static volatile uint16_t rx_tail = 0U;
static volatile uint32_t rx_overflow_count = 0U;
static volatile uint32_t rx_error_count = 0U;

static char rx_line[APP_SERIAL_RX_LINE_SIZE];
static uint16_t rx_index = 0U;

static bool cfg_received = false;

/*
 * ================================
 * UART RX QUEUE
 * ================================
 */

static void AppSerial_RxQueueReset(void)
{
  rx_head = 0U;
  rx_tail = 0U;
  rx_overflow_count = 0U;
  rx_error_count = 0U;

  rx_index = 0U;
  memset(rx_line, 0, sizeof(rx_line));
}

static void AppSerial_RxPush(uint8_t c)
{
  uint16_t next = (uint16_t)((rx_head + 1U) % APP_SERIAL_RX_QUEUE_SIZE);

  if (next == rx_tail)
  {
    rx_overflow_count++;
    return;
  }

  rx_queue[rx_head] = c;
  rx_head = next;
}

static bool AppSerial_RxPop(uint8_t *c)
{
  if (c == NULL)
  {
    return false;
  }

  if (rx_tail == rx_head)
  {
    return false;
  }

  *c = rx_queue[rx_tail];
  rx_tail = (uint16_t)((rx_tail + 1U) % APP_SERIAL_RX_QUEUE_SIZE);

  return true;
}

/*
 * Cette fonction est appelée depuis USART1_IRQHandler().
 * Elle doit rester courte : lire RX, pousser dans la queue, sortir.
 */
void AppSerialControl_OnUsart1Irq(void)
{
  if (LL_USART_IsActiveFlag_ORE(USART1))
  {
    LL_USART_ClearFlag_ORE(USART1);
    rx_error_count++;
  }

  if (LL_USART_IsActiveFlag_FE(USART1))
  {
    LL_USART_ClearFlag_FE(USART1);
    rx_error_count++;
  }

  if (LL_USART_IsActiveFlag_NE(USART1))
  {
    LL_USART_ClearFlag_NE(USART1);
    rx_error_count++;
  }

  while (LL_USART_IsActiveFlag_RXNE_RXFNE(USART1))
  {
    uint8_t c = LL_USART_ReceiveData8(USART1);
    AppSerial_RxPush(c);
  }
}

/*
 * ================================
 * TX HELPERS
 * ================================
 */

static void AppSerial_SendAck(const char *msg)
{
  AppDatalog_SendText("ACK,");
  AppDatalog_SendText(msg);
  AppDatalog_SendText("\r\n");
}

static void AppSerial_SendErr(const char *msg)
{
  AppDatalog_SendText("ERR,");
  AppDatalog_SendText(msg);
  AppDatalog_SendText("\r\n");
}

/*
 * ================================
 * PARSING HELPERS
 * ================================
 */

static void AppSerial_TrimInPlace(char *s)
{
  char *start;
  char *end;
  size_t len;

  if (s == NULL)
  {
    return;
  }

  start = s;

  while ((*start == ' ') || (*start == '\t') || (*start == '\r') || (*start == '\n'))
  {
    start++;
  }

  if (start != s)
  {
    memmove(s, start, strlen(start) + 1U);
  }

  len = strlen(s);

  while (len > 0U)
  {
    end = &s[len - 1U];

    if ((*end == ' ') || (*end == '\t') || (*end == '\r') || (*end == '\n'))
    {
      *end = '\0';
      len--;
    }
    else
    {
      break;
    }
  }
}

static char *AppSerial_FindCommand(char *line)
{
  char *p;

  if (line == NULL)
  {
    return NULL;
  }

  p = strstr(line, "SYNC");
  if (p != NULL)
  {
    return p;
  }

  p = strstr(line, "CFG,");
  if (p != NULL)
  {
    return p;
  }

  p = strstr(line, "START");
  if (p != NULL)
  {
    return p;
  }

  p = strstr(line, "STOP");
  if (p != NULL)
  {
    return p;
  }

  return NULL;
}


static void AppSerial_HandleSync(void)
{
  /*
   * Remise à plat côté carte avant une nouvelle session GUI.
   */
  AppMotorControl_Stop();
  AppDatalog_StopLogging();

  cfg_received = false;

  rx_index = 0U;
  memset(rx_line, 0, sizeof(rx_line));

  AppSerial_SendAck("SYNC");
}



static bool AppSerial_ExpectComma(char **p)
{
  if ((p == NULL) || (*p == NULL))
  {
    return false;
  }

  if (**p != ',')
  {
    return false;
  }

  (*p)++;
  return true;
}

static bool AppSerial_ParseFloat(char **p, float *out)
{
  char *s;
  int sign = 1;
  uint32_t int_part = 0U;
  uint32_t frac_part = 0U;
  uint32_t frac_scale = 1U;
  bool has_digit = false;
  float value;

  if ((p == NULL) || (*p == NULL) || (out == NULL))
  {
    return false;
  }

  s = *p;

  if (*s == '+')
  {
    s++;
  }
  else if (*s == '-')
  {
    sign = -1;
    s++;
  }

  while ((*s >= '0') && (*s <= '9'))
  {
    has_digit = true;
    int_part = (int_part * 10U) + (uint32_t)(*s - '0');
    s++;
  }

  if (*s == '.')
  {
    s++;

    while ((*s >= '0') && (*s <= '9'))
    {
      has_digit = true;

      if (frac_scale < 1000000U)
      {
        frac_part = (frac_part * 10U) + (uint32_t)(*s - '0');
        frac_scale *= 10U;
      }

      s++;
    }
  }

  if (!has_digit)
  {
    return false;
  }

  value = (float)int_part + ((float)frac_part / (float)frac_scale);

  if (sign < 0)
  {
    value = -value;
  }

  *out = value;
  *p = s;

  return true;
}

static bool AppSerial_ParseU32(char **p, uint32_t *out)
{
  char *endptr;
  unsigned long value;

  if ((p == NULL) || (*p == NULL) || (out == NULL))
  {
    return false;
  }

  value = strtoul(*p, &endptr, 10);

  if (endptr == *p)
  {
    return false;
  }

  *out = (uint32_t)value;
  *p = endptr;

  return true;
}

static bool AppSerial_ParseCfg(const char *line,
                               float *target_rpm,
                               float *iq_limit_a,
                               float *hard_limit_a,
                               float *accel_elec_hz_s,
                               uint32_t *datalog_ms,
                               uint32_t *ds18b20_ms)
{
  char *p = (char *)line;

  /*
   * Format attendu :
   * CFG,<target_rpm>,<iq_limit_a>,<hard_limit_a>,<accel_elec_hz_s>,<datalog_ms>,<ds18b20_ms>
   */

  if (strncmp(p, "CFG", 3U) != 0)
  {
    return false;
  }

  p += 3U;

  if (!AppSerial_ExpectComma(&p)) return false;
  if (!AppSerial_ParseFloat(&p, target_rpm)) return false;

  if (!AppSerial_ExpectComma(&p)) return false;
  if (!AppSerial_ParseFloat(&p, iq_limit_a)) return false;

  if (!AppSerial_ExpectComma(&p)) return false;
  if (!AppSerial_ParseFloat(&p, hard_limit_a)) return false;

  if (!AppSerial_ExpectComma(&p)) return false;
  if (!AppSerial_ParseFloat(&p, accel_elec_hz_s)) return false;

  if (!AppSerial_ExpectComma(&p)) return false;
  if (!AppSerial_ParseU32(&p, datalog_ms)) return false;

  if (!AppSerial_ExpectComma(&p)) return false;
  if (!AppSerial_ParseU32(&p, ds18b20_ms)) return false;

  /*
   * On tolère espaces finaux.
   */
  while ((*p == ' ') || (*p == '\t'))
  {
    p++;
  }

  if ((*p != '\0') && (*p != '\r') && (*p != '\n'))
  {
    return false;
  }

  return true;
}

/*
 * ================================
 * COMMAND HANDLERS
 * ================================
 */

static void AppSerial_HandleCfg(const char *line)
{
  float target_rpm;
  float iq_limit_a;
  float hard_limit_a;
  float accel_elec_hz_s;
  uint32_t datalog_ms;
  uint32_t ds18b20_ms;

  if (!AppSerial_ParseCfg(line,
                          &target_rpm,
                          &iq_limit_a,
                          &hard_limit_a,
                          &accel_elec_hz_s,
                          &datalog_ms,
                          &ds18b20_ms))
  {
    cfg_received = false;
    AppSerial_SendErr("BAD_CFG");
    return;
  }

  if ((!isfinite(target_rpm)) ||
      (!isfinite(iq_limit_a)) ||
      (!isfinite(hard_limit_a)) ||
      (!isfinite(accel_elec_hz_s)) ||
      (target_rpm < APP_CFG_MIN_TARGET_RPM) ||
      (target_rpm > APP_CFG_MAX_TARGET_RPM) ||
      (iq_limit_a <= 0.0f) ||
      (hard_limit_a <= 0.0f) ||
      (accel_elec_hz_s <= 0.0f) ||
      (accel_elec_hz_s > APP_CFG_MAX_ACCEL_HZ_S) ||
      (datalog_ms < APP_CFG_MIN_DATALOG_MS) ||
      (datalog_ms > APP_CFG_MAX_DATALOG_MS) ||
      (ds18b20_ms > APP_CFG_MAX_DS18B20_MS))
  {
    cfg_received = false;
    AppSerial_SendErr("CFG_VALUE_OUT_OF_RANGE");
    return;
  }

  /*
   * Iq et hard_limit ne sont volontairement plus comparés ici.
   * Le PC peut envoyer la paire de valeurs souhaitée sans rejet firmware.
   * Si hard_limit est inférieur au courant réel, le hard-stop applicatif peut
   * quand même arrêter le moteur pendant RUN.
   */

  if (ds18b20_ms < APP_CFG_MIN_DS18B20_MS)
  {
    ds18b20_ms = APP_CFG_MIN_DS18B20_MS;
  }

  AppMotorControl_SetRuntimeConfig(target_rpm,
                                   iq_limit_a,
                                   hard_limit_a,
                                   accel_elec_hz_s);

  AppDatalog_SetRuntimePeriods(datalog_ms,
                               ds18b20_ms);

  cfg_received = true;

  AppSerial_SendAck("CFG");

  {
    char line[160];
    snprintf(line,
             sizeof(line),
             "#CFG_OK,target_rpm=%ld,iq_mA=%ld,hard_mA=%ld,accel_hz_s=%ld,datalog_ms=%lu,ds18b20_ms=%lu\r\n",
             (long)target_rpm,
             (long)(iq_limit_a * 1000.0f),
             (long)(hard_limit_a * 1000.0f),
             (long)accel_elec_hz_s,
             (unsigned long)datalog_ms,
             (unsigned long)ds18b20_ms);
    AppDatalog_SendText(line);
  }
}

static void AppSerial_HandleStart(void)
{
  bool ok;

  if (!cfg_received)
  {
    AppSerial_SendErr("NO_VALID_CFG");
    return;
  }

  AppMotorControl_Stop();
  AppDatalog_StopLogging();

  HAL_Delay(20);

  AppDatalog_StartLogging();

  ok = AppMotorControl_Start();

  if (ok)
  {
    AppSerial_SendAck("START");
  }
  else
  {
    AppDatalog_StopLogging();
    AppSerial_SendErr("START_FAILED");
  }
}

static void AppSerial_HandleStop(void)
{
  AppMotorControl_Stop();
  AppDatalog_StopLogging();

  AppSerial_SendAck("STOP");
}

static void AppSerial_HandleLine(char *line)
{
  char *cmd;

  if ((line == NULL) || (line[0] == '\0'))
  {
    return;
  }

  AppSerial_TrimInPlace(line);

  cmd = AppSerial_FindCommand(line);

  if (cmd == NULL)
  {
#if APP_SERIAL_VERBOSE_UNKNOWN
    AppSerial_SendErr("UNKNOWN_CMD");
#endif
    return;
  }

  if (strcmp(cmd, "SYNC") == 0)
  {
    AppSerial_HandleSync();
  }
  else if (strncmp(cmd, "CFG,", 4U) == 0)
  {
    AppSerial_HandleCfg(cmd);
  }
  else if (strcmp(cmd, "START") == 0)
  {
    AppSerial_HandleStart();
  }
  else if (strcmp(cmd, "STOP") == 0)
  {
    AppSerial_HandleStop();
  }
  else
  {
  #if APP_SERIAL_VERBOSE_UNKNOWN
    AppSerial_SendErr("UNKNOWN_CMD");
  #endif
  }
}

/*
 * ================================
 * INIT / TASK
 * ================================
 */

void AppSerialControl_Init(void)
{
  AppSerial_RxQueueReset();

  cfg_received = false;

  /*
   * Réception UART par interruption.
   * Laisse USART1_IRQn activée même si Motor Pilot / ASPEP est désactivé.
   */
  LL_USART_ClearFlag_ORE(USART1);
  LL_USART_ClearFlag_FE(USART1);
  LL_USART_ClearFlag_NE(USART1);

  LL_USART_EnableIT_RXNE_RXFNE(USART1);
  LL_USART_EnableIT_ERROR(USART1);

  NVIC_EnableIRQ(USART1_IRQn);

  AppDatalog_SendText("#SERIAL_CONTROL_READY\r\n");
}

void AppSerialControl_Task(void)
{
  uint8_t c;

  while (AppSerial_RxPop(&c))
  {
    if (c == '\r')
    {
      continue;
    }

    if (c == '\n')
    {
      rx_line[rx_index] = '\0';

      AppSerial_HandleLine(rx_line);

      rx_index = 0U;
      memset(rx_line, 0, sizeof(rx_line));
    }
    else
    {
      /*
       * On garde uniquement les caractères ASCII utiles.
       * Ça évite qu'un octet parasite casse le parsing.
       */
      if ((c >= 32U) && (c <= 126U))
      {
        if (rx_index < (APP_SERIAL_RX_LINE_SIZE - 1U))
        {
          rx_line[rx_index] = (char)c;
          rx_index++;
        }
        else
        {
          rx_index = 0U;
          memset(rx_line, 0, sizeof(rx_line));
          AppSerial_SendErr("RX_LINE_TOO_LONG");
        }
      }
    }
  }
}
