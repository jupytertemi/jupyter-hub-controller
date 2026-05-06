#!/bin/bash

BUTTON_CHIP="gpiochip3"
BUTTON_LINE=17          # PIN_11
LED_PIN="PIN_13"

INTERVAL=0.2
HOLD_TIME=3             # seconds
REQUIRED_COUNT=$(echo "$HOLD_TIME / $INTERVAL" | bc)

PRESS_COUNT=0
BLINK_PID=""
LAST_VALUE=-1   # 2026-05-07 edge-only logging — was logging every 200ms = 153M lines/year/hub

echo "=== Button reset controller started ==="

blink_led() {
    echo "💡 LED blinking started"
    while true; do
        gpioset $(gpiofind $LED_PIN)=1
        sleep 0.5
        gpioset $(gpiofind $LED_PIN)=0
        sleep 0.5
    done
}

stop_blink() {
    if [ -n "$BLINK_PID" ] && kill -0 "$BLINK_PID" 2>/dev/null; then
        kill "$BLINK_PID"
        wait "$BLINK_PID" 2>/dev/null
        BLINK_PID=""
        gpioset $(gpiofind $LED_PIN)=0
        echo "💡 LED blinking stopped"
    fi
}

while true; do
    VALUE=$(gpioget $BUTTON_CHIP $BUTTON_LINE)
    if [ "$VALUE" != "$LAST_VALUE" ]; then
        echo "Button value: $VALUE (count=$PRESS_COUNT)"
        LAST_VALUE=$VALUE
    fi

    if [ "$VALUE" -eq 1 ]; then
        PRESS_COUNT=$((PRESS_COUNT + 1))

        if [ "$PRESS_COUNT" -eq "$REQUIRED_COUNT" ]; then
            echo "⏳ Button held for ${HOLD_TIME}s → START RESET"

            blink_led &
            BLINK_PID=$!

            echo "🚀 Running: Reset jupyter homeassistant"
            cd /root/jupyter-container

            rm -f hass/config/automations.yaml
            rm -f hass/config/scripts.yaml
            rm -f hass/config/scenes.yaml
            rm -rf hass/config/.storage
            rm -f hass/config/home-assistant_v2.db*

            echo []> hass/config/automations.yaml
            echo []> hass/config/scripts.yaml
            echo []> hass/config/scenes.yaml

            docker restart jupyter_homeassistant

            echo "🚀 Running: systemctl start jupyter-hub-reset.service"
            systemctl start jupyter-hub-reset.service
            RET=$?

            echo "🧾 hub-manager exit code: $RET"

            stop_blink

            if [ "$RET" -eq 0 ]; then
                echo "✅ Reset successful"
            else
                echo "❌ Reset failed"
            fi

            PRESS_COUNT=0
        fi
    else
        PRESS_COUNT=0
    fi

    sleep $INTERVAL
done
