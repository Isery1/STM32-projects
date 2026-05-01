/**
 * @file       NUCLEOF303K8Port.c
 * @brief      STM32F303K8 (Nucleo-F303K8) port for the AD5940/AD5941 library.
 * @version    V1.0.0
 * @author     Adapted from ADI NUCLEO-F411 port
 *
 * Pin mapping (SPI1 already configured in .ioc):
 *   SPI1_SCK  -> PB3 (Arduino D13)
 *   SPI1_MISO -> PB4 (Arduino D12)
 *   SPI1_MOSI -> PB5 (Arduino D11)
 *   AD5940_CS  -> PA8 (Arduino D7)  - GPIO output, already configured in .ioc
 *   AD5940_RST -> PA4 (Arduino D3)  - GPIO output, add manually
 *   AD5940_INT -> PA0 (Arduino A0)  - GPIO EXTI falling edge
 *
 * IMPORTANT: After adding this file, update the .ioc to configure PA4 as
 * GPIO_Output and PA0 as GPIO_EXTI_Falling, then re-generate.
 */

#include "ad5940.h"
#include <stdio.h>
#include "stm32f3xx_hal.h"

/* ------------------------------------------------------------------ */
/*  Pin & peripheral configuration                                     */
/* ------------------------------------------------------------------ */

/* SPI peripheral — must match MX_SPI1_Init() in main.c */
#define AD5940SPI                       SPI1

/* CS — PA8 is already configured as GPIO_Output in .ioc */
#define AD5940_CS_PIN                   GPIO_PIN_8
#define AD5940_CS_GPIO_PORT             GPIOA

/* RST — PA4, configure as GPIO_Output in .ioc */
#define AD5940_RST_PIN                  GPIO_PIN_4
#define AD5940_RST_GPIO_PORT            GPIOA
#define AD5940_RST_GPIO_CLK_ENABLE()    __HAL_RCC_GPIOA_CLK_ENABLE()

/* GP0 Interrupt from AD5940 — PA0, EXTI line 0 */
#define AD5940_GP0INT_PIN               GPIO_PIN_0
#define AD5940_GP0INT_GPIO_PORT         GPIOA
#define AD5940_GP0INT_GPIO_CLK_ENABLE() __HAL_RCC_GPIOA_CLK_ENABLE()
#define AD5940_GP0INT_IRQn              EXTI0_IRQn

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
 * @brief  Initialise MCU resources needed by the AD5940 driver.
 *         SPI1 and CS/SCK/MISO/MOSI are already configured by MX_SPI1_Init()
 *         and MX_GPIO_Init() in main.c.
 *         This function only adds RST and INT pins.
 */
uint32_t AD5940_MCUResourceInit(void *pCfg)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};

    /* RST pin — PA4 as push-pull output */
    AD5940_RST_GPIO_CLK_ENABLE();
    HAL_GPIO_WritePin(AD5940_RST_GPIO_PORT, AD5940_RST_PIN, GPIO_PIN_SET);
    GPIO_InitStruct.Pin   = AD5940_RST_PIN;
    GPIO_InitStruct.Mode  = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull  = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init(AD5940_RST_GPIO_PORT, &GPIO_InitStruct);

    /* INT pin — PA0 as EXTI falling edge */
    AD5940_GP0INT_GPIO_CLK_ENABLE();
    GPIO_InitStruct.Pin   = AD5940_GP0INT_PIN;
    GPIO_InitStruct.Mode  = GPIO_MODE_IT_FALLING;
    GPIO_InitStruct.Pull  = GPIO_PULLUP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init(AD5940_GP0INT_GPIO_PORT, &GPIO_InitStruct);

    /* Enable EXTI0 interrupt */
    HAL_NVIC_SetPriority(AD5940_GP0INT_IRQn, 5, 0);
    HAL_NVIC_EnableIRQ(AD5940_GP0INT_IRQn);

    /* Deassert CS and RST (both active-low) */
    AD5940_CsSet();
    AD5940_RstSet();

    return 0;
}

/* ------------------------------------------------------------------ */
/*  External interrupt handler for AD5940 GP0 → PA0 (EXTI0)          */
/* ------------------------------------------------------------------ */
void EXTI0_IRQHandler(void)
{
    ucInterrupted = 1;
    __HAL_GPIO_EXTI_CLEAR_IT(AD5940_GP0INT_PIN);
}
