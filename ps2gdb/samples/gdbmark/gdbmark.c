#include <stdio.h>
#include <debug.h>

int gdb_stub_main(int argc, char *argv[]);

volatile int g_counter = 0;
volatile int g_mark = 0;

static void bump(void)
{
    g_counter++;
}

int main(int argc, char *argv[])
{
    volatile int i = 0;

    init_scr();
    printf("MARK1: before gdb_stub_main - udptty alive so far\n");
    gdb_stub_main(0, NULL);
    printf("MARK2: stub returned - udptty SURVIVED ps2ips + ps2ip_init + gdb session\n");
    scr_printf("i = %d\n", i += 2);
    printf("MARK3: after scr_printf 1\n");
    scr_printf("i = %d\n", i += 10);
    printf("MARK4: parking now (breakpoint target: bump)\n");

    while (1) {
        bump();
        if (g_mark == 42) {
            printf("MARK-GDB: agent wrote g_mark=42 via GDB, counter=%d - live write proven\n", g_counter);
            scr_printf("GDB live write OK - g_mark=42 counter=%d\n", g_counter);
            g_mark = 0;
            break; // exit cleanly so ps2link reclaims the exec slot (no reset dance)
        }
    }
    printf("MARK5: exiting cleanly\n");
    return 0;
}
