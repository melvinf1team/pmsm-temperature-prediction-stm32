/* =============
Copyright (c) 2026, STMicroelectronics

All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted
provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this list of conditions
  and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice, this list of
  conditions and the following disclaimer in the documentation and/or other materials provided
  with the distribution.

* Neither the name of the copyright holders nor the names of its contributors may be used to
  endorse or promote products derived from this software without specific prior written
  permission.

*THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR
 IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER /
 OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
 CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
 OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 POSSIBILITY OF SUCH DAMAGE.*
*/


#ifndef NANOEDGEAI_H
#define NANOEDGEAI_H

#include <stdint.h>

/* NEAI ID */
#define NEAI_ID "6a8c351467dc61da627e5d30"

/* Input signal configuration */
#define NEAI_INPUT_SIGNAL_LENGTH 1
#define NEAI_INPUT_AXIS_NUMBER 55

/* NEAI State Enum */
enum neai_state {
	NEAI_OK = 0,
	NEAI_ERROR = 1,
	NEAI_NOT_INITIALIZED = 2,
	NEAI_INVALID_PARAM = 3,
	NEAI_NOT_SUPPORTED = 4,
	NEAI_LEARNING_DONE = 5,
	NEAI_LEARNING_IN_PROGRESS = 6
};


#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief  Must be called at the beginning to initialize the regression model by loading the
 *         pretrained model.
 * @return NEAI_OK on success, error code otherwise.
 */
enum neai_state neai_extrapolation_init(void);

/**
 * @brief  Perform regression on a new input sample by returning the predicted output values.
 * @param  in         [in]   Pointer to the input signal array
 *                           (size NEAI_INPUT_SIGNAL_LENGTH * NEAI_INPUT_AXIS_NUMBER).
 * @param  prediction [out]  Pointer to the predicted output values.
 * @return NEAI_OK on success.
 *         Error code otherwise.
 */
enum neai_state neai_extrapolation(float *in, float *prediction);

/* ===== Common getter functions ===== */
/**
 * @brief  Get the NEAI identifier.
 * @return Pointer to a string containing the NEAI ID.
 */
char* neai_get_id(void);

/**
 * @brief  Get the input signal size (number of samples per axis).
 * @return Input signal size.
 */
int neai_get_input_signal_size(void);

/**
 * @brief  Get the number of input axes/channels.
 * @return Number of input axes.
 */
int neai_get_axis_number(void);


#ifdef __cplusplus
}
#endif

#endif /* NANOEDGEAI_H */


/* =============
Declarations to add to your main program to use the NanoEdge AI library.
You may copy-paste them directly or rename variables as needed.
WARNING: Respect the structures, types, and buffer sizes; only variable names may be changed.

enum neai_state state;   // Captures return states from NEAI functions
float input_signal[NEAI_INPUT_SIGNAL_LENGTH * NEAI_INPUT_AXIS_NUMBER];   // Input signal buffer
float prediction;  // Predicted value by regression function
============= */
