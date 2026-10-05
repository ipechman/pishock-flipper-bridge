#include "controller_core.h"
#include <string.h>
static bool number(const char *p, size_t n, uint32_t max, uint32_t *value) {
    uint32_t v = 0;
    size_t i;
    if (!n)
        return false;
    for (i = 0; i < n; i++) {
        uint32_t d;
        if (p[i] < '0' || p[i] > '9')
            return false;
        d = (uint32_t)(p[i] - '0');
        if (v > (max - d) / 10 || d > max)
            return false;
        v = v * 10 + d;
    }
    *value = v;
    return true;
}
bool controller_parse_target(const char *text, size_t length, ControllerTarget *out) {
    size_t pos = 0;
    unsigned seen = 0;
    ControllerTarget t = {0, 0};
    if (!text || !out || !length || length > 256 || memchr(text, 0, length))
        return false;
    while (pos < length) {
        size_t start = pos, n;
        unsigned bit;
        uint32_t v;
        while (pos < length && text[pos] != '\n')
            pos++;
        n = pos - start;
        if (pos < length)
            pos++;
        if (n && text[start + n - 1] == '\r')
            n--;
        if (n == 35 && memcmp(text + start, "Filetype: PiShock Controller Target", 35) == 0)
            bit = 1;
        else if (n == 10 && memcmp(text + start, "Version: 1", 10) == 0)
            bit = 2;
        else if (n > 11 && memcmp(text + start, "ShockerId: ", 11) == 0) {
            bit = 4;
            if (!number(text + start + 11, n - 11, 65535, &v) || !v)
                return false;
            t.id = (uint16_t)v;
        } else if (n > 9 && memcmp(text + start, "Channel: ", 9) == 0) {
            bit = 8;
            if (!number(text + start + 9, n - 9, 2, &v))
                return false;
            t.channel = (uint8_t)v;
        } else
            return false;
        if (seen & bit)
            return false;
        seen |= bit;
    }
    if (seen != 15)
        return false;
    *out = t;
    return true;
}
void controller_init(ControllerState *s, const ControllerTarget *t) {
    memset(s, 0, sizeof(*s));
    s->mode = 'b';
    s->duration_ms = 500;
    if (t && t->id && t->channel <= 2) {
        s->target = *t;
        s->target_valid = true;
    }
}
static bool reached(uint32_t now, uint32_t deadline) {
    return (uint32_t)(now - deadline) < UINT32_C(0x80000000);
}
/* The adapter consumes Halt before replacing DMA with the zero terminator. */
static ControllerAction terminate(ControllerState *s, uint32_t now) {
    s->phase = ControllerPhaseTerminating;
    s->deadline_ms = now + 300;
    s->ok_down = false;
    return ControllerActionHalt | ControllerActionStartTerminator;
}
ControllerAction controller_input(ControllerState *s, ControllerInput in, uint32_t now) {
    bool fresh = in.physical && (uint32_t)(now - in.timestamp_ms) <= 250;
    /* Firmware uses one 30-bit sequence for Press, Long, Repeat and Release of a hold. */
    bool newer = !s->sequence_seen ||
                 (in.sequence != s->last_sequence &&
                  ((in.sequence - s->last_sequence) & UINT32_C(0x3fffffff)) < UINT32_C(0x20000000));
    bool settings_repeat = in.type == ControllerInputRepeat && in.key != ControllerKeyOk &&
                           in.sequence == s->last_sequence;
    bool matching_cycle = in.key == ControllerKeyOk && s->ok_down &&
                          in.sequence == s->ok_press_sequence &&
                          (in.type == ControllerInputLong || in.type == ControllerInputRelease);
    /* Stops dominate even duplicated or delayed queue entries. */
    if (in.key == ControllerKeyBack &&
        (in.type == ControllerInputPress || in.type == ControllerInputLong)) {
        s->armed = false;
        s->ok_down = false;
        s->arm_release_required = false;
        if (in.type == ControllerInputLong)
            s->exit_pending = true;
        if (s->phase == ControllerPhaseOperating)
            return terminate(s, now);
        if (s->phase == ControllerPhaseTerminating)
            return ControllerActionNone;
        if (s->exit_pending) {
            s->phase = ControllerPhaseExiting;
            return ControllerActionHalt | ControllerActionExit;
        }
        return ControllerActionHalt;
    }
    if (!fresh || (!newer && !matching_cycle && !settings_repeat))
        return ControllerActionNone;
    if (newer) {
        s->last_sequence = in.sequence;
        s->sequence_seen = true;
    }
    if (s->input_barrier_valid && !reached(in.timestamp_ms, s->input_barrier_ms + 1))
        return ControllerActionNone;
    if (s->phase != ControllerPhaseIdle)
        return ControllerActionNone;
    if (in.key == ControllerKeyOk) {
        if (in.type == ControllerInputRelease) {
            s->ok_down = false;
            s->arm_release_required = false;
        } else if (in.type == ControllerInputPress && !s->ok_down) {
            s->ok_down = true;
            s->ok_press_sequence = in.sequence;
            if (s->armed && !s->arm_release_required && s->target_valid) {
                s->phase = ControllerPhaseOperating;
                s->deadline_ms = now + s->duration_ms;
                return ControllerActionStartOperation;
            }
        } else if (in.type == ControllerInputLong && s->ok_down && !s->armed && s->target_valid &&
                   in.sequence == s->ok_press_sequence) {
            s->armed = true;
            s->arm_release_required = true;
        }
        return ControllerActionNone;
    }
    if (in.type != ControllerInputPress && in.type != ControllerInputRepeat)
        return ControllerActionNone;
    if (in.key == ControllerKeyUp)
        s->selection = (ControllerSelection)(((unsigned)s->selection + 2) % 3);
    else if (in.key == ControllerKeyDown)
        s->selection = (ControllerSelection)(((unsigned)s->selection + 1) % 3);
    else if (in.key == ControllerKeyLeft || in.key == ControllerKeyRight) {
        bool up = in.key == ControllerKeyRight;
        s->armed = false;
        s->ok_down = false;
        s->arm_release_required = false;
        if (s->selection == ControllerSelectionMode) {
            unsigned m = s->mode == 'b' ? 0U : s->mode == 'v' ? 1U : 2U;
            m = (m + (up ? 1U : 2U)) % 3;
            s->mode = m == 0 ? 'b' : m == 1 ? 'v' : 's';
            if (s->mode == 'b')
                s->intensity = 0;
        } else if (s->selection == ControllerSelectionIntensity && s->mode != 'b') {
            if (up && s->intensity < 100)
                s->intensity++;
            else if (!up && s->intensity > 0)
                s->intensity--;
        } else if (s->selection == ControllerSelectionDuration) {
            if (up && s->duration_ms < 10000)
                s->duration_ms += 100;
            else if (!up && s->duration_ms > 100)
                s->duration_ms -= 100;
        }
    }
    return ControllerActionNone;
}
ControllerAction controller_tick(ControllerState *s, uint32_t now, bool complete) {
    if (s->phase == ControllerPhaseOperating && (complete || reached(now, s->deadline_ms)))
        return terminate(s, now);
    if (s->phase == ControllerPhaseTerminating && (complete || reached(now, s->deadline_ms))) {
        s->input_barrier_ms = now;
        s->input_barrier_valid = true;
        s->ok_down = false;
        s->phase = s->exit_pending ? ControllerPhaseExiting : ControllerPhaseIdle;
        return ControllerActionHalt | (s->exit_pending ? ControllerActionExit : 0U);
    }
    return ControllerActionNone;
}
ControllerAction controller_tx_failed(ControllerState *s) {
    /* No clock argument: conservatively invalidate input through the old deadline. */
    if (s->phase == ControllerPhaseOperating || s->phase == ControllerPhaseTerminating) {
        s->input_barrier_ms = s->deadline_ms;
        s->input_barrier_valid = true;
    }
    s->armed = false;
    s->ok_down = false;
    s->arm_release_required = false;
    s->phase = s->exit_pending ? ControllerPhaseExiting : ControllerPhaseIdle;
    return ControllerActionHalt | (s->exit_pending ? ControllerActionExit : 0U);
}
