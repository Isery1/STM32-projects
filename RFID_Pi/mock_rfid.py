"""
Software stand-in for :class:`mfrc522.SimpleMFRC522` when ``RPi.GPIO`` / SPI are not available.

Typical use: development laptops or automated tests. The mock prompts on stdin (or returns random UIDs)
so the rest of the pipeline (:mod:`auth`, HTTP) can be exercised without hardware.
"""

import time
import random


class MockSimpleMFRC522:
    """Minimal API compatibility: only ``read_id`` and ``read`` are implemented like the real reader."""

    def __init__(self) -> None:
        """Print a short reminder that scans are simulated and require console interaction."""
        print("[MOCK] Initialized Mock RFID RC522 Reader.")
        print("[MOCK] Press Enter in the console to simulate a card tap, or Ctrl+C to quit.")

    def read_id(self):
        """
        Block until the operator types a UID or presses Enter for a random 12-digit decimal ID.

        Returns:
            str | int: Whatever should be sent upstream (``str`` if typed, ``int`` if random).

        Raises:
            KeyboardInterrupt: Propagated when the user aborts while waiting for input.
        """
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
        """
        Mimic the tuple API of the hardware driver: ``(numeric_id, unused_text_block)``.

        Returns:
            tuple: ``(id from :meth:`read_id`, short placeholder string)``
        """
        id = self.read_id()
        return id, "Mock Data Payload"
