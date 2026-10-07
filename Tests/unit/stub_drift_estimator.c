#include "stub_drift_estimator.h"
#include <string.h>

static DriftEstimate_t s_estimate;

void StubDriftEstimator_Set(DriftEstimate_t e) { s_estimate = e; }

void StubDriftEstimator_Reset(void) { memset(&s_estimate, 0, sizeof(s_estimate)); }

DriftEstimate_t DriftEstimator_Get(void) { return s_estimate; }
