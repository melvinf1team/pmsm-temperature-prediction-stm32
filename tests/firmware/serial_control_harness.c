/* Execute real command parsing/dispatch with separate motor and logger mocks. */
#include "test_platform.h"
#define USART1 0
#define LL_USART_IsActiveFlag_ORE(x) 0
#define LL_USART_IsActiveFlag_FE(x) 0
#define LL_USART_IsActiveFlag_NE(x) 0
#define LL_USART_IsActiveFlag_RXNE_RXFNE(x) 0
#define LL_USART_ClearFlag_ORE(x) ((void)0)
#define LL_USART_ClearFlag_FE(x) ((void)0)
#define LL_USART_ClearFlag_NE(x) ((void)0)
#define LL_USART_ReceiveData8(x) 0
#define LL_USART_EnableIT_RXNE_RXFNE(x) ((void)0)
#define LL_USART_EnableIT_ERROR(x) ((void)0)
#define NVIC_EnableIRQ(x) ((void)0)
#include FIRMWARE_SOURCE

static bool motor, logging;
static unsigned motor_starts, motor_stops, log_starts, log_stops;
static unsigned repeated_headers;
static unsigned datalog_ms, ds18b20_ms;
static AppMotorProfile_t last_profile;
static AppMotorProfileStartResult_t profile_result = APP_MOTOR_PROFILE_START_ACCEPTED;
static char output[8192];
static size_t used;
uint32_t HAL_GetTick(void) { return 1000U; }
void HAL_Delay(uint32_t ms) { (void)ms; }
bool AppDatalog_SendText(const char *s)
{
  size_t n = strlen(s);
  if (used + n >= sizeof(output)) return false;
  memcpy(output + used, s, n + 1U); used += n;
  return true;
}
void AppDatalog_StartLogging(void) { logging = true; log_starts++; }
void AppDatalog_StopLogging(void) { logging = false; log_stops++; }
bool AppDatalog_IsLogging(void) { return logging; }
void AppDatalog_RepeatHeader(void) { repeated_headers++; }
void AppDatalog_SetRuntimePeriods(uint32_t a, uint32_t b) { datalog_ms = a; ds18b20_ms = b; }
void AppMotorControl_Stop(void) { motor = false; motor_stops++; }
bool AppMotorControl_Start(void) { motor = true; motor_starts++; return true; }
bool AppMotorControl_IsRunning(void) { return motor; }
bool AppMotorControl_IsActive(void) { return motor; }
AppMotorProfileStartResult_t AppMotorControl_StartProfile(AppMotorProfile_t profile)
{
  last_profile = profile;
  if (profile_result == APP_MOTOR_PROFILE_START_ACCEPTED) { motor = true; motor_starts++; }
  return profile_result;
}
void AppMotorControl_SetRuntimeConfig(float a, float b, float c, float d)
{ (void)a; (void)b; (void)c; (void)d; }
bool AppMotorControl_SetLoadFixed(float a) { (void)a; return true; }
bool AppMotorControl_SetLoadVariable(void) { return true; }
void AppWiringDiag_Run(void) {}
void AppWiringDiag_SendStatus(void) {}
bool D6TIR_GetFrameTenths(int16_t *a, int16_t b[D6TIR_PIXEL_COUNT]) { (void)a; (void)b; return false; }
uint8_t D6TIR_GetSelectedPixel(void) { return 10U; }
static void command(const char *s)
{
  char line[192];
  strcpy(line, s); used = 0; output[0] = '\0';
  AppSerial_HandleLine(line);
}
#define CHECK(x) do { if (!(x)) return __LINE__; } while (0)
int main(void)
{
  motor = true; logging = true;
  command("HELLO");
  CHECK(motor && logging && motor_stops == 0 && log_stops == 0);
  CHECK(strstr(output, "#CONTROL,independent=1,motor=1,logging=1") != NULL);
  motor = logging = false;
  command("PROFILE_SET,STABLE");
  CHECK(strstr(output, "ACK,PROFILE_SET,STABLE\r\n") != NULL);
  CHECK(!motor && !logging && motor_starts == 0 && log_starts == 0);
  command("LOG_START,100,1000");
  CHECK(logging && !motor && datalog_ms == 100 && ds18b20_ms == 1000);
  command("MOTOR_START");
  CHECK(motor && logging && motor_starts == 1 && log_starts == 1);
  CHECK(last_profile == APP_MOTOR_PROFILE_STABLE);
  command("MOTOR_STOP");
  CHECK(!motor && logging && motor_stops == 1 && log_stops == 0);
  command("MOTOR_START");
  CHECK(motor && logging && motor_starts == 2 && log_starts == 1);
  command("LOG_STOP");
  CHECK(motor && !logging && motor_stops == 1 && log_stops == 1);
  command("LOG_START,50,750");
  CHECK(motor && logging && log_starts == 2 && motor_starts == 2);
  command("LOG_START,50,750");
  CHECK(log_starts == 2 && repeated_headers == 1); /* Reannounce schema without restarting. */
  command("MOTOR_START");
  CHECK(strstr(output, "ERR,MOTOR_BUSY") && motor_starts == 2);
  command("MOTOR_STOP");
  profile_result = APP_MOTOR_PROFILE_START_THERMAL_ERROR;
  command("MOTOR_START");
  CHECK(!motor && logging && strstr(output, "ERR,THERMAL_PROTECTION"));
  profile_result = APP_MOTOR_PROFILE_START_ACCEPTED;
  command("LOG_START"); /* Must never fall through to legacy START. */
  CHECK(!motor && motor_starts == 2 && strstr(output, "ERR,BAD_LOG_START"));
  command("LOG_START,0,1000");
  CHECK(logging && strstr(output, "ERR,ACQ_VALUE_OUT_OF_RANGE"));
  command("PROFILE_SET");
  CHECK(!motor && strstr(output, "ERR,BAD_PROFILE"));
  command("CFG,4000,28,30,50,100,1000");
  CHECK(cfg_received && !prepared_profile_valid && logging && !motor);
  command("LOG_START,250,1500");
  command("MOTOR_CFG,3500,28,30,50");
  CHECK(cfg_received && logging && !motor && datalog_ms == 250 && ds18b20_ms == 1500);
  CHECK(strstr(output, "ACK,MOTOR_CFG"));
  command("MOTOR_START");
  CHECK(motor && logging && motor_starts == 3 && log_starts == 2);
  command("MOTOR_STOP");
  command("SYNC");
  CHECK(!motor && !logging && !prepared_profile_valid && !cfg_received);
  command("MOTOR_START");
  CHECK(!motor && !logging && strstr(output, "ERR,NO_VALID_CFG"));
  return 0;
}
