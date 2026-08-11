/**
  ******************************************************************************
  * @file    pmsm_motor_parameters.h
  * @author  Motor Control SDK Team, ST Microelectronics
  * @brief   This file contains the parameters needed for the Motor Control SDK
  *          in order to configure the motor to drive.
  *
  ******************************************************************************
  * @attention
  *
  * <h2><center>&copy; Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.</center></h2>
  *
  * This software component is licensed by ST under Ultimate Liberty license
  * SLA0044, the "License"; You may not use this file except in compliance with
  * the License. You may obtain a copy of the License at:
  *                             www.st.com/SLA0044
  *
  ******************************************************************************
  */

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef PMSM_MOTOR_PARAMETERS_H
#define PMSM_MOTOR_PARAMETERS_H

/************************
 *** Motor Parameters ***
 ************************/

/***************** MOTOR ELECTRICAL PARAMETERS  ******************************/
#define POLE_PAIR_NUM           2 /* Number of motor pole pairs */
#define RS                      0.01 /* Stator resistance , ohm*/
#define LS                      0.000017 /* Stator inductance, H
                                                 For I-PMSM it is equal to Lq */

/* When using Id = 0, NOMINAL_CURRENT is utilized to saturate the output of the
   PID for speed regulation (i.e. reference torque).
   Transformation of real currents (A) into int16_t format must be done accordingly with
   formula:
   Phase current (int16_t 0-to-peak) = (Phase current (A 0-to-peak)* 32767 * Rshunt *
                                   *Amplifying network gain)/(MCU supply voltage/2)
*/

#define MOTOR_NAME              "DeWalt DCD996NT-TY2 Brus"  /*!< @brief Motor name */
#define MOTOR_RATED_FLUX        (float_t)(0.013006791) /*!< @brief Motor rated flux in V/Hz */
#define PID_SPD_KP              (float_t)(0.29673580049046194)  /*!< @brief PI Speed regulator proportional gain */
#define PID_SPD_KI              (float_t)(7.15693773016927)  /*!< @brief PI Speed regulator integral gain */
#define MOTOR_RS_SKINFACTOR     (float_t)(1.03)  /*!< @brief factor to compensate motor skin effect. */
#define MOTOR_MASS_COPPER_KG    (float_t)(0.2) /*!< @brief This is the total copper mass of the windings. */
#define MOTOR_COOLINGTAU_S      (float_t)(100.0)  /*!< @brief Thermal time constant of copper */

#define MOTOR_CURRENT_KP_ALIGN  (RS/4)
#define THROTTLE_OFFSET         0.0f
#define THROTTLE_GAIN           1.0f
#define MOTOR_MAX_SPEED_RPM     4000 /*!< Maximum rated speed  */
#define NOMINAL_CURRENT_A       12

#define ID_DEMAG_A              -5 /*!< Demagnetization current */

/***************** MOTOR SENSORS PARAMETERS  ******************************/
/* Motor sensors parameters are always generated but really meaningful only
   if the corresponding sensor is actually present in the motor         */

/*** Hall sensors ***/
#define HALL_SENSORS_PLACEMENT  DEGREES_120 /*!<Define here the
                                                 mechanical position of the sensors
                                                 withreference to an electrical cycle.
                                                 It can be either DEGREES_120 or
                                                 DEGREES_60 */

#define HALL_PHASE_SHIFT        300 /*!< Define here in degrees
                                                 the electrical phase shift between
                                                 the low to high transition of
                                                 signal H1 and the maximum of
                                                 the Bemf induced on phase A */
/*** Quadrature encoder ***/
#define M1_ENCODER_PPR          400  /*!< Number of pulses per
                                            revolution */

#endif /* PMSM_MOTOR_PARAMETERS_H */
/******************* (C) COPYRIGHT 2026 STMicroelectronics *****END OF FILE****/
