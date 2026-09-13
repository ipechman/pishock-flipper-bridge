/* SPDX-License-Identifier: GPL-3.0-only
 * CaiXianlin encoding adapted from OpenShock/FlipperZero protocols.c,
 * commit 0457742b12f8853f1b09c73ad7e1b9dc4f8cc8c8 (GPLv3).
 * Protocol reference: https://wiki.openshock.org/hardware/shockers/caixianlin
 * Finite iteration and command parsing are specific to this application.
 */
#include "radio_core.h"

#include <stddef.h>
#include <string.h>

bool radio_sequence_init(
    RadioSequence* sequence,
    uint16_t id,
    uint8_t channel,
    char mode,
    uint8_t intensity,
    uint32_t duration_ms) {
    if(sequence == NULL) return false;
    memset(sequence, 0, sizeof(*sequence));
    if(channel > 2U || intensity > 100U || duration_ms < 56U) return false;

    uint8_t action;
    switch(mode) {
    case 's':
        action = 1U;
        break;
    case 'v':
        action = 2U;
        break;
    case 'b':
        action = 3U;
        intensity = 0U;
        break;
    default:
        return false;
    }
    if(intensity > 99U) intensity = 99U;

    uint32_t payload = ((uint32_t)id << 16U) | ((uint32_t)channel << 12U) |
                       ((uint32_t)action << 8U) | intensity;
    uint8_t checksum = (uint8_t)(
        (payload >> 24U) + ((payload >> 16U) & 0xFFU) +
        ((payload >> 8U) & 0xFFU) + (payload & 0xFFU));
    uint64_t data = (((uint64_t)payload << 8U) | checksum) << 3U;

    sequence->encoded[0] = (RadioPulsePair){1400U, 750U};
    for(uint8_t index = 0U; index < 43U; ++index) {
        bool bit = ((data >> (42U - index)) & 1U) != 0U;
        sequence->encoded[index + 1U] = bit ? (RadioPulsePair){750U, 250U} :
                                                  (RadioPulsePair){250U, 750U};
    }
    /* Use 64-bit arithmetic before multiplying milliseconds to avoid overflow.
     * Even UINT32_MAX milliseconds yields a repeat count that fits uint32_t.
     */
    sequence->repeats_remaining =
        (uint32_t)(((uint64_t)duration_ms * 1000U) / RADIO_CYCLE_US);
    sequence->phase_high = true;
    return sequence->repeats_remaining != 0U;
}

bool radio_sequence_next(RadioSequence* sequence, RadioPulse* pulse) {
    if(sequence == NULL || pulse == NULL || sequence->repeats_remaining == 0U) {
        return false;
    }

    if(sequence->pair_index == RADIO_FRAME_PAIR_COUNT) {
        *pulse = (RadioPulse){false, RADIO_GAP_US};
        sequence->pair_index = 0U;
        sequence->phase_high = true;
        --sequence->repeats_remaining;
        return true;
    }

    const RadioPulsePair* pair = &sequence->encoded[sequence->pair_index];
    if(sequence->phase_high) {
        *pulse = (RadioPulse){true, pair->high_us};
        sequence->phase_high = false;
    } else {
        *pulse = (RadioPulse){false, pair->low_us};
        sequence->phase_high = true;
        ++sequence->pair_index;
    }
    return true;
}

static bool is_separator(char value) {
    return value == ' ' || value == '\t';
}

static void skip_separators(const char** cursor) {
    while(is_separator(**cursor)) ++(*cursor);
}

static bool take_word(const char** cursor, const char* word) {
    skip_separators(cursor);
    size_t length = strlen(word);
    if(strncmp(*cursor, word, length) != 0) return false;
    char next = (*cursor)[length];
    if(next != '\0' && next != '\r' && next != '\n' && !is_separator(next)) {
        return false;
    }
    *cursor += length;
    return true;
}

static bool take_uint(const char** cursor, uint32_t minimum, uint32_t maximum, uint32_t* result) {
    skip_separators(cursor);
    if(**cursor < '0' || **cursor > '9') return false;
    uint32_t value = 0U;
    do {
        uint32_t digit = (uint32_t)(**cursor - '0');
        if(value > maximum / 10U ||
           (value == maximum / 10U && digit > maximum % 10U)) {
            return false;
        }
        value = value * 10U + digit;
        ++(*cursor);
    } while(**cursor >= '0' && **cursor <= '9');
    if(value < minimum) return false;
    if(**cursor != '\0' && **cursor != '\r' && **cursor != '\n' && !is_separator(**cursor)) {
        return false;
    }
    *result = value;
    return true;
}

static bool at_end(const char* cursor) {
    skip_separators(&cursor);
    if(*cursor == '\r') ++cursor;
    if(*cursor == '\n') ++cursor;
    return *cursor == '\0';
}

bool radio_parse_command(const char* line, RadioCommand* command) {
    if(line == NULL || command == NULL) return false;
    RadioCommand parsed = {0};
    const char* cursor = line;
    uint32_t value;
    bool is_replace = false;

    if(take_word(&cursor, "HELLO")) {
        parsed.type = RadioCommandHello;
    } else if(take_word(&cursor, "PING")) {
        parsed.type = RadioCommandPing;
    } else if(take_word(&cursor, "SET")) {
        parsed.type = RadioCommandSet;
        if(!take_uint(&cursor, 1U, UINT16_MAX, &value)) return false;
        parsed.id = (uint16_t)value;
        if(!take_uint(&cursor, 0U, 2U, &value)) return false;
        parsed.channel = (uint8_t)value;
    } else if(take_word(&cursor, "RUN") || (is_replace = take_word(&cursor, "REPLACE"))) {
        parsed.type = is_replace ? RadioCommandReplace : RadioCommandRun;
        if(!take_uint(&cursor, 1U, UINT32_MAX, &parsed.sequence)) return false;
        skip_separators(&cursor);
        parsed.mode = *cursor;
        if(parsed.mode != 's' && parsed.mode != 'v' && parsed.mode != 'b') return false;
        ++cursor;
        if(!is_separator(*cursor)) return false;
        if(!take_uint(&cursor, 0U, 100U, &value)) return false;
        parsed.intensity = (uint8_t)value;
        if(!take_uint(&cursor, 100U, 10000U, &parsed.duration_ms)) return false;
    } else if(take_word(&cursor, "STOP")) {
        parsed.type = RadioCommandStop;
    } else if(take_word(&cursor, "DISARM")) {
        parsed.type = RadioCommandDisarm;
    } else {
        return false;
    }

    if(!at_end(cursor)) return false;
    *command = parsed;
    return true;
}
