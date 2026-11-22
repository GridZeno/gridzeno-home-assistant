# GridZeno for Home Assistant

The [GridZeno](https://gridzeno.com) dongle in Home Assistant, directly on your
home network. Home Assistant talks to the dongle; nothing goes through
GridZeno's servers, so it keeps working when the internet is down.

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=GridZeno&repository=gridzeno-home-assistant&category=integration)

## What you get

- Solar, consumption, grid import and export, the battery, each panel string
  and the temperatures, every 5 seconds.
- Energy counters for Home Assistant's energy dashboard, kept by the dongle
  itself, and today's totals.
- GridZeno's plan: what it does now and what comes next.
- With Smart or Pro: your own buy and sell prices for today and tomorrow, the
  cheapest and the most expensive quarter of each day, and GridZeno's solar
  forecast, also as the energy dashboard's forecast.
- Signals for your own automations: **Cheap hour now**, **Surplus now** and
  **Negative price now**, so a Shelly, Zigbee or Tuya switch in Home Assistant
  can follow GridZeno (see [Signals](#signals-for-your-automations)).
- When you control the plant yourself (Manual under Automation in GridZeno)
  and have given this Home Assistant the right to: actions to charge from the
  grid, hold the battery, sell from the battery and limit export, each for a
  set time, through the same safety limits as GridZeno's own control, and the
  same as entities for a dashboard (see [Control](#control)). While GridZeno
  controls the plant, Home Assistant only reads.
- A repair in Home Assistant when the dongle speaks another version of the
  local API, saying whether the integration or the dongle needs updating.

## Requirements

- Home Assistant 2025.10 or newer, and [HACS](https://hacs.xyz).
- A GridZeno Dongle on the same network as Home Assistant, with firmware that
  includes the local API. The dongle updates itself from GridZeno.
- A GridZeno account. Reading works on every subscription, Light included.

## Installation

1. Click the button above, or in HACS open the menu, choose
   **Custom repositories** and add
   `https://github.com/GridZeno/gridzeno-home-assistant` with the type
   **Integration**.
2. Download **GridZeno** in HACS and restart Home Assistant.
3. Home Assistant finds the dongle on your network. If it doesn't, add
   GridZeno under **Settings > Devices & services** and type the dongle's
   address.
4. Home Assistant shows a six-digit code. Open GridZeno, go to
   **Integrations > Home Assistant**, and approve the connection when GridZeno
   shows the same code. Choose whether Home Assistant may only read, or also
   control.

## Signals for your automations

Three on/off sensors and one time, worked out in Home Assistant from what
the dongle already sends. They switch nothing themselves: you use them in
your own automations, for a water heater, a pool pump or a heater on a
Shelly, Zigbee or Tuya switch.

| Entity | On when |
|---|---|
| **Cheap hour now** | the hour is one of the day's cheapest hours, by your buy price (the spot price when you have not chosen a grid company in GridZeno). 4 hours a day unless you choose another number. Each day counts by itself, and only the hours with known prices. The attribute `hours` lists them with their price. |
| **Next cheap hours** (a time) | when the next cheap hours begin, joined where they follow each other; the attribute `end` is when they end. While a cheap hour runs, its start. |
| **Surplus now** | you sell more than 1000 W (or the power you choose) in two readings in a row, and the battery is full (at the highest level you set in GridZeno) or there is no battery. It goes off when the house buys from the grid or the battery discharges, two readings in a row, or the battery falls 5 points below full. A consumer that uses the surplus does not switch itself off as long as the sun covers it. It stays on or off at least 5 minutes, also over a restart of Home Assistant. |
| **Negative price now** | your buy price is below zero. |

Cheap hour now, Next cheap hours and Negative price now need the prices, as
the price sensors do: Smart or Pro. Surplus now reads only the measurements
and works on every subscription, Light included.

Choose the number of cheap hours (1-12) and the surplus power under
**Settings > Devices & services > GridZeno > Configure**.

Example: turn on the water heater when **Cheap hour now** is on, and off
again when it goes off.

```yaml
automation:
  - alias: "Water heater in the cheap hours"
    triggers:
      - trigger: state
        entity_id: binary_sensor.gridzeno_gz_a1b2c3_cheap_hour_now
        to: ["on", "off"]
    actions:
      - action: >-
          {{ 'switch.turn_on' if trigger.to_state.state == 'on' else 'switch.turn_off' }}
        target:
          entity_id: switch.water_heater
```

Replace `gz_a1b2c3` with your dongle's number and `switch.water_heater` with
your own switch. The same works with `binary_sensor.gridzeno_gz_a1b2c3_surplus_now`
to use the sun's surplus, or both with a condition.

## Control

With the right to control, the dongle gets a **Command** select (The
inverter's own settings, Charge from the grid, Hold the battery, Sell from
the battery, Limit selling to the grid) and five numbers for what the next
command asks for: charge target, sell power, sell floor (empty: your minimum
in GridZeno), duration and export limit. The numbers are kept over a restart.
Choosing a command sends it with them; changing a number does not change a
running command. The select shows the command running now.

## Actions

| Action | Fields |
|---|---|
| `gridzeno.charge_from_grid` | dongle, target %, duration (15-720 min), export limit % (optional) |
| `gridzeno.hold_battery` | dongle, duration, export limit % (optional) |
| `gridzeno.sell_from_battery` | dongle, power W, floor % (optional), duration, export limit % (optional) |
| `gridzeno.limit_export` | dongle, export limit %, duration |
| `gridzeno.release` | dongle |

Every command stops by itself when its duration ends, and at once when
GridZeno takes over. A value outside the battery limits you set in GridZeno
is refused, not adjusted.

## Security

Pairing is an elliptic-curve key exchange: the key Home Assistant and the
dongle share never crosses the network, and GridZeno's servers never see it.
The six-digit code both sides show is what you compare when approving; it is
built so nobody between Home Assistant and the dongle can make the two codes
match. Every request and every answer is signed with the key, reads carry a
counter and commands a one-time number from the dongle, so nothing can be
replayed. The traffic itself is plain HTTP on your network: others on the
network can see the readings, as they can on the dongle's own page, but they
cannot ask the dongle anything or control it.

## More

- The guide on the website: [GridZeno in Home Assistant](https://gridzeno.com/dk/en/guides/home-assistant/)
- Questions and bugs: [open an issue](https://github.com/GridZeno/gridzeno-home-assistant/issues), or write to contact@gridzeno.com.
