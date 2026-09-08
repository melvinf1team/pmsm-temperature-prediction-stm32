#ifndef DATALOG_MODULE_H
#define DATALOG_MODULE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct
{
  const char *csv_columns;

  void     (*init)(void);
  bool     (*start_measurement)(void);
  bool     (*read_csv_values)(char *dst, size_t dst_len);

  uint32_t conversion_time_ms;

} DatalogModule_t;

#endif /* DATALOG_MODULE_H */