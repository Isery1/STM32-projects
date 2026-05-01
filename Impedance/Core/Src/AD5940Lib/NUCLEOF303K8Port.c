/**
 * @file       NUCLEOF303K8Port.c
 * @brief      STM32F303K8 (Nucleo-F303K8) port for the AD5940/AD5941 library.
 * @version    V1.1.0
 * @author     Adapted from ADI NUCLEO-F411 port
 *
 * Pin mapping — all pins now configured by CubeMX via .ioc:
 *   SPI1_SCK  -> PB3 (Arduino D13)
 *   SPI1_MISO -> PB4 (Arduino D12)
 *   SPI1_MOSI -> PB5 (Arduino D11)
 *   AD5940_CS  -> PA8 (Arduino D7)  - GPIO Output, configured in .ioc
 *   AD5940_RST -> PA4 (Arduino D3)  - GPIO Output HIGH, configured in .ioc
 *   AD5940_INT -> PA0 (Arduino A0)  - GPIO EXTI0 Falling + NVIC, configured in .ioc
 *
 * NOTE: EXTI0_IRQHandler lives in stm32f3xx_it.c (CubeMX-generated).
 *       It calls AD5940_SetMCUIntFlag() which is defined here.
 *       AD5940_MCUResourceInit() is a no-op for GPIO — CubeMX handles that.
 */

#include "ad5940.h"
#include <stdio.h>
#include "stm32f3xx_hal.h"

/* ------------------------------------------------------------------ */
/*  Pin & peripheral configuration                                     */
/* ------------------------------------------------------------------ */

/* SPI peripheral — must match MX_SPI1_Init() in main.c */
#define AD5940SPI                       SPI1

/* CS — PA8, GPIO Output configured in .ioc */
#define AD5940_CS_PIN                   GPIO_PIN_8
#define AD5940_CS_GPIO_PORT             GPIOA

/* RST — PA4, GPIO Output HIGH configured in .ioc */
#define AD5940_RST_PIN                  GPIO_PIN_4
#define AD5940_RST_GPIO_PORT            GPIOA

/* GP0 Interrupt — PA0, EXTI0 Falling + NVIC configured in .ioc */
#define AD5940_GP0INT_PIN               GPIO_PIN_0

/* ------------------------------------------------------------------ */
/*  SysTick timing                                                     */
/* ------------------------------------------------------------------ */
/* STM32F303K8 runs at 8 MHz HSI (no PLL in this project) */
#define SYSTICK_CLKFREQ   8000000L

/* ------------------------------------------------------------------ */
/*  Internal state                                                     */
/* ------------------------------------------------------------------ */
/* SpiHandle is declared in main.c (hspi1) — extern it here           */
extern SPI_HandleTypeDef hspi1;

volatile static uint8_t ucInterrupted = 0;

/* ------------------------------------------------------------------ */
/*  AD5940 HAL port functions (called by ad5940.c)                    */
/* ------------------------------------------------------------------ */

/**
 * @brief  Transmit/receive N bytes over SPI (blocking).
 */
void AD5940_ReadWriteNBytes(unsigned char *pSendBuffer,
                             unsigned char *pRecvBuff,
                             unsigned long  length)
{
    HAL_SPI_TransmitReceive(&hspi1, pSendBuffer, pRecvBuff,
                             (uint16_t)length, (uint32_t)-1);
}

void AD5940_CsClr(void)
{
    HAL_GPIO_WritePin(AD5940_CS_GPIO_PORT, AD5940_CS_PIN, GPIO_PIN_RESET);
}

void AD5940_CsSet(void)
{
    HAL_GPIO_WritePin(AD5940_CS_GPIO_PORT, AD5940_CS_PIN, GPIO_PIN_SET);
}

void AD5940_RstSet(void)
{
    HAL_GPIO_WritePin(AD5940_RST_GPIO_PORT, AD5940_RST_PIN, GPIO_PIN_SET);
}

void AD5940_RstClr(void)
{
    HAL_GPIO_WritePin(AD5940_RST_GPIO_PORT, AD5940_RST_PIN, GPIO_PIN_RESET);
}

/**
 * @brief  Delay in multiples of 10 µs.
 *         HAL_Delay works in ms, so we round up.
 */
void AD5940_Delay10us(uint32_t time)
{
    /* Convert 10µs ticks -> ms, minimum 1 ms */
    uint32_t ms = (time + 99) / 100;
    if (ms == 0) ms = 1;
    HAL_Delay(ms);
}

uint32_t AD5940_GetMCUIntFlag(void)
{
    return ucInterrupted;
}

uint32_t AD5940_ClrMCUIntFlag(void)
{
    ucInterrupted = 0;
    return 1;
}

/**
 * @brief  Called from main.c after MX_GPIO_Init() and MX_SPI1_Init().
 *         CubeMX already configured PA4 (RST), PA0 (EXTI0) and NVIC,
 *         so this function only ensures the initial pin states are correct.
 */
uint32_t AD5940_MCUResourceInit(void *pCfg)
{
    /* CS and RST are both active-low — deassert them at startup */
    AD5940_CsSet();
    AD5940_RstSet();
    return 0;
}

/* ------------------------------------------------------------------ */
/*  Public flag setter — called from EXTI0_IRQHandler in              */
/*  stm32f3xx_it.c (USER CODE section)                                */
/* ------------------------------------------------------------------ */
void AD5940_SetMCUIntFlag(void)
{
    ucInterrupted = 1;
}
