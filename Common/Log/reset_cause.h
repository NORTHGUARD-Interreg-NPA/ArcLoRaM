/*!
 * \file      reset_cause.h
 *
 * \brief     Why the MCU last reset: the RCC_CSR flags decoded into one word
 *            for the BOOT line (`rst=`).
 *
 * \details   Issue #101. A reboot used to say nothing about how the previous
 *            run ended. The CM4 reads RCC_CSR first thing after any reset
 *            (the CM0+ is released later), keeps the flags for the CM0+ in
 *            the shared region, and clears them (RMVF) so that the next
 *            reset starts from zero. Both cores print the same cause, since
 *            a reset of the STM32WL takes both.
 *
 *            The flags that go with each kind of reset are those the
 *            reference manual describes: PINRSTF is set by the other resets
 *            too (they pull NRST), BORRSTF by a power-up and a brown-out
 *            alike (the two cannot be told apart). So the decoder picks the
 *            most telling flag, not the first one set. The bench checks the
 *            flags on the boards (Test Record of #101).
 *
 *            The bit positions are those of RCC_CSR (stm32wl55xx.h); the
 *            glue asserts them against the device header at compile time.
 *
 * \author    Simon R.C. Langlais ( Celium )
 *
 */
#ifndef RESET_CAUSE_H
#define RESET_CAUSE_H

#include <stdint.h>

#define RESET_FLAG_RF     (1u << 14)   /*!< RFRSTF: the radio was reset (not the MCU) */
#define RESET_FLAG_RMVF   (1u << 23)   /*!< write 1 to clear the flags: not a flag itself */
#define RESET_FLAG_RFILA  (1u << 24)   /*!< RFILARSTF: radio illegal access (not the MCU) */
#define RESET_FLAG_OBL    (1u << 25)   /*!< OBLRSTF: option byte loader */
#define RESET_FLAG_PIN    (1u << 26)   /*!< PINRSTF: NRST pin */
#define RESET_FLAG_BOR    (1u << 27)   /*!< BORRSTF: power-up or brown-out */
#define RESET_FLAG_SFT    (1u << 28)   /*!< SFTRSTF: software (SYSRESETREQ) */
#define RESET_FLAG_IWDG   (1u << 29)   /*!< IWDGRSTF */
#define RESET_FLAG_WWDG   (1u << 30)   /*!< WWDGRSTF */
#define RESET_FLAG_LPWR   (1u << 31)   /*!< LPWRRSTF: illegal low-power entry */

/*! Every flag the record keeps (RMVF is a command, not a flag). */
#define RESET_FLAGS_MASK  (RESET_FLAG_RF | RESET_FLAG_RFILA | RESET_FLAG_OBL | RESET_FLAG_PIN | \
                           RESET_FLAG_BOR | RESET_FLAG_SFT | RESET_FLAG_IWDG | RESET_FLAG_WWDG | \
                           RESET_FLAG_LPWR)

typedef enum {
    RESET_CAUSE_NONE = 0,   /*!< no flag: a core restarted alone, or the flags were cleared */
    RESET_CAUSE_POWER,
    RESET_CAUSE_PIN,
    RESET_CAUSE_SOFT,
    RESET_CAUSE_IWDG,
    RESET_CAUSE_WWDG,
    RESET_CAUSE_LPWR,
    RESET_CAUSE_OBL,
} ResetCause_t;

/*!
 * \brief   The most telling cause of an RCC_CSR value.
 *
 * \details Flags accumulate until RMVF, so several can be set: a watchdog
 *          bite counts for more than the software reset that came before it.
 *          The low-power and watchdog resets first, then the software and the
 *          option byte ones, then the power-up, and the pin last, since the
 *          pin flag comes with all of them.
 */
static inline ResetCause_t ResetCause_Decode(uint32_t csr)
{
    if (csr & RESET_FLAG_LPWR) return RESET_CAUSE_LPWR;
    if (csr & RESET_FLAG_WWDG) return RESET_CAUSE_WWDG;
    if (csr & RESET_FLAG_IWDG) return RESET_CAUSE_IWDG;
    if (csr & RESET_FLAG_SFT)  return RESET_CAUSE_SOFT;
    if (csr & RESET_FLAG_OBL)  return RESET_CAUSE_OBL;
    if (csr & RESET_FLAG_BOR)  return RESET_CAUSE_POWER;
    if (csr & RESET_FLAG_PIN)  return RESET_CAUSE_PIN;
    return RESET_CAUSE_NONE;
}

static inline const char *ResetCause_Name(ResetCause_t cause)
{
    switch (cause) {
    case RESET_CAUSE_NONE:  return "none";
    case RESET_CAUSE_POWER: return "power";
    case RESET_CAUSE_PIN:   return "pin";
    case RESET_CAUSE_SOFT:  return "soft";
    case RESET_CAUSE_IWDG:  return "iwdg";
    case RESET_CAUSE_WWDG:  return "wwdg";
    case RESET_CAUSE_LPWR:  return "lpwr";
    case RESET_CAUSE_OBL:   return "obl";
    default:                return "?";
    }
}

#endif /* RESET_CAUSE_H */
