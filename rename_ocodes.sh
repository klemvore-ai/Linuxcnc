#!/bin/bash
# Rename O-code files to the number-only format LinuxCNC searches for
# "O100 call" -> searches for file "100.ngc"
cd /home/darrell/linuxcnc/nc_files || exit 1

for n in 100 101 102 103 104 105 106 107 108 109 110 111 112 113; do
    for candidate in "O$n" "o$n" "O$n.ngc" "o$n.ngc"; do
        if [ -f "$candidate" ] && [ "$candidate" != "$n.ngc" ]; then
            mv "$candidate" "$n.ngc"
            echo "Renamed: $candidate -> $n.ngc"
        fi
    done
done

echo "---"
echo "Current O-code files:"
ls -la [0-9]*.ngc 2>/dev/null || echo "None found"
