# =============================================================================
# add_unity_test(TEST_NAME  src1.c  [src2.c ...])
#
# Creates a test executable and registers it with CTest.
#
#   TEST_NAME  — name used for the executable and for "ctest -R <name>"
#   src files  — the test file PLUS every .c file you want to test
#
# The paths to source files are relative to THIS directory (Tests/unit/).
# Use ../../ to reach the project root.  Examples:
#
#   add_unity_test(test_cell_eligibility
#       test_cell_eligibility.c
#       ../../Common/Protocol/cell_eligibility.c
#   )
#
#   add_unity_test(test_cm4_scheduler
#       test_cm4_scheduler.c
#       ../../CM4/SubGHz_Phy/Logic/scheduler.c
#   )
# =============================================================================
# The Sync profile the host tests build with: the bring-up table (a Sync phase
# every 30 s, three packets), which the state machine and TDMA tests were
# written against. A test of another profile sets TEST_SYNC_PROFILE before
# add_unity_test (empty: the source default).
set(TEST_SYNC_PROFILE SYNC_PROFILE_BRINGUP)

macro(add_unity_test TEST_NAME)
    # Create an executable from all sources listed after TEST_NAME
    add_executable(${TEST_NAME} ${ARGN})

    if(TEST_SYNC_PROFILE)
        target_compile_definitions(${TEST_NAME} PRIVATE SYNC_PROFILE=${TEST_SYNC_PROFILE})
    endif()

    # Link the Unity framework and the HAL stubs
    target_link_libraries(${TEST_NAME} PRIVATE unity hal_stubs)

    # Expose the Logic source directories so #include "my_module.h" resolves.
    # CMAKE_SOURCE_DIR is Tests/, so ../ goes to ArcLoRaM_Base/.
    target_include_directories(${TEST_NAME} PRIVATE
        ${CMAKE_SOURCE_DIR}/../CM4/SubGHz_Phy/Logic
        ${CMAKE_SOURCE_DIR}/../CM0PLUS/SubGHz_Phy/Logic
        ${CMAKE_SOURCE_DIR}/../Common/SharedMemory
        ${CMAKE_SOURCE_DIR}/../Common/Protocol
    )

    # Register with CTest — running "ctest" will execute this binary
    add_test(NAME ${TEST_NAME} COMMAND ${TEST_NAME})
endmacro()

# =============================================================================
# Tests
# =============================================================================

# Placeholder — verifies the toolchain works. Delete once real tests exist.
add_unity_test(test_placeholder
    test_placeholder.c
)

# -----------------------------------------------------------------------------
# ADD NEW TESTS HERE using the macro above:
#
# add_unity_test(test_my_module
#     test_my_module.c
#     ../../Common/Protocol/my_module.c
# )
# -----------------------------------------------------------------------------

add_unity_test(test_tdma_table
    test_tdma_table.c
    ../../Common/Protocol/tdma_table.c
)

add_unity_test(test_day_ms
    test_day_ms.c
)

# The Sync profiles against the 1 % duty cycle: the bring-up table (denied after
# ~7 min), DEV and PROD (never denied in 4 h), and the source default (DEV).
foreach(prof BRINGUP DEV PROD)
    set(TEST_SYNC_PROFILE SYNC_PROFILE_${prof})
    string(TOLOWER ${prof} prof_lower)
    add_unity_test(test_sync_budget_${prof_lower}
        test_sync_budget.c
        ../../Common/Protocol/tdma_table.c
        ../../CM0PLUS/SubGHz_Phy/Logic/compliance_engine.c
        ../../CM0PLUS/SubGHz_Phy/Logic/mac_state_machine_c3.c
    )
endforeach()
# DEV with the largest boot burst (a whole phase of packets): paid from the full credit.
set(TEST_SYNC_PROFILE SYNC_PROFILE_DEV)
add_unity_test(test_sync_budget_dev_burst
    test_sync_budget.c
    ../../Common/Protocol/tdma_table.c
    ../../CM0PLUS/SubGHz_Phy/Logic/compliance_engine.c
    ../../CM0PLUS/SubGHz_Phy/Logic/mac_state_machine_c3.c
)
target_compile_definitions(test_sync_budget_dev_burst PRIVATE SYNC_BOOT_BURST=10u)
set(TEST_SYNC_PROFILE "")
add_unity_test(test_sync_profile_default
    test_sync_profile.c
)
set(TEST_SYNC_PROFILE SYNC_PROFILE_BRINGUP)

# The MAC takes the SyncStamp in ticks and judges the error in us (C1 and C2 MAC).
foreach(cls c1 c2)
    add_unity_test(test_mac_fine_stamp_${cls}
        test_mac_fine_stamp.c
        ../../CM0PLUS/SubGHz_Phy/Logic/mac_state_machine_${cls}.c
        ../../Common/Protocol/tdma_table.c
    )
endforeach()

# The C3's boot burst (SYNC_BOOT_BURST=5), on the DEV profile (one packet per phase).
set(TEST_SYNC_PROFILE SYNC_PROFILE_DEV)
add_unity_test(test_mac_sync_boot_burst_c3
    test_mac_sync_boot_burst_c3.c
    ../../CM0PLUS/SubGHz_Phy/Logic/mac_state_machine_c3.c
    ../../Common/Protocol/tdma_table.c
)
target_compile_definitions(test_mac_sync_boot_burst_c3 PRIVATE SYNC_BOOT_BURST=5u)
set(TEST_SYNC_PROFILE SYNC_PROFILE_BRINGUP)

# Timing probe helper: with BENCH_PROBE (lines, wrap, limit) and production (nothing).
add_unity_test(test_probe
    test_probe.c
    ../../Common/Log/probe.c
)
target_compile_definitions(test_probe PRIVATE BENCH_PROBE)
add_unity_test(test_probe_off
    test_probe.c
    ../../Common/Log/probe.c
)

add_unity_test(test_sync_stamp
    test_sync_stamp.c
)

add_unity_test(test_lora_toa
    test_lora_toa.c
)

add_unity_test(test_rtc_set_plan
    test_rtc_set_plan.c
)

add_unity_test(test_rtc_calr
    test_rtc_calr.c
)

add_unity_test(test_calr_policy
    test_calr_policy.c
)

# The formula of the guard is tested with the Rx start latency pinned to 0, so that its
# hand-worked values do not move with the measured latency; the default latency is
# pinned by test_guard_policy_default, and where the latency enters by _latency.
add_unity_test(test_guard_policy
    test_guard_policy.c
)
target_compile_definitions(test_guard_policy PRIVATE GUARD_RX_START_LATENCY_US=0u)

# The constants as they ship, the measured Rx start latency included.
add_unity_test(test_guard_policy_default
    test_guard_policy_default.c
)

# This build gives the Rx start latency another value, to pin where it enters.
add_unity_test(test_guard_policy_latency
    test_guard_policy_latency.c
)
target_compile_definitions(test_guard_policy_latency PRIVATE GUARD_RX_START_LATENCY_US=2000u)

add_unity_test(test_drift_estimator
    test_drift_estimator.c
    ../../CM0PLUS/SubGHz_Phy/Logic/drift_estimator.c
)
target_link_libraries(test_drift_estimator PRIVATE m)

add_unity_test(test_date_bcd
    test_date_bcd.c
)

add_unity_test(test_protocol_types
    test_protocol_types.c
)

add_unity_test(test_freq_resolver
    test_freq_resolver.c
    ../../CM0PLUS/SubGHz_Phy/Logic/freq_resolver.c
    ../../Common/SharedMemory/shared_mem.c
)

add_unity_test(test_compliance_engine
    test_compliance_engine.c
    ../../CM0PLUS/SubGHz_Phy/Logic/compliance_engine.c
)

add_unity_test(test_mac_state_machine_c1
    test_mac_state_machine_c1.c
    ../../CM0PLUS/SubGHz_Phy/Logic/mac_state_machine_c1.c
    ../../Common/Protocol/tdma_table.c
)

add_unity_test(test_mac_state_machine_c2
    test_mac_state_machine_c2.c
    ../../CM0PLUS/SubGHz_Phy/Logic/mac_state_machine_c2.c
    ../../Common/Protocol/tdma_table.c
)

add_unity_test(test_mac_state_machine_c3
    test_mac_state_machine_c3.c
    ../../CM0PLUS/SubGHz_Phy/Logic/mac_state_machine_c3.c
    ../../Common/Protocol/tdma_table.c
)

add_unity_test(test_tdma_machine_c2
    test_tdma_machine_c2.c
    stub_drift_estimator.c
    ../../CM0PLUS/SubGHz_Phy/Logic/tdma_machine.c
    ../../CM0PLUS/SubGHz_Phy/Logic/mac_state_machine_c2.c
    ../../CM0PLUS/SubGHz_Phy/Logic/freq_resolver.c
    ../../CM0PLUS/SubGHz_Phy/Logic/compliance_engine.c
    ../../CM0PLUS/SubGHz_Phy/Logic/guard_time_resolver.c
    ../../Common/Protocol/tdma_table.c
    ../../Common/SharedMemory/shared_mem.c
)
target_compile_definitions(test_tdma_machine_c2 PRIVATE NODE_CLASS=2 GUARD_RX_START_LATENCY_US=0u)

add_unity_test(test_tdma_machine_c1
    test_tdma_machine_c1.c
    stub_drift_estimator.c
    ../../CM0PLUS/SubGHz_Phy/Logic/tdma_machine.c
    ../../CM0PLUS/SubGHz_Phy/Logic/mac_state_machine_c1.c
    ../../CM0PLUS/SubGHz_Phy/Logic/freq_resolver.c
    ../../CM0PLUS/SubGHz_Phy/Logic/compliance_engine.c
    ../../CM0PLUS/SubGHz_Phy/Logic/guard_time_resolver.c
    ../../Common/Protocol/tdma_table.c
    ../../Common/SharedMemory/shared_mem.c
)
target_compile_definitions(test_tdma_machine_c1 PRIVATE NODE_CLASS=1)

add_unity_test(test_tdma_machine_c2_2sync
    test_tdma_machine_c2_2sync.c
    stub_drift_estimator.c
    stub_tdma_table_2sync.c
    ../../CM0PLUS/SubGHz_Phy/Logic/tdma_machine.c
    ../../CM0PLUS/SubGHz_Phy/Logic/mac_state_machine_c2.c
    ../../CM0PLUS/SubGHz_Phy/Logic/freq_resolver.c
    ../../CM0PLUS/SubGHz_Phy/Logic/compliance_engine.c
    ../../CM0PLUS/SubGHz_Phy/Logic/guard_time_resolver.c
    ../../Common/SharedMemory/shared_mem.c
)
target_compile_definitions(test_tdma_machine_c2_2sync PRIVATE NODE_CLASS=2)

# The Rx guard outside the Sync phase: a peer sender, and a contention slot (issue #36).
add_unity_test(test_tdma_machine_c2_phases
    test_tdma_machine_c2_phases.c
    stub_drift_estimator.c
    stub_tdma_table_contention.c
    ../../CM0PLUS/SubGHz_Phy/Logic/tdma_machine.c
    ../../CM0PLUS/SubGHz_Phy/Logic/mac_state_machine_c2.c
    ../../CM0PLUS/SubGHz_Phy/Logic/freq_resolver.c
    ../../CM0PLUS/SubGHz_Phy/Logic/compliance_engine.c
    ../../CM0PLUS/SubGHz_Phy/Logic/guard_time_resolver.c
    ../../Common/SharedMemory/shared_mem.c
)
target_compile_definitions(test_tdma_machine_c2_phases PRIVATE NODE_CLASS=2 GUARD_RX_START_LATENCY_US=0u)

add_unity_test(test_tdma_machine_c3
    test_tdma_machine_c3.c
    ../../CM0PLUS/SubGHz_Phy/Logic/tdma_machine.c
    ../../CM0PLUS/SubGHz_Phy/Logic/mac_state_machine_c3.c
    ../../CM0PLUS/SubGHz_Phy/Logic/freq_resolver.c
    ../../CM0PLUS/SubGHz_Phy/Logic/compliance_engine.c
    ../../CM0PLUS/SubGHz_Phy/Logic/guard_time_resolver.c
    ../../Common/Protocol/tdma_table.c
    ../../Common/SharedMemory/shared_mem.c
)
target_compile_definitions(test_tdma_machine_c3 PRIVATE NODE_CLASS=3)

add_unity_test(test_tdma_machine_c3_beacon
    test_tdma_machine_c3_beacon.c
    stub_tdma_table_beacon.c
    ../../CM0PLUS/SubGHz_Phy/Logic/tdma_machine.c
    ../../CM0PLUS/SubGHz_Phy/Logic/mac_state_machine_c3.c
    ../../CM0PLUS/SubGHz_Phy/Logic/freq_resolver.c
    ../../CM0PLUS/SubGHz_Phy/Logic/compliance_engine.c
    ../../CM0PLUS/SubGHz_Phy/Logic/guard_time_resolver.c
    ../../Common/SharedMemory/shared_mem.c
)
target_compile_definitions(test_tdma_machine_c3_beacon PRIVATE NODE_CLASS=3)

add_unity_test(test_cm4_diag
    test_cm4_diag.c
    ../../CM4/SubGHz_Phy/Logic/cm4_diag.c
)

add_unity_test(test_rtc_ssr
    test_rtc_ssr.c
)

add_unity_test(test_node_id
    test_node_id.c
    ../../Common/Protocol/node_id.c
)
