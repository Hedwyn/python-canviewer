# 1.6.0

* Added support for relative timestamps in `canviewer-jsonify`. Make the timestamps relative by default when enabling timestamping. Allow enabling legacy absolute timestamp behaviour with `-abs/--absolute-time` flag.
* Added a diff option for accumulation mode. Only show the changed values of a given message instead of showing the entire message.
* Added an option to `canviewer-jsonify` to do pattern substitution on CAN IDs.

# 1.5.0

* Add an 'always-show-value' flag allowing to show value instead of name for named signal values (enum-like ones)
