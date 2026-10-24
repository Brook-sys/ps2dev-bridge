EE_BIN = gdbmark.elf
EE_OBJS = gdbmark.o

EE_CFLAGS += -g -w
EE_LIBS = -ldebug -lps2gdbStub -lps2ips
EE_LDFLAGS += -L../lib

all:
	cp -f $(PS2SDK)/iop/irx/ps2ips.irx ./
	$(MAKE) $(EE_BIN)

clean:
	rm -f $(EE_BIN) $(EE_OBJS) ps2ips.irx

include $(PS2SDK)/samples/Makefile.pref
include $(PS2SDK)/samples/Makefile.eeglobal

# Appended AFTER the includes so these win over eeglobal defaults
# (make's last-flag-wins: -O2 would otherwise override -O0).
EE_CFLAGS += -O0 -fno-omit-frame-pointer
