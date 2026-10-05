// SPDX-License-Identifier: GPL-3.0-or-later
#include <furi.h>
#include <furi_hal.h>
#include <gui/gui.h>
#include <gui/view_port.h>
#include <input/input.h>
#include <storage/storage.h>
#include <lib/subghz/devices/cc1101_configs.h>
#include <stdio.h>
#include <stdlib.h>
#include "controller_core.h"
#include "radio_core.h"

#define TARGET_PATH "/ext/apps_data/pishock_controller/target.conf"
#define FLAG_INPUT (1U << 0)
#define FLAG_BACK (1U << 1)
#define FLAG_EXIT (1U << 2)
#define FLAG_OVERFLOW (1U << 3)
#define STOP_FLAGS (FLAG_BACK | FLAG_EXIT | FLAG_OVERFLOW)
#define ALL_FLAGS (FLAG_INPUT | STOP_FLAGS)

typedef struct {
    ControllerInput input;
    bool during_tx;
} QueuedInput;

typedef struct {
    ControllerState state;
    bool tx_failed;
} Display;

typedef struct {
    ControllerState state;
    RadioSequence sequence;
    FuriThreadId thread;
    FuriMessageQueue* queue;
    FuriMutex* input_mutex;
    FuriMutex* display_mutex;
    Display display;
    uint32_t stop_flags;
    bool tx_gate;
    bool tx_active;
    bool charge_suppressed;
    bool tx_failed;
    bool exiting;
} ControllerApp;

static bool load_target(ControllerTarget* target) {
    Storage* storage = furi_record_open(RECORD_STORAGE);
    File* file = storage_file_alloc(storage);
    char data[256];
    bool valid = false;
    if(storage_file_open(file, TARGET_PATH, FSAM_READ, FSOM_OPEN_EXISTING)) {
        uint64_t size = storage_file_size(file);
        if(size > 0 && size <= sizeof(data)) {
            size_t read = storage_file_read(file, data, (size_t)size);
            char extra;
            valid = read == size && storage_file_read(file, &extra, 1) == 0 &&
                    storage_file_get_error(file) == FSE_OK &&
                    controller_parse_target(data, read, target);
        }
    }
    storage_file_close(file);
    storage_file_free(file);
    furi_record_close(RECORD_STORAGE);
    return valid;
}

static void input_callback(InputEvent* event, void* context) {
    ControllerApp* app = context;
    /* Short is derived from Release and must not become a second press. */
    if(event->type == InputTypeShort) return;
    ControllerInputType type;
    switch(event->type) {
    case InputTypePress: type = ControllerInputPress; break;
    case InputTypeRelease: type = ControllerInputRelease; break;
    case InputTypeLong: type = ControllerInputLong; break;
    case InputTypeRepeat: type = ControllerInputRepeat; break;
    default: return;
    }
    ControllerKey key;
    switch(event->key) {
    case InputKeyUp: key = ControllerKeyUp; break;
    case InputKeyDown: key = ControllerKeyDown; break;
    case InputKeyLeft: key = ControllerKeyLeft; break;
    case InputKeyRight: key = ControllerKeyRight; break;
    case InputKeyOk: key = ControllerKeyOk; break;
    case InputKeyBack: key = ControllerKeyBack; break;
    default: return;
    }
    /* Capture arrival time, not queue-consumption time. One counter per hold. */
    QueuedInput queued = {
        .input = {key, type, event->sequence_source == INPUT_SEQUENCE_SOURCE_HARDWARE,
                  event->sequence_counter, furi_get_tick()},
    };
    uint32_t flags = FLAG_INPUT;
    furi_mutex_acquire(app->input_mutex, FuriWaitForever);
    queued.during_tx = app->tx_gate;
    if(key == ControllerKeyBack &&
       (type == ControllerInputPress || type == ControllerInputLong)) {
        flags = type == ControllerInputLong ? FLAG_EXIT : FLAG_BACK;
        app->stop_flags |= flags;
    } else if(!queued.during_tx &&
              furi_message_queue_put(app->queue, &queued, 0) != FuriStatusOk) {
        flags = FLAG_OVERFLOW;
        app->stop_flags |= flags;
    }
    furi_mutex_release(app->input_mutex);
    furi_thread_flags_set(app->thread, flags);
}

static LevelDuration radio_yield(void* context) {
    ControllerApp* app = context;
    RadioPulse pulse;
    if(!radio_sequence_next(&app->sequence, &pulse)) return level_duration_reset();
    return level_duration_make(pulse.level, pulse.duration_us);
}

static void radio_halt(ControllerApp* app) {
    if(app->tx_active) {
        /* Synchronous DMA shutdown must precede every sequence replacement. */
        furi_hal_subghz_stop_async_tx();
        app->tx_active = false;
        furi_hal_subghz_sleep();
    }
    if(app->charge_suppressed) {
        furi_hal_power_suppress_charge_exit();
        app->charge_suppressed = false;
    }
}

static bool radio_start(ControllerApp* app, bool terminator) {
    furi_assert(!app->tx_active);
    bool encoded = radio_sequence_init(
        &app->sequence, app->state.target.id, app->state.target.channel,
        terminator ? 'v' : app->state.mode, terminator ? 0 : app->state.intensity,
        terminator ? RADIO_TERMINATOR_MS : app->state.duration_ms);
    if(!encoded) return false;
    furi_hal_subghz_reset();
    furi_hal_subghz_idle();
    furi_hal_subghz_load_custom_preset(subghz_device_cc1101_preset_ook_650khz_async_regs);
    furi_hal_subghz_set_frequency_and_path(433920000U);
    furi_hal_power_suppress_charge_enter();
    app->charge_suppressed = true;
    if(!furi_hal_subghz_start_async_tx(radio_yield, app)) {
        furi_hal_subghz_sleep();
        radio_halt(app);
        return false;
    }
    app->tx_active = true;
    return true;
}

static void apply_action(ControllerApp* app, ControllerAction action) {
    if(action & ControllerActionHalt) radio_halt(app);
    if(action & (ControllerActionStartOperation | ControllerActionStartTerminator)) {
        bool terminator = (action & ControllerActionStartTerminator) != 0;
        /* A stop captured after dequeuing OK still wins before starting output. */
        furi_mutex_acquire(app->input_mutex, FuriWaitForever);
        bool interrupted = !terminator && app->stop_flags;
        if(!interrupted) app->tx_gate = true;
        /* Linearize reservation/start against stop capture. The DMA callback
           only reads sequence and never takes this mutex. */
        bool started = !interrupted && radio_start(app, terminator);
        furi_mutex_release(app->input_mutex);
        if(interrupted) {
            apply_action(app, controller_tx_failed(&app->state));
        } else if(!started) {
            app->tx_failed = true;
            apply_action(app, controller_tx_failed(&app->state));
        } else {
            app->tx_failed = false;
        }
    }
    if(action & ControllerActionExit) app->exiting = true;
    if(app->state.phase == ControllerPhaseIdle || app->state.phase == ControllerPhaseExiting) {
        furi_mutex_acquire(app->input_mutex, FuriWaitForever);
        app->tx_gate = false;
        furi_mutex_release(app->input_mutex);
    }
}

static void draw_callback(Canvas* canvas, void* context) {
    ControllerApp* app = context;
    Display display;
    furi_mutex_acquire(app->display_mutex, FuriWaitForever);
    display = app->display;
    furi_mutex_release(app->display_mutex);
    ControllerState* s = &display.state;
    char text[32];
    canvas_clear(canvas);
    canvas_set_font(canvas, FontSecondary);
    if(s->target_valid) {
        snprintf(text, sizeof(text), "ID %u  Channel %u", s->target.id, s->target.channel);
        canvas_draw_str(canvas, 0, 8, text);
    } else {
        canvas_draw_str(canvas, 0, 8, "ID --  Channel --");
        canvas_draw_str(canvas, 0, 22, "Setup from bridge");
        canvas_draw_str(canvas, 0, 34, "Install Controller first");
        canvas_draw_str(canvas, 0, 48, "DISARMED");
        canvas_draw_str(canvas, 0, 62, "Back stop; hold exit");
        return;
    }
    const char* mode = s->mode == 'b' ? "Beep" : s->mode == 'v' ? "Vibrate" : "Shock";
    snprintf(text, sizeof(text), "%c Mode: %s", s->selection == ControllerSelectionMode ? '>' : ' ', mode);
    canvas_draw_str(canvas, 0, 19, text);
    snprintf(text, sizeof(text), "%c Intensity: %u%%", s->selection == ControllerSelectionIntensity ? '>' : ' ', s->intensity);
    canvas_draw_str(canvas, 0, 29, text);
    snprintf(text, sizeof(text), "%c Duration: %lu.%lu s", s->selection == ControllerSelectionDuration ? '>' : ' ',
             (unsigned long)(s->duration_ms / 1000), (unsigned long)((s->duration_ms % 1000) / 100));
    canvas_draw_str(canvas, 0, 39, text);
    const char* status = s->phase == ControllerPhaseOperating ? "RUNNING" :
                         s->phase == ControllerPhaseTerminating ? "STOPPING" :
                         s->armed ? "ARMED: release, OK" :
                         display.tx_failed ? "TX failed: DISARMED" : "DISARMED: hold OK";
    canvas_draw_str(canvas, 0, 50, status);
    canvas_draw_str(canvas, 0, 62, "Back stop; hold exit");
}

int32_t pishock_controller_app(void* argument) {
    UNUSED(argument);
    ControllerApp* app = calloc(1, sizeof(ControllerApp));
    app->thread = furi_thread_get_current_id();
    app->queue = furi_message_queue_alloc(16, sizeof(QueuedInput));
    app->input_mutex = furi_mutex_alloc(FuriMutexTypeNormal);
    app->display_mutex = furi_mutex_alloc(FuriMutexTypeNormal);
    ControllerTarget target;
    controller_init(&app->state, load_target(&target) ? &target : NULL);
    app->display.state = app->state;
    Gui* gui = furi_record_open(RECORD_GUI);
    ViewPort* viewport = view_port_alloc();
    view_port_draw_callback_set(viewport, draw_callback, app);
    view_port_input_callback_set(viewport, input_callback, app);
    gui_add_view_port(gui, viewport, GuiLayerFullscreen);
    while(!app->exiting) {
        furi_thread_flags_wait(ALL_FLAGS, FuriFlagWaitAny, furi_ms_to_ticks(10));
        /* Stop priority and dequeue are one critical section with callbacks. */
        furi_mutex_acquire(app->input_mutex, FuriWaitForever);
        uint32_t stops = app->stop_flags;
        app->stop_flags = 0;
        QueuedInput queued;
        bool received = false;
        if(stops) {
            while(furi_message_queue_get(app->queue, &queued, 0) == FuriStatusOk) {}
        } else {
            received = furi_message_queue_get(app->queue, &queued, 0) == FuriStatusOk;
        }
        furi_mutex_release(app->input_mutex);
        uint32_t now = furi_get_tick();
        if(stops) {
            ControllerInput stop = {ControllerKeyBack,
                                    stops & FLAG_EXIT ? ControllerInputLong : ControllerInputPress,
                                    false, 0, now};
            apply_action(app, controller_input(&app->state, stop, now));
        } else if(received && !queued.during_tx) {
            apply_action(app, controller_input(&app->state, queued.input, now));
        }
        apply_action(app, controller_tick(&app->state, furi_get_tick(),
            app->tx_active && furi_hal_subghz_is_async_tx_complete()));
        furi_mutex_acquire(app->display_mutex, FuriWaitForever);
        app->display.state = app->state;
        app->display.tx_failed = app->tx_failed;
        furi_mutex_release(app->display_mutex);
        view_port_update(viewport);
    }
    radio_halt(app);
    view_port_enabled_set(viewport, false);
    gui_remove_view_port(gui, viewport);
    view_port_free(viewport);
    furi_record_close(RECORD_GUI);
    furi_message_queue_free(app->queue);
    furi_mutex_free(app->input_mutex);
    furi_mutex_free(app->display_mutex);
    free(app);
    return 0;
}
