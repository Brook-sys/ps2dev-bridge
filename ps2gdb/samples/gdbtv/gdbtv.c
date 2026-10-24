/* gdbtv.c — owner-visible GDB proof (cycle 3)
 *
 * Anti-forgery protocol (by construction):
 *   1. This ELF is built and its sha256 is published BEFORE the owner
 *      picks the two "magic" numbers.
 *   2. This program NEVER writes g_magic itself: it starts at 0 and is
 *      declared volatile. The only possible writer is an external
 *      debugger writing the variable's memory over the network (the
 *      stub's M/G packets) — there is no other input device.
 *   3. The two numbers appear on the TV at two distinct moments, each
 *      right after a GDB breakpoint stop + resume, and the final
 *      "PROOF COMPLETE" line parks on screen forever.
 *
 * What the owner sees on the TV:
 *   counter ticking ... frozen at 40  (GDB stop #1, ~20 s)
 *   -> "GDB WROTE magic=NUM1" -> ticking
 *   -> frozen at 80  (GDB stop #2, ~20 s)
 *   -> "GDB WROTE magic=NUM2" -> ticking to 200
 *   -> PROOF COMPLETE line -> parked (SleepThread), message stays.
 *
 * DelayThread takes MICROSECONDS (ps2sdk delaythread.h:21:
 * "extern s32 DelayThread(s32 microseconds);" — verified 2026-10-24;
 * also measured on hardware: 10*1000*1000 = 10 s).
 */

#include <stdio.h>
#include <debug.h>
#include <delaythread.h>
#include <kernel.h>

int gdb_stub_main(int argc, char *argv[]);

volatile int g_counter = 0; /* completed loop iterations          */
volatile int g_magic = 0;   /* written ONLY by gdb; starts at 0    */
volatile int g_writes = 0; /* external writes observed by us      */

/* Breakpoint target: GDB stops here every 40 iterations.
 * Empty body on purpose — the stop IS the event, there is nothing to
 * compute. -O0 keeps it a real out-of-line symbol (same pattern as
 * bump() in sample2, proven on hardware 2026-10-24). */
static void checkpoint(void)
{
}

int main(int argc, char *argv[])
{
    int i;
    int last_magic = 0;

    init_scr();
    printf("GDBTV1: boot ok, calling gdb_stub_main\n");
    gdb_stub_main(0, NULL);
    scr_printf("GDBTV1: stub up - waiting for gdb\n");
    scr_printf("g_magic starts at %d\n", g_magic);
    printf("GDBTV2: stub alive, demo loop starts\n");

    for (i = 0; i < 200; i++) {
        g_counter = i + 1;

        if (g_magic != last_magic) {
            g_writes++;
            last_magic = g_magic;
            scr_printf("*** GDB WROTE magic=%d (write %d) ***\n",
                       g_magic, g_writes);
            printf("GDBTV-WRITE: magic=%d write=%d at counter=%d\n",
                   g_magic, g_writes, g_counter);
        }

        scr_printf("counter=%d magic=%d\n", g_counter, g_magic);

        if (g_counter % 40 == 0)
            checkpoint();

        DelayThread(300 * 1000); /* 300 ms */
    }

    scr_printf("PROOF COMPLETE: magic=%d counter=%d writes=%d\n",
               g_magic, g_counter, g_writes);
    scr_printf("Is magic=%d YOUR number? Parking.\n", g_magic);
    printf("GDBTV-DONE: magic=%d counter=%d writes=%d - parking\n",
           g_magic, g_counter, g_writes);

    SleepThread(); /* park: proof stays on the TV, ps2link alive */
    return 0;      /* not reached */
}
