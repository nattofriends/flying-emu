flying-emu.service:
	m4 -DCURDIR=$(CURDIR) $@.m4 > $@
