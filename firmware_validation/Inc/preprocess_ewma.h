#ifndef PREPROCESS_EWMA_H
#define PREPROCESS_EWMA_H

#include <stdbool.h>
#include <stdint.h>

#define PREPROCESS_EWMA_SAMPLE_PERIOD_MS   100U
#define PREPROCESS_EWMA_SIGNAL_COUNT       11U
#define PREPROCESS_EWMA_SPAN_COUNT         4U
#define PREPROCESS_EWMA_OUTPUT_COUNT       55U

typedef struct
{
  float ds18b20_temp_c;
  float motor_ud_v;
  float motor_uq_v;
  float motor_speed_mech_rpm;
  float motor_id_a;
  float motor_iq_a;
} PreprocessEwmaInput_t;

typedef struct
{
  float mean[PREPROCESS_EWMA_SIGNAL_COUNT][PREPROCESS_EWMA_SPAN_COUNT];
  float old_weight[PREPROCESS_EWMA_SIGNAL_COUNT][PREPROCESS_EWMA_SPAN_COUNT];
  bool initialized[PREPROCESS_EWMA_SIGNAL_COUNT][PREPROCESS_EWMA_SPAN_COUNT];
} PreprocessEwmaContext_t;

void PreprocessEwma_Reset(PreprocessEwmaContext_t *context);

bool PreprocessEwma_Process(
  PreprocessEwmaContext_t *context,
  const PreprocessEwmaInput_t *input,
  float output[PREPROCESS_EWMA_OUTPUT_COUNT]);

#endif /* PREPROCESS_EWMA_H */