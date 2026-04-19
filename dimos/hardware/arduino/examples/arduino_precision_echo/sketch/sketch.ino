/*
 * Precision Echo — DimOS Arduino hardware test sketch.
 *
 * Echoes Bool and Vector3 messages back to the host.  Validates
 * round-trip serialization correctness including float64->float32
 * precision on AVR.  Limited to 2 message types to fit within
 * Arduino Uno's 2KB SRAM.
 */

#include "dimos_arduino.h"
#include <util/delay.h>

void on_bool(const char *ch, const void *msg, void *ctx) {
    (void)ch; (void)ctx;
    const dimos_msg__Bool *m = (const dimos_msg__Bool *)msg;
    uint8_t buf[1];
    int n = dimos_msg__Bool__encode(buf, 0, sizeof(buf), m);
    if (n > 0) dimos_publish(DIMOS_CHANNEL__BOOL_OUT, &dimos_msg__Bool__type, buf, n);
}

void on_vec3(const char *ch, const void *msg, void *ctx) {
    (void)ch; (void)ctx;
    const dimos_msg__Vector3 *m = (const dimos_msg__Vector3 *)msg;
    uint8_t buf[24];
    int n = dimos_msg__Vector3__encode(buf, 0, sizeof(buf), m);
    if (n > 0) dimos_publish(DIMOS_CHANNEL__VEC3_OUT, &dimos_msg__Vector3__type, buf, n);
}

void setup() {
    dimos_init(DIMOS_BAUDRATE);
    dimos_subscribe(DIMOS_CHANNEL__BOOL_IN, &dimos_msg__Bool__type, on_bool, NULL);
    dimos_subscribe(DIMOS_CHANNEL__VEC3_IN, &dimos_msg__Vector3__type, on_vec3, NULL);
    DimosSerial.println("PrecisionEcho ready");
}

void loop() {
    dimos_handle(10);
    _delay_ms(1);
}
