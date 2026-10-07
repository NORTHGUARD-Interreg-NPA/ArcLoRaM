/*
 * Link stub of the drift estimator for the TDMA machine tests (issue #36).
 *
 * The TDMA machine takes its guard from DriftEstimator_Get() through the guard
 * time resolver. A test that links this file instead of drift_estimator.c sets
 * the estimate directly, so it does not depend on how the estimator fits. The
 * default (a zeroed estimate) is "not valid": the guard is the cap, as it was
 * before the guard followed the estimate.
 */
#ifndef STUB_DRIFT_ESTIMATOR_H
#define STUB_DRIFT_ESTIMATOR_H

#include "drift_estimator.h"

/*! The estimate DriftEstimator_Get() returns from now on. */
void StubDriftEstimator_Set(DriftEstimate_t e);

/*! Back to the default: not valid. Call it from setUp. */
void StubDriftEstimator_Reset(void);

#endif /* STUB_DRIFT_ESTIMATOR_H */
