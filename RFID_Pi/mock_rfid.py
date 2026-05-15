import time
import random

class MockSimpleMFRC522:
    """A mock implementation of SimpleMFRC522 to allow testing without hardware."""
    
    def __init__(self):
        print("[MOCK] Initialized Mock RFID RC522 Reader.")
        print("[MOCK] Press Enter in the console to simulate a card tap, or Ctrl+C to quit.")

    def read_id(self):
        """Simulates waiting for a card and returns a custom typed or randomly generated UID."""
        try:
            # Allow typing custom data to send to server
            user_input = input("\n[MOCK] >>> TYPE CUSTOM UID & ENTER, OR JUST PRESS ENTER FOR RANDOM <<< ").strip()
            
            if user_input:
                print(f"[MOCK] Sending Custom Simulated Card ID: {user_input}")
                return user_input
            
            # Fallback: Generate a predictable but realistic 12 digit numeric ID
            mock_id = int("".join([str(random.randint(0, 9)) for _ in range(12)]))
            print(f"[MOCK] Read simulated Card ID: {mock_id}")
            return mock_id
        except (KeyboardInterrupt, EOFError):
            # Graceful exit Simulation
            print("\n[MOCK] Reader shutdown.")
            raise KeyboardInterrupt
            
    def read(self):
        """Returns a tuple of (id, text)."""
        id = self.read_id()
        return id, "Mock Data Payload"
