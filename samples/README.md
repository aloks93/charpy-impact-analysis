# `samples/` — synthetic demo data

> **These files are not real measurements.** They were produced by a
> simulation, not by the Arduino + encoder rig. They exist only so the
> application can be demonstrated and tried out — including the **Animación**
> tab — without a physical pendulum. Do not use them as engineering reference
> data.

## What is in here

| File | Release → rebound | Absorbed (gross) | Absorbed (net) | What it shows |
|---|---|---|---|---|
| `synthetic_ductile_specimen_140deg.txt` | +140° → −15° | 5.0972 J | 4.9772 J | A ductile specimen: large rebound, high absorbed energy. |
| `synthetic_brittle_specimen_140deg.txt` | +140° → −135° | 0.1735 J | 0.1235 J | A brittle specimen: almost no rebound, low absorbed energy. |
| `synthetic_truncated_capture.txt` | +150° → −31.5° (estimated) | 5.0580 J | — | No result block in the file, and the capture stops right after the impact. The rebound angle can only be estimated, so the app raises its "unreliable rebound" warning and the value is an overestimate. |

The absorbed energies are for the default parameters used by the GUI and the CLI,
`m = 1.0 kg` and `R = 0.30 m`; the application scales linearly with the mass and
radius entered. The **net** column subtracts the friction loss reported in the
file's result block, when that block is present.

The first two files also include the firmware-style result block
(`Angulo inicial:`, `Angulo final (rebote):`, `Energia bruta ...:`, …), so the
metadata parser is exercised as well. The third file deliberately omits it.

## How they were generated

Produced by code with a small synthetic generator (intentionally not committed to
the repository):

1. A physical pendulum, `θ'' = -(g/R)·sin(θ)`, is integrated with RK4 from rest
   (`g = 9.81 m/s²`, `R = 0.30 m`).
2. A velocity jump is applied at the zero crossing so the swing reaches a chosen
   rebound peak — a stand-in for the energy absorbed by the specimen.
3. The motion is then sampled the way the firmware samples it: ~3500 µs polling
   with jitter, the angle quantised to the encoder's real 0.15°/count resolution,
   a maximum of 300 samples, and the rebound peak confirmed once the angle falls
   more than 1° below the running maximum.
4. The firmware-style result block is written alongside the time/angle table.

One trajectory is deliberately cut short (see `synthetic_truncated_capture.txt`)
to reproduce the 300-sample buffer limit described in the main README.

## Using them

1. Launch the app (`python main.py`, or the released `.exe`).
2. **Cargar archivo .txt** (*Load file*) → pick a file from this folder.
3. Keep the defaults (`m = 1.0 kg`, `R = 0.30 m`) and press **Calcular**
   (*Compute*).
4. Open the **Animación** tab and press **▶ Reproducir**.
