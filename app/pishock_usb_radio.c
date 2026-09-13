// SPDX-License-Identifier: GPL-3.0-or-later
#include <furi.h>
#include <furi_hal.h>
#include <furi_hal_usb_cdc.h>
#include <gui/gui.h>
#include <gui/view_port.h>
#include <input/input.h>
#include <lib/subghz/devices/cc1101_configs.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "radio_core.h"

#define RADIO_HZ 433920000U
#define USB_IF 1
#define LEASE_MS 1000U
#define FLAG_RX (1U << 0)
#define FLAG_TX (1U << 1)
#define FLAG_LOST (1U << 2)
#define FLAG_BACK (1U << 3)
#define FLAG_EXIT (1U << 4)
#define FLAG_OK (1U << 5)
#define FLAGS_ALL 0x3FU

typedef struct {
    char state[24];
    uint16_t id;
    uint8_t channel;
    bool armed;
} Display;

typedef struct {
    FuriThreadId thread;
    Gui* gui;
    ViewPort* viewport;
    FuriMutex* display_mutex;
    Display display;
    Display current;
    FuriMessageQueue* replies;
    bool usb_tx_busy;
    bool connected;
    bool ping_seen;
    bool exit_requested;
    bool usb_ready;
    bool target_configured;
    uint32_t ping_tick;
    uint32_t last_sequence;
    uint32_t deadline;
    RadioPhase phase;
    RadioKeepAlive keepalive;
    RadioSequence sequence;
    char line[96];
    size_t line_len;
    bool drop_line;
} App;

static void status(App* app, const char* text) {
    snprintf(app->current.state, sizeof(app->current.state), "%s", text);
}

static bool lease_valid(const App* app) {
    return app->connected && app->ping_seen &&
           (furi_get_tick() - app->ping_tick < furi_ms_to_ticks(LEASE_MS));
}

static LevelDuration radio_yield(void* context) {
    App* app = context;
    RadioPulse pulse;
    if(!radio_sequence_next(&app->sequence, &pulse)) return level_duration_reset();
    return level_duration_make(pulse.level, pulse.duration_us);
}

static void radio_halt(App* app) {
    if(app->phase == RadioIdle) return;
    // This HAL routine stops DMA and the radio, including any buffered pulses.
    furi_hal_subghz_stop_async_tx();
    furi_hal_subghz_sleep();
    furi_hal_power_suppress_charge_exit();
    app->phase = RadioIdle;
    radio_keepalive_activity(&app->keepalive, furi_get_tick());
}

static bool radio_start(App* app, char mode, uint8_t intensity, uint32_t ms, RadioPhase phase) {
    furi_assert(app->phase == RadioIdle);
    bool encoded = phase == RadioKeeping ?
                       radio_keepalive_sequence_init(&app->sequence, app->current.id, app->current.channel) :
                       radio_sequence_init(&app->sequence, app->current.id, app->current.channel, mode, intensity, ms);
    if(!encoded) {
        radio_keepalive_disable(&app->keepalive);
        app->current.armed = false;
        return false;
    }
    furi_hal_subghz_reset();
    furi_hal_subghz_idle();
    furi_hal_subghz_load_custom_preset(subghz_device_cc1101_preset_ook_650khz_async_regs);
    furi_hal_subghz_set_frequency_and_path(RADIO_HZ);
    furi_hal_power_suppress_charge_enter();
    if(!furi_hal_subghz_start_async_tx(radio_yield, app)) {
        furi_hal_subghz_sleep();
        furi_hal_power_suppress_charge_exit();
        radio_keepalive_disable(&app->keepalive);
        app->current.armed = false;
        status(app, "Radio TX unavailable");
        return false;
    }
    app->phase = phase;
    app->deadline = furi_get_tick() + furi_ms_to_ticks(ms + 20U);
    return true;
}

static void end_operation(App* app) {
    if(app->phase == RadioKeeping) {
        radio_halt(app);
        return;
    }
    if(app->phase != RadioOperating) return;
    radio_halt(app);
    // CaiXianlin has no duration field. Send an explicit vibrate-zero terminator.
    if(!radio_start(app, 'v', 0, RADIO_TERMINATOR_MS, RadioEnding)) app->current.armed = false;
}

static void physical_disarm(App* app, const char* reason) {
    app->current.armed = false;
    end_operation(app);
    radio_keepalive_activity(&app->keepalive, furi_get_tick());
    status(app, reason);
}

static void disarm(App* app, const char* reason) {
    radio_keepalive_disable(&app->keepalive);
    physical_disarm(app, reason);
}

static void queue_reply(App* app, const char* text) {
    char reply[64] = {0};
    snprintf(reply, sizeof(reply), "%s\n", text);
    if(furi_message_queue_put(app->replies, reply, 0) != FuriStatusOk)
        disarm(app, "USB overloaded");
}

static void process_command(App* app) {
    RadioCommand cmd;
    if(!radio_parse_command(app->line, &cmd)) {
        queue_reply(app, "ERR INVALID");
        return;
    }
    switch(cmd.type) {
    case RadioCommandHello:
        disarm(app, "USB ready / disarmed");
        app->last_sequence = 0;
        app->ping_seen = false;
        app->target_configured = false;
        queue_reply(app, "OK RADIO1");
        break;
    case RadioCommandPing:
        app->connected = (furi_hal_cdc_get_ctrl_line_state(USB_IF) & CdcCtrlLineDTR) != 0;
        app->ping_seen = true;
        app->ping_tick = furi_get_tick();
        queue_reply(app, "OK PING");
        break;
    case RadioCommandSet:
        if(app->current.armed || app->phase != RadioIdle) {
            queue_reply(app, "ERR BUSY");
            break;
        }
        app->current.id = cmd.id;
        app->current.channel = cmd.channel;
        app->target_configured = true;
        radio_keepalive_disable(&app->keepalive);
        status(app, "Configured / disarmed");
        queue_reply(app, "OK SET");
        break;
    case RadioCommandAwake:
        if(!cmd.enabled) {
            radio_keepalive_disable(&app->keepalive);
            if(app->phase == RadioKeeping) radio_halt(app);
            queue_reply(app, "OK AWAKE");
        } else if(radio_keepalive_enable(
                      &app->keepalive, furi_get_tick(), furi_ms_to_ticks(RADIO_KEEPALIVE_INTERVAL_MS),
                      app->target_configured && app->current.id != 0U, lease_valid(app))) {
            queue_reply(app, "OK AWAKE");
        } else {
            if(app->phase == RadioKeeping) radio_halt(app);
            queue_reply(app, "ERR DISARMED");
        }
        break;
    case RadioCommandRun:
    case RadioCommandReplace:
        if(!app->target_configured || !app->current.armed || !lease_valid(app)) {
            queue_reply(app, "ERR DISARMED");
        } else if(!radio_phase_accepts_run(app->phase, cmd.type == RadioCommandReplace)) {
            queue_reply(app, "ERR BUSY");
        } else if(cmd.sequence <= app->last_sequence) {
            queue_reply(app, "ERR SEQUENCE");
        } else {
            // Consume before touching hardware: an uncertain result is never retried.
            app->last_sequence = cmd.sequence;
            // A fresh, accepted hub update supersedes this target's previous output.
            // Halt DMA before replacing the pulse buffer; no commands are queued.
            if(cmd.type == RadioCommandReplace || app->phase == RadioKeeping) radio_halt(app);
            if(radio_start(app, cmd.mode, cmd.intensity, cmd.duration_ms, RadioOperating)) {
                status(app, "Transmitting");
                queue_reply(app, cmd.type == RadioCommandReplace ? "OK REPLACE" : "OK RUN");
            } else {
                app->current.armed = false;
                queue_reply(app, "ERR RADIO");
            }
        }
        break;
    case RadioCommandStop:
        end_operation(app);
        radio_keepalive_activity(&app->keepalive, furi_get_tick());
        status(app, "Stopped");
        queue_reply(app, "OK STOP");
        break;
    case RadioCommandDisarm:
        disarm(app, "Disarmed");
        queue_reply(app, "OK DISARM");
        break;
    }
}

static void receive_usb(App* app) {
    uint8_t data[CDC_DATA_SZ];
    int32_t count = furi_hal_cdc_receive(USB_IF, data, sizeof(data));
    for(int32_t i = 0; i < count; ++i) {
        uint8_t ch = data[i];
        if(ch == '\n') {
            if(!app->drop_line && app->line_len) {
                app->line[app->line_len] = '\0';
                process_command(app);
            } else if(app->drop_line) {
                queue_reply(app, "ERR INVALID");
            }
            app->line_len = 0;
            app->drop_line = false;
        } else if(ch == '\r') {
            // CR is not stripped from within a token: parser accepts trailing whitespace.
            if(app->line_len < sizeof(app->line) - 1) app->line[app->line_len++] = (char)ch;
            else app->drop_line = true;
        } else if(!app->drop_line) {
            if(ch < 32 || ch > 126 || app->line_len >= sizeof(app->line) - 1) {
                app->drop_line = true;
                disarm(app, "Invalid USB frame");
            } else {
                app->line[app->line_len++] = (char)ch;
            }
        }
    }
}

static void usb_tx_done(void* context) {
    App* app = context;
    furi_thread_flags_set(app->thread, FLAG_TX);
}
static void usb_rx(void* context) {
    App* app = context;
    furi_thread_flags_set(app->thread, FLAG_RX);
}
static void usb_state(void* context, CdcState state) {
    App* app = context;
    if(state == CdcStateDisconnected) furi_thread_flags_set(app->thread, FLAG_LOST);
}
static void usb_control(void* context, CdcCtrlLine lines) {
    App* app = context;
    if(!(lines & CdcCtrlLineDTR)) furi_thread_flags_set(app->thread, FLAG_LOST);
}
static CdcCallbacks usb_callbacks = {
    .tx_ep_callback = usb_tx_done,
    .rx_ep_callback = usb_rx,
    .state_callback = usb_state,
    .ctrl_line_callback = usb_control,
};

static void draw(Canvas* canvas, void* context) {
    App* app = context;
    Display d;
    furi_mutex_acquire(app->display_mutex, FuriWaitForever);
    d = app->display;
    furi_mutex_release(app->display_mutex);
    char buf[40];
    canvas_clear(canvas);
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str(canvas, 0, 10, "PiShock USB Radio");
    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str(canvas, 0, 22, d.state);
    snprintf(buf, sizeof(buf), "ID %u  Ch %u", d.id, d.channel);
    canvas_draw_str(canvas, 0, 34, buf);
    canvas_draw_str(canvas, 0, 46, d.armed ? "ARMED - OK / Back: stop" : "DISARMED - OK: arm");
    canvas_draw_str(canvas, 0, 60, "Hold Back: exit");
}

static void input(InputEvent* event, void* context) {
    App* app = context;
    uint32_t flag = 0;
    if(event->key == InputKeyBack && event->type == InputTypePress) flag = FLAG_BACK;
    if(event->key == InputKeyBack && event->type == InputTypeLong) flag = FLAG_EXIT;
    if(event->key == InputKeyOk && event->type == InputTypeShort) flag = FLAG_OK;
    if(flag) furi_thread_flags_set(app->thread, flag);
}

static bool app_signal(uint32_t signal, void* arg, void* context) {
    UNUSED(arg);
    if(signal != FuriSignalExit) return false;
    App* app = context;
    furi_thread_flags_set(app->thread, FLAG_EXIT);
    return true;
}

int32_t pishock_usb_radio_app(void* context) {
    UNUSED(context);
    App* app = calloc(1, sizeof(App));
    app->thread = furi_thread_get_current_id();
    furi_thread_set_signal_callback(furi_thread_get_current(), app_signal, app);
    app->display_mutex = furi_mutex_alloc(FuriMutexTypeNormal);
    app->replies = furi_message_queue_alloc(16, 64);
    app->viewport = view_port_alloc();
    app->gui = furi_record_open(RECORD_GUI);
    status(app, "Open computer bridge");
    app->display = app->current;
    view_port_draw_callback_set(app->viewport, draw, app);
    view_port_input_callback_set(app->viewport, input, app);
    gui_add_view_port(app->gui, app->viewport, GuiLayerFullscreen);

    FuriHalUsbInterface* previous_usb = furi_hal_usb_get_config();
    // Avoid taking over another application's USB session or unsupported configuration.
    if(!furi_hal_usb_is_locked() && previous_usb == &usb_cdc_single &&
       furi_hal_usb_set_config(&usb_cdc_dual, NULL)) {
        app->usb_ready = true;
        furi_hal_cdc_set_callbacks(USB_IF, &usb_callbacks, app);
    } else {
        status(app, "Close other USB app");
    }

    while(!app->exit_requested || app->phase != RadioIdle) {
        uint32_t flags = furi_thread_flags_wait(FLAGS_ALL, FuriFlagWaitAny, 10);
        if(flags & FuriFlagError) flags = 0;
        if(flags & FLAG_LOST) {
            app->connected = false;
            app->ping_seen = false;
            app->target_configured = false;
            app->last_sequence = 0;
            app->line_len = 0;
            app->drop_line = false;
            app->usb_tx_busy = false;
            furi_message_queue_reset(app->replies);
            disarm(app, "USB disconnected");
        }
        if((app->current.armed || app->keepalive.enabled || app->phase == RadioKeeping) && !lease_valid(app))
            disarm(app, "Computer timed out");
        if(flags & FLAG_BACK) physical_disarm(app, "Disarmed");
        if(flags & FLAG_EXIT) disarm(app, "Disarmed");
        if(flags & FLAG_EXIT) app->exit_requested = true;
        if((flags & FLAG_OK) && !(flags & (FLAG_BACK | FLAG_EXIT | FLAG_LOST))) {
            if(app->current.armed) physical_disarm(app, "Disarmed");
            else if(app->usb_ready && app->target_configured && app->current.id && lease_valid(app) &&
                    (app->phase == RadioIdle || app->phase == RadioKeeping) &&
                    !app->exit_requested) {
                app->current.armed = true;
                status(app, "Armed / waiting");
            } else status(app, "Configure + connect PC");
        }
        if(app->phase != RadioIdle &&
           (furi_hal_subghz_is_async_tx_complete() || (int32_t)(furi_get_tick() - app->deadline) >= 0)) {
            if(app->phase == RadioOperating) end_operation(app);
            else {
                radio_halt(app);
                status(app, app->current.armed ? "Armed / waiting" : "Disarmed");
            }
        }
        // Read only one USB packet per pass so traffic cannot starve stop/deadline handling.
        if(app->usb_ready && !app->exit_requested) receive_usb(app);
        if(!app->exit_requested && radio_keepalive_due(
               &app->keepalive, furi_get_tick(), app->target_configured && app->current.id != 0U,
               lease_valid(app), app->phase == RadioIdle)) {
            // The backend-ready gate and USB lease are independent of arming.
            // This finite stop frame has zero output; it never replaces an operation.
            if(radio_start(app, 'v', 0U, RADIO_TERMINATOR_MS, RadioKeeping))
                status(app, "Keeping shocker awake");
        }
        if(flags & FLAG_TX) app->usb_tx_busy = false;
        if(app->usb_ready && !app->usb_tx_busy &&
           (furi_hal_cdc_get_ctrl_line_state(USB_IF) & CdcCtrlLineDTR)) {
            char reply[64];
            if(furi_message_queue_get(app->replies, reply, 0) == FuriStatusOk) {
                app->usb_tx_busy = true;
                furi_hal_cdc_send(USB_IF, (uint8_t*)reply, strlen(reply));
            }
        }
        furi_mutex_acquire(app->display_mutex, FuriWaitForever);
        app->display = app->current;
        furi_mutex_release(app->display_mutex);
        view_port_update(app->viewport);
    }
    furi_thread_set_signal_callback(furi_thread_get_current(), NULL, NULL);
    if(app->usb_ready) {
        furi_hal_cdc_set_callbacks(USB_IF, NULL, NULL);
        furi_hal_usb_set_config(previous_usb, NULL);
    }
    view_port_enabled_set(app->viewport, false);
    gui_remove_view_port(app->gui, app->viewport);
    view_port_free(app->viewport);
    furi_record_close(RECORD_GUI);
    furi_message_queue_free(app->replies);
    furi_mutex_free(app->display_mutex);
    free(app);
    return 0;
}
