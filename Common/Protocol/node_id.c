/*!
 * \file      node_id.c
 *
 * \brief     Node ID table and lookup.
 *
 * \code
 *              ____  ______  _         ___   _   _  __  __
 *             / ___||  ____|| |       |_ _| | | | ||  \/  |
 *            | |    | |__   | |        | |  | | | || |\/| |
 *            | |___ |  __|  | |___    _| |_ | |_| || |  | |
 *             \____||______| \_____| |_____| \___/ |_|  |_|
 *            (C)2025-2026 Celium
 *
 * \endcode
 *
 * \author    Simon R.C. Langlais ( Celium )
 */
#include "node_id.h"

#ifndef HOST_TEST
#include "stm32wlxx_hal.h"   /* HAL_GetUIDw0/1/2 */
#endif

/* =========================================================================
 * Registered boards
 *
 * To register a board, copy the entry that arclog prints for its
 * "BOOT id=0 uid=..." line, with the next free Node ID (1-254).
 * Node IDs and UIDs must be unique (checked by Tests/unit/test_node_id.c).
 * ========================================================================= */

static const NodeIdEntry_t k_table[] = {
    /* { { w0, w1, w2 }, node_id }, */
    { { 0x0014008Fu, 0x32325014u, 0x20383543u }, 1u },   /* bench C3 */
    { { 0x0026001Au, 0x32325014u, 0x20383543u }, 2u },   /* bench C2 */
    { { 0x004100ADu, 0x32325014u, 0x20383543u }, 3u },   /* bench C2, second */
    { { 0x00340044u, 0x32325014u, 0x20383543u }, 4u },   /* bench C2, third */
    { { 0x0014001Bu, 0x32325014u, 0x20383543u }, 5u },   /* Pi Node nuna-node-01, remote C3 */
    { { 0x001E0011u, 0x4C42500Du, 0x20363552u }, 6u },   /* Pi Node nuna-node-08 */
    { { 0x00090024u, 0x4C42500Du, 0x20363552u }, 7u },   /* Pi Node nuna-node-09 */
    { { 0x002E0026u, 0x4C42500Du, 0x20363552u }, 8u },   /* new Pi Node board, first flashed on a local ST-LINK */

    /* End marker: keeps the array non-empty, not counted. */
    { { 0u, 0u, 0u }, NODE_ID_UNPROVISIONED },
};

#define TABLE_COUNT  ((sizeof(k_table) / sizeof(k_table[0])) - 1u)

/* =========================================================================
 * Public API
 * ========================================================================= */

const NodeIdEntry_t *NodeId_Table(size_t *count)
{
    *count = TABLE_COUNT;
    return k_table;
}

uint8_t NodeId_Find(const NodeIdEntry_t *table, size_t count,
                    const uint32_t uid[NODE_UID_WORDS])
{
    for (size_t i = 0u; i < count; i++) {
        if (table[i].uid[0] == uid[0] &&
            table[i].uid[1] == uid[1] &&
            table[i].uid[2] == uid[2]) {
            return table[i].node_id;
        }
    }
    return NODE_ID_UNPROVISIONED;
}

uint8_t NodeId_FromUid(const uint32_t uid[NODE_UID_WORDS])
{
    return NodeId_Find(k_table, TABLE_COUNT, uid);
}

#ifndef HOST_TEST
void NodeId_ReadUid(uint32_t uid[NODE_UID_WORDS])
{
    uid[0] = HAL_GetUIDw0();
    uid[1] = HAL_GetUIDw1();
    uid[2] = HAL_GetUIDw2();
}

uint8_t NodeId_Self(void)
{
    uint32_t uid[NODE_UID_WORDS];
    NodeId_ReadUid(uid);
    return NodeId_FromUid(uid);
}
#endif
