#ifndef PISHOCK_RADIO_CORE_H
#define PISHOCK_RADIO_CORE_H

#include <stdbool.h>
#include <stdint.h>

#define RADIO_FRAME_PAIR_COUNT 44U
#define RADIO_FRAME_US 45150U
#define RADIO_GAP_US 10000U
#define RADIO_CYCLE_US (RADIO_FRAME_US + RADIO_GAP_US)
#define RADIO_KEEPALIVE_INTERVAL_MS 60000U
#define RADIO_TERMINATOR_MS 300U

typedef enum { RadioIdle, RadioOperating, RadioEnding, RadioKeeping } RadioPhase;

typedef struct {
    bool enabled;
    uint32_t last_activity;
    uint32_t interval;
} RadioKeepAlive;

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
    RadioCommandAwake,
} RadioCommandType;

typedef struct {
    RadioCommandType type;
    uint32_t sequence;
    uint16_t id;
    uint8_t channel;
    uint8_t intensity;
    char mode;
    uint32_t duration_ms;
    bool enabled;
} RadioCommand;

/* The host explicitly enables maintenance only for a ready backend session.
 * All times use one caller-selected wrapping uint32 clock. No immediate radio
 * work is requested on enable; activity postpones maintenance by one interval.
 * Losing either the configured target or USB lease disables the gate until the
 * host explicitly enables it again. Physical arming is independent of this
 * zero-intensity maintenance gate.
 */
bool radio_keepalive_enable(
    RadioKeepAlive* state, uint32_t now, uint32_t interval, bool configured, bool lease_valid);
void radio_keepalive_disable(RadioKeepAlive* state);
void radio_keepalive_activity(RadioKeepAlive* state, uint32_t now);
bool radio_keepalive_due(
    RadioKeepAlive* state, uint32_t now, bool configured, bool lease_valid, bool idle);

/* Maintenance is lower priority than every accepted user operation. */
bool radio_phase_accepts_run(RadioPhase phase, bool replace);

/* A CaiXianlin keepalive is the same vibrate-zero frame as a stop terminator.
 * Its packet has no duration field: use a finite radio burst, never a beep or
 * a nonzero stimulation intensity.
 */
bool radio_keepalive_sequence_init(RadioSequence* sequence, uint16_t id, uint8_t channel);

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
