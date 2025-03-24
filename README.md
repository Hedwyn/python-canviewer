# Canviewer


-----

**Table of Contents**

- [Installation](#installation)
- [Usage](#usage)
  - [Filtering](#filtering)
  - [Pagination](#pagination)
  - [Plotting signals](#plotting-signals)
  - [Live commands](#live-commands)
    - [Taking snapshots](#taking-snapshots)
    - [Navigating](#navigating)
    - [Zooming in and out](#zooming-in-and-out)
- [License](#license)

# Installation

* **From PyPi**
This package is available on PyPi and can be directly installed with:
`pip install canviewer`

* **From source**:<br>
Clone this repository and simply run `pip install .` at the root (preferrably in a virtual environment). This tool uses hatchling backend for building, you can consider using hatch as a frontend (`hatch shell`).<br>


# Usage
After installing, you can summon this utility with `canviewer`:
```shell
Usage: canviewer [OPTIONS]

  For every CAN ID found on the CAN bus, displays the data for the last
  message received. If the message is declared in one of the passed databases,
  shows the decoded data.

Options:
  -c, --channel TEXT              Name of the CAN channel to monitor
  -d, --driver TEXT               Specifies which CAN driver to use if
                                  multiple available
  -db, --databases TEXT           Path to .kcd files or to a folder containing
                                  kcd files
  -f, --filters TEXT              Either a name or a numeric ID, only passed
                                  messages will be displayed
  -s, --single-message TEXT       Tracks a single message, shows a custom
                                  table with one column per signal
  -i, --ignore-unknown-messages   Hides messages that are not declared in one
                                  of your databases
  -r, --record-signals TEXT       Records the values for a given signal,
                                  exports them to CSV on exiting. You shall
                                  pass your target signal as
                                  message_name.signal_name
  -n, --inline                    Disables full-screen
  -sf, --snapshot-format [json|csv]
                                  Format to use for snapshots
  --help                          Show this message and exit.


```

`canviewer` will show the latest data for every message received on your CAN bus.<br>
You can pass `dbc` or `kcd` databases to the tool if you want it to decode your data as follows:
```
canviewer -db path/to/afolder path/to/a_database.kcd
```

When passing a folder, `dbc` or `kcd` files will be automatically discovered in the folder. You can pass many items after the `-db` flag.<br>

If omitting the CAN channel the tool will use the default on your system: `can0` on Linux and `PCAN_USBBUS1` on Windows. You can specify the channel with `-c` flag. Same applies to the CAN driver, which can be passed with `-d`.

## Filtering
You can filter out unknown messages by passsing `-i`.<br>
You can only display some selected messages by passing their name or ID to `-f` flag (flag can be passed multiple times):
```
canviewer -db my_db.kcd -f My_Message_Name
```

## Plotting signals
You can ask `canviewer` to plot one or multiple signals in real-time by using the `pl` (`--plot`) flag. It should be apssed as`-pl message_name.signal_name` and you can pass this flag as many times as you want (one plot will be spawned per signal). The signal will also be recorded to csv file. Note that if you'd like to only record without plotting, use `-r` instead.

## Pagination
If the tables cannot fit on your screen, it will be splitted in multiple tables. You can press enter to navigate (enter goes forwards and `b` + enter goes backward, see live commands below).

## Live commands
There are a few interactive commands you can use whil canviewer is running. Not that all commands require to press enter (*to flush stdin*) to be applied, as `canviewer` is a simple textual program and not a full-fledged user interface.

### Taking snapshots
While running, press `s` + ENTER to take a snapshot. The snapshot will contain a dump of the current value for every known signal of the loaded databases. Your filters will be applied. You may select the snapshot format when starting the utility through the `-sf/--snapshot-format` flag (`csv` or `json` supported).<br>
The snapshot is created in your current working directory and will be named `snapshot_canviewer_{datetime}.{format}`.

### Navigating
To navigate through pages, you can:
* Use an empty string (`[ENTER]`) to go one page forward
* Use `b` (`b + [ENTER]`) to go one page backward
* type any page number to go to that page directly

### Zooming in and out
Use `+` and `-` to decrease or increase the number of lines per page.




# License

`canviewer` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
