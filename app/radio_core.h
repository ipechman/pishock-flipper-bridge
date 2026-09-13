#ifndef PISHOCK_RADIO_CORE_H
#define PISHOCK_RADIO_CORE_H

#include <stdbool.h>
#include <stdint.h>

#define RADIO_FRAME_PAIR_COUNT 44U
#define RADIO_FRAME_US 45150U
#define RADIO_GAP_US 10000U
#define RADIO_CYCLE_US (RADIO_FRAME_US + RADIO_GAP_US)

typedef struct {
    bool level;
    uint32_t duration_us;
} RadioPulse;

typedef struct {
    uint16_t high_us;
    uint16_t low_us;
} RadioPulsePair;

typedef struct {
    RadioPulsePair encoded[RADIO_FRAME_PAIR_COUNT];
    uint32_t repeats_remaining;
    uint8_t pair_index;
    bool phase_high;
} RadioSequence;

typedef enum {
    RadioCommandHello,
    RadioCommandPing,
    RadioCommandSet,
    RadioCommandRun,
    RadioCommandReplace,
    RadioCommandStop,
    RadioCommandDisarm,
} RadioCommandType;

typedef struct {
    RadioCommandType type;
    uint32_t sequence;
    uint16_t id;
    uint8_t channel;
    uint8_t intensity;
    char mode;
    uint32_t duration_ms;
} RadioCommand;

/* Encode one CaiXianlin frame and bound its repeats, including each 10 ms gap,
 * by duration_ms. Invalid arguments or duration_ms < 56 leave an empty sequence.
 * Mode is s=shock, v=vibrate, b=beep. Intensity 100 maps to the RF maximum 99;
 * beep always uses zero. This function does not emit a termination command.
 */
bool radio_sequence_init(
    RadioSequence* sequence,
    uint16_t id,
    uint8_t channel,
    char mode,
    uint8_t intensity,
    uint32_t duration_ms);

/* Return the next high/low duration, or false after the finite sequence ends.
 * A sequence belongs to one caller; do not modify it during transmission.
 */
bool radio_sequence_next(RadioSequence* sequence, RadioPulse* pulse);

/* Parse one command. Space/tab separators and an optional final CR/LF are
 * accepted; numeric signs, overflows, extra tokens and embedded newlines are
 * rejected. The output is unchanged when parsing fails.
 */
bool radio_parse_command(const char* line, RadioCommand* command);

#endif
