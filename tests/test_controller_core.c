#include "../controller/controller_core.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static uint32_t seq;
static ControllerAction input(ControllerState *s, ControllerKey k, ControllerInputType t,
                              uint32_t now) {
    if (t == ControllerInputPress || (t == ControllerInputRepeat && k != ControllerKeyOk))
        seq++;
    ControllerInput i = {k, t, true, seq, now};
    return controller_input(s, i, now);
}
static void arm(ControllerState *s, uint32_t now) {
    assert(input(s, ControllerKeyOk, ControllerInputPress, now) == 0);
    assert(input(s, ControllerKeyOk, ControllerInputLong, now + 1000) == 0);
    assert(s->armed);
    assert(input(s, ControllerKeyOk, ControllerInputRelease, now + 1001) == 0);
}
static void parser(void) {
    const char *good =
        "Filetype: PiShock Controller Target\nVersion: 1\nShockerId: 12345\nChannel: 0\n";
    const char *bad[] = {
        "",
        "Filetype: PiShock Controller Target\nVersion: 2\nShockerId: 1\nChannel: 0\n",
        "Filetype: PiShock Controller Target\nVersion: 1\nShockerId: 0\nChannel: 0\n",
        "Filetype: PiShock Controller Target\nVersion: 1\nShockerId: -1\nChannel: 0\n",
        "Filetype: PiShock Controller Target\nVersion: 1\nShockerId: 65536\nChannel: 0\n",
        "Filetype: PiShock Controller Target\nVersion: 1\nShockerId: 1\nChannel: 3\n",
        "Filetype: PiShock Controller Target\nVersion: 1\nShockerId: 1\nChannel: 0\nChannel: 0\n",
        "Filetype: PiShock Controller Target\nVersion: 1\nShockerId: 1\n",
        "Filetype: PiShock Controller Target\nVersion: 1\nShockerId: 1\nChannel: 0\nJunk: 1\n"};
    ControllerTarget t = {9, 2};
    size_t j;
    char huge[257];
    assert(controller_parse_target(good, strlen(good), &t));
    assert(t.id == 12345 && t.channel == 0);
    good =
        "Filetype: PiShock Controller Target\r\nVersion: 1\r\nShockerId: 65535\r\nChannel: 2\r\n";
    assert(controller_parse_target(good, strlen(good), &t));
    assert(t.id == 65535 && t.channel == 2);
    good = "Filetype: PiShock Controller Target\nVersion: 1\nShockerId: 1\nChannel: 1\n";
    assert(controller_parse_target(good, strlen(good), &t));
    assert(t.id == 1 && t.channel == 1);
    for (j = 0; j < sizeof(bad) / sizeof(bad[0]); j++) {
        t.id = 9;
        t.channel = 2;
        assert(!controller_parse_target(bad[j], strlen(bad[j]), &t));
        assert(t.id == 9 && t.channel == 2);
    }
    memset(huge, 'a', sizeof(huge));
    assert(!controller_parse_target(huge, sizeof(huge), &t));
    assert(!controller_parse_target(good, strlen(good) + 1, &t));
}
int main(void) {
    ControllerState s;
    ControllerTarget t = {12345, 0};
    ControllerInput i;
    parser();
    controller_init(&s, &t);
    assert(!s.armed && s.mode == 'b' && s.intensity == 0 && s.duration_ms == 500);
    input(&s, ControllerKeyOk, ControllerInputLong, 0);
    assert(!s.armed);
    controller_init(&s, 0);
    input(&s, ControllerKeyOk, ControllerInputPress, 1);
    input(&s, ControllerKeyOk, ControllerInputLong, 1001);
    assert(!s.armed);
    controller_init(&s, &t);
    arm(&s, 10);
    assert(input(&s, ControllerKeyOk, ControllerInputRepeat, 1100) == 0);
    i = (ControllerInput){ControllerKeyOk, ControllerInputPress, false, ++seq, 1101};
    assert(controller_input(&s, i, 1101) == 0);
    i.physical = true;
    i.timestamp_ms = 1101;
    i.sequence = ++seq;
    assert(controller_input(&s, i, 1352) == 0);
    assert(input(&s, ControllerKeyOk, ControllerInputPress, 1400) ==
           ControllerActionStartOperation);
    i = (ControllerInput){ControllerKeyOk, ControllerInputPress, true, seq, 1400};
    assert(controller_input(&s, i, 1400) == 0);
    input(&s, ControllerKeyRight, ControllerInputPress, 1500);
    assert(s.mode == 'b' && s.intensity == 0);
    assert(controller_tick(&s, 1899, false) == 0);
    assert(controller_tick(&s, 1900, false) ==
           (ControllerActionHalt | ControllerActionStartTerminator));
    assert(controller_tick(&s, 2199, false) == 0);
    assert(controller_tick(&s, 2200, false) == ControllerActionHalt);
    assert(s.armed);
    i = (ControllerInput){ControllerKeyOk, ControllerInputPress, true, ++seq, 2190};
    assert(controller_input(&s, i, 2201) == 0);
    assert(input(&s, ControllerKeyOk, ControllerInputPress, 2202) ==
           ControllerActionStartOperation);
    assert(input(&s, ControllerKeyBack, ControllerInputPress, 2203) ==
           (ControllerActionHalt | ControllerActionStartTerminator));
    assert(!s.armed);
    assert(input(&s, ControllerKeyBack, ControllerInputLong, 2204) == 0);
    assert(controller_tick(&s, 2205, true) == (ControllerActionHalt | ControllerActionExit));
    assert(controller_tick(&s, 2500, true) == 0);
    controller_init(&s, &t);
    arm(&s, UINT32_MAX - 2000);
    assert(input(&s, ControllerKeyOk, ControllerInputPress, UINT32_MAX - 200) ==
           ControllerActionStartOperation);
    assert(controller_tick(&s, 298, false) == 0);
    assert(controller_tick(&s, 299, false) ==
           (ControllerActionHalt | ControllerActionStartTerminator));
    assert(controller_tick(&s, 599, false) == ControllerActionHalt);
    controller_init(&s, &t);
    arm(&s, 0);
    input(&s, ControllerKeyRight, ControllerInputPress, 1002);
    assert(!s.armed && s.mode == 'v');
    s.selection = ControllerSelectionIntensity;
    for (unsigned n = 0; n < 110; n++)
        input(&s, ControllerKeyRight, ControllerInputRepeat, 1100 + n);
    assert(s.intensity == 100);
    for (unsigned n = 0; n < 110; n++)
        input(&s, ControllerKeyLeft, ControllerInputRepeat, 1300 + n);
    assert(s.intensity == 0);
    s.selection = ControllerSelectionDuration;
    for (unsigned n = 0; n < 110; n++)
        input(&s, ControllerKeyRight, ControllerInputRepeat, 1500 + n);
    assert(s.duration_ms == 10000);
    for (unsigned n = 0; n < 110; n++)
        input(&s, ControllerKeyLeft, ControllerInputRepeat, 1700 + n);
    assert(s.duration_ms == 100);
    arm(&s, 2000);
    assert(input(&s, ControllerKeyOk, ControllerInputPress, 3002) ==
           ControllerActionStartOperation);
    assert(controller_tx_failed(&s) == ControllerActionHalt);
    assert(!s.armed);
    controller_init(&s, &t);
    i = (ControllerInput){ControllerKeyOk, ControllerInputPress, true, 0x3fffffff, 10};
    assert(controller_input(&s, i, 10) == 0);
    i.type = ControllerInputLong;
    i.timestamp_ms = 11;
    assert(controller_input(&s, i, 262) == 0);
    assert(!s.armed);
    assert(controller_input(&s, i, 11) == 0);
    assert(s.armed);
    assert(controller_input(&s, i, 12) == 0);
    i.type = ControllerInputPress;
    i.timestamp_ms = 13;
    assert(controller_input(&s, i, 13) == 0);
    i.type = ControllerInputRelease;
    i.timestamp_ms = 14;
    assert(controller_input(&s, i, 14) == 0);
    i.type = ControllerInputPress;
    i.timestamp_ms = 15;
    i.sequence = 0;
    assert(controller_input(&s, i, 15) == ControllerActionStartOperation);
    assert(controller_tick(&s, 16, true) ==
           (ControllerActionHalt | ControllerActionStartTerminator));
    assert(controller_tick(&s, 17, true) == ControllerActionHalt);
    i.sequence = 1;
    i.timestamp_ms = 16;
    assert(controller_input(&s, i, 18) == 0);
    i.timestamp_ms = 19;
    assert(controller_input(&s, i, 19) == 0);
    i.sequence = 2;
    assert(controller_input(&s, i, 19) == ControllerActionStartOperation);
    i.key = ControllerKeyBack;
    i.physical = false;
    i.timestamp_ms = 0;
    assert(controller_input(&s, i, 20) == (ControllerActionHalt | ControllerActionStartTerminator));
    assert(!s.armed);
    puts("controller core tests passed");
    return 0;
}
