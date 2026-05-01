# STM32 Projects — Development Log

> **Repository:** [Isery1/STM32-projects](https://github.com/Isery1/STM32-projects)  
> **Board:** NUCLEO-F303K8 (STM32F303K8Tx, LQFP32)  
> **IDE:** STM32CubeIDE 1.19.0  
> **Toolchain:** arm-none-eabi-gcc, HAL Firmware FW_F3 V1.11.6  

---

## Session Log — 2026-05-01

---

### 1. Git Installation & Repository Setup

- **Installed Git** on Windows via `winget install --id Git.Git`.
- **Configured global identity:**
  ```
  user.name  = Isery1
  user.email = hetteggermatthias@yahoo.de
  ```
- **Created** a new GitHub repository: [`Isery1/STM32-projects`](https://github.com/Isery1/STM32-projects).
- **Initialized** local git repository in `workspace_1.19.0/`.
- Added a `.gitignore` tailored for STM32CubeIDE (excludes `Debug/`, `Release/`, build artefacts, IDE metadata).
- **Initial commit and push** of all existing workspace projects to `main` branch.

---

### 2. Project: `Test` — SPI1 + LED Blink

#### Overview
Empty STM32CubeIDE HAL project on the Nucleo-F303K8. Goal: configure SPI1 and blink the built-in LED at a 1-second interval.

#### Hardware Pin Mapping

| Function     | Pin  | Mode              |
|---|---|---|
| SPI1_SCK     | PA5  | AF5 (SPI1)        |
| SPI1_MISO    | PA6  | AF5 (SPI1)        |
| SPI1_MOSI    | PA7  | AF5 (SPI1)        |
| SPI1_CS (SW) | PA4  | GPIO Output       |
| LED (LD3)    | PB3  | GPIO Output PP    |
| USART2_TX    | PA2  | AF7 (USART2)      |
| USART2_RX    | PA15 | AF7 (USART2)      |

#### Changes Made

**`Test/Core/Inc/main.h`**
- Added LED pin defines inside `USER CODE Private defines`:
  ```c
  #define LED_Pin       GPIO_PIN_3
  #define LED_GPIO_Port GPIOB
  ```

**`Test/Core/Src/main.c`**
- Added `SPI_HandleTypeDef hspi1` global variable.
- Added `MX_SPI1_Init()` prototype and implementation (Mode 0, 8-bit, software NSS, 1 MHz).
- Added GPIO configuration for LED (PB3) in `MX_GPIO_Init()` USER CODE section.
- Added LED blink loop in `while(1)`:
  ```c
  HAL_GPIO_TogglePin(LED_GPIO_Port, LED_Pin);
  HAL_Delay(1000);
  ```

#### Bug Fix — Duplicate Symbols After CubeMX Regeneration

When the `.ioc` editor was used to add SPI1, CubeMX regenerated its own `MX_SPI1_Init()`, `hspi1` variable, and prototype **outside** USER CODE blocks. The existing USER CODE versions caused **6 duplicate definition errors**:

| Duplicate | Fix |
|---|---|
| `SPI_HandleTypeDef hspi1` in USER CODE PV | Removed |
| `MX_SPI1_Init` prototype in USER CODE PFP | Removed |
| `MX_SPI1_Init()` call in USER CODE 2 | Removed |
| `__HAL_RCC_SPI1_CLK_ENABLE()` in GPIO init | Removed (now in `hal_msp.c`) |
| SPI GPIO config (PA4–7) in GPIO init | Removed (now in `hal_msp.c`) |
| Duplicate `MX_SPI1_Init` function body in USER CODE 4 | Removed |

**Lesson:** CubeMX generates SPI GPIO and clock init in `stm32f3xx_hal_msp.c` — never put them in USER CODE blocks.

---

### 3. Project: `Impedance` — AD5940/AD5941 Impedance Measurement

#### Overview
STM32F303K8 project for impedance measurement using the **Analog Devices AD5940/AD5941** front-end IC over SPI1. Sends results via USART2 to a PC terminal.

#### Hardware Pin Mapping

| Function       | Pin  | Mode              |
|---|---|---|
| SPI1_SCK       | PB3  | AF1 (SPI1)        |
| SPI1_MISO      | PB4  | AF1 (SPI1)        |
| SPI1_MOSI      | PB5  | AF1 (SPI1)        |
| AD5940 CS      | PA8  | GPIO Output       |
| AD5940 RST     | PA4  | GPIO Output (runtime) |
| AD5940 INT/GP0 | PA0  | EXTI0 Falling     |
| USART2_TX      | PA2  | AF7 (USART2)      |
| USART2_RX      | PA15 | AF7 (USART2)      |

#### SPI1 Configuration (from `.ioc`)
- Master mode, Full-duplex, 8-bit
- CPOL=Low, CPHA=1Edge (Mode 0)
- Software NSS
- Baud rate prescaler: /64 → **125 kHz** SCK

---

#### Fix 1 — Duplicate `Src/` / `Inc/` / `Startup/` Folders

**Root cause:** The project was originally created as a bare-metal (no HAL) project. When CubeMX re-generated the project into `Core/`, the old skeleton directories remained and were compiled alongside, causing a **duplicate `main()` linker error**.

**Deleted:**
- `Impedance/Src/` — contained bare-metal `main.c`, `syscalls.c`, `sysmem.c`
- `Impedance/Inc/` — empty legacy include folder
- `Impedance/Startup/` — duplicate `startup_stm32f303k8tx.s`
- `Impedance/Debug/` — stale build artefacts referencing deleted files

---

#### Fix 2 — Missing Include Paths in `.cproject`

**Root cause:** The `.cproject` still referenced `../Inc` (the deleted legacy folder) instead of the correct CubeIDE-standard paths.

**Updated in `.cproject` (Debug & Release configurations):**

| Before | After |
|---|---|
| `../Inc` only | `../Core/Inc` |
| — | `../Drivers/STM32F3xx_HAL_Driver/Inc` |
| — | `../Drivers/STM32F3xx_HAL_Driver/Inc/Legacy` |
| — | `../Drivers/CMSIS/Device/ST/STM32F3xx/Include` |
| — | `../Drivers/CMSIS/Include` |

**Also fixed:**
- Added missing compiler defines `USE_HAL_DRIVER` and `STM32F303x8`.
- Changed source entries from `""` (whole project root) to explicit `Core` and `Drivers` folders.

---

#### Feature — AD5940 Library Integration

**Library source:** [analogdevicesinc/ad5940-examples](https://github.com/analogdevicesinc/ad5940-examples) (cloned with `--recursive` to pull the [ad5940lib](https://github.com/analogdevicesinc/ad5940lib) submodule).

**Files copied into `Impedance/Core/`:**

| File | Description |
|---|---|
| `Core/Inc/ad5940.h` | AD5940 driver header — all register definitions and API |
| `Core/Inc/Impedance.h` | Impedance application config structs and function prototypes |
| `Core/Src/AD5940Lib/ad5940.c` | Hardware-agnostic AD5940 driver implementation |
| `Core/Src/AD5940Lib/Impedance.c` | Impedance sweep application logic (DFT-based) |
| `Core/Src/AD5940Lib/AD5940Main.c` | Example entry: sweeps 100 Hz–100 kHz, prints Magnitude & Phase |
| `Core/Src/AD5940Lib/NUCLEOF303K8Port.c` | **Custom MCU port** written for STM32F303K8 |

**`NUCLEOF303K8Port.c` — what it implements:**

The AD5940 library requires 7 HAL bridge functions that must be implemented per-MCU:

| Function | Implementation |
|---|---|
| `AD5940_ReadWriteNBytes()` | `HAL_SPI_TransmitReceive(&hspi1, ...)` |
| `AD5940_CsClr()` / `AD5940_CsSet()` | `HAL_GPIO_WritePin(GPIOA, GPIO_PIN_8, ...)` |
| `AD5940_RstClr()` / `AD5940_RstSet()` | `HAL_GPIO_WritePin(GPIOA, GPIO_PIN_4, ...)` |
| `AD5940_Delay10us()` | `HAL_Delay()` (ms resolution) |
| `AD5940_GetMCUIntFlag()` / `AD5940_ClrMCUIntFlag()` | Volatile flag set by EXTI0 ISR |
| `AD5940_MCUResourceInit()` | Configures RST (PA4) and INT (PA0/EXTI0) GPIOs |
| `EXTI0_IRQHandler()` | Sets interrupt flag, clears EXTI pending bit |

**`Impedance/Core/Src/main.c` changes:**

```c
/* Includes added */
#include "ad5940.h"
#include "Impedance.h"

/* printf redirect — results sent over USART2 to PC terminal */
int _write(int file, char *data, int len) {
  HAL_UART_Transmit(&huart2, (uint8_t*)data, (uint16_t)len, HAL_MAX_DELAY);
  return len;
}

/* After peripheral init */
AD5940_MCUResourceInit(NULL);   // sets up RST + INT pins

/* In while(1) */
AD5940_Main();   // runs frequency sweep, prints results over UART
```

**Impedance sweep configuration (in `AD5940Main.c`):**
- Frequency sweep: **100 Hz → 100 kHz** (101 log-spaced points)
- RCAL reference: **10 kΩ**
- HSTIA TIA gain: **5 kΩ**
- DFT points: 16384
- ADC sample rate: 400 kSPS (SINC3 OSR=2)
- Power mode: HP (required above 80 kHz)
- Output per point: `Freq`, `RzMag [Ω]`, `RzPhase [°]` via UART

---

### 4. Git Commit History

| Commit | Description |
|---|---|
| `Initial commit` | Pushed workspace projects (Test, SPI_Loopback) |
| `feat: add SPI1 init and LED blink on PB3 (1s interval)` | Test project — SPI + LED |
| `fix: remove duplicate Src/Inc/Startup folders from Impedance project` | Removed legacy folders |
| `fix: update Impedance project include paths and source entries` | Fixed `.cproject` |
| `feat: integrate AD5940 impedance library (ADI ad5940lib)` | AD5940 full integration |
| `docs: add project changelog` | This file |

---

## Next Steps

- [ ] Add PA4 (RST) and PA0 (INT) to `Impedance.ioc` to prevent CubeMX from overwriting them on regeneration
- [ ] Connect physical AD5940/AD5941 evaluation board and verify SPI communication
- [ ] Tune RCAL value and HSTIA gain for actual target impedance range
- [ ] Parse UART output with a Python script or use SensorPal GUI
- [ ] Consider adding DMA-based SPI transfer for higher throughput
