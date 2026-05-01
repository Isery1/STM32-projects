# Impedance Project — Development Log

> **Board:** NUCLEO-F303K8 (STM32F303K8Tx, LQFP32)  
> **IDE:** STM32CubeIDE 1.19.0  
> **Toolchain:** arm-none-eabi-gcc, HAL Firmware FW_F3 V1.11.6  
> **Project path:** `workspace_1.19.0/Impedance/`

---

## Project Purpose

Impedance spectroscopy on the STM32F303K8 using the **Analog Devices AD5940/AD5941** front-end IC.  
The AD5940 sweeps a configurable frequency range (100 Hz – 100 kHz), measures the complex impedance of a connected device under test (DUT), and sends magnitude + phase results over USART2 to a PC terminal.

---

## Hardware Pin Mapping

| Function       | Pin  | Mode                  | Notes                          |
|---|---|---|---|
| SPI1_SCK       | PB3  | AF1 (SPI1)            | Configured in `.ioc`           |
| SPI1_MISO      | PB4  | AF1 (SPI1)            | Configured in `.ioc`           |
| SPI1_MOSI      | PB5  | AF1 (SPI1)            | Configured in `.ioc`           |
| AD5940 CS      | PA8  | GPIO Output (High)    | Configured in `.ioc`           |
| AD5940 RST     | PA4  | GPIO Output (High)    | Configured in `.ioc` ✅        |
| AD5940 INT/GP0 | PA0  | GPIO EXTI0, Falling   | Configured in `.ioc` ✅, EXTI0 NVIC enabled |
| USART2_TX      | PA2  | AF7 (USART2)          | Nucleo virtual COM port        |
| USART2_RX      | PA15 | AF7 (USART2)          | Nucleo virtual COM port        |

---

## SPI1 Configuration

| Parameter        | Value                       |
|---|---|
| Mode             | Master, Full-Duplex         |
| Data Size        | 8-bit                       |
| Clock Polarity   | Low (CPOL=0)                |
| Clock Phase      | 1 Edge (CPHA=0) — Mode 0   |
| NSS              | Software                    |
| Baud Prescaler   | /64 → **125 kHz SCK**       |

---

## Changes & Fixes

### Fix 1 — Duplicate `Src/` / `Inc/` / `Startup/` Folders

**Problem:** The project was originally created as a bare-metal (no HAL) project. When CubeMX regenerated the project using the `.ioc` editor, it placed all source files into `Core/`. However, the original skeleton folders remained and were compiled alongside, causing a **duplicate `main()` linker error** at link time.

**Root cause:** Both `./Core/Src/main.o` and `./Src/main.o` were listed in `Debug/objects.list` and linked together.

**Fix — deleted the following:**

| Deleted path | Reason |
|---|---|
| `Impedance/Src/` | Bare-metal `main.c`, `syscalls.c`, `sysmem.c` — duplicate of `Core/Src/` |
| `Impedance/Inc/` | Empty legacy include folder |
| `Impedance/Startup/` | Duplicate `startup_stm32f303k8tx.s` |
| `Impedance/Debug/` | Stale build artefacts referencing the deleted files |

---

### Fix 2 — Missing Include Paths in `.cproject`

**Problem:** After removing the legacy `Inc/` folder, the `.cproject` compiler settings still pointed to `../Inc` as the only include path. This caused 25 fatal errors of the form:
```
fatal error: stm32f3xx_hal.h: No such file or directory
```

**Fix — updated include paths in `.cproject` (Debug & Release):**

| Before | After |
|---|---|
| `../Inc` | `../Core/Inc` |
| — | `../Drivers/STM32F3xx_HAL_Driver/Inc` |
| — | `../Drivers/STM32F3xx_HAL_Driver/Inc/Legacy` |
| — | `../Drivers/CMSIS/Device/ST/STM32F3xx/Include` |
| — | `../Drivers/CMSIS/Include` |

**Also fixed in `.cproject`:**
- Added compiler defines `USE_HAL_DRIVER` and `STM32F303x8` (required by the HAL).
- Changed `<sourceEntries>` from `name=""` (whole root) to explicit `Core` and `Drivers` entries.

---

### Feature — AD5940 Library Integration

**Library source:** [analogdevicesinc/ad5940-examples](https://github.com/analogdevicesinc/ad5940-examples)  
The `ad5940lib` core driver is a Git submodule inside that repository: [analogdevicesinc/ad5940lib](https://github.com/analogdevicesinc/ad5940lib).

**Files added to the project:**

| File | Description |
|---|---|
| `Core/Inc/ad5940.h` | AD5940 driver header — all register definitions and API |
| `Core/Inc/Impedance.h` | Impedance application config structs and API |
| `Core/Src/AD5940Lib/ad5940.c` | Hardware-agnostic AD5940 driver |
| `Core/Src/AD5940Lib/Impedance.c` | Impedance sweep application logic (DFT-based) |
| `Core/Src/AD5940Lib/AD5940Main.c` | Example entry — sweeps frequencies, prints results |
| `Core/Src/AD5940Lib/NUCLEOF303K8Port.c` | **Custom MCU port** for STM32F303K8 |

#### MCU Port — `NUCLEOF303K8Port.c`

The AD5940 library is hardware-agnostic. It requires 7 bridge functions to be implemented per MCU platform (adapted from the official `NUCLEO-F411` reference port):

| Function | Implementation |
|---|---|
| `AD5940_ReadWriteNBytes()` | `HAL_SPI_TransmitReceive(&hspi1, ...)` — uses the existing `hspi1` handle from `main.c` |
| `AD5940_CsClr()` / `AD5940_CsSet()` | `HAL_GPIO_WritePin(GPIOA, GPIO_PIN_8, ...)` |
| `AD5940_RstClr()` / `AD5940_RstSet()` | `HAL_GPIO_WritePin(GPIOA, GPIO_PIN_4, ...)` |
| `AD5940_Delay10us()` | `HAL_Delay()` — rounds 10 µs ticks up to nearest ms |
| `AD5940_GetMCUIntFlag()` / `AD5940_ClrMCUIntFlag()` | Volatile flag managed by EXTI0 ISR |
| `AD5940_MCUResourceInit()` | Deasserts CS and RST at startup — GPIO config now handled by CubeMX |
| `AD5940_SetMCUIntFlag()` | Sets interrupt flag — called from `EXTI0_IRQHandler` in `stm32f3xx_it.c` |

#### `main.c` Changes

```c
/* Includes */
#include "ad5940.h"
#include "Impedance.h"

/* printf redirect — sends output to USART2 (Nucleo virtual COM port) */
int _write(int file, char *data, int len) {
    HAL_UART_Transmit(&huart2, (uint8_t*)data, (uint16_t)len, HAL_MAX_DELAY);
    return len;
}

/* After MX_SPI1_Init() */
AD5940_MCUResourceInit(NULL);  // deasserts CS + RST (GPIO already set up by CubeMX)

/* In while(1) */
AD5940_Main();  // runs the impedance sweep — has its own internal loop
```

#### Impedance Sweep Parameters (default, configurable in `AD5940Main.c`)

| Parameter         | Value                          |
|---|---|
| Frequency range   | 100 Hz → 100 kHz               |
| Points            | 101 (log-spaced)               |
| RCAL reference    | 10 kΩ                          |
| HSTIA TIA gain    | 5 kΩ                           |
| DFT points        | 16384                          |
| ADC sample rate   | 400 kSPS (SINC3 OSR=2)         |
| Power mode        | HP (required above 80 kHz)     |
| Output per point  | `Freq [Hz]`, `|Z| [Ω]`, `∠Z [°]` over UART |

---

### Fix 3 — Duplicate `EXTI0_IRQHandler` After Adding PA0 in `.ioc`

**Problem:** CubeMX regenerated `EXTI0_IRQHandler()` in `stm32f3xx_it.c` when PA0 was added as a GPIO EXTI0 pin. The port file `NUCLEOF303K8Port.c` also defined `EXTI0_IRQHandler()`, which would cause a **multiple definition linker error**.

Additionally, `AD5940_MCUResourceInit()` was re-initializing PA4 and PA0 GPIOs that CubeMX now manages, causing redundant configuration.

**Fix — `NUCLEOF303K8Port.c` (v1.1.0):**
- Removed `EXTI0_IRQHandler()` entirely from the port file.
- Replaced it with `AD5940_SetMCUIntFlag()` — a simple public function that sets the internal interrupt flag.
- Simplified `AD5940_MCUResourceInit()` to only deassert CS and RST pins; all GPIO init is now handled by CubeMX.

**Fix — `stm32f3xx_it.c`:**
- Added `extern void AD5940_SetMCUIntFlag(void);` in USER CODE Includes.
- Called `AD5940_SetMCUIntFlag()` inside the CubeMX-generated `EXTI0_IRQHandler()` USER CODE block.

**Interrupt flow after fix:**
```
AD5940 pulls GP0/PA0 LOW
  → EXTI0_IRQHandler()  in stm32f3xx_it.c  (CubeMX-owned)
      → HAL_GPIO_EXTI_IRQHandler()          (clears EXTI pending bit)
      → AD5940_SetMCUIntFlag()              (sets ucInterrupted = 1)
  → AD5940_GetMCUIntFlag() returns 1 in AD5940_Main() loop
  → AppIMPISR() processes FIFO data
```

---

### Feature — Hardware Self-Test Mode (`TEST_MODE`)

**Purpose:** Validate USART2 and GPIO without the physical AD5940 IC. The test uses only components already available: the NUCLEO board and an external LED + resistor.

**To switch between test mode and measurement mode:** change one line in `main.c`:
```c
#define TEST_MODE  1   /* 1 = self-test | 0 = AD5940 measurement */
```

#### Wiring for the test

```
PA8 ──[ 330 Ω ]──[ LED ]── GND
```

Connect an LED with a ~330 Ω series resistor between **PA8** (Arduino pin D7) and **GND**. PA8 is the AD5940 CS pin — it is safe to use it as an LED driver when the IC is not connected.

#### What the test does

| Test | Behaviour |
|---|---|
| **USART2 TX** | Prints a startup banner on power-on |
| **LED blink** | PA8 toggles every **500 ms** (1 Hz blink rate) |
| **UART status** | Prints `[tick ms] LED blinks: N | USART2 OK` once per second |
| **UART echo** | Any character you type in the terminal is echoed back as `ECHO < 0xXX 'c' >` |

#### Terminal settings (PuTTY / Tera Term / CubeIDE console)

| Setting | Value |
|---|---|
| Port | Nucleo USB virtual COM (STLink VCP) |
| Baud | **38400** |
| Data | 8 bits, No parity, 1 stop bit (8N1) |
| Flow control | None |

#### Expected terminal output (example)

```
=== Impedance Board Self-Test ===
USART2 OK  : 38400 baud, 8N1
LED        : PA8 (blink every 500 ms)
SPI1       : PB3/PB4/PB5 configured (AD5940 not connected)
Type any key to echo it back.
---------------------------------
[  1001 ms] LED blinks: 2 | USART2 OK
[  2001 ms] LED blinks: 4 | USART2 OK
ECHO < 0x41 'A' >
[  3001 ms] LED blinks: 6 | USART2 OK
```

#### Switching to AD5940 mode (when IC is available)

1. Set `#define TEST_MODE 0` in `main.c`
2. Uncomment the AD5940 `#include` lines in USER CODE Includes
3. Uncomment `AD5940_MCUResourceInit(NULL)` in USER CODE 2
4. Uncomment `AD5940_Main()` in USER CODE 3

---

## Git Commit History

| Commit | Description |
|---|---|
| `fix: remove duplicate Src/Inc/Startup folders from Impedance project` | Deleted legacy bare-metal folders |
| `fix: update Impedance project include paths and source entries` | Fixed `.cproject` |
| `feat: integrate AD5940 impedance library (ADI ad5940lib)` | Full AD5940 integration |
| `docs: move changelog into Impedance project, scope to Impedance only` | Changelog relocated |
| `fix: resolve duplicate EXTI0_IRQHandler, wire AD5940 INT to CubeMX handler` | Fixed IRQ conflict after PA0 added to `.ioc` |
| `docs: update changelog with PA0/PA4 ioc config and EXTI0 fix` | Changelog update |
| `feat: add hardware self-test mode (LED blink + UART echo)` | Test program for use without AD5940 |

---

## Next Steps

- [x] Add **PA4** (RST) and **PA0** (INT) to `Impedance.ioc` and re-generate
- [x] Verify UART output with self-test mode
- [x] Verify LED blink with self-test mode
- [ ] Connect physical AD5940/AD5941 evaluation board
- [ ] Set `TEST_MODE 0` and verify SPI communication with AD5940
- [ ] Verify RCAL value matches the on-board calibration resistor
- [ ] Tune HSTIA gain (`HstiaRtiaSel`) for target impedance range
- [ ] Consider DMA-based SPI for higher data throughput
