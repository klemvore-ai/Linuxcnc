#!/usr/bin/env python3
import time
import sys
try:
    import hal
    h = hal.component('cncfusion_gui')
    h.ready()
except ImportError:
    pass
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass