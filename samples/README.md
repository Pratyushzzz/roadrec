# Sample event files

Example event files in the format the recorder writes (`t, ax, ay, az, gx, gy, gz, speed`; acceleration in g,
rotation in deg/s, speed in m/s, 100 Hz). Load any of them in `web/index.html`, or run the C++ detector on them:

    cd firmware
    g++ -O2 -std=c++17 -Ilib/roadcore host/replay.cpp lib/roadcore/roadcore.cpp -o host/replay
    host/replay ../samples/evt_0001_DROP.csv

| File | Labels from the firmware detector | Source |
|---|---|---|
| `evt_0001_DROP.csv` | `DROP`: free-fall 180 ms, vehicle not moving | Phone recording |
| `evt_0002_CRASH.csv` | `CRASH`: impact 7.4 g, orientation changed 94°, then stopped; plus a trailing `INCOMPLETE` (recording ends too soon to judge) | Phone recording |
| `evt_0003_synthetic_HARD_BRAKE.csv` | `HARD_BRAKE`: 0.47 g for 1150 ms, no swerve | **Synthetic**, generated to show what the rule looks for |

None of these come from the recorder hardware. The CRASH file is a phone being dropped or tipped over, not a real vehicle crash.
