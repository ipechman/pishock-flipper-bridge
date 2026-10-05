#ifndef CONTROLLER_CORE_H
#define CONTROLLER_CORE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef struct {
    uint16_t id;
    uint8_t channel;
} ControllerTarget;
typedef enum {
    ControllerKeyUp,
    ControllerKeyDown,
    ControllerKeyLeft,
    ControllerKeyRight,
    ControllerKeyOk,
    ControllerKeyBack
} ControllerKey;
typedef enum {
    ControllerInputPress,
    ControllerInputRelease,
    ControllerInputLong,
    ControllerInputRepeat
} ControllerInputType;
typedef enum {
    ControllerPhaseIdle,
    ControllerPhaseOperating,
    ControllerPhaseTerminating,
    ControllerPhaseExiting
} ControllerPhase;
typedef enum {
    ControllerSelectionMode,
    ControllerSelectionIntensity,
    ControllerSelectionDuration
} ControllerSelection;
typedef struct {
    ControllerKey key;
    ControllerInputType type;
    bool physical;
    uint32_t sequence;
    uint32_t timestamp_ms;
} ControllerInput;
typedef uint32_t ControllerAction;
enum {
    ControllerActionNone = 0,
    ControllerActionStartOperation = 1,
    ControllerActionHalt = 2,
    ControllerActionStartTerminator = 4,
    ControllerActionExit = 8
};
typedef struct {
    ControllerTarget target;
    bool target_valid;
    char mode;
    uint8_t intensity;
    uint32_t duration_ms;
    ControllerSelection selection;
    ControllerPhase phase;
    bool armed;
    uint32_t deadline_ms;
    uint32_t last_sequence;
    bool sequence_seen;
    bool ok_down;
    bool arm_release_required;
    bool exit_pending;
    uint32_t ok_press_sequence;
    uint32_t input_barrier_ms;
    bool input_barrier_valid;
} ControllerState;
bool controller_parse_target(const char *text, size_t length, ControllerTarget *out);
void controller_init(ControllerState *state, const ControllerTarget *target);
ControllerAction controller_input(ControllerState *state, ControllerInput input, uint32_t now_ms);
ControllerAction controller_tick(ControllerState *state, uint32_t now_ms, bool tx_complete);
ControllerAction controller_tx_failed(ControllerState *state);
#endif
