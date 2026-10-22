#ifndef TEST_PLATFORM_H
#define TEST_PLATFORM_H

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <math.h>

typedef int32_t fixp16_t;
typedef int32_t fixp30_t;
#define FIXP16(x) ((fixp16_t)((x) * 65536.0f))
#define FIXP30(x) ((fixp30_t)((x) * 1073741824.0f))
#define FIXP30_toF(x) ((float)(x) / 1073741824.0f)
#define NBR_OF_MOTORS 1
#define M1 0
#define MC_NO_FAULTS 0U
#define MC_SUCCESS 0
#define MC_OVER_TEMP 8U
#define MCM_SPEED_MODE 1
#define MCI_START 1
#define MCI_STOP 2
#define HAL_OK 0
#define DAC_CHANNEL_2 2
#define DAC_ALIGN_12B_R 0

typedef enum { IDLE, RUN, STOP, FAULT_NOW, FAULT_OVER } MCI_State_t;
typedef struct { float D, Q; } dq_float_t;
typedef struct { fixp30_t upper, lower, ui; } TestPid;
typedef struct { TestPid PIDSpeed; fixp30_t speed_ref_ramped_pu; } TestStc;
typedef struct { TestStc *pSTC; int DirectCommand; } MCI_Handle_t;
typedef struct { int unused; } DAC_HandleTypeDef;

uint32_t HAL_GetTick(void);
void HAL_Delay(uint32_t ms);
int HAL_DAC_SetValue(DAC_HandleTypeDef *, int, int, uint32_t);
int HAL_DAC_Start(DAC_HandleTypeDef *, int);
void Error_Handler(void);
static inline uint32_t __get_PRIMASK(void) { return 0; }
static inline void __disable_irq(void) {}
static inline void __enable_irq(void) {}
static inline void __NOP(void) {}

MCI_State_t MC_GetSTMStateMotor1(void);
uint32_t MC_GetCurrentFaultsMotor1(void);
uint32_t MC_GetOccurredFaultsMotor1(void);
bool MC_AcknowledgeFaultsMotor1(void);
int MC_StartWithPolarizationMotor1(void);
bool MC_StopMotor1(void);
float MC_GetSpeedMotor1_F(void);
dq_float_t MC_GetCurrentMotor1_F(void);
bool MC_SetControlModeMotor1(int);
void MC_SetAccelerationMotor1_F(float);
void MC_SetSpeedReferenceMotor1_F(float);
void MCI_SetIdRef(MCI_Handle_t *, fixp16_t);
void MCI_SetMaxCurrent(MCI_Handle_t *, fixp16_t);
void MCI_SetPidSpeedKp(MCI_Handle_t *, float);
void MCI_SetPidSpeedKi(MCI_Handle_t *, float);
static inline void PIDREG_SPEED_setUi_pu(TestPid *p, fixp30_t v) { p->ui = v; }
static inline void PIDREG_SPEED_setOutputLimits(TestPid *p, fixp30_t hi, fixp30_t lo)
{ p->upper = hi; p->lower = lo; }

/* GPIO declarations let the real D6T driver compile. Its I2C functions are
 * discarded by --gc-sections in cache/freshness tests, never executed. */
typedef struct { uint32_t Pin, Mode, Pull, Speed; } GPIO_InitTypeDef;
typedef struct { uint32_t DEMCR; } TestCoreDebug;
typedef struct { uint32_t CYCCNT, CTRL; } TestDwt;
extern TestCoreDebug *CoreDebug;
extern TestDwt *DWT;
extern uint32_t SystemCoreClock;
#define CoreDebug_DEMCR_TRCENA_Msk 1U
#define DWT_CTRL_CYCCNTENA_Msk 1U
#define GPIOB 0
#define GPIO_PIN_6 64U
#define GPIO_PIN_9 512U
#define GPIO_PIN_SET 1
#define GPIO_PIN_RESET 0
#define GPIO_MODE_OUTPUT_OD 0
#define GPIO_NOPULL 0
#define GPIO_SPEED_FREQ_LOW 0
#define __HAL_RCC_GPIOB_CLK_ENABLE() ((void)0)
void HAL_GPIO_WritePin(int, uint32_t, int);
int HAL_GPIO_ReadPin(int, uint32_t);
void HAL_GPIO_Init(int, GPIO_InitTypeDef *);

#endif
