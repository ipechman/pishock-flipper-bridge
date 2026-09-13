#include "../app/radio_core.h"

#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

static uint64_t decode_frame(const RadioSequence* sequence) {
    assert(sequence->encoded[0].high_us == 1400U);
    assert(sequence->encoded[0].low_us == 750U);
    uint64_t data = 0U;
    for(size_t index = 1U; index < RADIO_FRAME_PAIR_COUNT; ++index) {
        RadioPulsePair pair = sequence->encoded[index];
        assert((pair.high_us == 750U && pair.low_us == 250U) ||
               (pair.high_us == 250U && pair.low_us == 750U));
        data = (data << 1U) | (pair.high_us == 750U ? 1U : 0U);
    }
    assert((data & 7U) == 0U);
    return data >> 3U;
}

static void test_vectors(void) {
    /* First vector is a published remote capture, independent of this code:
     * Nat-the-Kat/caixianlin_remote_shocker, "remote signal decoding.txt".
     * Other vectors cover channel bits, checksum wrap and the stop frame.
     */
    const struct {
        uint16_t id;
        uint8_t channel;
        char mode;
        uint8_t intensity;
        uint64_t expected;
    } vectors[] = {
        {0xDECBU, 0U, 's', 1U, UINT64_C(0xDECB0101AB)},
        {0x1234U, 0U, 'b', 0U, UINT64_C(0x1234030049)},
        {0x1234U, 0U, 'b', 100U, UINT64_C(0x1234030049)},
        {0x1234U, 0U, 'v', 0U, UINT64_C(0x1234020048)},
        {0x1234U, 2U, 'v', 25U, UINT64_C(0x1234221981)},
        {0xFFFFU, 2U, 's', 99U, UINT64_C(0xFFFF216382)},
        {0xFFFFU, 2U, 's', 100U, UINT64_C(0xFFFF216382)},
        {0U, 0U, 'b', 0U, UINT64_C(0x0000030003)},
    };
    for(size_t index = 0U; index < sizeof(vectors) / sizeof(vectors[0]); ++index) {
        RadioSequence sequence;
        assert(radio_sequence_init(
            &sequence,
            vectors[index].id,
            vectors[index].channel,
            vectors[index].mode,
            vectors[index].intensity,
            100U));
        assert(decode_frame(&sequence) == vectors[index].expected);
    }
}

static void test_finite_sequences(void) {
    /* Sweep every accepted RUN duration: this verifies finite, complete frames,
     * exact waveform timing, and a hard upper bound on generated airtime.
     */
    for(uint32_t duration = 56U; duration <= 10000U; ++duration) {
        RadioSequence sequence;
        assert(radio_sequence_init(&sequence, 0x1234U, 0U, 'b', 0U, duration));
        uint32_t expected_frames = duration * 1000U / RADIO_CYCLE_US;
        uint64_t elapsed = 0U;
        uint32_t pulses = 0U;
        RadioPulse pulse;
        while(radio_sequence_next(&sequence, &pulse)) {
            uint32_t position = pulses % (RADIO_FRAME_PAIR_COUNT * 2U + 1U);
            assert(pulse.duration_us != 0U);
            if(position == RADIO_FRAME_PAIR_COUNT * 2U) {
                assert(!pulse.level);
                assert(pulse.duration_us == RADIO_GAP_US);
            } else {
                RadioPulsePair pair = sequence.encoded[position / 2U];
                assert(pulse.level == (position % 2U == 0U));
                assert(pulse.duration_us ==
                       (uint32_t)(pulse.level ? pair.high_us : pair.low_us));
            }
            elapsed += pulse.duration_us;
            ++pulses;
            assert(elapsed <= (uint64_t)duration * 1000U);
            assert(pulses <= expected_frames * (RADIO_FRAME_PAIR_COUNT * 2U + 1U));
        }
        assert(pulses == expected_frames * (RADIO_FRAME_PAIR_COUNT * 2U + 1U));
        assert(elapsed == (uint64_t)expected_frames * RADIO_CYCLE_US);
        assert(!radio_sequence_next(&sequence, &pulse));
        assert(!radio_sequence_next(&sequence, &pulse));
    }

    RadioSequence sequence;
    RadioPulse pulse;
    for(uint32_t duration = 0U; duration < 56U; ++duration) {
        assert(!radio_sequence_init(&sequence, 1U, 0U, 'v', 0U, duration));
        assert(!radio_sequence_next(&sequence, &pulse));
    }
    assert(radio_sequence_init(&sequence, 1U, 0U, 'v', 0U, UINT32_MAX));
    assert(sequence.repeats_remaining ==
           (uint32_t)(((uint64_t)UINT32_MAX * 1000U) / RADIO_CYCLE_US));
    assert(!radio_sequence_init(&sequence, 1U, 3U, 'v', 0U, 100U));
    assert(!radio_sequence_next(&sequence, &pulse));
    assert(!radio_sequence_init(&sequence, 1U, 0U, 'x', 0U, 100U));
    assert(!radio_sequence_next(&sequence, &pulse));
    assert(!radio_sequence_init(&sequence, 1U, 0U, 'v', 101U, 100U));
    assert(!radio_sequence_next(&sequence, &pulse));
    assert(!radio_sequence_init(NULL, 1U, 0U, 'v', 0U, 100U));
    assert(!radio_sequence_next(NULL, &pulse));
    assert(!radio_sequence_next(&sequence, NULL));
}

static void test_valid_commands(void) {
    RadioCommand command;
    assert(radio_parse_command("HELLO", &command));
    assert(command.type == RadioCommandHello);
    assert(radio_parse_command(" \tPING \r\n", &command));
    assert(command.type == RadioCommandPing);
    assert(radio_parse_command("AWAKE 1", &command));
    assert(command.type == RadioCommandAwake && command.enabled);
    assert(radio_parse_command("AWAKE 0\r\n", &command));
    assert(command.type == RadioCommandAwake && !command.enabled);
    assert(radio_parse_command("STOP\n", &command));
    assert(command.type == RadioCommandStop);
    assert(radio_parse_command("DISARM\r", &command));
    assert(command.type == RadioCommandDisarm);
    assert(radio_parse_command("SET 1 0", &command));
    assert(command.type == RadioCommandSet && command.id == 1U && command.channel == 0U);
    assert(radio_parse_command("SET\t65535\t2", &command));
    assert(command.type == RadioCommandSet && command.id == 65535U && command.channel == 2U);
    assert(radio_parse_command("RUN 1 v 0 100", &command));
    assert(command.type == RadioCommandRun && command.sequence == 1U && command.mode == 'v');
    assert(command.intensity == 0U && command.duration_ms == 100U);
    assert(radio_parse_command("RUN 4294967295 s 100 10000", &command));
    assert(command.sequence == UINT32_MAX && command.mode == 's');
    assert(command.intensity == 100U && command.duration_ms == 10000U);
    assert(radio_parse_command("RUN 0002 b 001 0100", &command));
    assert(command.sequence == 2U && command.mode == 'b');
    assert(command.intensity == 1U && command.duration_ms == 100U);
    assert(radio_parse_command("REPLACE 3 b 0 500", &command));
    assert(command.type == RadioCommandReplace && command.sequence == 3U);
    assert(command.mode == 'b' && command.intensity == 0U && command.duration_ms == 500U);
    assert(radio_parse_command("REPLACE 4294967295 v 100 10000", &command));
    assert(command.type == RadioCommandReplace && command.sequence == UINT32_MAX);
}

static void test_invalid_commands(void) {
    const char* invalid[] = {
        "", " ", "hello", "HELLOX", "HELLO 1", "PING!", "PING\nPING", "STOP 1", "DISARMX",
        "AWAKE", "AWAKE 2", "AWAKE -1", "AWAKE +1", "AWAKE 256", "AWAKE 1 0",
        "AWAKE 1\nRUN 1 s 100 100", "AWAKEX 1", "AWAKE 1.0",
        "SET", "SET 1", "SET 0 0", "SET 65536 0", "SET 4294967297 0", "SET -1 0",
        "SET +1 0", "SET 1 -1", "SET 1 +1", "SET 1 3", "SET 1 9", "SET 1 256",
        "SET 1 2 extra", "SET 0x1234 0", "SET 1.0 0", "SET 1\n0", "SET 1 0\nRUN 1 v 0 100",
        "RUN", "RUN 1", "RUN 1 v", "RUN 1 v 0", "RUN 0 v 0 100", "RUN 4294967296 v 0 100",
        "RUN 9999999999999999999999999 v 0 100", "RUN -1 v 0 100", "RUN +1 v 0 100",
        "RUN 1 S 1 100", "RUN 1 x 1 100", "RUN 1 vv 1 100", "RUN 1 v1 100",
        "RUN 1 v -1 100", "RUN 1 v +1 100", "RUN 1 v 101 100", "RUN 1 v 256 100",
        "RUN 1 v 4294967296 100", "RUN 1 v 0 0", "RUN 1 v 0 99", "RUN 1 v 0 10001",
        "RUN 1 v 0 4294967296", "RUN 1 v 0 100garbage", "RUN 1 v 0 100 1",
        "RUN 1 v 0 1e3", "RUN 1 v 0 100\nSTOP", "RUN 1\nv 0 100", "RUN 1 v 0\n100",
        "RUN 1 v 0 100\n\n", "RUN 1 v 0 100\r\r", "RUN 1 v 0 100\v", "SET 1\v0",
        "REPLACE", "REPLACE 0 b 0 500", "REPLACE 4294967296 b 0 500",
        "REPLACE 1 v 101 100", "REPLACE 1 s 20 10001", "REPLACE 1 s 20 99",
        "REPLACE 1 b 0 500\nSTOP", "REPLACE 1 b 0 500 extra", "REPLACEX 1 b 0 500",
    };
    RadioCommand command;
    memset(&command, 0xA5, sizeof(command));
    unsigned char unchanged[sizeof(command)];
    memcpy(unchanged, &command, sizeof(command));
    for(size_t index = 0U; index < sizeof(invalid) / sizeof(invalid[0]); ++index) {
        if(radio_parse_command(invalid[index], &command)) {
            fprintf(stderr, "Unexpected accepted command: %s\n", invalid[index]);
            assert(false);
        }
        assert(memcmp(&command, unchanged, sizeof(command)) == 0);
    }
    assert(!radio_parse_command(NULL, &command));
    assert(!radio_parse_command("HELLO", NULL));
}

static void test_keepalive_frame(void) {
    RadioSequence sequence;
    assert(radio_keepalive_sequence_init(&sequence, 0x1234U, 0U));
    /* Independent expected stop packet: ID 12 34, channel/type 02,
     * intensity 00, checksum 48; there is no duration in this packet.
     */
    assert(decode_frame(&sequence) == UINT64_C(0x1234020048));
    assert(sequence.repeats_remaining == 5U);
    RadioPulse pulse;
    uint32_t elapsed = 0U;
    while(radio_sequence_next(&sequence, &pulse)) elapsed += pulse.duration_us;
    assert(elapsed == 275750U);
    assert(elapsed <= RADIO_TERMINATOR_MS * 1000U);
    assert(!radio_keepalive_sequence_init(&sequence, 0U, 0U));
    assert(!radio_sequence_next(&sequence, &pulse));
    assert(!radio_keepalive_sequence_init(&sequence, 0x1234U, 3U));
    assert(!radio_keepalive_sequence_init(NULL, 0x1234U, 0U));
}

static void test_keepalive_schedule_and_lease(void) {
    RadioKeepAlive state = {0};
    assert(!radio_keepalive_due(&state, 100000U, true, true, true));
    assert(!radio_keepalive_enable(&state, 100U, 60000U, false, true));
    assert(!radio_keepalive_enable(&state, 100U, 60000U, true, false));
    assert(!radio_keepalive_enable(&state, 100U, 0U, true, true));
    assert(!radio_keepalive_enable(&state, 100U, UINT32_MAX, true, true));
    assert(radio_keepalive_enable(&state, 100U, 60000U, true, true));
    assert(!radio_keepalive_due(&state, 100U, true, true, true));
    assert(!radio_keepalive_due(&state, 60099U, true, true, true));
    assert(radio_keepalive_due(&state, 60100U, true, true, true));
    /* It cannot interrupt active RF, and the end of that activity postpones
     * maintenance even when the original timer expired during the operation.
     */
    assert(!radio_keepalive_due(&state, 65000U, true, true, false));
    radio_keepalive_activity(&state, 70000U);
    assert(!radio_keepalive_due(&state, 70000U, true, true, true));
    assert(!radio_keepalive_due(&state, 129999U, true, true, true));
    assert(radio_keepalive_due(&state, 130000U, true, true, true));
    radio_keepalive_activity(&state, 130300U);
    assert(!radio_keepalive_due(&state, 190299U, true, true, true));
    assert(radio_keepalive_due(&state, 190300U, true, true, true));
    /* Lease expiry is latched, including when no operation is armed. A later
     * USB PING alone cannot restart maintenance for a failed backend session.
     */
    assert(!radio_keepalive_due(&state, 200000U, true, false, true));
    assert(!state.enabled);
    assert(!radio_keepalive_due(&state, 300000U, true, true, true));
    assert(radio_keepalive_enable(&state, 300000U, 60000U, true, true));
    assert(!radio_keepalive_due(&state, 400000U, false, true, true));
    assert(!state.enabled);
    assert(radio_keepalive_enable(&state, 400000U, 60000U, true, true));
    radio_keepalive_disable(&state);
    radio_keepalive_activity(&state, 500000U);
    assert(!radio_keepalive_due(&state, 600000U, true, true, true));
    /* Tick wrap does not run maintenance early or strand it for a full wrap. */
    uint32_t start = UINT32_MAX - 20000U;
    assert(radio_keepalive_enable(&state, start, 60000U, true, true));
    assert(!radio_keepalive_due(&state, start + 59999U, true, true, true));
    assert(radio_keepalive_due(&state, start + 60000U, true, true, true));
    assert(!radio_keepalive_enable(NULL, 0U, 60000U, true, true));
    assert(!radio_keepalive_due(NULL, 0U, true, true, true));
    radio_keepalive_disable(NULL);
    radio_keepalive_activity(NULL, 0U);
}

static void test_maintenance_is_lower_priority_than_user_output(void) {
    assert(radio_phase_accepts_run(RadioIdle, false));
    assert(radio_phase_accepts_run(RadioIdle, true));
    assert(radio_phase_accepts_run(RadioKeeping, false));
    assert(radio_phase_accepts_run(RadioKeeping, true));
    assert(!radio_phase_accepts_run(RadioOperating, false));
    assert(!radio_phase_accepts_run(RadioEnding, false));
    assert(radio_phase_accepts_run(RadioOperating, true));
    assert(radio_phase_accepts_run(RadioEnding, true));
    assert(!radio_phase_accepts_run((RadioPhase)99, true));
}

int main(void) {
    test_vectors();
    test_finite_sequences();
    test_valid_commands();
    test_invalid_commands();
    test_keepalive_frame();
    test_keepalive_schedule_and_lease();
    test_maintenance_is_lower_priority_than_user_output();
    puts("radio_core: all tests passed (vectors, duration sweep, parser, keepalive state)");
    return 0;
}
