# GridZeno for Home Assistant

The [GridZeno](https://gridzeno.com) dongle in Home Assistant, directly on your
home network. Home Assistant talks to the dongle; nothing goes through
GridZeno's servers, so it keeps working when the internet is down.

The integration is in beta.

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
