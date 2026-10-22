/* Execute the real application with deterministic HAL/MCSDK boundary mocks. */
#include "test_platform.h"
#include FIRMWARE_SOURCE

static uint32_t mock_tick, mock_faults, mock_starts, mock_stops;
static uint32_t mock_max_age;
static MCI_State_t mock_state;
static bool mock_d6t_valid;
static bool mock_start_queued;
static bool mock_dac_fail;
static float mock_temperature, mock_speed_rpm;
static float mock_speed_reference_rpm, mock_acceleration;
static dq_float_t mock_current;
static fixp16_t mock_id_ref, mock_current_limit;
static TestStc mock_stc;
static MCI_Handle_t mock_mci = { .pSTC = &mock_stc };
MCI_Handle_t *pMCI[NBR_OF_MOTORS] = { &mock_mci };
DAC_HandleTypeDef hdac1;

uint32_t HAL_GetTick(void) { return mock_tick; }
void HAL_Delay(uint32_t ms) { mock_tick += ms; }
int HAL_DAC_SetValue(DAC_HandleTypeDef *d, int c, int a, uint32_t v)
{
  (void)d; (void)c; (void)a; (void)v;
  if (mock_dac_fail) { mock_dac_fail = false; return 1; }
  return HAL_OK;
}
int HAL_DAC_Start(DAC_HandleTypeDef *d, int c)
{ (void)d; (void)c; return HAL_OK; }
void Error_Handler(void) { for (;;) {} }
MCI_State_t MC_GetSTMStateMotor1(void) { return mock_state; }
uint32_t MC_GetCurrentFaultsMotor1(void) { return mock_faults; }
bool MC_AcknowledgeFaultsMotor1(void) { return true; }
int MC_StartWithPolarizationMotor1(void)
{
  mock_starts++;
  mock_mci.DirectCommand = MCI_START;
  if (!mock_start_queued) mock_state = RUN;
  return MC_SUCCESS;
}
bool MC_StopMotor1(void) { mock_stops++; mock_state = IDLE; return true; }
float MC_GetSpeedMotor1_F(void) { return mock_speed_rpm * 2.0f / 60.0f; }
dq_float_t MC_GetCurrentMotor1_F(void) { return mock_current; }
bool MC_SetControlModeMotor1(int mode) { return mode == MCM_SPEED_MODE; }
void MC_SetAccelerationMotor1_F(float a) { mock_acceleration = a; }
void MC_SetSpeedReferenceMotor1_F(float a)
{ mock_speed_reference_rpm = a * 60.0f / 2.0f; }
void MCI_SetIdRef(MCI_Handle_t *p, fixp16_t v) { (void)p; mock_id_ref = v; }
void MCI_SetMaxCurrent(MCI_Handle_t *p, fixp16_t v) { (void)p; mock_current_limit = v; }
void MCI_SetPidSpeedKp(MCI_Handle_t *p, float v) { (void)p; (void)v; }
void MCI_SetPidSpeedKi(MCI_Handle_t *p, float v) { (void)p; (void)v; }
bool AppDatalog_SendText(const char *t) { (void)t; return true; }
bool D6TIR_GetFreshTemperatureC(float *t, uint32_t max_age)
{ mock_max_age = max_age; *t = mock_temperature; return mock_d6t_valid; }

static void reset(void)
{
  mock_tick = 1000U;
  mock_faults = mock_starts = mock_stops = 0U;
  mock_state = IDLE;
  mock_d6t_valid = true;
  mock_start_queued = false;
  mock_dac_fail = false;
  mock_mci.DirectCommand = 0;
  mock_temperature = 25.0f;
  mock_speed_rpm = 2500.0f;
  mock_current.D = 0.0f;
  mock_current.Q = 5.0f;
  AppMotorControl_Init();
  AppMotorControl_SetRuntimeConfig(2500.0f, 30.0f, 30.0f, 50.0f);
}

static void step(uint32_t ms)
{
  mock_tick += ms;
  AppMotorControl_Task();
}

#define CHECK(x) do { if (!(x)) return __LINE__; } while (0)
#define NEAR(a, b) (fabsf((a) - (b)) < 0.001f)

/* Follow the emitted reference, modelling a motor that tracks its command.
 * Physical observer/current-loop dynamics are deliberately not simulated. */
static bool finish_custom_start(void)
{
  for (int i = 0; i < 400; i++)
  {
    if (app_custom_phase == APP_CUSTOM_DONE) return true;
    if (app_mc_state != APP_MC_RUNNING) return false;
    /* Stay below the existing strict 4500 rpm overspeed ceiling. */
    mock_speed_rpm = fminf(mock_speed_reference_rpm, 4490.0f);
    step(100U);
  }
  return false;
}

static int test_custom_start(void)
{
  reset();
  AppMotorControl_SetRuntimeConfig(4500.0f, 30.0f, 30.0f, 50.0f);
  CHECK(AppMotorControl_SetLoadFixed(0.25f));
  CHECK(AppMotorControl_Start());
  CHECK(NEAR(mock_speed_reference_rpm, 2000.0f));
  CHECK(NEAR(mock_acceleration, 500.0f));
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), 0.05f));
  CHECK(NEAR(app_target_speed_rpm, 4500.0f));
  CHECK(NEAR(app_active_iq_limit_a, 4.5f));
  AppMotorControl_Task();
  mock_speed_rpm = 2000.0f;
  uint32_t start = mock_tick;
  for (int i = 0; i < 20; i++) step(100U);
  CHECK(NEAR(mock_speed_reference_rpm, 2000.0f));
  step(100U);
  CHECK(app_custom_phase == APP_CUSTOM_SPEED_RAMP);
  CHECK(mock_acceleration > 16.6f && mock_acceleration < 16.7f);
  CHECK(NEAR(AppMotorControl_GetOverspeedReferenceRpm(), 2000.0f));

  for (int i = 0; i < 50; i++)
  {
    float previous = mock_speed_reference_rpm;
    mock_speed_rpm = previous;
    step(100U);
    CHECK(mock_speed_reference_rpm <= previous + 50.01f);
    CHECK(mock_speed_reference_rpm >= previous);
    CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), 0.05f));
    CHECK(mock_id_ref == 0);
    CHECK(mock_current_limit <= FIXP16(30.0f));
  }
  CHECK(NEAR(mock_speed_reference_rpm, 4500.0f));
  CHECK(app_custom_phase == APP_CUSTOM_FINAL_SPEED);
  mock_speed_rpm = 4490.0f;
  for (int i = 0; i < 11; i++) step(100U);
  CHECK(app_custom_phase == APP_CUSTOM_LOAD_RAMP);
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), 0.05f));
  for (int i = 0; i < 50; i++)
  {
    float previous = AppMotorControl_GetLoadSetpointA();
    step(100U);
    CHECK(AppMotorControl_GetLoadSetpointA() <= previous + 0.00401f);
    CHECK(AppMotorControl_GetLoadSetpointA() >= previous);
    CHECK(AppMotorControl_GetLoadSetpointA() <= 0.25001f);
    CHECK(mock_id_ref == 0);
  }
  step(100U); /* Allow floating-point rounding on the final DAC step. */
  CHECK(app_custom_phase == APP_CUSTOM_DONE);
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), 0.25f));
  CHECK((mock_tick - start) >= 13000U && (mock_tick - start) <= 13500U);
  for (int i = 0; i < 10; i++) step(100U);
  CHECK(mock_id_ref == 0);
  for (int i = 0; i < 5; i++) step(100U);
  CHECK(app_active_id_a > 0.0f); /* Heating follows the completed startup. */

  /* A Custom LOAD update cannot bypass the initial low-load phase. */
  reset();
  CHECK(AppMotorControl_Start());
  AppMotorControl_Task();
  CHECK(AppMotorControl_SetLoadFixed(0.25f));
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), 0.05f));
  CHECK(finish_custom_start());
  CHECK(AppMotorControl_SetLoadFixed(0.10f));
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), 0.10f));
  CHECK(AppMotorControl_SetLoadFixed(0.25f));
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), 0.10f));
  CHECK(app_custom_phase == APP_CUSTOM_FINAL_SPEED);
  CHECK(finish_custom_start());

  /* Brake growth pauses on speed loss, then waits for 1 s stable recovery. */
  AppMotorControl_SetRuntimeConfig(4500.0f, 30.0f, 30.0f, 50.0f);
  for (int i = 0; i < 200 && app_custom_phase != APP_CUSTOM_LOAD_RAMP; i++)
  {
    mock_speed_rpm = fminf(mock_speed_reference_rpm, 4490.0f);
    step(100U);
  }
  CHECK(app_custom_phase == APP_CUSTOM_LOAD_RAMP);
  mock_speed_rpm = 4490.0f;
  for (int i = 0; i < 5; i++) step(100U);
  float held_load = AppMotorControl_GetLoadSetpointA();
  CHECK(held_load > 0.05f && held_load < 0.25f);
  mock_speed_rpm = 3900.0f;
  for (int i = 0; i < 10; i++) step(100U);
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), held_load));
  CHECK(mock_id_ref == 0);
  mock_speed_rpm = 4490.0f;
  for (int i = 0; i < 11; i++) step(100U);
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), held_load));
  step(1000U); /* A delayed iteration must not catch up the brake ramp. */
  CHECK(AppMotorControl_GetLoadSetpointA() <= held_load + 0.00401f);
  CHECK(finish_custom_start());

  /* A smaller speed/load request is never exceeded by startup settings. */
  reset();
  AppMotorControl_SetRuntimeConfig(1200.0f, 5.0f, 6.0f, 50.0f);
  CHECK(AppMotorControl_SetLoadFixed(0.05f));
  CHECK(AppMotorControl_Start());
  CHECK(NEAR(mock_speed_reference_rpm, 1200.0f));
  AppMotorControl_Task();
  CHECK(finish_custom_start());
  CHECK(NEAR(mock_speed_reference_rpm, 1200.0f));
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), 0.05f));

  /* Speed growth also pauses when the measured motor cannot follow. */
  reset();
  AppMotorControl_SetRuntimeConfig(4500.0f, 30.0f, 30.0f, 10.0f);
  CHECK(AppMotorControl_Start());
  AppMotorControl_Task();
  mock_speed_rpm = 2000.0f;
  for (int i = 0; i < 21; i++) step(100U);
  CHECK(app_custom_phase == APP_CUSTOM_SPEED_RAMP);
  CHECK(NEAR(mock_acceleration, 10.0f));
  step(1000U);
  CHECK(mock_speed_reference_rpm <= 2030.01f); /* 300 rpm/s, dt capped at 100 ms. */
  float held_speed = mock_speed_reference_rpm;
  mock_speed_rpm = 1500.0f;
  for (int i = 0; i < 10; i++) step(100U);
  CHECK(NEAR(mock_speed_reference_rpm, held_speed));
  mock_speed_rpm = held_speed;
  step(100U);
  CHECK(mock_speed_reference_rpm > held_speed);

  /* A new CFG uses the current reference, not a jump to the new target. */
  held_speed = mock_speed_reference_rpm;
  AppMotorControl_SetRuntimeConfig(3500.0f, 20.0f, 25.0f, 50.0f);
  CHECK(NEAR(mock_speed_reference_rpm, held_speed));
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), 0.05f));
  CHECK(finish_custom_start());
  CHECK(NEAR(mock_speed_reference_rpm, 3500.0f));

  /* A sustained blocked/stalled start stops instead of vibrating forever. */
  reset();
  CHECK(AppMotorControl_Start());
  AppMotorControl_Task();
  mock_speed_rpm = 0.0f;
  for (int i = 0; i < 51; i++) step(100U);
  CHECK(app_mc_state == APP_MC_FAULT && mock_state == IDLE);
  CHECK(!strcmp(app_mc_fault_reason, "CUSTOM_SPEED_TIMEOUT"));
  CHECK(app_custom_phase == APP_CUSTOM_OFF);
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), 0.05f));
  uint32_t starts = mock_starts;
  for (int i = 0; i < 20; i++) step(100U);
  CHECK(mock_starts == starts);

  /* Stop/restart and thermal faults cancel a partially applied brake ramp. */
  reset();
  CHECK(AppMotorControl_SetLoadFixed(0.25f));
  CHECK(AppMotorControl_Start());
  AppMotorControl_Task();
  for (int i = 0; i < 200 && AppMotorControl_GetLoadSetpointA() < 0.10f; i++)
  {
    mock_speed_rpm = mock_speed_reference_rpm;
    step(100U);
  }
  CHECK(AppMotorControl_GetLoadSetpointA() >= 0.10f);
  AppMotorControl_Stop();
  CHECK(app_custom_phase == APP_CUSTOM_OFF);
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), 0.05f));
  CHECK(AppMotorControl_Start());
  CHECK(NEAR(mock_speed_reference_rpm, 2000.0f));
  AppMotorControl_Task();
  mock_temperature = 70.1f;
  step(100U);
  CHECK(app_mc_state == APP_MC_FAULT && app_custom_phase == APP_CUSTOM_OFF);

  /* DAC failure and a stopped MCSDK cannot leave the sequence progressing. */
  reset();
  CHECK(AppMotorControl_SetLoadFixed(0.25f));
  CHECK(AppMotorControl_Start());
  AppMotorControl_Task();
  for (int i = 0; i < 200 && app_custom_phase != APP_CUSTOM_LOAD_RAMP; i++)
  {
    mock_speed_rpm = mock_speed_reference_rpm;
    step(100U);
  }
  CHECK(app_custom_phase == APP_CUSTOM_LOAD_RAMP);
  mock_dac_fail = true;
  step(100U);
  CHECK(app_mc_state == APP_MC_FAULT && mock_state == IDLE);
  CHECK(!strcmp(app_mc_fault_reason, "LOAD_DAC"));
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), 0.05f));
  reset();
  CHECK(AppMotorControl_Start());
  AppMotorControl_Task();
  mock_state = IDLE;
  step(100U);
  CHECK(app_mc_state == APP_MC_FAULT && app_custom_phase == APP_CUSTOM_OFF);

  /* STOP also cancels a Custom start still queued before the first SDK tick. */
  reset();
  mock_start_queued = true;
  CHECK(AppMotorControl_Start());
  CHECK(mock_mci.DirectCommand == MCI_START);
  AppMotorControl_Stop();
  CHECK(mock_mci.DirectCommand == MCI_STOP);
  CHECK(app_custom_phase == APP_CUSTOM_OFF);

  /* Both stability intervals and ramps survive a HAL tick rollover. */
  reset();
  AppMotorControl_SetRuntimeConfig(4500.0f, 30.0f, 30.0f, 50.0f);
  CHECK(AppMotorControl_SetLoadFixed(0.25f));
  mock_tick = UINT32_MAX - 3000U;
  CHECK(AppMotorControl_Start());
  AppMotorControl_Task();
  CHECK(finish_custom_start());
  CHECK(mock_tick < 20000U);

  /* Custom variable load starts drawing only after the speed sequence. */
  reset();
  CHECK(AppMotorControl_Start());
  AppMotorControl_Task();
  CHECK(AppMotorControl_SetLoadVariable());
  CHECK(!app_tb200s_change_scheduled);
  CHECK(finish_custom_start());
  CHECK(app_tb200s_change_scheduled);
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), 0.05f));

  /* B2 and PROFILE presets retain their original speed/load behavior. */
  reset();
  CHECK(AppMotorControl_StartProfile(APP_MOTOR_PROFILE_STABLE) ==
        APP_MOTOR_PROFILE_START_ACCEPTED);
  AppMotorControl_Task();
  AppMotorControl_Task();
  CHECK(app_custom_phase == APP_CUSTOM_OFF);
  CHECK(NEAR(mock_speed_reference_rpm, 2500.0f));
  CHECK(NEAR(mock_acceleration, 500.0f));
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), 0.10f));
  AppMotorControl_Stop();
  UI_HandleStartStopButton_cb();
  AppMotorControl_Task();
  AppMotorControl_Task();
  CHECK(app_custom_phase == APP_CUSTOM_OFF && app_button_speed_variable);
  CHECK(mock_speed_reference_rpm >= 2000.0f && mock_speed_reference_rpm <= 4000.0f);
  CHECK(NEAR(mock_acceleration, 500.0f));
  CHECK(NEAR(AppMotorControl_GetLoadSetpointA(), 0.05f));
  return 0;
}

int main(void)
{
  int custom_result = test_custom_start();
  if (custom_result != 0) return custom_result;
  reset();
  CHECK(AppMotorControl_Start());
  CHECK(mock_id_ref == 0);
  AppMotorControl_Task(); /* STARTING -> RUNNING */
  CHECK(finish_custom_start());
  /* The completion tick starts tracking stable speed for Id. */
  for (int i = 0; i < 10; i++) step(100U);
  CHECK(mock_id_ref == 0); /* No Id during the stability interval. */
  for (int i = 0; i < 40; i++) step(100U);
  CHECK(NEAR(app_active_id_a, 1.0f));
  for (int i = 0; i < 41; i++) step(100U);
  CHECK(NEAR(app_active_id_a, 2.0f));
  CHECK(NEAR((float)mock_id_ref / 65536.0f, 2.0f));
  CHECK(mock_current_limit <= FIXP16(30.0f));
  CHECK(mock_max_age == 1000U);

  /* Stop, startup and any external reconfiguration clear the old Id. */
  AppMotorControl_Stop();
  CHECK(mock_id_ref == 0 && app_active_id_a == 0.0f);
  CHECK(AppMotorControl_Start());
  CHECK(mock_id_ref == 0);
  AppMotorControl_Task();
  app_active_id_a = 1.0f;
  AppMotorControl_SetRuntimeConfig(2500.0f, 5.0f, 6.0f, 50.0f);
  CHECK(mock_id_ref == 0 && app_active_id_a == 0.0f);

  /* Unstable speed never starts the heating ramp. */
  reset();
  mock_speed_rpm = 2200.0f;
  CHECK(AppMotorControl_Start());
  AppMotorControl_Task();
  for (int i = 0; i < 100; i++) step(100U);
  CHECK(mock_id_ref == 0);

  /* Adding Id preserves a small Iq budget instead of consuming its circle. */
  reset();
  app_active_iq_limit_a = 2.0f;
  app_hard_stop_current_a = 8.0f;
  app_active_id_a = 2.0f;
  AppMotorControl_SetIqLimit(2.0f, false);
  CHECK(NEAR((float)mock_current_limit / 65536.0f, sqrtf(8.0f)));

  /* Id gives way to the measured Q-current at the total-current ceiling. */
  app_hard_stop_current_a = 10.0f;
  app_active_iq_limit_a = 30.0f;
  AppMotorControl_UpdateIdReference(mock_tick, 9.95f);
  CHECK(app_active_id_a > 0.9f && app_active_id_a < 1.0f);
  CHECK(mock_current_limit <= FIXP16(10.0f));

  /* Strict >70 C trip; no queued restart, even after cooling. */
  reset();
  CHECK(AppMotorControl_Start());
  AppMotorControl_Task();
  CHECK(finish_custom_start());
  app_active_id_a = 1.0f;
  MCI_SetIdRef(pMCI[M1], FIXP16(1.0f));
  mock_temperature = 70.0f;
  step(100U);
  CHECK(app_mc_state == APP_MC_RUNNING);
  app_motor_start_requested = true;
  app_button_toggle_pending = true;
  mock_temperature = 70.1f;
  step(100U);
  CHECK(app_mc_state == APP_MC_FAULT);
  CHECK(app_thermal_latched && !app_motor_start_requested);
  CHECK(!app_button_toggle_pending);
  CHECK(!app_button_profile_active && mock_id_ref == 0);
  CHECK(!strcmp(app_mc_fault_reason, "D6T_OVERTEMP"));
  uint32_t starts = mock_starts;
  mock_temperature = 66.0f;
  CHECK(!AppMotorControl_Start());
  CHECK(AppMotorControl_StartProfile(APP_MOTOR_PROFILE_STABLE) ==
        APP_MOTOR_PROFILE_START_THERMAL_ERROR);
  mock_temperature = 65.0f;
  for (int i = 0; i < 50; i++) step(100U);
  CHECK(mock_starts == starts); /* Cooling alone is not a restart command. */
  CHECK(AppMotorControl_Start());
  CHECK(!app_thermal_latched && mock_id_ref == 0);

  /* A missing/stale measurement blocks both entry points and stops RUN. */
  reset();
  mock_d6t_valid = false;
  CHECK(!AppMotorControl_Start());
  CHECK(AppMotorControl_StartProfile(APP_MOTOR_PROFILE_VARIABLE_ALL) ==
        APP_MOTOR_PROFILE_START_THERMAL_ERROR);
  CHECK(mock_starts == 0U);
  mock_d6t_valid = true;
  CHECK(AppMotorControl_Start());
  AppMotorControl_Task();
  app_active_id_a = 1.0f;
  mock_d6t_valid = false;
  step(100U);
  CHECK(app_mc_state == APP_MC_FAULT && mock_id_ref == 0);
  CHECK(!strcmp(app_mc_fault_reason, "D6T_UNAVAILABLE"));

  /* A thermal fault must also cancel an SDK start not yet serviced in IDLE. */
  reset();
  mock_start_queued = true;
  CHECK(AppMotorControl_Start());
  CHECK(mock_state == IDLE && mock_mci.DirectCommand == MCI_START);
  mock_temperature = 70.1f;
  step(1U);
  CHECK(app_mc_state == APP_MC_FAULT && mock_mci.DirectCommand == MCI_STOP);
  CHECK(mock_id_ref == 0 && !app_motor_start_requested);

  /* -Ofast must not optimize the NaN safety check away. */
  reset();
  union { uint32_t bits; float f; } nan_value = { .bits = 0x7FC00000U };
  mock_temperature = nan_value.f;
  CHECK(!AppMotorControl_Start());
  CHECK(!strcmp(app_mc_fault_reason, "D6T_UNAVAILABLE"));

  /* MCSDK faults also reset the heating reference. */
  reset();
  CHECK(AppMotorControl_Start());
  AppMotorControl_Task();
  app_active_id_a = 1.0f;
  mock_faults = MC_OVER_TEMP;
  step(100U);
  CHECK(app_mc_state == APP_MC_FAULT && mock_id_ref == 0);

  /* A tick-counter wrap must not restart the one-second stability interval. */
  reset();
  CHECK(AppMotorControl_Start());
  AppMotorControl_Task();
  CHECK(finish_custom_start());
  mock_tick = UINT32_MAX - 500U;
  app_id_ramp_last_ms = mock_tick;
  app_id_speed_stable = false;
  for (int i = 0; i < 52; i++) step(100U);
  CHECK(app_active_id_a > 0.9f && app_active_id_a < 1.1f);
  return 0;
}
