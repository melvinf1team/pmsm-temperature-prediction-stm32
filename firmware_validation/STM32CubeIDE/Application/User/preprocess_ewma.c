#include "preprocess_ewma.h"

#include <math.h>
#include <string.h>

static const uint32_t preprocess_ewma_spans[PREPROCESS_EWMA_SPAN_COUNT] =
{
  6600U,
  16800U,
  31800U,
  47400U
};

static bool PreprocessEwma_IsFinite(float value)
{
  uint32_t bits;

  memcpy(&bits, &value, sizeof(bits));
  return (bits & 0x7F800000UL) != 0x7F800000UL;
}

static void PreprocessEwma_BuildSignals(
  const PreprocessEwmaInput_t *input,
  float signals[PREPROCESS_EWMA_SIGNAL_COUNT])
{
  float u_s;
  float i_s;
  float apparent_power;

  signals[0] = input->ds18b20_temp_c;
  signals[1] = input->motor_ud_v;
  signals[2] = input->motor_uq_v;
  signals[3] = input->motor_speed_mech_rpm;
  signals[4] = input->motor_id_a;
  signals[5] = input->motor_iq_a;

  u_s = sqrtf((input->motor_ud_v * input->motor_ud_v) +
              (input->motor_uq_v * input->motor_uq_v));
  i_s = sqrtf((input->motor_id_a * input->motor_id_a) +
              (input->motor_iq_a * input->motor_iq_a));
  apparent_power = 1.5f * u_s * i_s;

  signals[6] = u_s;
  signals[7] = i_s;
  signals[8] = apparent_power;
  signals[9] = input->motor_speed_mech_rpm * i_s;
  signals[10] = input->motor_speed_mech_rpm * apparent_power;
}

void PreprocessEwma_Reset(PreprocessEwmaContext_t *context)
{
  if (context != NULL)
  {
    memset(context, 0, sizeof(*context));
  }
}

bool PreprocessEwma_Process(
  PreprocessEwmaContext_t *context,
  const PreprocessEwmaInput_t *input,
  float output[PREPROCESS_EWMA_OUTPUT_COUNT])
{
  float signals[PREPROCESS_EWMA_SIGNAL_COUNT];
  size_t output_index = 0U;

  if ((context == NULL) || (input == NULL) || (output == NULL))
  {
    return false;
  }

  PreprocessEwma_BuildSignals(input, signals);

  for (size_t signal_index = 0U;
       signal_index < PREPROCESS_EWMA_SIGNAL_COUNT;
       signal_index++)
  {
    float value = signals[signal_index];
    bool value_is_valid = PreprocessEwma_IsFinite(value);

    output[output_index++] = value_is_valid ? value : 0.0f;

    for (size_t span_index = 0U;
         span_index < PREPROCESS_EWMA_SPAN_COUNT;
         span_index++)
    {
      float alpha = 2.0f / ((float)preprocess_ewma_spans[span_index] + 1.0f);
      float old_weight_factor = 1.0f - alpha;

      if (context->initialized[signal_index][span_index])
      {
        context->old_weight[signal_index][span_index] *= old_weight_factor;

        if (value_is_valid)
        {
          float mean = context->mean[signal_index][span_index];
          float old_weight = context->old_weight[signal_index][span_index];

          if (mean != value)
          {
            context->mean[signal_index][span_index] =
              ((old_weight * mean) + (alpha * value)) /
              (old_weight + alpha);
          }

          context->old_weight[signal_index][span_index] = 1.0f;
        }
      }
      else if (value_is_valid)
      {
        context->mean[signal_index][span_index] = value;
        context->old_weight[signal_index][span_index] = 1.0f;
        context->initialized[signal_index][span_index] = true;
      }

      output[output_index++] =
        context->initialized[signal_index][span_index]
          ? context->mean[signal_index][span_index]
          : 0.0f;
    }
  }

  return output_index == PREPROCESS_EWMA_OUTPUT_COUNT;
}