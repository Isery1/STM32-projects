################################################################################
# Automatically-generated file. Do not edit!
# Toolchain: GNU Tools for STM32 (14.3.rel1)
################################################################################

# Add inputs and outputs from these tool invocations to the build variables 
C_SRCS += \
../Core/Src/AD5940Lib/AD5940Main.c \
../Core/Src/AD5940Lib/Impedance.c \
../Core/Src/AD5940Lib/NUCLEOF303K8Port.c \
../Core/Src/AD5940Lib/ad5940.c 

OBJS += \
./Core/Src/AD5940Lib/AD5940Main.o \
./Core/Src/AD5940Lib/Impedance.o \
./Core/Src/AD5940Lib/NUCLEOF303K8Port.o \
./Core/Src/AD5940Lib/ad5940.o 

C_DEPS += \
./Core/Src/AD5940Lib/AD5940Main.d \
./Core/Src/AD5940Lib/Impedance.d \
./Core/Src/AD5940Lib/NUCLEOF303K8Port.d \
./Core/Src/AD5940Lib/ad5940.d 


# Each subdirectory must supply rules for building sources it contributes
Core/Src/AD5940Lib/%.o Core/Src/AD5940Lib/%.su Core/Src/AD5940Lib/%.cyclo: ../Core/Src/AD5940Lib/%.c Core/Src/AD5940Lib/subdir.mk
	arm-none-eabi-gcc "$<" -mcpu=cortex-m4 -std=gnu11 -g3 -DDEBUG -DUSE_HAL_DRIVER -DSTM32F303x8 -DSTM32 -DSTM32F303K8Tx -DSTM32F3 -DCHIPSEL_594X -c -I../Core/Inc -I../Drivers/STM32F3xx_HAL_Driver/Inc -I../Drivers/STM32F3xx_HAL_Driver/Inc/Legacy -I../Drivers/CMSIS/Device/ST/STM32F3xx/Include -I../Drivers/CMSIS/Include -O0 -ffunction-sections -fdata-sections -Wall -fstack-usage -fcyclomatic-complexity -MMD -MP -MF"$(@:%.o=%.d)" -MT"$@" --specs=nano.specs -mfpu=fpv4-sp-d16 -mfloat-abi=hard -mthumb -o "$@"

clean: clean-Core-2f-Src-2f-AD5940Lib

clean-Core-2f-Src-2f-AD5940Lib:
	-$(RM) ./Core/Src/AD5940Lib/AD5940Main.cyclo ./Core/Src/AD5940Lib/AD5940Main.d ./Core/Src/AD5940Lib/AD5940Main.o ./Core/Src/AD5940Lib/AD5940Main.su ./Core/Src/AD5940Lib/Impedance.cyclo ./Core/Src/AD5940Lib/Impedance.d ./Core/Src/AD5940Lib/Impedance.o ./Core/Src/AD5940Lib/Impedance.su ./Core/Src/AD5940Lib/NUCLEOF303K8Port.cyclo ./Core/Src/AD5940Lib/NUCLEOF303K8Port.d ./Core/Src/AD5940Lib/NUCLEOF303K8Port.o ./Core/Src/AD5940Lib/NUCLEOF303K8Port.su ./Core/Src/AD5940Lib/ad5940.cyclo ./Core/Src/AD5940Lib/ad5940.d ./Core/Src/AD5940Lib/ad5940.o ./Core/Src/AD5940Lib/ad5940.su

.PHONY: clean-Core-2f-Src-2f-AD5940Lib

