import sys
import time

try:
    from mfrc522 import MFRC522
    import RPi.GPIO as GPIO
except ImportError:
    print("❌ ERROR: RPi.GPIO or mfrc522 library not installed!")
    print("Please run this command inside your Pi's active python virtual environment.")
    sys.exit(1)

def run_diagnostics():
    print("\n" + "="*50)
    print("📡 RC522 RFID HARDWARE DIAGNOSTIC UTILITY")
    print("="*50)
    print("[1/3] Initializing GPIO & SPI bus...")
    
    try:
        # Instantiate the raw underlying driver object
        reader = MFRC522()
        
        # The MFRC522 Version Register is located at 0x37.
        # It should contain a fixed manufacturer constant if SPI wiring is electrically sound.
        version = reader.Read_MFRC522(0x37)
        
        print("\n[2/3] Fetching Hardware Firmware Identification...")
        print(f"-> Register 0x37 (VersionReg) returned: 0x{version:02X}")
        
        is_valid_comms = True
        
        if version == 0x91:
            print("✅ SUCCESS: Authentic NXP MFRC522 v1.0 chip detected!")
            print("Your physical wiring & SPI pipeline are working perfectly.")
        elif version == 0x92:
            print("✅ SUCCESS: Authentic NXP MFRC522 v2.0 chip detected!")
            print("Your physical wiring & SPI pipeline are working perfectly.")
        elif version == 0x88:
            print("✅ SUCCESS: Clone/Compatible MFRC522 chip detected.")
            print("Your physical wiring & SPI pipeline are working perfectly.")
        elif version in (0x00, 0xFF):
            is_valid_comms = False
            print("\n❌ FATAL COMMUNICATION FAILURE!")
            print("The Pi reached out on the SPI line, but the RFID reader returned blank/dead bits.")
            
            print("\n⚠️ TOP 3 TROUBLESHOOTING FIXES:")
            print("-"*50)
            print("1. ⚡ DID YOU SOLDER THE PINS?")
            print("   The RC522 ships with bare header pins. You CANNOT just push the wires through.")
            print("   If you haven't melted solder to fuse the pins to the board, it WILL fail.")
            print("\n2. 🔄 CHECK MOSI & MISO WIRING!")
            print("   The most common error is swapping Pin 19 (MOSI) and Pin 21 (MISO). Double check them!")
            print("\n3. 🔌 INSPECT THE SDA (SS) LINE")
            print("   Make sure SDA is connected to Pi Physical Pin 24 (GPIO 8 / CE0).")
            print("-"*50)
        else:
            print(f"\n⚠️ UNKNOWN HARDWARE STATE (Returned 0x{version:02X})")
            print("The chip is answering, but the communication is noisy or corrupted.")
            print("Check your GND connection and make sure jumper wires are tight.")

        if is_valid_comms:
            print("\n[3/3] Entering Live Antenna Loop. Waiting for RFID Tag...")
            print("Please hold your RFID card/keyfob directly flat against the reader...")
            print("(Press Ctrl+C to exit anytime)\n")
            
            try:
                last_scan_time = 0
                while True:
                    # Send request command to antenna to look for idle cards
                    (status, TagType) = reader.MFRC522_Request(reader.PICC_REQIDL)
                    
                    if status == reader.MI_OK:
                        current_time = time.time()
                        # Throttle print output to prevent flooding
                        if current_time - last_scan_time > 1.0:
                            print("🟢 TARGET SPOTTED: RFID Signal Detected! Reading Unique ID...")
                            
                            # Perform anti-collision handshake to grab 5-byte UID array
                            (status, uid) = reader.MFRC522_Anticoll()
                            
                            if status == reader.MI_OK:
                                formatted_uid = "-".join([f"{x:02X}" for x in uid[:4]])
                                print(f"🎉 UID HARVESTED SUCCESS: [ {formatted_uid} ]")
                                last_scan_time = current_time
                            else:
                                print("⚠️ HANDSHAKE COLLISION: Saw tag but failed to verify checksum.")
                                
                    time.sleep(0.1)
            except KeyboardInterrupt:
                print("\n[SHUTDOWN] Exiting loop via user request.")
                
    except Exception as err:
        print(f"\n❌ RUNTIME CRASH: {err}")
        print("Ensure no other program (like main.py) is currently running and locking the SPI bus!")
    finally:
        GPIO.cleanup()
        print("\nGPIO Pins cleared. Diagnostic process closed.\n")

if __name__ == "__main__":
    run_diagnostics()
