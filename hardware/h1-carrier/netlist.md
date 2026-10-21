# H1 carrier board -- netlist

**STATUS: DESIGN ONLY. NOT BUILT, NOT ORDERED, NOT MEASURED, NOT POWERED.**
No board exists. No PCB has been laid out, no gerbers have been generated, no
part has been purchased, no board has been assembled and nothing has been
measured on hardware. Every figure in this file is either a datasheet-class
specification (named in the `references` field of each block) or a derived
calculation from those specifications. Nothing here was observed.

Scope: the carrier board around a Raspberry Pi Compute Module 4 (CM4) that
turns the CM4 into the VNX-DNA Drive controller: a USB-C host link in device
(gadget) mode, a power-loss hold-up that survives a write flush, a secure
element for key storage, tamper detection, and sensor/helper connectors for the
DNA instrument modules.

Format: a structured netlist, not a KiCad file. KiCad's `.kicad_sch` is a
generated, position-dependent S-expression format; hand-writing one produces a
file that opens but cannot be netlisted. This file is the unambiguous source
that a KiCad schematic must be transcribed from, and it is the artefact that
is reviewed.

Reference designators follow the usual convention: `U` integrated circuits,
`J` connectors, `R`/`C`/`L` passives, `Y` crystals, `F` fuses, `D` diodes,
`TP` test points, `H` mounting holes, `MH` mechanical.

---

## 0. Conventions and scope notes

* **Net naming**: `PWR_<rail>` for power, `GND` for the single ground plane,
  `<PERIPH>_<signal>` for everything else. Signal names match the pad names of
  the parts they connect to.
* **Voltage domains**: see `power-bus.md` for the rail list and the current
  budget. Logic levels are 3.3 V CMOS throughout, except the USB-C CC pins
  (1.5 V pull-up domain) and the Pi's 1.8 V IO on the CM4 module itself.
* **Ground**: one continuous ground plane on layer 2. The USB shield ground
  connects to it at a single point next to the USB-C connector, with the
  connector's shell soldered to the shield and the shield tied to GND through
  the ESD device's reference node. There is no "earth" connection.
* **Differential pairs**: the USB 2.0 D+/D- pair is a 90 ohm differential
  pair over a continuous reference plane; the length matching tolerance is
  0.5 mm.

---

## 1. Block map

| block | refdes range | summary |
|---|---|---|
| CM4 module + carrier connector | J1, J2, U1 | Raspberry Pi CM4, 100-pin high-density board-to-board connector |
| Power input + protection | F1, F2, D1, L1..L3, C1..C8 | 5 V USB-C input, eFuse, reverse-polarity and surge protection |
| USB-C device-mode port | J3, U2, U3, R1..R4, D2 | USB 2.0 device (gadget) port with CC and ESD |
| Power-loss hold-up | U4, C9..C12, R5..R8 | Supercapacitor + load switch, PGOOD, sized for a write flush |
| Secure element | U5, R9..R11, C13, C14 | ATECC608B secure element on I2C for key storage |
| Tamper detection | U6, SW1, D3, R12, R13 | switch + accelerometer/tamper loop, monitored by the Pi |
| Sensor / helper connectors | J4, J5, J6, U7 | I2C sensor bus, GPIO for pumps/valves/motors, UART for instruments |
| Clock + reset | Y1, C15, R14, SW2 | 24 MHz crystal for the helper MCU, reset button |
| Helper microcontroller | U7 | ESP32-class MCU on a serial link: temperature/humidity, tamper, pumps/valves/motors |
| Indicators | D4, D5, R15, R16 | power and status LEDs |
| Test points | TP1..TP12 | rails, CC, PGOOD, UART, I2C |

---

## 2. Net list (by block)

### 2.1 CM4 module and carrier connector (U1, J1, J2)

The CM4 plugs into the board through the 100-pin connector; the carrier
provides the power, USB and exposed interfaces. The CM4's own USB OTG port
(the one this design uses for the host link) is brought out on the carrier.

| net | from | to | note |
|---|---|---|---|
| `PWR_5V0` | J1.32 (5V) | J1.34 (5V) | both 5 V pins tied |
| `PWR_3V3` | J1.1, J1.17 (3V3) | J1.2, J1.18 | both 3V3 pins tied |
| `PWR_3V3` | J1.1 | J2.9 | to the I/O connector |
| `GND` | J1.6, J1.12, J1.30, J1.36 | J2.10, J2.14, J2.20 | module ground |
| `USB_D_P` | J1.11 (USB_DP) | U2.6 (D+) | 90 ohm differential pair |
| `USB_D_N` | J1.13 (USB_DN) | U2.4 (D-) | 90 ohm differential pair |
| `USB_VBUS_DET` | J1.9 (VBUS) | U7.16 (GPIO) | divider to 3.3 V, host-power detection |
| `PWR_3V3` | — | R1.1 | 15 kOhm CC pull-up (see 2.3) |
| `GPIO_25` | J1.22 | U7.20 | `PWR_GOOD` from the hold-up load switch |
| `GPIO_24` | J1.21 | U7.21 | `TAMPER_ALERT` from the tamper detector |
| `GPIO_23` | J1.19 | U7.22 | `SE_LOCK` (secure element lock / tamper latch) |
| `GPIO_27` | J1.23 | U7.23 | `USB_ENABLE` (gadget enable to the host) |
| `ID_SD` | J1.14 | J1.15 | CM4 ID_SD: leave open on a carrier (not a compute module 4 without the SD stack) -- see note |
| `PWR_3V3` | J1.3 | U5.1 (SCL pull-up) | 4.7 kOhm to 3V3 on the secure-element I2C |
| `GPIO_22` | J1.20 | U5.4 (SDA) | secure-element I2C data |

Note: the exact CM4 pin numbers above are from the CM4 mechanical drawing and
must be checked against the carrier-board schematic in KiCad before any layout
starts. They are marked UNVERIFIED here because no CM4 datasheet was opened
while writing this file. The *signal names* and the *topology* are the
design; the pin numbers are a transcription to be verified.

### 2.2 Power input and protection (F1, F2, D1, L1..L3, C1..C8)

The board is powered from a USB-C source (a host port or a wall adapter) and
must survive an abrupt host-side power cut, so the input stage is protected and
the hold-up in 2.4 covers the ride-through.

| refdes | value | part | package | description |
|---|---|---|---|---|
| J3 | — | USB-C receptacle, 16-pin | SMD | the host-facing port (device mode) |
| F1 | 2 A | resettable fuse (polyfuse) | 1206 | input over-current |
| F2 | 1 A | resettable fuse | 1206 | 3V3 branch |
| D1 | — | Schottky diode, 40 V, 3 A | SMA | reverse-polarity / reverse-current block on the 5 V input |
| L1 | 22 uH | power inductor, 3 A | 1210 | 5 V input filter |
| L2 | 10 uH | power inductor, 2 A | 1210 | 3V3 branch filter |
| L3 | 4.7 uH | power inductor, 1 A | 0805 | hold-up branch filter |
| C1..C3 | 22 uF | ceramic X5R 10 V | 0805 | 5 V bulk, at the connector |
| C4..C6 | 22 uF | ceramic X5R 6.3 V | 0805 | 3V3 bulk |
| C7, C8 | 100 nF | ceramic X7R | 0402 | bypass, one per rail at the connector |

| net | from | to | note |
|---|---|---|---|
| `USB_VBUS_5V` | J3.VBUS (A4,B9,A9,B4) | F1.1 | the 5 V input rail from the host |
| `USB_VBUS_5V` | F1.1 | D1.A | |
| `PWR_5V0` | D1.K | L1.1 | protected 5 V |
| `PWR_5V0` | L1.2 | U4.VIN | feeds the hold-up load switch |
| `PWR_3V3` | U4.VOUT | L2.1 | switched 3V3 to the CM4 and the logic |
| `GND` | F1.2, D1.2, L1.GND, L2.GND, L3.GND | plane | |

### 2.3 USB-C device-mode port (J3, U2, U3, R1..R4, D2)

The board presents itself to the host as a USB device (the VNX-DNA Drive
gadget). For a USB-C receptacle in device (sink) role, the Configuration
Channel (CC) pins must be terminated so that the host's source advertises
current and the device is recognised as a sink. A USB-C device that does not
terminate CC correctly is not enumerated at all.

Requirements implemented here:

1. **CC pull-up (Rd)**: a 5.1 kOhm pull-up from each CC pin to GND declares
   the receptacle a UFP sink and advertises the default current (900 mA) or
   1.5 A / 3.0 A with the R2/R3 selector resistors. This board uses the
   default 900 mA advertisement, so both CC pins carry a 5.1 kOhm Rd to GND.
2. **CC-VBUS short**: on a device that draws from VBUS, the CC and VBUS pins
   are NOT shorted (that is the source-side Rp case). No CC-VBUS capacitor
   is fitted.
3. **ESD protection**: a low-capacitance (< 1 pF) ESD array on D+/D- and on
   the CC pins, referenced to the ground plane next to the connector.
4. **Shield**: the connector shell is soldered to a shield pad that connects
   to the ground plane through the ESD device's ground pins.

| refdes | value | part | package | description |
|---|---|---|---|---|
| U3 | — | USB-C CC controller, e.g. FUSB302 | QFN-12 | optional; simplifies the CC/Pd contract. This board uses discrete Rd only, so U3 is DNP (not populated) in revision A. |
| R1 | 5.1 kOhm | 1 % resistor | 0402 | CC1 pull-up (Rd) to GND |
| R2 | 5.1 kOhm | 1 % resistor | 0402 | CC2 pull-up (Rd) to GND |
| R3 | 5.1 kOhm | 1 % resistor | 0402 | CC1 alternate Rd (unpopulated in rev A) |
| R4 | 5.1 kOhm | 1 % resistor | 0402 | CC2 alternate Rd (unpopulated in rev A) |
| D2 | — | ESD array, 2-channel, < 1 pF, e.g. USBLC6-2SC6 | SOT-23-6 | D+/D- and CC protection |
| D3 | — | TVS diode on VBUS, 5 V | SOD-123 | input surge protection |

| net | from | to | note |
|---|---|---|---|
| `USB_CC1` | J3.CC1 (A5,B5) | R1.1, D2.5 | 5.1 kOhm to GND |
| `USB_CC1` | R1.2 | GND | Rd to ground |
| `USB_CC2` | J3.CC2 (A12,B12) | R2.1, D2.6 | 5.1 kOhm to GND |
| `USB_CC2` | R2.2 | GND | Rd to ground |
| `USB_D_P` | J3.DP (A6,B6,A7,B7) | D2.3, U2.6 | |
| `USB_D_N` | J3.DN (A2,B2,A3,B3) | D2.1, U2.4 | |
| `USB_SHIELD` | J3.SHIELD | D2.2 | shell to ground plane via the ESD ground |
| `GND` | D2.2 | plane | |

Note: the CM4's USB is a USB 2.0 device-mode port. The host link carries
SCSI Bulk-Only Transport commands (the V14 external controller design, stage
C1). UAS is not used because upstream UEFI firmware drives BOT only.

### 2.4 Power-loss hold-up (U4, C9..C12, R5..R8)

**What it is for.** The VNX-DNA Drive is DNA-only: every write goes through
chemistry, and the *electronic* part of a write is small (a journal record, a
header). The hold-up keeps the board alive long enough for the software to
finish the electronic part of a write and reach a consistent state when the
host cuts the power. It is not a battery and it does not store user data; the
DNA pool is the storage.

**Sizing.** The ride-through must cover (a) the time to detect the power
event, (b) the time for the software to flush the journal to the medium it is
allowed to write, and (c) the time to unmount cleanly. The software budget
BUDGET is 2 s of hold-up (a design target, not a measurement; the actual
flush time is a property of the medium and is not yet characterised).

Energy needed: E = 3.3 V * I * t = 3.3 V * 0.25 A * 2 s = 1.65 J. With a
usable fraction of 0.8 of the supercapacitor's stored energy, the capacitor
must hold E/0.8 = 2.06 J. For a single 2.7 V cell at 300 F: E = 1/2 * C * V^2
= 0.5 * 300 * 7.29 = 1093 J, of which the usable fraction down to the 2.0 V
cutoff is (2.0^2 - 0.5^2)/2.7^2 = 3.75/7.29 = 51 %. That is 560 J usable --
three orders of magnitude more than the 1.65 J needed, because a supercap is
sized for power density, not energy. The design therefore sizes the
supercapacitor for **power**, not energy: it must supply 0.25 A for 2 s
without its voltage collapsing, and the energy margin above is what makes that
possible. A 10 F, 5.5 V part would also satisfy the requirement with a large
margin; the 300 F part is chosen for the inrush behaviour of the CM4.

The 3.3 V rail is the one that must be held up, because the CM4 and the
helper MCU draw from it. The 5 V input rail is *not* held up: the load switch
for 3V3 is fed from 5 V and its own output is the hold-up domain.

| refdes | value | part | package | description |
|---|---|---|---|
| U4 | — | load switch with reverse current blocking and a PGOOD output, e.g. TPS25940 | VSON-8 | 5 V in, 3.3 V out, `PGOOD` open-drain, reverse-blocking (so the supercap does not back-feed the host) |
| C9 | 300 F | supercapacitor, 2.7 V, series cell | 21 mm can | the hold-up energy store |
| C10 | 100 uF | electrolytic, 6.3 V, low ESR | SMD | ripple current smoothing at the load switch |
| C11 | 100 nF | ceramic | 0402 | load switch bypass |
| C12 | 100 uF | electrolytic, 6.3 V | SMD | at the supercap, limits inrush into the switch |
| R5 | 100 kOhm | resistor | 0603 | `PGOOD` pull-up to 3V3 |
| R6 | 0 Ohm | jumper (option) | 0402 | connects the hold-up domain to the 3V3 rail (fitted in rev A) |
| R7 | 10 kOhm | resistor | 0402 | `SUPERCAP_SENSE` divider |
| R8 | 10 kOhm | resistor | 0402 | `SUPERCAP_SENSE` divider |

| net | from | to | note |
|---|---|---|---|
| `PWR_5V0` | L1.2 | U4.VIN | 5 V input |
| `PWR_3V3_HOLD` | U4.VOUT | L2.1, C10.1, C9.1 | the held-up 3.3 V rail |
| `PWR_3V3_HOLD` | C9.1 | C12.1 | supercap bank on the held-up rail |
| `GND` | U4.GND, C9.2, C10.2, C12.2 | plane | |
| `PWR_GOOD` | U4.PGOOD | R5.1, J1.22 (GPIO_25) | open-drain, pulled up to 3V3 |
| `SUPERCAP_SENSE` | C9.1 | R7.1 | divided by 2 into the helper MCU's ADC |
| `SUPERCAP_SENSE` | R7.2 | U7.ADC1 | |
| `SUPERCAP_SENSE` | R7.2 | R8.1 | |
| `GND` | R8.2 | plane | divider bottom |

**Power-good semantics.** `PWR_GOOD` low means the 3.3 V rail is not
established: the software must not start a write and must not bind the USB
gadget. The helper MCU samples it on an interrupt; the CM4 reads it over the
serial link. This is the hardware half of the write refusal rule; the
software half lives in the job engine (see `../interfaces.md`).

### 2.5 Secure element (U5, R9..R11, C13, C14)

The secure element holds the device key material (the device identity, the
per-pool key-encryption keys, the audit-chain signing key) so that the
private keys never leave the element and are not readable by the operating
system.

The ATECC608B speaks I2C at 3.3 V. Its I2C pins are open-drain, so the bus
needs pull-ups.

| refdes | value | part | package | description |
|---|---|---|---|---|
| U5 | — | ATECC608B secure element (or SE050) | SOIC-8 / UDFN-8 | I2C key storage, with an internal monotonic counter and a tamper flag |
| R9 | 4.7 kOhm | 1 % resistor | 0402 | SCL pull-up to 3V3 |
| R10 | 4.7 kOhm | 1 % resistor | 0402 | SDA pull-up to 3V3 |
| R11 | 10 kOhm | resistor | 0402 | `SE_LOCK` pull-up (active-low tamper latch) |
| C13 | 100 nF | ceramic | 0402 | decoupling |
| C14 | 1 uF | ceramic | 0402 | internal regulator decoupling (per the part's datasheet) |

| net | from | to | note |
|---|---|---|---|
| `I2C0_SCL` | U5.2, R9.1, J1.3 | J4.3, U7.SCL | shared bus |
| `I2C0_SCL` | R9.2 | PWR_3V3_HOLD | pull-up |
| `I2C0_SDA` | U5.1, R10.1, J1.22 | J4.5, U7.SDA | shared bus |
| `I2C0_SDA` | R10.2 | PWR_3V3_HOLD | pull-up |
| `SE_LOCK` | U5.6 | R11.1, J1.19 (GPIO_23) | the element's tamper/rotation output, active-low |
| `SE_LOCK` | R11.2 | PWR_3V3_HOLD | pull-up |
| `PWR_3V3_HOLD` | U5.8, C13.1, C14.1 | rail | |
| `GND` | U5.5, C13.2, C14.2 | plane | |

Note: the ATECC608B's I2C address is fixed at 0x60 and does not conflict with
the other bus members if the sensor bus uses 0x48-0x4F (see 2.7).

### 2.6 Tamper detection (U6, SW1, D4, R12, R13)

Two independent tamper paths:

1. **Enclosure switch** (`SW1`): a normally-closed microswitch on the lid.
   Opening the lid breaks the loop, which the helper MCU sees on a GPIO. It is
   the "was the case opened" signal, not a live sensor.
2. **Shock/vibration sensor** (`U6`): an accelerometer on the I2C bus whose
   interrupt line is wired to the helper MCU. It detects movement while the
   drive is in use, which for a portable DNA cartridge means the cartridge was
   moved during a synthesis or retrieval run.

| refdes | value | part | package | description |
|---|---|---|---|---|
| U6 | — | 3-axis accelerometer with an interrupt output, e.g. LIS2DH12 | LGA-12 | I2C, address 0x19 |
| SW1 | — | normally-closed microswitch | THT | lid tamper switch |
| D4 | — | LED, red | 0402 | tamper indicator (lit on a latched tamper) |
| R12 | 10 kOhm | resistor | 0402 | tamper loop pull-up |
| R13 | 1 kOhm | resistor | 0402 | LED current limit |

| net | from | to | note |
|---|---|---|---|
| `TAMPER_SW` | SW1.1 | R12.1, U7.GPIO8 | closed = high (untampered) |
| `TAMPER_SW` | R12.2 | PWR_3V3_HOLD | pull-up |
| `TAMPER_SW` | SW1.2 | GND | the switch ties to ground when the lid is closed |
| `TAMPER_INT` | U6.INT1 | U7.GPIO9 | accelerometer interrupt |
| `I2C0_SCL` | U6.SCL | bus | |
| `I2C0_SDA` | U6.SDA | bus | |
| `TAMPER_ALERT` | U7.GPIO9 | J1.21 (GPIO_24) | to the CM4 |
| `LED_TAMPER` | D4.1 | R13.1, U7.GPIO10 | |
| `LED_TAMPER` | R13.2 | GND | |

### 2.7 Sensor and helper connectors (J4, J5, J6, U7)

The DNA instrument modules (electrode array synthesiser, nanopore reader) are
external boxes that plug into the drive. The carrier provides the connectors
and the helper microcontroller that drives them.

**U7 (helper MCU)**: an ESP32-class microcontroller with Wi-Fi disabled (the
device is offline-only, AP-17) and deep-sleep between polls. It runs on the
3.3 V hold-up rail, so it survives the power cut long enough to close the
fluidic valves and park the pumps.

| refdes | value | part | package | description |
|---|---|---|---|---|
| U7 | — | ESP32-class MCU (e.g. ESP32-S3-WROOM-1) | module | helper: sensors, tamper, pumps/valves/motors |
| J4 | 4-pin, 1.27 mm | IDC / JST connector | THT | I2C sensor bus (temperature, humidity, pressure) |
| J5 | 8-pin, 2.54 mm | header | THT | GPIO for pumps, valves, motor drivers |
| J6 | 4-pin, 1.27 mm | connector | THT | UART to the instrument modules |
| C15 | 22 uF | ceramic | 0805 | U7 bulk |
| Y1 | 24 MHz | crystal | SMD | U7 clock (40 MHz is also acceptable; the crystal is a placeholder for the USB-serial bridge clock) |
| C16, C17 | 12 pF | ceramic | 0402 | Y1 load capacitors |
| SW2 | — | push button | THT | reset (MCU reset and the `PGOOD` latch clear) |
| R14 | 10 kOhm | resistor | 0402 | reset pull-up |

| net | from | to | note |
|---|---|---|---|
| `I2C0_SCL` | J4.1, U5.2, U6.SCL | J4.3, U7.SCL | the shared sensor bus |
| `I2C0_SDA` | J4.2, U5.1, U6.SDA | J4.5, U7.SDA | |
| `I2C0_SCL` | J4.4 | PWR_3V3_HOLD | bus power (5 V tolerant devices only; 3.3 V otherwise) |
| `I2C0_SDA` | J4.6 | GND | |
| `PUMP_1` | U7.GPIO2 | J5.1 | pump 1 drive (through a MOSFET, see below) |
| `PUMP_2` | U7.GPIO3 | J5.2 | pump 2 drive |
| `VALVE_1` | U7.GPIO4 | J5.3 | valve 1 drive |
| `VALVE_2` | U7.GPIO5 | J5.4 | valve 2 drive |
| `MOTOR_STEP` | U7.GPIO6 | J5.5 | stepper pulse (microfluidic pump positioning) |
| `MOTOR_DIR` | U7.GPIO7 | J5.6 | stepper direction |
| `SPARE_IO1` | U7.GPIO10 | J5.7 | spare |
| `SPARE_IO2` | U7.GPIO11 | J5.8 | spare |
| `UART_TX` | U7.U0TXD | J6.3 | to the instrument module (115200 8N1) |
| `UART_RX` | U7.U0RXD | J6.4 | from the instrument module |
| `GND` | J4.7, J5.9, J6.2 | plane | |
| `PWR_5V0` | J6.1 | instrument module 5 V (for a module that needs it) | |

**Pump/valve drive.** The MCU GPIO cannot drive a pump or a valve directly.
Revision A provides the *logic* side only: the connector carries the logic
level and the actual MOSFET drivers are on the instrument module's board,
because the driver topology depends on the pump and valve that are chosen and
those are not yet decided. This is recorded as an open item in
`power-bus.md` and in `../../docs/HARDWARE_DESIGN.md`.

### 2.8 Indicators and test points

| refdes | value | part | description |
|---|---|---|---|
| D5 | — | LED, green | power present |
| R15 | 1 kOhm | resistor | LED current limit |
| D6 | — | LED, blue | `PWR_GOOD` / gadget attached |
| R16 | 1 kOhm | resistor | LED current limit |
| TP1..TP3 | — | test points | `PWR_5V0`, `PWR_3V3_HOLD`, `GND` |
| TP4..TP6 | — | test points | `USB_D_P`, `USB_D_N`, `USB_CC1` |
| TP7 | — | test point | `PWR_GOOD` |
| TP8 | — | test point | `SUPERCAP_SENSE` |
| TP9..TP10 | — | test points | `I2C0_SCL`, `I2C0_SDA` |
| TP11..TP12 | — | test points | `UART_TX`, `UART_RX` |

---

## 3. Design rules for the PCB

* **Layer stack**: 4 layers. L1 signal, L2 solid `GND`, L3 power (5 V / 3.3 V
  pours), L4 signal. The USB pair runs on L1 over the L2 ground plane.
* **USB differential pair**: 90 ohm differential impedance, length matched
  within 0.5 mm, no vias on the pair, a ground pour on both sides.
* **ESD return path**: D2 sits within 5 mm of J3 so the surge current returns
  to the ground plane over a short loop.
* **Hold-up domain**: the supercap, U4, C10 and C12 form a region whose
  ground is the same plane but whose 3.3 V pour is separate from the rest of
  the board until R6.
* **Secure element**: U5 is placed away from the board edge, with its
  exposed pad (if any) tied to `GND`, and its region kept free of user-
  accessible test points.
* **Thermal**: the CM4 dissipates its heat through the module's own thermal
  pads; the carrier provides the ground-plane copper under the connector and
  a keep-out in the enclosure lid above the module.
* **No copper under the supercap** in the region of the leakage-sensitive
  analogue inputs of U6 (the accelerometer): U6 sits in a quiet area with a
  ground guard.

---

## 4. What is deliberately NOT on this board

* No Ethernet. The device is offline-only (AP-17).
* No display or user interface.
* No mass-storage controller chip: the CM4 runs Linux and the gadget stack
  presents the device; the VNX-DNA Drive is not a USB stick that a host
  formats.
* No battery: the hold-up is a supercapacitor, which cannot be shipped across
  the air with a charge. This is a transport constraint, not an oversight.
* No second USB port: one host link only.

---

## 5. Open items (design not yet closed)

| id | item | why it is open |
|---|---|---|
| H1-1 | CM4 pin numbers | transcribed from the mechanical drawing without the datasheet open; marked UNVERIFIED |
| H1-2 | MOSFET driver stage for pumps/valves | depends on the pump and valve selection, which is a wet-lab decision (H5) |
| H1-3 | USB-C source/sink role handling | rev A is a sink-only device; adding source capability (for charging the host) needs a PD controller and is out of scope |
| H1-4 | Supercapacitor balancing | a single 2.7 V cell needs no balancing; a series stack would |
| H1-5 | Enclosure vent pattern | the airflow requirement depends on the thermal load of the DNA modules, not yet characterised |

---

references:
* USB Type-C Cable and Connector Specification, USB-IF: CC pull-up (Rd) 5.1 kOhm
  for a sink, the VBUS/CC short rule and the ESD placement guidance
* Raspberry Pi CM4 product brief and the CM4 mechanical drawing (pin list)
* Microchip ATECC608B datasheet (I2C address 0x60, I2C operating voltage)
* Texas Instruments TPS25940 load-switch datasheet (PGOOD, reverse-current
  blocking, current limit)
* Raspberry Pi documentation, USB device mode / gadget (V14_BOOT_ARCHITECTURE.md
  §5, V14_EXTERNAL_CONTROLLER.md)
* docs/V14_BOOT_ARCHITECTURE.md, docs/LAB_INTERFACE.md (the software contracts
  this board serves)
