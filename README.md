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
- [Other utilities](#other-utilities)
  - [canviewer-jsonify](#canviewer-jsonify)
- [License](#license)

# Installation

- **From PyPi**
This package is available on PyPi and can be directly installed with:
`pip install canviewer`
**Requires python3.12+.**

- **From source**:<br>
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

- Use an empty string (`[ENTER]`) to go one page forward
- Use `b` (`b + [ENTER]`) to go one page backward
- type any page number to go to that page directly

### Zooming in and out

Use `+` and `-` to decrease or increase the number of lines per page.

# Other utilities

## canviewer-jsonify

*Linux-only*

This is an alternate entrypoint you can call with `canviewer-jsonify`. This command expects to receive the path to a database file, and will spawn one JSON file per message in a temporary folder (note: everything will be wiped on exiting the command).
This command monitors the can bus actively and does the following:

- **RX**: everytime a message is received, the corresponding JSON file will be updated with the new values. You can watch them in real time.with `tail -f` or similar commands.
- **TX**: Whenever a JSON file for TX message is edited manually, the new values will be sent automatically to the CAN bus.

> [!WARNING]
> Modifications on JSON files are monitored using inotify. Any message received on the bus that's not sent by this command itself will be considered RX and filtered out from inotify monitoring, so modifying them manually will not trigger a message send.

```shell
❯ canviewer-jsonify --help
Usage: canviewer-jsonify [OPTIONS] DATABASE

  database: Path to the database to JSONify

Options:
  -c, --channel TEXT              Name of the CAN channel to monitor
  -sp, --substitute-prefix TEXT   A replace pattern to apply at the beginning
                                  of CAN IDs
  -l, --log-level [critical|fatal|error|warn|warning|info|debug|notset]
                                  Log level to apply
  -a, --accumulate                When passed, stores all passed values in the
                                  message JSON file instead of only the last
                                  one
  -d, --diff                      In accumulate mode, only appends the values
                                  that changed instead of appending
                                  everything. If timestamping is on the new
                                  timestamp is always shown
  -o, --output-folder TEXT        If passed, the temp folder for JSON files
                                  will be created in this location. If not, it
                                  will be created somewhere in /tmp
  -p, --preserve-files            Whether the temp folder and its JSON files
                                  should be deleted on exit. Disabled by
                                  default.
  -t, --timestamps                Writes last reception timestamp in the JSON
                                  file for message, in a LAST_RECEIVED key.
  -abs, --absolute-time           Uses absolute time instead of relative for
                                  timestamps.
  -raw, --show-raw-data           Includes raw data (ID and payload) in the
                                  generated JSON
  -no-rx, --disable-rx            Does not monitor RX messages and uses the
                                  model only to send message. Can be used when
                                  trying to override RX messages, as they are
                                  by default not overwritable when being
                                  monitored
  --help                          Show this message and exit.
```

 (e.g.) If calling `canviewer-jsonify my_database.kcd` you should get something like this:

 ```shell
Path to model:
/tmp/tmp0zkw00sm
Use Ctrl + C to leave
 ```

Simply `cd` to the displayed path and you should find all the JSON files for the messages in the passed database there. Files should be named following the schema `{message_name}.json`.

### Features

`canviewer-jsonify` supports a few variations in the few it operates:

- by default it shows the last (current) value of each message as a single dictionary in the JSON file. You can switch to `accumulate` mode (`-a/--accumulate`), in which all the history of values is shown as a list.
- When using `accumulate`, you can show only the changed values by enabling diff mode (`-d/--diff`). This can be used to detect rising edges and value getting updated easily.
- You can include the timestamp in the generated JSON by passing `-t/--timestamps`. The timestamp will be appended as a `$timestamp` key (leading '$' being there to differentiate it from normal signal names).
- Timestamps are relative by default (to the very first message received). To enable absolute time instead, use `-a/--absolute`
- You can modify pass a substitution pattern for the prefix of a given message with `-sp/--substitute-prefix`. For example, if you messages (in hex, 2 bits form) start with `123` and you'd like to replace with `ABC` for decoding, use `sp "123:ABC"`. Use wildcard to match any character, for example, passing `sp 1*3:A*C` on `0x17301234` will return `0xA7C01234`.

### Watching values in real-time

To follow values for a given RX message, using `watch` command is recommended:

```
watch -n 1 cat Bootloader_Identification.json
```

> [!NOTE]  
> -n defines the period at which the output should be refreshed, here 1 second

# License

`canviewer` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
