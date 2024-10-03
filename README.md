# Canviewer


-----

**Table of Contents**

- [Installation](#installation)
- [Usage](#installation)
- [License](#license)

## Installation

* **From source**:<br>
Clone this repository and simply run `pip install .` at the root (preferrably in a virtual environment). This tool uses hatchling backend for building, you can consider using hatch as a frontend (`hatch shell`).<br>
* **As a dependency of other packages**:<br>
Add the following dependency to your `requirements.txt` or `pyproject.toml`:
```
  canviewer@git+ssh://git@github.com/Hedwyn/canviewer",
```
Note that you need a valid git SSH access.

## Usage
After installing, you can summon this utility with `canviewer`:
```shell
Usage: canviewer [OPTIONS]

  For every CAN ID found on the CAN bus, displays the data for the last
  message received. If the message is declared in one of the passed databases,
  shows the decoded data.

Options:
  -c, --channel TEXT             Name of the CAN channel to monitor
  -d, --driver TEXT              Specifies which CAN driver to use if multiple
                                 available
  -db, --databases TEXT          Path to .kcd files or to a folder containing
                                 kcd files
  -f, --filters TEXT             Either a name or a numeric ID, only passed
                                 messages will be displayed
  -i, --ignore-unknown-messages  Hides messages that are not declared in one
                                 of your databases
  --help                         Show this message and exit.
```

`canviewer` will show the latest data for every message received on your CAN bus.<br>
You can pass `dbc` or `kcd` databases to the tool if you want it to decode your data as follows:
```
canviewer -db path/to/afolder path/to/a_database.kcd
```

When passing a folder, `dbc` or `kcd` files will be automatically discovered in the folder. You can pass many items after the `-db` flag.<br>

If omitting the CAN channel the tool will use the default on your system: `can0` on Linux and `PCAN_USBBUS1` on Windows. You can specify the channel with `-c` flag. Same applies to the CAN driver, which can be passed with `-d`.

### Filtering
You can filter out unknown messages by passsing `-i`.<br>
You can only display some selected messages by passing their name or ID to `-f` flag (flag can be passed multiple times):
```
canviewer -db my_db.kcd -f My_Message_Name
``` 

## License

`canviewer` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
