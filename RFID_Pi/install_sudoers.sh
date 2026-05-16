#!/bin/bash
# Run this once on the Raspberry Pi as root to allow the kiosk app to
# control display brightness without a password prompt.
#
# Usage:  sudo bash install_sudoers.sh
#
# After running, the kiosk app can write to the backlight sysfs file via:
#   echo VALUE | sudo tee /sys/class/backlight/rpi_backlight/brightness

RULE_FILE="/etc/sudoers.d/kiosk-brightness"
BACKLIGHT_PATHS=(
    "/sys/class/backlight/rpi_backlight/brightness"
    "/sys/class/backlight/soc:backlight/brightness"
)

# Detect the user running the kiosk (default: pi)
KIOSK_USER="${1:-pi}"

echo "Installing brightness sudoers rule for user: $KIOSK_USER"

{
  echo "# Allow kiosk user to set display brightness without password"
  for path in "${BACKLIGHT_PATHS[@]}"; do
    echo "$KIOSK_USER ALL=(root) NOPASSWD: /usr/bin/tee $path"
  done
} > "$RULE_FILE"

chmod 440 "$RULE_FILE"
echo "Installed: $RULE_FILE"
echo "Done. The brightness slider in the kiosk app will now work without root login."
